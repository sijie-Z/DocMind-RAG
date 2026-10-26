"""命令执行入口 —— ``execute(agent, line, ...)``：直接对 Agent 执行，无模型轮次。

语义（issue #475 / #498 / DSH ``ctx.commands.execute``）：
1. **解析**：``/name args``（前导 ``/`` 可选）；不是命令 / 缺命令名 → 显式错误
   （绝不回落给模型）。
2. **解析命令**：agent 作用域优先、全局兜底；未知命令 → ``unknown_command``。
3. **权限**：``permission="admin"`` 仅组织管理员/平台超管可执行（可见性同源）。
4. **参数**：位置绑定 + required/choices/default；错误带 ``error_field``。
5. **并发（issue #498）**：Redis 分布式锁
   ``SET cmd:lock:{session_id or user_id} <owner_token> NX EX 30``；执行完用
   Lua **compare-and-del** 只释放自己的锁（TTL 兜底防死锁）。拿不到锁 →
   409 ``command_busy``。多 worker 部署下同一会话仍互斥。
6. **幂等（issue #498）**：可选 ``idempotency_key`` →
   ``cmd:idem:{user_id}:{key}``。先解析命令并绑定参数，再以规范化命令、scope、
   有效 org/session 和 args 计算 SHA-256 指纹；空 session 统一归一为 None。首个请求写入
   v2 **pending 信封**（短 TTL，默认 300s，须覆盖单命令超时上限；worker 崩溃后可自愈
   重试），并记录已加载原 session 的 resource_org_id。完成后由原 owner CAS 写入封装响应
   的 completed 信封（TTL 24h）；**串行**同指纹请求在复核原会话资源归属与当前权限后
   返回同一 ``execution_id``，handler 只执行一次。**执行中并发**的同指纹请求最多等待
   ~1s，不同指纹或 legacy/损坏条目立即 409 ``command_busy``。
   失败不缓存（compare-and-del 自己的 pending，允许重试）。
7. **Redis 不可用**：**fail-closed** 返回 ``service_unavailable``（HTTP 503），
   绝不退回进程内锁假装互斥。
8. **超时 / 取消**：handler 由 ``asyncio.wait_for`` 包裹（命令自带 timeout）；
   调用方取消（SSE 断连）按 ``CancelledError`` 原样传播，不吞；锁在 finally
   中 compare-and-del 释放。
9. **审计**：who/where/what/outcome/duration 进 INFO 日志（不含命令输出正文）。

返回 ``CommandExecution`` 是**总返回**：预期失败（未知/权限/参数/忙/超时/
handler 预期失败/依赖不可用）都收敛为结构化结果，不向 SSE 流抛半截异常；
仅 ``CancelledError`` 逃逸。HTTP 端点按 ``error_code`` 映射状态码，SSE 出口发
``command_error`` 事件。

已知边界（issue #498 报告如实记录）：
- 锁 TTL 固定 30s，而单命令超时上限 120s——超过 30s 的命令在 TTL 过期后
  可能失去互斥（后续可把 EX 调到 ``max(30, ceil(timeout)+5)`` 或用续租）；
- 幂等键里存完整响应 JSON（含 /export 正文），键上限即结果上限（1MB）；
- 幂等 pending 阶段并发的重复请求最多等待 ~1s，仍未完成则返回 409 忙。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import time
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable

from app.agent.commands.parser import CommandSyntaxError, bind_args, parse_line
from app.agent.commands.registry import CommandRegistry, agent_scope_for_session
from app.agent.commands.types import (
    DEFAULT_COMMAND_TIMEOUT,
    ERROR_COMMAND_BUSY,
    ERROR_COMMAND_FAILED,
    ERROR_COMMAND_TIMEOUT,
    ERROR_PERMISSION_DENIED,
    ERROR_SERVICE_UNAVAILABLE,
    ERROR_UNKNOWN_COMMAND,
    MAX_COMMAND_TIMEOUT,
    CommandContext,
    CommandExecution,
    CommandFailure,
)
from app.agent.events import AgentEvent
from app.core.config import settings
from app.core.log_safety import log_safe
from app.core.prometheus import AGENT_COMMAND_EXECUTIONS
from app.core.redis import redis_client
from app.core.security import is_org_admin
from app.services.session_service import load_accessible_session, resolve_session_access_scope

logger = logging.getLogger(__name__)

_LOCK_PREFIX = "cmd:lock:"
_IDEM_PREFIX = "cmd:idem:"
_IDEM_VERSION = 2
_IDEM_PENDING = "pending"
_IDEM_COMPLETED = "completed"

# Lua compare-and-del：只有仍持有锁（token 相同）才删除，避免误删后来者的锁。
_RELEASE_LOCK_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
end
return 0
"""

# 完成写回同样必须持有原 pending owner；TTL 过期后的新 claimant 不得被旧 writer 覆盖。
_COMPLETE_IDEMPOTENCY_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    redis.call("set", KEYS[1], ARGV[2], "EX", tonumber(ARGV[3]))
    return 1
end
return 0
"""

ReplayAuthorizer = Callable[[int | None, str | None, int | None], Awaitable[None]]

# 幂等 pending 的等待窗口：50ms × 20 = 1s（覆盖普通命令的完成时间）。
_IDEM_POLL_INTERVAL = 0.05
_IDEM_POLL_ATTEMPTS = 20


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


def idempotency_redis_key(user_id: int, idempotency_key: str | None) -> str | None:
    """幂等键的 Redis 形态；空键 → ``None``（不做幂等）。"""
    cleaned = (idempotency_key or "").strip()
    if not cleaned:
        return None
    return f"{_IDEM_PREFIX}{user_id}:{cleaned}"


def idempotency_pending_ttl() -> int:
    """pending 占位 TTL：至少覆盖单命令超时上限 + 30s（配置过小时兜底）。"""
    configured = int(settings.COMMANDS_IDEMPOTENCY_PENDING_TTL_SECONDS or 0)
    return max(configured, int(MAX_COMMAND_TIMEOUT) + 30)


def lock_redis_key(agent: CommandContext) -> str:
    """同会话（无会话时同用户）命令互斥键。"""
    return f"{_LOCK_PREFIX}{agent.session_id or agent.user_id}"


def _idempotency_fingerprint(
    agent: CommandContext,
    command_name: str,
    resolved_scope: str,
    args: dict,
) -> str:
    """绑定规范化命令、有效上下文和已绑定参数；角色在重放时另行复核。"""
    payload = {
        "version": _IDEM_VERSION,
        "canonical_command": command_name,
        "resolved_scope": resolved_scope,
        "organization_id": agent.organization_id,
        "session_id": agent.session_id or None,
        "args": args,
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _serialize_idempotency_envelope(
    agent: CommandContext,
    *,
    state: str,
    fingerprint: str,
    command_name: str,
    resolved_scope: str,
    resource_org_id: int | None,
    owner: str,
    execution: CommandExecution | None = None,
) -> str:
    payload = {
        "version": _IDEM_VERSION,
        "state": state,
        "fingerprint": fingerprint,
        "organization_id": agent.organization_id,
        "session_id": agent.session_id or None,
        "resource_org_id": resource_org_id if agent.session_id else None,
        "command": command_name,
        "scope": resolved_scope,
        "owner": owner,
    }
    if execution is not None:
        payload["execution"] = execution.to_dict()
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _valid_completed_execution(execution: dict, envelope: dict) -> bool:
    """completed 的嵌套响应必须与外层上下文一致，且不得夹带失败/双重正文。"""
    required = {
        "execution_id",
        "command",
        "line",
        "scope",
        "success",
        "status",
        "result",
        "content",
        "data",
        "args",
        "error_code",
        "error_message",
        "error_field",
        "duration_ms",
    }
    if not required.issubset(execution):
        return False
    if not isinstance(execution["execution_id"], str) or not execution["execution_id"]:
        return False
    if execution["command"] != envelope["command"] or execution["scope"] != envelope["scope"]:
        return False
    if execution["success"] is not True or execution["status"] != "completed":
        return False
    if not isinstance(execution["line"], str) or not isinstance(execution["args"], dict):
        return False
    content = execution["content"]
    data = execution["data"]
    result = execution["result"]
    if not isinstance(content, str) or not isinstance(data, dict) or not isinstance(result, dict):
        return False
    if result.get("content") != content or result.get("data") != data:
        return False
    if any(execution[field] != "" for field in ("error_code", "error_message", "error_field")):
        return False
    duration = execution["duration_ms"]
    return (
        isinstance(duration, (int, float))
        and not isinstance(duration, bool)
        and math.isfinite(duration)
        and duration >= 0
    )


def _decode_idempotency_envelope(raw) -> dict | None:
    """只接受完整 v2 信封；legacy、损坏和内部不一致数据一律返回 None。"""
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    required = {
        "version",
        "state",
        "fingerprint",
        "organization_id",
        "session_id",
        "resource_org_id",
        "command",
        "scope",
        "owner",
    }
    if not isinstance(payload, dict) or not required.issubset(payload):
        return None
    if type(payload["version"]) is not int or payload["version"] != _IDEM_VERSION:
        return None
    if payload["state"] not in (_IDEM_PENDING, _IDEM_COMPLETED):
        return None
    fingerprint = payload["fingerprint"]
    if (
        not isinstance(fingerprint, str)
        or len(fingerprint) != 64
        or any(char not in "0123456789abcdef" for char in fingerprint)
    ):
        return None
    organization_id = payload["organization_id"]
    if organization_id is not None and type(organization_id) is not int:
        return None
    session_id = payload["session_id"]
    if session_id is not None and (not isinstance(session_id, str) or not session_id):
        return None
    resource_org_id = payload["resource_org_id"]
    if resource_org_id is not None and type(resource_org_id) is not int:
        return None
    if session_id is None and resource_org_id is not None:
        return None
    if any(
        not isinstance(payload[field], str) or not payload[field]
        for field in ("command", "scope", "owner")
    ):
        return None
    if payload["state"] == _IDEM_PENDING:
        return payload if "execution" not in payload else None
    execution = payload.get("execution")
    if not isinstance(execution, dict) or not _valid_completed_execution(execution, payload):
        return None
    return payload


async def _wait_for_idempotent_result(
    client,
    key: str,
    *,
    fingerprint: str,
    command_name: str,
    resolved_scope: str,
) -> dict | None:
    """同指纹 pending 才等待；任何 legacy/损坏/冲突条目都立即 fail-closed。"""
    for attempt in range(_IDEM_POLL_ATTEMPTS):
        raw = await client.get(key)
        if raw is None:
            return None
        envelope = _decode_idempotency_envelope(raw)
        if envelope is None or envelope["fingerprint"] != fingerprint:
            return None
        if envelope["command"] != command_name or envelope["scope"] != resolved_scope:
            return None
        if envelope["state"] == _IDEM_COMPLETED:
            return envelope
        if attempt + 1 < _IDEM_POLL_ATTEMPTS:
            await asyncio.sleep(_IDEM_POLL_INTERVAL)
    return None


async def execute(
    agent: CommandContext,
    line: str,
    *,
    registry: CommandRegistry | None = None,
    idempotency_key: str | None = None,
    session_resource_org_id: int | None = None,
    replay_authorizer: ReplayAuthorizer | None = None,
    redis=None,
) -> CommandExecution:
    """对 ``agent`` 执行一行命令（无模型轮次、不写会话）。

    ``session_resource_org_id`` 由 HTTP 入口从已加载的原 session 读取；历史 NULL 资源
    也为 None，由非空 session_id 与无会话命令区分。
    ``redis`` 仅用于测试注入（FakeRedis）；生产走 ``app.core.redis.redis_client``。
    """
    if registry is None:
        from app.agent.commands import command_registry

        registry = command_registry

    started = time.perf_counter()
    text = (line or "").strip()
    execution = CommandExecution(
        command="", line=text, success=False, execution_id=uuid.uuid4().hex
    )
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

    client = redis if redis is not None else redis_client
    if not client:
        return fail(
            ERROR_SERVICE_UNAVAILABLE,
            "命令服务暂不可用（Redis 不可用），请稍后重试",
            data={"reason": "redis_unavailable"},
        )

    # 幂等指纹必须基于解析、注册表解析和默认值绑定后的规范语义。
    idem_key = idempotency_redis_key(agent.user_id, idempotency_key)
    try:
        name, tokens = parse_line(text)
    except CommandSyntaxError as exc:
        return fail(exc.code, exc.message, field_name=exc.field_name)

    command, scope = registry.resolve(name, agent_scope=agent_scope)
    if command is None:
        return fail(ERROR_UNKNOWN_COMMAND, f"未知命令：/{name}。输入 /help 查看可用命令。")

    execution.command = command.name
    execution.scope = scope

    if (
        not idem_key
        and command.permission == "admin"
        and not (agent.is_admin or agent.is_superuser)
    ):
        return fail(ERROR_PERMISSION_DENIED, f"命令 /{command.name} 仅限组织管理员使用")

    try:
        args = bind_args(command, tokens)
    except CommandSyntaxError as exc:
        return fail(exc.code, exc.message, field_name=exc.field_name)
    execution.args = args

    # ── 幂等：规范语义完成后才抢占；旧条目和冲突上下文一律 fail-closed。 ─────
    idem_pending = False
    idem_pending_value = ""
    idem_owner = ""
    if idem_key:
        fingerprint = _idempotency_fingerprint(agent, command.name, scope, args)
        idem_owner = uuid.uuid4().hex
        idem_pending_value = _serialize_idempotency_envelope(
            agent,
            state=_IDEM_PENDING,
            fingerprint=fingerprint,
            command_name=command.name,
            resolved_scope=scope,
            resource_org_id=session_resource_org_id,
            owner=idem_owner,
        )
        try:
            claimed = await client.set(
                idem_key,
                idem_pending_value,
                nx=True,
                ex=idempotency_pending_ttl(),
            )
        except Exception as exc:  # noqa: BLE001 — 依赖不可用必须 fail closed
            logger.warning("命令幂等键写入失败（fail-closed）：%s", type(exc).__name__)
            return fail(
                ERROR_SERVICE_UNAVAILABLE,
                "命令服务暂不可用（幂等存储不可用），请稍后重试",
                data={"reason": "redis_unavailable"},
            )
        if not claimed:
            try:
                cached = await _wait_for_idempotent_result(
                    client,
                    idem_key,
                    fingerprint=fingerprint,
                    command_name=command.name,
                    resolved_scope=scope,
                )
            except Exception as exc:  # noqa: BLE001 — 读取失败同样 fail closed
                logger.warning("命令幂等键读取失败（fail-closed）：%s", type(exc).__name__)
                return fail(
                    ERROR_SERVICE_UNAVAILABLE,
                    "命令服务暂不可用（幂等存储不可用），请稍后重试",
                    data={"reason": "redis_unavailable"},
                )
            if cached is None:
                return fail(
                    ERROR_COMMAND_BUSY,
                    "幂等键已绑定到其他请求，或相同命令仍在执行",
                )
            cached_execution = cached["execution"]
            if cached_execution["args"] != args:
                return fail(ERROR_COMMAND_BUSY, "幂等键的请求参数或缓存上下文不一致")
            if cached["organization_id"] != agent.organization_id:
                return fail(ERROR_COMMAND_BUSY, "幂等键的原组织与当前有效组织不一致")
            if cached["session_id"] != (agent.session_id or None):
                return fail(ERROR_COMMAND_BUSY, "幂等键的原会话与当前请求不一致")
            if cached["session_id"] is not None and replay_authorizer is None:
                return fail(
                    ERROR_SERVICE_UNAVAILABLE,
                    "命令服务暂不可用（无法复核原会话），请稍后重试",
                    data={"reason": "replay_authorizer_unavailable"},
                )
            if replay_authorizer is not None:
                try:
                    await replay_authorizer(
                        cached["organization_id"],
                        cached["session_id"],
                        cached["resource_org_id"],
                    )
                except CommandFailure as exc:
                    return fail(
                        exc.code,
                        exc.message,
                        field_name=exc.field_name,
                        data=exc.data or None,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 — 授权依赖失败不得释放正文
                    logger.warning("命令重放授权失败（fail-closed）：%s", type(exc).__name__)
                    return fail(
                        ERROR_SERVICE_UNAVAILABLE,
                        "命令服务暂不可用（重放授权失败），请稍后重试",
                        data={"reason": "replay_authorizer_unavailable"},
                    )
            if command.permission == "admin" and not (agent.is_admin or agent.is_superuser):
                return fail(
                    ERROR_PERMISSION_DENIED,
                    f"命令 /{command.name} 仅限组织管理员使用",
                )
            try:
                replay = CommandExecution.from_dict(cached_execution)
            except Exception as exc:  # noqa: BLE001 — 损坏响应不得外泄或触发重跑
                logger.warning("命令幂等响应校验失败（fail-closed）：%s", type(exc).__name__)
                return fail(ERROR_COMMAND_BUSY, "幂等缓存响应无效，请稍后重试")
            logger.info(
                "命令幂等重放 (execution_id=%s, user=%s, key=%s)",
                replay.execution_id,
                agent.user_id,
                log_safe(idempotency_key or ""),
            )
            _audit(agent, replay)
            return replay
        idem_pending = True

    try:
        # 首次执行在 handler 前拒绝；重放则在上方完成原上下文复核后按当前角色拒绝。
        if (
            idem_key
            and command.permission == "admin"
            and not (agent.is_admin or agent.is_superuser)
        ):
            return fail(ERROR_PERMISSION_DENIED, f"命令 /{command.name} 仅限组织管理员使用")

        # 超时口径：非数值 / <=0 回退默认值；上限 MAX_COMMAND_TIMEOUT（防呆）。
        try:
            timeout = float(command.timeout)
        except (TypeError, ValueError):
            timeout = DEFAULT_COMMAND_TIMEOUT
        if timeout <= 0:
            timeout = DEFAULT_COMMAND_TIMEOUT
        timeout = min(timeout, MAX_COMMAND_TIMEOUT)

        # ── 分布式锁：SET NX EX 30（拿不到 → 409 忙）────────────────────
        lock_key = lock_redis_key(agent)
        owner_token = uuid.uuid4().hex
        try:
            acquired = await client.set(
                lock_key,
                owner_token,
                nx=True,
                ex=settings.COMMANDS_LOCK_TTL_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001 — 锁不可用必须 fail closed
            logger.warning("命令并发锁获取失败（fail-closed）：%s", type(exc).__name__)
            return fail(
                ERROR_SERVICE_UNAVAILABLE,
                "命令服务暂不可用（并发锁不可用），请稍后重试",
                data={"reason": "redis_unavailable"},
            )
        if not acquired:
            return fail(ERROR_COMMAND_BUSY, "当前会话已有命令在执行，请稍后再试")

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
            # compare-and-del：只释放自己的锁；失败由 TTL 兜底（不影响本次结果）。
            try:
                await client.eval(_RELEASE_LOCK_LUA, 1, lock_key, owner_token)
            except Exception as exc:  # noqa: BLE001 — 释放失败不掩盖执行结果
                logger.warning("命令锁释放失败（TTL 兜底）：%s", type(exc).__name__)

        execution.success = True
        execution.content = result.content
        execution.data = result.data
        execution.duration_ms = (time.perf_counter() - started) * 1000
        _audit(agent, execution)

        # 成功结果只能由原 pending owner 通过 CAS 写回；写回失败保留 pending 至 TTL，
        # 绝不清理成 cache miss 让重试请求再次执行已完成的 handler。
        if idem_key:
            idem_pending = False
            try:
                completed_value = _serialize_idempotency_envelope(
                    agent,
                    state=_IDEM_COMPLETED,
                    fingerprint=fingerprint,
                    command_name=command.name,
                    resolved_scope=scope,
                    resource_org_id=session_resource_org_id,
                    owner=idem_owner,
                    execution=execution,
                )
                stored = await client.eval(
                    _COMPLETE_IDEMPOTENCY_LUA,
                    1,
                    idem_key,
                    idem_pending_value,
                    completed_value,
                    str(settings.COMMANDS_IDEMPOTENCY_TTL_SECONDS),
                )
                if not stored:
                    logger.warning("命令幂等结果未写回：pending 已被新 owner 接管")
            except Exception as exc:  # noqa: BLE001 — 已执行完成，不因缓存失败改判
                logger.warning(
                    "命令幂等结果写入失败（保留 pending 至 TTL）：%s",
                    type(exc).__name__,
                )
        return execution
    finally:
        # 失败 / 取消：只清掉**自己写入**的 pending 占位（compare-and-del）。
        # 占位已过期并被新请求接管时，旧请求不得误删新占位（评审边界）。
        if idem_key and idem_pending:
            try:
                await client.eval(_RELEASE_LOCK_LUA, 1, idem_key, idem_pending_value)
            except Exception as exc:  # noqa: BLE001 — 清理失败由 TTL 兜底
                logger.warning("命令幂等 pending 键清理失败（TTL 兜底）：%s", type(exc).__name__)


def _audit(agent: CommandContext, execution: CommandExecution) -> None:
    """审计行 + 指标：身份 + 会话 + 命令 + 结果摘要（不记录命令输出正文）。"""
    logger.info(
        "命令执行 (command=%s, user=%s, org=%s, session=%s, scope=%s, success=%s, "
        "duration_ms=%.1f, error_code=%s, execution_id=%s)",
        log_safe(execution.command or execution.line[:40]),
        agent.user_id,
        agent.organization_id,
        log_safe(agent.session_id or ""),
        execution.scope,
        execution.success,
        execution.duration_ms,
        execution.error_code or "-",
        execution.execution_id or "-",
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
    else:
        body = text.split()
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
