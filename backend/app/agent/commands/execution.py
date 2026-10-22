"""命令执行入口 —— ``execute(agent, line, ...)``：直接对 Agent 执行，无模型轮次。

语义（issue #475 / DSH ``ctx.commands.execute``）：
1. **解析**：``/name args``；不是命令 / 缺命令名 → 显式错误（绝不回落给模型）。
2. **解析命令**：agent 作用域优先、全局兜底；未知命令 → ``unknown_command``。
3. **权限**：``permission="admin"`` 仅组织管理员/平台超管可执行（可见性同源）。
4. **参数**：位置绑定 + required/choices/default；错误带 ``error_field``。
5. **并发**：同一会话同一时间只允许一个命令（进程内 asyncio 锁；多 worker
   部署的边界见下）。
6. **超时 / 取消**：handler 由 ``asyncio.wait_for`` 包裹（命令自带 timeout）；
   调用方取消（SSE 断连）按 ``CancelledError`` 原样传播，不吞。
7. **审计**：who/where/what/outcome/duration 进 INFO 日志（不含命令输出正文）。

返回 ``CommandExecution`` 是**总返回**：预期失败（未知/权限/参数/忙/超时/
handler 预期失败）都收敛为结构化结果，不向 SSE 流抛半截异常；仅
``CancelledError`` 逃逸。HTTP 端点按 ``error_code`` 映射状态码，SSE 出口发
``command_error`` 事件。

并发锁的诚实边界：锁是**进程内**的（与 skills/scheduler 的单实例语义一致）。
多 worker 部署需要 sticky session 或后续换成 Redis 锁；本 issue 不引入分布式
锁，也不把"进程内锁"伪装成全局互斥。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator

from app.agent.commands.parser import CommandSyntaxError, bind_args, parse_line
from app.agent.commands.registry import CommandRegistry, agent_scope_for_session
from app.agent.commands.types import (
    DEFAULT_COMMAND_TIMEOUT,
    ERROR_COMMAND_BUSY,
    ERROR_COMMAND_FAILED,
    ERROR_COMMAND_TIMEOUT,
    ERROR_PERMISSION_DENIED,
    ERROR_UNKNOWN_COMMAND,
    MAX_COMMAND_TIMEOUT,
    CommandContext,
    CommandExecution,
    CommandFailure,
)
from app.agent.events import AgentEvent
from app.core.log_safety import log_safe
from app.core.prometheus import AGENT_COMMAND_EXECUTIONS
from app.core.security import is_org_admin
from app.services.session_service import load_accessible_session, resolve_session_access_scope

logger = logging.getLogger(__name__)

# 进程内"同会话单命令"锁（键 = 会话 id）。空锁在释放时移除，避免随会话增长。
_session_locks: dict[str, asyncio.Lock] = {}


async def resolve_is_admin(db, user, organization_id: int | None) -> bool:
    """统一管理员判定（组织管理员 / 平台超管）；失败 **fail closed** 为 False。

    fail-closed 的理由：管理命令的授权面不能因 DB 抖动而放宽——判定失败时
    宁可按普通用户处理（管理命令被拒），普通命令不受影响。
    """
    try:
        return await is_org_admin(db, user, organization_id)
    except Exception as exc:  # noqa: BLE001 — 授权判定失败必须 fail closed
        logger.warning("命令管理员判定失败（按非管理员处理）：%s", type(exc).__name__)
        return False


async def resolve_session_scope_for_command(
    db, user, session_id: str | None
) -> tuple[bool, str | None]:
    """聊天入口的命令会话归属前置校验（与 execute 端点同口径的纵深防御）。

    返回 ``(ok, agent_scope)``：
    - 未指定会话 → ``(True, None)``（合法：全局作用域命令）；
    - 指定且可见 → ``(True, "session:<id>")``；
    - 指定但不存在 / 他人 / 跨组织 / 存储不可用 → ``(False, None)``（fail closed）。

    数据读取 handler（/status、/export）自身仍走 ``session_access_conditions``
    兜底；此处只是把拒绝提前到命令执行之前，避免把不可见会话 id 当作命令
    作用域使用。
    """
    if not session_id:
        return True, None
    try:
        access = await resolve_session_access_scope(user, db)
        session = await load_accessible_session(db, session_id, user, access)
    except Exception as exc:  # noqa: BLE001 — 归属无法判定时拒绝
        logger.warning("命令会话归属校验失败（fail-closed）：%s", type(exc).__name__)
        return False, None
    if session is None:
        return False, None
    return True, agent_scope_for_session(session_id)


async def execute(
    agent: CommandContext,
    line: str,
    *,
    registry: CommandRegistry | None = None,
) -> CommandExecution:
    """对 ``agent`` 执行一行命令（无模型轮次、不写会话）。"""
    if registry is None:
        from app.agent.commands import command_registry

        registry = command_registry

    started = time.perf_counter()
    text = (line or "").strip()
    execution = CommandExecution(command="", line=text, success=False)
    agent_scope = agent_scope_for_session(agent.session_id) if agent.session_id else None
    agent.scope = agent_scope or "global"
    agent.registry = registry

    def fail(
        code: str,
        message: str,
        *,
        field_name: str = "",
        data: dict | None = None,
    ) -> CommandExecution:
        execution.success = False
        execution.error_code = code
        execution.error_message = message
        execution.error_field = field_name
        if data:
            execution.data = data
        execution.duration_ms = (time.perf_counter() - started) * 1000
        _audit(agent, execution)
        return execution

    try:
        name, tokens = parse_line(text)
    except CommandSyntaxError as exc:
        return fail(exc.code, exc.message, field_name=exc.field_name)

    command, scope = registry.resolve(name, agent_scope=agent_scope)
    if command is None:
        return fail(ERROR_UNKNOWN_COMMAND, f"未知命令：/{name}。输入 /help 查看可用命令。")

    execution.command = command.name
    execution.scope = scope

    if command.permission == "admin" and not agent.is_admin:
        return fail(ERROR_PERMISSION_DENIED, f"命令 /{command.name} 仅限组织管理员使用")

    try:
        args = bind_args(command, tokens)
    except CommandSyntaxError as exc:
        return fail(exc.code, exc.message, field_name=exc.field_name)
    execution.args = args

    # 超时口径：非数值 / <=0 回退默认值；上限 MAX_COMMAND_TIMEOUT（防呆）。
    try:
        timeout = float(command.timeout)
    except (TypeError, ValueError):
        timeout = DEFAULT_COMMAND_TIMEOUT
    if timeout <= 0:
        timeout = DEFAULT_COMMAND_TIMEOUT
    timeout = min(timeout, MAX_COMMAND_TIMEOUT)

    lock: asyncio.Lock | None = None
    if agent.session_id:
        lock = _session_locks.setdefault(agent.session_id, asyncio.Lock())
        if lock.locked():
            return fail(ERROR_COMMAND_BUSY, "当前会话已有命令在执行，请稍后再试")
        await lock.acquire()

    try:
        try:
            result = await asyncio.wait_for(command.handler(agent, args), timeout=timeout)
        except TimeoutError:
            return fail(
                ERROR_COMMAND_TIMEOUT,
                f"命令 /{command.name} 执行超时（{timeout:g}s）",
            )
        except CommandFailure as exc:
            return fail(exc.code, exc.message, field_name=exc.field_name, data=exc.data or None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — handler 异常收敛为结构化失败
            logger.error(
                "命令执行异常 (command=%s, user=%s, org=%s, session=%s): %s",
                command.name,
                agent.user_id,
                agent.organization_id,
                log_safe(agent.session_id or ""),
                log_safe(exc),
                exc_info=True,
            )
            return fail(ERROR_COMMAND_FAILED, "命令执行失败，请稍后重试")
    finally:
        if lock is not None:
            lock.release()
            if not lock.locked():
                _session_locks.pop(agent.session_id, None)

    execution.success = True
    execution.content = result.content
    execution.data = result.data
    execution.duration_ms = (time.perf_counter() - started) * 1000
    _audit(agent, execution)
    return execution


def _audit(agent: CommandContext, execution: CommandExecution) -> None:
    """审计行 + 指标：身份 + 会话 + 命令 + 结果摘要（不记录命令输出正文）。"""
    logger.info(
        "命令执行 (command=%s, user=%s, org=%s, session=%s, scope=%s, success=%s, "
        "duration_ms=%.1f, error_code=%s)",
        execution.command or execution.line[:40],
        agent.user_id,
        agent.organization_id,
        log_safe(agent.session_id or ""),
        execution.scope,
        execution.success,
        execution.duration_ms,
        execution.error_code or "-",
    )
    # 指标标签只含命令名与结果码（低基数；不含用户/会话/参数）。
    AGENT_COMMAND_EXECUTIONS.labels(
        command=execution.command or "unknown",
        result="success" if execution.success else (execution.error_code or "failed"),
    ).inc()


async def iter_command_events(
    agent: CommandContext,
    line: str,
    *,
    registry: CommandRegistry | None = None,
) -> AsyncGenerator[AgentEvent, None]:
    """命令执行 → 既有 SSE 事件通道（command_start/result/error，之后由端点发 done）。

    ``command_start`` 先于执行发出（客户端可立即显示"正在执行"）；执行结果
    以 ``command_result``（成功）或 ``command_error``（失败）外发。事件字段
    复用 events.py 的既有信封（content / command_name / command_data /
    error_type），不另造协议。
    """
    text = (line or "").strip()
    parsed_name = ""
    if text.startswith("/"):
        body = text[1:].strip().split()
        parsed_name = body[0].lower() if body else ""
    yield AgentEvent(
        type="command_start",
        content=f"执行命令 {text}" if text else "执行命令",
        command_name=parsed_name,
    )
    execution = await execute(agent, line, registry=registry)
    if execution.success:
        yield AgentEvent(
            type="command_result",
            content=execution.content,
            command_name=execution.command,
            command_data=execution.data,
        )
    else:
        yield AgentEvent(
            type="command_error",
            content=execution.error_message,
            command_name=execution.command or parsed_name,
            error_type=execution.error_code,
        )
