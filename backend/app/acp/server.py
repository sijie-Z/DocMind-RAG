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
  外发迟到 update、审批超时按 deny 收尾、断连不悬空任务。
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
from app.rpc.dispatcher import Dispatcher, RPCError
from app.rpc.server import ProtocolWriter, _close_dependencies, _stderr_only_stdout

logger = logging.getLogger(__name__)

# ACP v1 协议版本（v2 仍是草案，不实现）。
ACP_PROTOCOL_VERSION = 1


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
            logger.debug("ACP session/cancel 通知未生效: %s", exc.message)
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
            if stop_reason == "end_turn":
                await self._save_interaction(session_id, text, "".join(answer))
            logger.info(
                "ACP prompt done: session=%s stop=%s duration_ms=%.1f",
                session_id,
                stop_reason,
                (time.perf_counter() - started) * 1000.0,
            )
            return {"stopReason": stop_reason}

    async def session_cancel(self, params: dict) -> dict:
        session_id = self._require_session_id(params)
        state = self.sessions.get(session_id)
        if state is None:
            raise RPCError(JSONRPC_NOT_FOUND, "会话不存在或未在本连接中打开")
        logger.info("ACP cancel: session=%s", session_id)
        await self._cancel_session(state)
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
        """
        while True:
            resumed: Any | None = None
            paused_without_resume = False
            pending_error: RPCError | None = None
            terminal_type = ""
            last_error_event: Any | None = None
            async for event in events:
                if event.type == "chunk":
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
                if event.type in ("done", "run_cancelled", "error"):
                    terminal_type = event.type
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
                if state.cancel_event.is_set():
                    return "cancelled"
                message = (
                    sanitized_error_content(last_error_event)
                    if last_error_event is not None
                    else "服务器处理出错，请稍后重试"
                )
                raise RPCError(JSONRPC_INTERNAL_ERROR, message)
            if terminal_type == "run_cancelled":
                return "cancelled"
            # 取消优先：cancel 已到达则一律 cancelled（且不落库）。
            return "cancelled" if state.cancel_event.is_set() else "end_turn"

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
            # 超时（ACP_PERMISSION_TIMEOUT_SECONDS）/ 传输不支持 → 按拒绝。
            logger.warning(
                "ACP 审批等待失败，按拒绝处理（session=%s，等待 %.1fs）：%s",
                state.session_id,
                time.perf_counter() - started,
                exc.message,
            )
            return {"cancelled": True, "timeout": True}
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

    async def _latest_task_id(self, session_id: str) -> str | None:
        from app.services.session_service import get_latest_task_id

        try:
            async with self._db_factory() as db:
                return await get_latest_task_id(db, session_id)
        except Exception:  # noqa: BLE001 - 取消/断连清理路径不得二次爆炸
            logger.warning("ACP latest-task lookup failed (session=%s)", session_id, exc_info=True)
            return None

    async def _cancel_session(self, state: AcpSessionState) -> None:
        """会话级取消：置旗、丢未发送更新、取消待审批、任务收敛（不悬空）。"""
        await state.cancel()
        task_id = await self._latest_task_id(state.session_id)
        if not task_id:
            return
        from app.agent import checkpoint
        from app.models.agent_task import TaskStatus

        try:
            task = await checkpoint.load_task(
                task_id=task_id,
                user_id=self.user_id,
                organization_id=self.organization_id,
            )
        except Exception:  # noqa: BLE001
            logger.warning("ACP cancel: load task failed (task=%s)", task_id, exc_info=True)
            return
        if task is None:
            return
        try:
            if task.status in (TaskStatus.WAITING_APPROVAL, TaskStatus.WAITING_PLAN_APPROVAL):
                # 待审批任务按取消收尾（审批请求随之关闭，绝不悬空）。
                await self._mark_task_cancelled(task_id)
            elif not task.is_terminal:
                await checkpoint.request_cancel(task_id)
        except Exception:  # noqa: BLE001
            logger.warning("ACP cancel cleanup failed (task=%s)", task_id, exc_info=True)

    async def _mark_task_cancelled(self, task_id: str) -> None:
        from app.agent import checkpoint
        from app.models.agent_task import TaskStatus

        try:
            await checkpoint.mark_terminal(
                task_id,
                TaskStatus.CANCELLED,
                error="用户取消（ACP）",
                error_type="user_cancelled",
            )
        except Exception:  # noqa: BLE001 - 清理路径不得二次爆炸
            logger.warning("ACP mark cancelled failed (task=%s)", task_id, exc_info=True)

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

    async def _save_interaction(self, session_id: str, query: str, answer: str) -> None:
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
            )
        except Exception:  # noqa: BLE001 - 落库失败如实记日志，不打断协议流
            logger.error("ACP save interaction failed (session=%s)", session_id, exc_info=True)

    async def _background_resume(self, state: AcpSessionState, task_id: str) -> None:
        """``session/resume`` 的后台续跑（不回放历史；更新仍走串行 sender）。"""
        async with state.lock:
            state.reset_for_prompt()
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
                if stop_reason == "end_turn":
                    query = await self._task_query(task_id)
                    if query:
                        await self._save_interaction(state.session_id, query, "".join(answer))
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
