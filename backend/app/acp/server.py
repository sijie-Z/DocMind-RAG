"""ACP v1 stdio 服务器（issue #481 §5.2 / §5.5 / §5.6）。

职责：

- 连接循环：NDJSON over stdio（单行 ≤ ``RPC_LINE_MAX_BYTES``），复用
  :class:`app.rpc.dispatcher.Dispatcher` 的请求 id 表 / 双向请求 /
  ``$/cancel_request``——本模块不另写 JSON-RPC 帧解析；
- 方法表：``initialize`` / ``authenticate`` / ``session/new|list|resume|close|
  set_config_option|prompt|cancel``；``session/load`` 不在表中 → 自动
  ``-32601``；``session/cancel`` 是通知（无 result），但为让
  ``$/cancel_request`` 能转发，仍注册同一处理器；
- 身份校验（:func:`validate_identity`）：用户存在、``is_active``、属于
  ``--org-id``；``--api-key-file`` 提供时 key 必须与身份一致；
- stdout 纯净：真实 stdout 交给 :class:`app.rpc.server.ProtocolWriter`
  （写锁 + flush），``sys.stdout`` 指向 stderr；
- 每会话单 prompt（并发 → ``-32009``）、同一会话 update 串行、取消后不再
  外发迟到 update、审批超时按 deny 收尾、断连不悬空任务；
- cancel 真取消（issue #494）：``session/cancel`` 复用 #493 的
  ``cancel_task_and_wait`` 等待 DB 收敛为 ``CANCELLED``（≤5s）；通道不可用 /
  超时 / 任务先结束 → ``-32603``，绝不假报成功；``_drive`` 取消后排干事件流
  以让服务终态收尾，事件流结束后再由 ACP 侧兜底落 CANCELLED。
"""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import sys
import threading
import time
from typing import Any, BinaryIO, TextIO

from sqlalchemy import select

from app.acp.mapping import (
    decision_from_permission_result,
    event_to_update,
    merge_plan_entry,
    permission_request_params,
    plan_entry_from_event,
    sanitized_error_content,
    tool_call_id_for,
)
from app.acp.session import (
    AcpSessionRegistry,
    AcpSessionState,
    create_chat_session,
    list_chat_sessions,
    load_chat_session,
    load_history,
    update_session_settings,
)
from app.core.config import settings
from app.core.error_mapping import (
    JSONRPC_CONFLICT,
    JSONRPC_INTERNAL_ERROR,
    JSONRPC_INVALID_PARAMS,
    JSONRPC_INVALID_REQUEST,
    JSONRPC_NOT_FOUND,
    JSONRPC_PARSE_ERROR,
)
from app.core.redis import RedisUnavailableError
from app.rpc.dispatcher import Dispatcher, RPCError, cancel_task_and_wait
from app.rpc.server import ProtocolWriter, _close_dependencies, _stderr_only_stdout

logger = logging.getLogger(__name__)

# ACP v1 协议版本（v2 仍是草案，不实现）。
ACP_PROTOCOL_VERSION = 1

# session/cancel 的端到端预算（issue #494）：含「等任务行登记 + 置取消旗 +
# 等 DB 收敛」三个阶段，任一段超时都必须如实 -32603（不假报成功）。
CANCEL_TOTAL_TIMEOUT_SECONDS = 5.0
CANCEL_POLL_INTERVAL_SECONDS = 0.1


class IdentityError(Exception):
    """启动身份校验失败（entrypoint 打印原因并 exit 2）。"""


async def validate_identity(
    db_factory,
    *,
    organization_id: int,
    user_id: int,
    api_key: str | None = None,
):
    """校验 ``--org-id/--user-id``（+ 可选 API Key）与库中身份一致。

    规则（issue §5.5）：
    - 用户必须存在且 ``is_active=true``；
    - 必须属于该组织：主组织字段命中，或 ``user_organization`` 成员行命中
      （与 auth_service 的成员回退同口径——主组织非空时以其为准，历史用户
      未必都有成员行）；
    - 提供 ``--api-key-file`` 时：key 必须能认证且指纹对应用户；key 自带
      组织时也必须等于 ``--org-id``（不允许拿 A 组织的 key 冒用 B 组织）。
    """
    from app.models.organization import user_organization
    from app.models.user import User

    async with db_factory() as db:
        user = await db.get(User, user_id)
        if user is None:
            raise IdentityError(f"用户 {user_id} 不存在")
        if not user.is_active:
            raise IdentityError(f"用户 {user_id} 已被禁用（is_active=false）")

        member = user.organization_id == organization_id
        if not member:
            row = await db.execute(
                select(user_organization.c.user_id).where(
                    user_organization.c.user_id == user_id,
                    user_organization.c.organization_id == organization_id,
                )
            )
            member = row.first() is not None
        if not member:
            raise IdentityError(f"用户 {user_id} 不属于组织 {organization_id}")

        if api_key:
            from app.services.api_key_service import api_key_service

            key_user = await api_key_service.authenticate(api_key, db)
            if key_user is None or key_user.id != user_id:
                raise IdentityError("--api-key-file 与 --user-id 不匹配或 key 无效")
            key_org = getattr(key_user, "token_organization_id", None)
            if key_org is None:
                key_org = key_user.organization_id
            if key_org is not None and key_org != organization_id:
                raise IdentityError(
                    f"--api-key-file 所属组织 {key_org} 与 --org-id {organization_id} 不一致"
                )
        return user


def extract_prompt_text(prompt: Any) -> str | None:
    """ACP ``prompt``（``[{type:"text", text}]``）→ 纯文本；无有效文本返回 None。"""
    if not isinstance(prompt, list) or not prompt:
        return None
    parts: list[str] = []
    for part in prompt:
        if (
            isinstance(part, dict)
            and part.get("type") == "text"
            and isinstance(part.get("text"), str)
        ):
            parts.append(part["text"])
    text = "\n".join(parts).strip()
    return text or None


def _ensure_utf8_stdout(stream: TextIO) -> None:
    """把协议 stdout 重配为 UTF-8（Windows 中文 locale 默认 cp936）。

    ACP 帧里会出现中文与 emoji（agent 事件自带 ✅/❌）。cp936 编码不覆盖
    emoji，``write`` 会抛 ``UnicodeEncodeError``——一条响应丢失后客户端就会
    挂住。JSON 是 UTF-8 协议，这里在写第一帧之前重配；不可重配的流（已被
    分离/替换为内存流）保持原样（测试/嵌入场景由调用方保证编码）。
    """
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(encoding="utf-8", errors="strict")
    except (ValueError, OSError):  # pragma: no cover - 流已关闭/不可重配
        logger.warning("ACP stdout 无法重配为 UTF-8，沿用当前编码")


class AcpServer:
    """一个连接上的 ACP v1 协议服务（stdio；进程内直接调 service，不走 HTTP）。"""

    def __init__(
        self,
        *,
        organization_id: int,
        user_id: int,
        is_superuser: bool = False,
        db_factory=None,
        agent_service_obj=None,
        stdin: BinaryIO | None = None,
        stdout: TextIO | None = None,
        permission_timeout: float | None = None,
        plan_approval_mode: str | None = None,
        redirect_stdout: bool = True,
        install_signal_handlers: bool = True,
    ) -> None:
        self.organization_id = organization_id
        self.user_id = user_id
        self.is_superuser = is_superuser
        if db_factory is None:
            from app.core.database import AsyncSessionLocal

            db_factory = AsyncSessionLocal
        self._db_factory = db_factory
        self._agent_service = agent_service_obj
        self._stdin = stdin if stdin is not None else sys.stdin.buffer
        self._stdout = stdout if stdout is not None else sys.stdout
        self._redirect_stdout = redirect_stdout
        self._install_signal_handlers = install_signal_handlers
        self.permission_timeout = (
            float(permission_timeout)
            if permission_timeout is not None
            else float(settings.ACP_PERMISSION_TIMEOUT_SECONDS)
        )
        self.plan_approval_mode = plan_approval_mode or settings.ACP_PLAN_APPROVAL_MODE

        self.sessions = AcpSessionRegistry()
        self.dispatcher: Dispatcher | None = None
        self._writer: ProtocolWriter | None = None
        self._notification_tasks: set[asyncio.Task] = set()
        self._background_tasks: set[asyncio.Task] = set()

    # ── 装配 ──────────────────────────────────────────────────────────────

    @property
    def service(self):
        if self._agent_service is None:
            from app.agent.service import agent_service

            self._agent_service = agent_service
        return self._agent_service

    def method_table(self) -> dict[str, Any]:
        return {
            "initialize": self.initialize,
            "authenticate": self.authenticate,
            "session/new": self.session_new,
            "session/list": self.session_list,
            "session/resume": self.session_resume,
            "session/close": self.session_close,
            "session/set_config_option": self.session_set_config_option,
            "session/prompt": self.session_prompt,
            # session/cancel 是通知；注册处理器是为了让 dispatcher 的
            # $/cancel_request 转发通道可用（带 id 的请求形式也顺带支持）。
            "session/cancel": self.session_cancel,
        }

    def build_dispatcher(self) -> Dispatcher:
        assert self._writer is not None
        self.dispatcher = Dispatcher(
            self.method_table(),
            send=self._writer.send,
            on_disconnect=self.on_disconnect,
            permission_timeout=self.permission_timeout,
        )
        return self.dispatcher

    # ── 连接循环 ──────────────────────────────────────────────────────────

    async def run(self) -> int:
        """运行 stdio 循环直到 stdin EOF / SIGTERM / SIGINT；返回进程退出码。"""
        real_stdout = self._stdout
        _ensure_utf8_stdout(real_stdout)
        if self._redirect_stdout:
            _stderr_only_stdout(real_stdout)
        writer = ProtocolWriter(real_stdout)
        self._writer = writer
        dispatcher = self.build_dispatcher()

        loop = asyncio.get_running_loop()
        line_queue: asyncio.Queue[bytes] = asyncio.Queue()
        stop = asyncio.Event()
        max_line = int(settings.RPC_LINE_MAX_BYTES)
        stdin_stream = self._stdin

        def _pump_stdin() -> None:
            while True:
                raw = stdin_stream.readline(max_line + 1)
                if raw is None:
                    raw = b""
                loop.call_soon_threadsafe(line_queue.put_nowait, raw)
                if not raw:
                    return

        threading.Thread(target=_pump_stdin, name="acp-stdin", daemon=True).start()

        def _request_stop(*_args: Any) -> None:
            loop.call_soon_threadsafe(stop.set)

        installed: list[signal.Signals] = []
        fallback_installed: list[tuple[signal.Signals, Any]] = []
        if self._install_signal_handlers:
            for sig in (signal.SIGTERM, signal.SIGINT):
                try:
                    loop.add_signal_handler(sig, _request_stop)
                    installed.append(sig)
                except (NotImplementedError, RuntimeError, ValueError):
                    try:
                        previous = signal.getsignal(sig)
                        signal.signal(sig, _request_stop)
                        fallback_installed.append((sig, previous))
                    except (ValueError, OSError):  # pragma: no cover - 非主线程/受限环境
                        pass

        try:
            while not stop.is_set():
                try:
                    raw = await asyncio.wait_for(line_queue.get(), timeout=0.2)
                except TimeoutError:
                    continue
                if raw == b"":
                    break
                if len(raw) > max_line:
                    logger.warning("ACP line exceeds %s bytes, dropped", max_line)
                    await writer.send(
                        {
                            "jsonrpc": "2.0",
                            "id": None,
                            "error": {
                                "code": JSONRPC_INVALID_REQUEST,
                                "message": "请求行超过 1MB 上限",
                            },
                        }
                    )
                    continue
                text = raw.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    message = json.loads(text)
                except json.JSONDecodeError:
                    await writer.send(
                        {
                            "jsonrpc": "2.0",
                            "id": None,
                            "error": {"code": JSONRPC_PARSE_ERROR, "message": "Parse error"},
                        }
                    )
                    continue
                self._dispatch_message(message)

            await dispatcher.wait_idle(timeout=10.0)
        finally:
            for sig in installed:
                try:
                    loop.remove_signal_handler(sig)
                except (NotImplementedError, RuntimeError, ValueError):
                    pass
            for sig, previous in fallback_installed:
                try:
                    signal.signal(sig, previous)
                except (ValueError, OSError):  # pragma: no cover - 非主线程/受限环境
                    pass
            for task in list(self._notification_tasks):
                task.cancel()
            if self._notification_tasks:
                await asyncio.gather(*self._notification_tasks, return_exceptions=True)
            await self.on_disconnect()
            await dispatcher.close()
            await _close_dependencies()
        return 0

    def _dispatch_message(self, message: Any) -> None:
        """一条已解析消息 → dispatcher；仅拦截 ``session/cancel`` 通知。"""
        if (
            isinstance(message, dict)
            and message.get("method") == "session/cancel"
            and "id" not in message
        ):
            params = message.get("params")
            task = asyncio.create_task(
                self._handle_cancel_notification(params if isinstance(params, dict) else {})
            )
            self._notification_tasks.add(task)
            task.add_done_callback(self._notification_tasks.discard)
            return
        assert self.dispatcher is not None
        self.dispatcher.dispatch(message)

    async def _handle_cancel_notification(self, params: dict) -> None:
        try:
            await self.session_cancel(params)
        except RPCError as exc:
            if exc.code == JSONRPC_NOT_FOUND:
                # 通知形态的 cancel 常与"本轮刚结束"竞态：没有进行中任务是正常 no-op。
                logger.debug("ACP session/cancel 通知未生效: %s", exc.message)
            else:
                # 取消通道不可用 / 收敛失败属于运维必须看到的事件（issue #494）。
                logger.warning("ACP session/cancel 通知未生效: %s", exc.message)
        except Exception:  # noqa: BLE001 - 通知无响应，绝不打断循环
            logger.warning("ACP session/cancel notification failed", exc_info=True)

    async def on_disconnect(self) -> None:
        """客户端断连：取消全部会话（待审批按 deny/取消收尾，任务不悬空）。"""
        sessions = self.sessions.values()
        for state in sessions:
            await self._cancel_session(state)
        for task in list(self._background_tasks):
            task.cancel()
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
            self._background_tasks.clear()
        for state in sessions:
            await state.close()
        self.sessions.clear()

    # ── 方法：握手 / 身份 ─────────────────────────────────────────────────

    async def initialize(self, params: dict) -> dict:
        return {
            "protocolVersion": ACP_PROTOCOL_VERSION,
            "agentCapabilities": {
                "loadSession": False,
                "promptCapabilities": {
                    "image": False,
                    "audio": False,
                    "embeddedContext": False,
                },
                "dshExtensions": True,
            },
            "agentInfo": {"name": "DocMind", "version": settings.APP_VERSION},
        }

    async def authenticate(self, params: dict) -> dict:
        # stdio 传输 = 本地信任；身份已在进程启动时由 --org-id/--user-id
        # （及可选 --api-key-file）校验。token 字段接受但不需要。
        method = params.get("method")
        if method is not None and not isinstance(method, str):
            raise RPCError(JSONRPC_INVALID_PARAMS, "method 必须是字符串")
        return {"ok": True}

    # ── 方法：会话 ────────────────────────────────────────────────────────

    async def session_new(self, params: dict) -> dict:
        # 忽略 cwd/mcpServers（ACP 客户端能力一律不声明支持）。
        session_id = await create_chat_session(
            self._db_factory,
            user_id=self.user_id,
            organization_id=self.organization_id,
        )
        self.sessions.ensure(session_id, self._send_update)
        logger.info(
            "ACP session created: session=%s open_sessions=%d",
            session_id,
            len(self.sessions.values()),
        )
        return {"sessionId": session_id}

    async def session_list(self, params: dict) -> dict:
        rows = await list_chat_sessions(
            self._db_factory,
            user_id=self.user_id,
            organization_id=self.organization_id,
            is_superuser=self.is_superuser,
        )
        return {
            "sessions": [
                {
                    "sessionId": row.id,
                    "title": row.title or "Untitled",
                    "updatedAt": (row.updated_at or row.created_at).isoformat()
                    if (row.updated_at or row.created_at)
                    else "",
                }
                for row in rows
            ]
        }

    async def session_resume(self, params: dict) -> dict:
        session_id = self._require_session_id(params)
        await self._require_chat_session(session_id)
        state = self.sessions.ensure(session_id, self._send_update)
        if state.lock.locked():
            raise RPCError(JSONRPC_CONFLICT, "该会话已有进行中的 prompt（每会话单 prompt）")
        task_id = await self._latest_task_id(session_id)
        if task_id is None:
            raise RPCError(JSONRPC_NOT_FOUND, "会话没有可恢复的任务")
        # 预检 + 认领（409/404 语义由 AppError → JSON-RPC 映射）；不回放历史。
        await self.service.prepare_resume(
            task_id,
            user_id=self.user_id,
            organization_id=self.organization_id,
        )
        task = asyncio.create_task(self._background_resume(state, task_id))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return {"sessionId": session_id, "status": "running"}

    async def session_close(self, params: dict) -> dict:
        session_id = self._require_session_id(params)
        state = self.sessions.pop(session_id)
        if state is None:
            raise RPCError(JSONRPC_NOT_FOUND, "会话不存在或未在本连接中打开")
        await self._cancel_session(state)
        await state.close()
        return {"ok": True}

    async def session_set_config_option(self, params: dict) -> dict:
        session_id = self._require_session_id(params)
        option = params.get("option")
        value = params.get("value")
        if option not in ("model", "plan_mode"):
            raise RPCError(JSONRPC_INVALID_PARAMS, "option 仅支持 model / plan_mode")
        if option == "model":
            if not isinstance(value, str) or not value.strip():
                raise RPCError(JSONRPC_INVALID_PARAMS, "model 必须是非空字符串")
            value = value.strip()
        else:
            if value not in ("always", "never"):
                raise RPCError(JSONRPC_INVALID_PARAMS, "plan_mode 必须是 always / never")
        from app.models.chat import ChatSession

        async with self._db_factory() as db:
            row = await db.get(ChatSession, session_id)
            if row is None or row.user_id != self.user_id:
                raise RPCError(JSONRPC_NOT_FOUND, "会话不存在或无权访问")
            if (
                not self.is_superuser
                and row.organization_id is not None
                and row.organization_id != self.organization_id
            ):
                raise RPCError(JSONRPC_NOT_FOUND, "会话不存在或无权访问")
            await update_session_settings(row, option=option, value=value)
            await db.commit()
        return {"ok": True}

    # ── 方法：prompt / cancel ─────────────────────────────────────────────

    async def session_prompt(self, params: dict) -> dict:
        session_id = self._require_session_id(params)
        text = extract_prompt_text(params.get("prompt"))
        if text is None:
            raise RPCError(JSONRPC_INVALID_PARAMS, "prompt 必须是非空 text 段列表")
        state = self.sessions.get(session_id)
        if state is None:
            raise RPCError(JSONRPC_NOT_FOUND, "会话不存在或未在本连接中打开")
        if state.lock.locked():
            raise RPCError(JSONRPC_CONFLICT, "该会话已有进行中的 prompt（每会话单 prompt）")

        async with state.lock:
            state.reset_for_prompt()
            # 记录本轮开始前的最新任务（上一轮遗留）：取消在新任务行登记前到达时，
            # 只有**不是**这条的任务终态才算"本轮取消必输"（评审 B6）。查询失败时
            # 保留 None（未知）——此时不得用"最新任意任务"推断本轮终态（评审 nit）。
            state.pre_prompt_task_id = await self._pre_prompt_baseline(session_id)
            started = time.perf_counter()
            logger.info(
                "ACP prompt start: session=%s open_sessions=%d",
                session_id,
                len(self.sessions.values()),
            )
            chat_session = await self._require_chat_session(session_id)
            config = self._agent_config_for(chat_session)
            history = await load_history(self._db_factory, session_id)
            answer: list[str] = []
            try:
                stop_reason = await self._drive(
                    state,
                    self.service.chat(
                        query=text,
                        history=history,
                        organization_id=self.organization_id,
                        user_id=self.user_id,
                        session_id=session_id,
                        config=config,
                    ),
                    answer,
                )
            except asyncio.CancelledError:
                logger.info("ACP prompt cancelled (task-level): session=%s", session_id)
                await self._cancel_session(state)
                raise
            except Exception:
                # 错误响应也必须排在已入队 update 之后（同一会话的帧序不得乱）。
                await state.barrier()
                raise
            # 结算语义：更新写尽、持久化完成之后才返回 stopReason。
            await state.barrier()
            if stop_reason == "cancelled":
                # 真取消兜底（issue #494）：事件流已结束 = 本轮 run 已死。若服务
                # 终态收尾没写成（mark_terminal 失败 / 替身 service 不写库），
                # 这里保证任务行落 CANCELLED——绝不留下 RUNNING 的"已取消"。
                await self._ensure_task_cancelled(state)
            elif stop_reason == "end_turn":
                await self._save_interaction(
                    session_id, text, "".join(answer), state.active_task_id
                )
            logger.info(
                "ACP prompt done: session=%s stop=%s duration_ms=%.1f",
                session_id,
                stop_reason,
                (time.perf_counter() - started) * 1000.0,
            )
            return {"stopReason": stop_reason}

    async def session_cancel(self, params: dict) -> dict:
        """取消会话当前任务（issue #494：真取消，不假报成功）。

        ``session/cancel`` 是 ACP 通知（无 result），但同处理器也接受带 id 的
        请求形式。语义（与 RPC 面 #493 的 ``session/cancel`` 同口径）：

        - 运行中任务：置协作式取消旗并**等待** DB 收敛为 ``CANCELLED``（≤5s）；
          若 prompt 已受理但任务行尚未登记，先等任务行出现（同一 5s 预算）；
        - 审批/计划审批等待中：直接 DB 终态化（没有活跃 run 会观察取消旗）；
        - Redis 不可用 / 收敛超时 / 任务先以其他终态结束 / 没有进行中的任务：
          如实 ``-32004``（无任务）或 ``-32603``（取消未生效），绝不假报成功。
        """
        session_id = self._require_session_id(params)
        state = self.sessions.get(session_id)
        if state is None:
            raise RPCError(JSONRPC_NOT_FOUND, "会话不存在或未在本连接中打开")
        logger.info("ACP cancel: session=%s", session_id)
        await self._cancel_session(state, wait=True)
        return {"cancelled": True}

    # ── 事件流驱动 ────────────────────────────────────────────────────────

    async def _drive(self, state: AcpSessionState, events: Any, answer: list[str]) -> str:
        """消费事件流直到**自然结束**；审批/计划审批暂停时续跑。返回 stopReason。

        两条硬纪律：

        1. 事件流必须一直消费到 ``StopAsyncIteration``（镜像 #480 RPCService
           的 ``_stream_events``）。``done`` / ``run_cancelled`` / ``error``
           只是**终局信号**，不能据此提前 return——service.chat 在 yield 之后
           还要执行任务收尾（COMPLETED / CANCELLED / FAILED、落库、通知）；
           提前丢弃生成器会让这些收尾永不执行，GC 时的 ``GeneratorExit``
           兜底反而把任务按断流收敛。
        2. 审批暂停后同样先把旧流消费到尽（loop 捕获 ApprovalRequired 后以
           ``paused_for_approval=True`` 平静收尾），再切换到 resume 流；否则
           旧生成器被 GC 关闭时，断流兜底可能在 resume 已开跑之后覆盖新 run
           的快照或把任务误判 FAILED。

        取消纪律（issue #494）：``state.cancel_event`` 置位后**不再外发任何
        update**（``AcpSessionState.enqueue_update``/``drain`` 保证），但事件流
        仍要排干到尽——``AgentService.chat`` 在终局 yield **之后**才执行任务
        收尾（mark_terminal CANCELLED、落库、通知）；提前 break 会让任务按
        断流兜底收敛为 FAILED，与"真取消"语义相反。终局只保留 stopReason。
        """
        cancel_observed = False
        run_task_id = ""
        while True:
            resumed: Any | None = None
            paused_without_resume = False
            pending_error: RPCError | None = None
            terminal_type = ""
            terminal_task_state = ""
            last_error_event: Any | None = None
            async for event in events:
                event_task_id = getattr(event, "task_id", "")
                if event_task_id:
                    # service 包装层给每个事件盖章 task_id（chat/resume 同一契约）；
                    # 记录本轮任务，取消终态兜底/竞态判定只认它（issue #494）。
                    run_task_id = event_task_id
                    state.active_task_id = event_task_id
                if state.cancel_event.is_set() and not cancel_observed:
                    # 取消已到达：清空未发送更新；此后 enqueue_update 一律丢弃。
                    # 继续排干（见 docstring 取消纪律），只保留终局信号。
                    cancel_observed = True
                    dropped = state.drain()
                    logger.info(
                        "ACP cancel observed while driving: session=%s dropped_updates=%d",
                        state.session_id,
                        dropped,
                    )
                if event.type == "done" and getattr(event, "final_answer", ""):
                    # 成功终帧是权威正文；ready resume 不得与 chunk 重复拼接。
                    answer.clear()
                    answer.append(event.final_answer)
                elif event.type == "chunk":
                    # answer 是"本轮完整输出"，与 update 抑制解耦（issue #494 评审
                    # B4）：取消输掉竞态时 prompt 报 end_turn 并落库完整答案，
                    # 绝不能只保存取消前收到的半截。
                    answer.append(event.content)
                if event.type == "approval_required":
                    try:
                        resumed = await self._on_approval_required(state, event)
                    except RPCError as exc:
                        pending_error = exc
                        resumed = None
                    paused_without_resume = resumed is None
                    continue  # 继续消费旧流到尽（见 docstring 纪律 1/2）
                if event.type == "plan_approval_required":
                    try:
                        resumed = await self._on_plan_approval_required(state, event)
                    except RPCError as exc:
                        pending_error = exc
                        resumed = None
                    paused_without_resume = resumed is None
                    continue
                if event.type == "warning":
                    logger.info(
                        "ACP agent warning (session=%s): %s", state.session_id, event.content
                    )
                    continue
                if event.type == "hook_error":
                    # #474：hook 失败不静默（SSE 面以事件外发）。ACP 无对应
                    # update 变体，这里以 warning 留痕，不打断/不污染会话流。
                    logger.warning(
                        "ACP hook_error (session=%s): %s", state.session_id, event.content
                    )
                    continue
                if event.type in ("done", "run_cancelled", "error"):
                    terminal_type = event.type
                    if event.type == "done":
                        terminal_task_state = getattr(event, "task_state", "")
                    if event.type == "error":
                        last_error_event = event
                    continue
                update = self._event_update(state, event)
                if update is not None:
                    state.enqueue_update(update)
                else:
                    # 其余诊断类事件不外发；DEBUG 留一行（issue §5.3 "warning→仅日志"
                    # 的同一口径：绝不静默吞掉线索）。
                    logger.debug("ACP event not mapped to session/update: %s", event.type)
            # 事件流自然结束（service.chat 的收尾逻辑已全部执行完毕）。
            if pending_error is not None:
                raise pending_error
            if resumed is not None:
                events = resumed
                continue
            if paused_without_resume:
                return "cancelled"
            if terminal_type == "error":
                if state.cancel_event.is_set() and not await self._cancel_lost_race(run_task_id):
                    return "cancelled"
                message = (
                    sanitized_error_content(last_error_event)
                    if last_error_event is not None
                    else "服务器处理出错，请稍后重试"
                )
                raise RPCError(JSONRPC_INTERNAL_ERROR, message)
            if terminal_type == "run_cancelled":
                return "cancelled"
            if terminal_type == "done" and last_error_event is not None:
                raise RPCError(
                    JSONRPC_INTERNAL_ERROR,
                    sanitized_error_content(last_error_event),
                )
            if terminal_task_state == "failed":
                raise RPCError(JSONRPC_INTERNAL_ERROR, "Agent 执行失败")
            if state.cancel_event.is_set():
                # 诚实 stopReason（issue #494）：取消到达但任务已按其它终态收尾
                # （典型：自然完成赢得竞态）→ 报 end_turn，不把完成改写为 cancelled。
                if await self._cancel_lost_race(run_task_id):
                    return "end_turn"
                return "cancelled"
            return "end_turn"

    def _event_update(self, state: AcpSessionState, event: Any) -> dict[str, Any] | None:
        if event.type in ("tool_call", "tool_result", "tool_error"):
            tool_call_id = tool_call_id_for(event, state.update_seq + 1)
            if event.type == "tool_call":
                state.last_tool_call_id = tool_call_id
            elif not event.tool_call_id and state.last_tool_call_id:
                # 结果事件偶发缺 id：沿用同流最近一次 tool_call 的 id（保持配对）。
                tool_call_id = state.last_tool_call_id
            return event_to_update(event, tool_call_id=tool_call_id)
        if event.type == "plan_step":
            key = event.plan_id or "plan"
            entries = state.plan_entries.setdefault(key, [])
            merge_plan_entry(entries, plan_entry_from_event(event, index=len(entries) + 1))
            return event_to_update(event, plan_entries=entries)
        if event.type == "plan_complete":
            entries = state.plan_entries.get(event.plan_id or "plan") or []
            return event_to_update(event, plan_entries=entries)
        return event_to_update(event)

    # ── 审批（双向请求）─────────────────────────────────────────────────

    async def _on_approval_required(self, state: AcpSessionState, event: Any) -> Any | None:
        task_id = event.task_id or await self._latest_task_id(state.session_id)
        if not task_id:
            raise RPCError(JSONRPC_INTERNAL_ERROR, "审批事件缺少任务上下文")
        tool_call_id = (
            event.tool_call_id or state.last_tool_call_id or f"call-{state.update_seq + 1}"
        )
        state.last_tool_call_id = tool_call_id
        params = permission_request_params(
            state.session_id, tool_call_id=tool_call_id, tool_name=event.tool_name
        )
        result = await self._request_permission(state, params)
        decision = decision_from_permission_result(result)
        timed_out = isinstance(result, dict) and bool(result.get("timeout"))

        from app.agent import checkpoint

        if decision == "cancelled" and state.cancel_event.is_set():
            # 取消优先：不写审批决定、不续跑；任务按取消收尾（不悬空）。
            await self._mark_task_cancelled(task_id)
            return None

        # allow_once → approve；reject_once / cancelled / 超时（RPCError 已被
        # _request_permission 收敛为 cancelled）→ deny。超时按拒绝收尾。
        allow = decision == "allow"
        if timed_out:
            note = "acp:permission_timeout"
        elif decision != "cancelled":
            note = "acp:request_permission"
        else:
            note = "acp:permission_cancelled"
        logger.info(
            "ACP permission decision: session=%s task=%s decision=%s note=%s",
            state.session_id,
            task_id,
            "approve" if allow else "deny",
            note,
        )
        try:
            await checkpoint.apply_approval_decision(
                task_id=task_id,
                user_id=self.user_id,
                organization_id=self.organization_id,
                decision="approve" if allow else "deny",
                note=note,
            )
        except Exception:
            # 取消与记账竞态：取消已到（任务可能被判终态）→ 按取消收尾；
            # 其余失败如实上抛（由 dispatcher 映射 -32603/-32009）。
            if state.cancel_event.is_set():
                await self._mark_task_cancelled(task_id)
                return None
            raise
        if state.cancel_event.is_set():
            await self._mark_task_cancelled(task_id)
            return None
        return self.service.resume_chat(task_id, user_id=self.user_id, config=None, claim=False)

    async def _request_permission(self, state: AcpSessionState, params: dict) -> Any:
        """发 ``session/request_permission`` 并等待响应；超时/断连按 cancelled。"""
        assert self.dispatcher is not None
        # 先 flush 已入队 update：客户端应先看到工具/消息上下文，再收到审批卡片。
        await state.barrier()
        started = time.perf_counter()
        task = asyncio.create_task(
            self.dispatcher.request_client(
                "session/request_permission",
                params,
                session_id=state.session_id,
                timeout=self.permission_timeout,
            )
        )
        state.pending_permission_tasks.add(task)
        task.add_done_callback(state.pending_permission_tasks.discard)
        try:
            return await task
        except asyncio.CancelledError:
            if state.cancel_event.is_set():
                return {"cancelled": True}
            raise
        except RPCError as exc:
            # 超时（ACP_PERMISSION_TIMEOUT_SECONDS）/ 客户端回 error / 传输不支持
            # → 一律按拒绝；但审计 note 只给"确实等满超时"的路径打 timeout
            # （客户端 error 响应/连接已关闭会立刻返回，不误标超时）。
            elapsed = time.perf_counter() - started
            timed_out = elapsed >= max(self.permission_timeout - 0.05, 0.0)
            logger.warning(
                "ACP 审批等待失败，按拒绝处理（session=%s，等待 %.1fs，timeout=%s）：%s",
                state.session_id,
                elapsed,
                timed_out,
                exc.message,
            )
            return {"cancelled": True, "timeout": timed_out}
        finally:
            logger.info(
                "ACP permission wait: session=%s duration_ms=%.1f",
                state.session_id,
                (time.perf_counter() - started) * 1000.0,
            )

    async def _on_plan_approval_required(self, state: AcpSessionState, event: Any) -> Any | None:
        task_id = event.task_id or await self._latest_task_id(state.session_id)
        if not task_id:
            raise RPCError(JSONRPC_INTERNAL_ERROR, "计划审批事件缺少任务上下文")
        from app.agent import checkpoint
        from app.models.agent_task import TaskStatus

        if self.plan_approval_mode == "auto":
            logger.info(
                "ACP plan approval AUTO-APPROVED (audit): session=%s task=%s plan=%s",
                state.session_id,
                task_id,
                getattr(event, "plan_id", ""),
            )
            await checkpoint.apply_plan_approval_decision(
                task_id=task_id,
                user_id=self.user_id,
                organization_id=self.organization_id,
                decision="approve",
                note="acp:auto",
            )
            if state.cancel_event.is_set():
                await self._mark_task_cancelled(task_id)
                return None
            return self.service.resume_chat(task_id, user_id=self.user_id, config=None, claim=False)

        # reject（默认）：ACP 模式不支持交互式计划审批——计划审批按拒绝收尾，
        # 任务落到终态 REJECTED（不悬空），prompt 返回 -32009。
        logger.info(
            "ACP plan approval REJECTED (ACP_PLAN_APPROVAL_MODE=reject): session=%s task=%s",
            state.session_id,
            task_id,
        )
        ok = await checkpoint.mark_terminal(
            task_id,
            TaskStatus.REJECTED,
            error="ACP 不支持交互式计划审批（ACP_PLAN_APPROVAL_MODE=reject）",
            error_type="plan_approval_unsupported",
        )
        if not ok:
            # 不悬空是硬约束：终态没写成功必须响亮留痕（-32009 仍如实返回）。
            logger.error(
                "ACP plan approval reject: mark_terminal failed (task=%s)，任务可能停留非终态",
                task_id,
            )
        raise RPCError(
            JSONRPC_CONFLICT,
            "ACP 不支持交互式计划审批（ACP_PLAN_APPROVAL_MODE=reject）",
        )

    # ── 内部：会话 / 任务 / 配置 ─────────────────────────────────────────

    def _require_session_id(self, params: dict) -> str:
        session_id = params.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            raise RPCError(JSONRPC_INVALID_PARAMS, "sessionId 必须是非空字符串")
        return session_id

    async def _require_chat_session(self, session_id: str):
        row = await load_chat_session(
            self._db_factory,
            session_id,
            user_id=self.user_id,
            organization_id=self.organization_id,
            is_superuser=self.is_superuser,
        )
        if row is None:
            raise RPCError(JSONRPC_NOT_FOUND, "会话不存在或无权访问")
        return row

    async def _latest_task_id(self, session_id: str, *, strict: bool = False) -> str | None:
        from app.services.session_service import get_latest_ready_final_task_id, get_latest_task_id

        try:
            async with self._db_factory() as db:
                task_id = await get_latest_task_id(db, session_id)
                if task_id is None:
                    task_id = await get_latest_ready_final_task_id(db, session_id)
                return task_id
        except Exception as exc:  # noqa: BLE001 - 取消/断连清理路径不得二次爆炸
            if strict:
                # session/cancel 的等待路径：读不到状态就不能确认取消，必须如实失败。
                raise RPCError(JSONRPC_INTERNAL_ERROR, "任务状态读取失败，无法确认取消") from exc
            logger.warning("ACP latest-task lookup failed (session=%s)", session_id, exc_info=True)
            return None

    async def _pre_prompt_baseline(self, session_id: str) -> str | None:
        """本轮 prompt 开始前的任务基线（issue #494 评审 B6/nit）。

        返回 ``""`` = 明确没有任务；非空 = 基线任务 id；``None`` = 查询失败/未知
        （未知时取消等待不得用"最新任意任务"推断本轮终态）。
        """
        from sqlalchemy import select

        from app.models.agent_task import AgentTask

        try:
            async with self._db_factory() as db:
                stmt = (
                    select(AgentTask.id)
                    .where(AgentTask.session_id == session_id)
                    .order_by(AgentTask.created_at.desc())
                    .limit(1)
                )
                row = (await db.execute(stmt)).first()
        except Exception:  # noqa: BLE001 - 基线未知按未知处理，绝不误判上一轮任务
            logger.warning(
                "ACP pre-prompt baseline lookup failed (session=%s)", session_id, exc_info=True
            )
            return None
        return row[0] if row is not None else ""

    async def _latest_any_task(self, session_id: str):
        """按会话读取**最新任务行（含终态）**（issue #494 评审 B2/B4）。

        只用于取消语义判定（区分「从未有任务」与「最新任务已终态」）；归属校验
        与 ``load_task`` 同口径（本人 + 严格同组织）。任何读取故障返回 None。
        """
        from sqlalchemy import select
        from sqlalchemy.orm import defer

        from app.models.agent_task import AgentTask

        try:
            async with self._db_factory() as db:
                stmt = (
                    select(AgentTask)
                    .where(AgentTask.session_id == session_id)
                    # 快照列可能是截断/半写 JSON（与 checkpoint 的毒丸防线同口径），
                    # 取消判定不需要它，defer 掉（评审 nit）。
                    .options(defer(AgentTask.context_snapshot))
                    .order_by(AgentTask.created_at.desc())
                    .limit(1)
                )
                task = (await db.execute(stmt)).scalars().first()
        except Exception:  # noqa: BLE001 - 取消路径不得二次爆炸
            logger.warning(
                "ACP latest-task(session) lookup failed (session=%s)", session_id, exc_info=True
            )
            return None
        if task is None or task.user_id != self.user_id:
            return None
        if task.organization_id != self.organization_id:
            return None
        return task

    async def _wait_for_task_row(self, state: AcpSessionState, deadline: float) -> str:
        """等本会话出现**非终态**任务行（prompt 在飞时的登记窗口，评审 B1）。

        - 出现终态任务（取消必输）或 run 结束仍无任务行 → 恢复 update 抑制并
          如实 ``-32004``（取消不可能生效，不能让 run 收尾输出静默丢失，B4/B5）；
        - 超出总预算 → 恢复抑制并 ``-32603``（从未发出取消旗，不得继续抑制，B5）。
        """
        while True:
            task_id = await self._latest_task_id(state.session_id, strict=True)
            if task_id:
                return task_id
            # 只有**本轮**任务的终态才意味着"取消必输"：上一轮遗留的终态任务
            # 不能被当成本轮任务（issue #494 评审 B6）。本轮任务的识别优先级：
            # 事件盖章的 active_task_id（resume 开跑前已知）→ 排除 pre-prompt 基线。
            current = state.active_task_id
            if current:
                current_task = await self._load_task_safe(current)
            else:
                baseline = state.pre_prompt_task_id
                if baseline is None:
                    # 基线未知（prompt 开始查询在途/失败）：不能用"最新任意任务"
                    # 判本轮终态（可能误判上一轮遗留任务），继续等本轮任务行。
                    current_task = None
                else:
                    latest = await self._latest_any_task(state.session_id)
                    current_task = latest if latest is not None and latest.id != baseline else None
            if current_task is not None and current_task.is_terminal:
                state.resume_updates()
                raise RPCError(JSONRPC_NOT_FOUND, "会话没有进行中的任务")
            if not state.lock.locked():
                state.resume_updates()
                raise RPCError(JSONRPC_NOT_FOUND, "会话没有进行中的任务")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                state.resume_updates()
                raise RPCError(
                    JSONRPC_INTERNAL_ERROR,
                    "任务尚未登记，取消未能在 5 秒内受理",
                )
            await asyncio.sleep(min(CANCEL_POLL_INTERVAL_SECONDS, remaining))

    async def _cancel_session(self, state: AcpSessionState, *, wait: bool = False) -> None:
        """会话级取消：置旗、丢未发送更新、取消待审批、任务收敛（不悬空）。

        ``wait=False``（断连 / close / 取消中断路径）：协作式置旗后立即返回，
        收尾由事件流排干后的服务终态完成（断连竞态属 #494 已知风险）。
        ``wait=True``（``session/cancel``）：在 ``CANCEL_TOTAL_TIMEOUT_SECONDS``
        预算内完成「等任务行登记 → 置协作式取消旗 → 等 DB 收敛为 CANCELLED」；
        Redis 不可用 / 超时 / 任务先以其它终态结束 / 无进行中任务 → 如实抛
        ``-32603`` / ``-32004``，绝不假报成功（issue #494）。

        取消**未生效**的失败路径必须 ``state.resume_updates()``：run 还在跑时
        客户端必须继续看到输出、end_turn 答案也要完整落库（评审 B4/B5）；
        只有「取消旗已发出但收敛未确认」的超时路径保留抑制。
        """
        await state.cancel()
        if not wait:
            task_id = await self._latest_task_id(state.session_id)
            if not task_id:
                return
            await self._cancel_task_nowait(task_id)
            return

        deadline = time.monotonic() + CANCEL_TOTAL_TIMEOUT_SECONDS
        try:
            task_id = await self._latest_task_id(state.session_id, strict=True)
            if task_id is None:
                task_id = await self._wait_for_task_row(state, deadline)
        except RPCError:
            # 旗尚未发出（读故障 / 无任务 / 本轮任务已终态）：取消未生效。
            # 恢复 update 外发——run 仍在跑时客户端不得静默（评审 B5/B7）。
            state.resume_updates()
            raise

        from app.models.agent_task import TaskStatus

        task = await self._load_task_safe(task_id)
        if task is None:
            state.resume_updates()
            raise RPCError(JSONRPC_INTERNAL_ERROR, "任务状态读取失败，无法确认取消")

        try:
            if task.status in (
                TaskStatus.WAITING_APPROVAL,
                TaskStatus.WAITING_PLAN_APPROVAL,
                # 无活跃 run 会观察取消旗的挂起态（与 RPC 面 #493 同口径）：
                # 必须直接 DB 终态化，否则会白等到预算耗尽。
                TaskStatus.WAITING_HUMAN,
                TaskStatus.PAUSED,
            ):
                ok = await self._mark_task_cancelled(task_id)
                if not ok:
                    state.resume_updates()
                    raise RPCError(JSONRPC_INTERNAL_ERROR, "任务取消收尾失败（状态未能写入）")
                return
            if task.is_terminal:
                if task.status == TaskStatus.CANCELLED:
                    return
                state.resume_updates()
                raise RPCError(
                    JSONRPC_INTERNAL_ERROR,
                    f"任务在取消生效前已结束（{task.status.value}），取消未生效",
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                state.resume_updates()
                raise RPCError(JSONRPC_INTERNAL_ERROR, "取消未能在 5 秒内确认")
            # 端到端预算（评审 B3）：request_cancel 自身也可能被 redis-py 的
            # 重试拖到 ~26s；wait_for 把「置旗 + 轮询收敛」一起框进剩余预算。
            cancelled = await asyncio.wait_for(
                cancel_task_and_wait(
                    task_id,
                    user_id=self.user_id,
                    organization_id=self.organization_id,
                ),
                timeout=remaining,
            )
        except RPCError:
            raise
        except RedisUnavailableError as exc:
            # 取消通道不可用（旗确定未置上）：恢复 update 外发，如实 -32603。
            state.resume_updates()
            raise RPCError(JSONRPC_INTERNAL_ERROR, "Redis 不可用，无法取消") from exc
        except TimeoutError as exc:
            # 旗可能已在 setex 半途发出：保留抑制，如实报"未确认"（评审 B3/B5）。
            raise RPCError(
                JSONRPC_INTERNAL_ERROR,
                "取消未能在 5 秒内确认（Redis 或任务收敛超时）",
            ) from exc
        except Exception as exc:  # noqa: BLE001 - 收敛路径异常同样不得假报成功
            logger.warning("ACP cancel failed (task=%s)", task_id, exc_info=True)
            raise RPCError(JSONRPC_INTERNAL_ERROR, "取消任务失败") from exc
        if not cancelled:
            # 取消旗已置，但任务先以其它终态收尾：run 可能仍在收尾输出，
            # 恢复外发，让 end_turn 落库完整答案（评审 B4）。
            state.resume_updates()
            raise RPCError(JSONRPC_INTERNAL_ERROR, "任务在取消生效前已结束，取消未生效")

    async def _cancel_task_nowait(self, task_id: str) -> None:
        """断连 / close 路径：协作式置旗即可；活跃 run 会在检查点收敛。"""
        from app.agent import checkpoint
        from app.models.agent_task import TaskStatus

        task = await self._load_task_safe(task_id)
        if task is None:
            return
        delivery_state = await checkpoint.read_final_delivery_state(task_id)
        if delivery_state is not False:
            return
        if task.status in (
            TaskStatus.WAITING_APPROVAL,
            TaskStatus.WAITING_PLAN_APPROVAL,
            TaskStatus.WAITING_HUMAN,
            TaskStatus.PAUSED,
        ):
            await self._mark_task_cancelled(task_id)
            return
        if task.is_terminal:
            return
        if not await checkpoint.request_cancel(task_id):
            logger.warning("ACP cancel: 取消旗不可用，任务将按自然结束收敛 (task=%s)", task_id)

    async def _load_task_safe(self, task_id: str):
        """按归属读取任务行；任何失败返回 None（取消/清理路径不得二次爆炸）。"""
        from app.agent import checkpoint

        try:
            return await checkpoint.load_task(
                task_id=task_id,
                user_id=self.user_id,
                organization_id=self.organization_id,
            )
        except Exception:  # noqa: BLE001
            logger.warning("ACP cancel: load task failed (task=%s)", task_id, exc_info=True)
            return None

    async def _cancel_lost_race(self, task_id: str) -> bool:
        """取消到达但任务已按其它终态收尾（如自然完成）→ True（取消未生效）。

        无 task_id / 读不到状态时保守返回 False（按取消处理，交给终态兜底）。
        """
        if not task_id:
            return False
        from app.models.agent_task import TaskStatus

        task = await self._load_task_safe(task_id)
        return task is not None and task.is_terminal and task.status != TaskStatus.CANCELLED

    async def _ensure_task_cancelled(
        self, state: AcpSessionState, *, task_id: str | None = None
    ) -> None:
        """取消流结束后的终态兜底：本轮任务仍非终态 → 落 CANCELLED（best-effort）。

        优先用本轮 run 的事件盖章 task_id（``state.active_task_id``/显式传入），
        避免同一 ChatSession 多写入者时误伤别的活跃任务；只有拿不到时才回退到
        会话级「最新非终态」。幂等：已是终态（含 CANCELLED）时不动。
        """
        task_id = task_id or state.active_task_id
        if not task_id:
            task_id = await self._latest_task_id(state.session_id)
        if not task_id:
            return
        task = await self._load_task_safe(task_id)
        if task is None or task.is_terminal:
            return
        await self._mark_task_cancelled(task_id)

    async def _mark_task_cancelled(self, task_id: str) -> bool:
        """把任务落 CANCELLED 终态；返回是否成功（从不抛异常）。"""
        from app.agent import checkpoint
        from app.models.agent_task import TaskStatus

        try:
            return bool(
                await checkpoint.mark_terminal(
                    task_id,
                    TaskStatus.CANCELLED,
                    error="用户取消（ACP）",
                    error_type="user_cancelled",
                )
            )
        except Exception:  # noqa: BLE001 - 清理路径不得二次爆炸
            logger.warning("ACP mark cancelled failed (task=%s)", task_id, exc_info=True)
            return False

    def _agent_config_for(self, chat_session) -> Any:
        """ChatSession.settings → AgentConfig（model / plan_approval_mode）。"""
        from app.agent.config import AgentConfig

        config = AgentConfig()
        settings_map = chat_session.settings if isinstance(chat_session.settings, dict) else {}
        model = settings_map.get("model")
        if isinstance(model, str) and model.strip():
            config.model = model.strip()
        plan_mode = settings_map.get("plan_approval_mode")
        if plan_mode in ("always", "never"):
            config.plan_approval_mode = plan_mode
        return config

    async def _save_interaction(
        self, session_id: str, query: str, answer: str, agent_task_id: str | None = None
    ) -> None:
        from app.services.session_service import save_session_interaction

        try:
            await save_session_interaction(
                session_id,
                self.user_id,
                query,
                self.organization_id,
                answer,
                session_factory=self._db_factory,
                bypass_org=self.is_superuser,
                agent_task_id=agent_task_id,
            )
        except Exception:  # noqa: BLE001 - 落库失败如实记日志，不打断协议流
            logger.error("ACP save interaction failed (session=%s)", session_id, exc_info=True)

    async def _background_resume(self, state: AcpSessionState, task_id: str) -> None:
        """``session/resume`` 的后台续跑（不回放历史；更新仍走串行 sender）。"""
        async with state.lock:
            state.reset_for_prompt()
            # resume 的本轮任务在开跑前已知：登记为 active，取消等待窗口据此
            # 区分「本轮任务已终态」与「上一轮遗留」（issue #494 评审 B6）。
            state.active_task_id = task_id
            answer: list[str] = []
            try:
                stop_reason = await self._drive(
                    state,
                    self.service.resume_chat(
                        task_id, user_id=self.user_id, config=None, claim=False
                    ),
                    answer,
                )
                await state.barrier()
                if stop_reason == "cancelled":
                    await self._ensure_task_cancelled(state, task_id=task_id)
                elif stop_reason == "end_turn":
                    query = await self._task_query(task_id)
                    if query:
                        await self._save_interaction(
                            state.session_id, query, "".join(answer), state.active_task_id
                        )
            except RPCError as exc:
                logger.warning("ACP session/resume run failed: %s", exc.message)
            except asyncio.CancelledError:
                await self._cancel_session(state)
                raise
            except Exception:  # noqa: BLE001 - 后台任务异常只记日志
                logger.exception("ACP session/resume crashed (session=%s)", state.session_id)

    async def _task_query(self, task_id: str) -> str:
        from app.agent import checkpoint

        try:
            task = await checkpoint.load_task(
                task_id=task_id,
                user_id=self.user_id,
                organization_id=self.organization_id,
            )
        except Exception:  # noqa: BLE001
            return ""
        return task.query if task is not None else ""

    async def _send_update(self, message: dict) -> None:
        assert self._writer is not None
        await self._writer.send(message)
