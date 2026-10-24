"""JSON-RPC 2.0 dispatcher 与方法实现（issue #480 §5.3 / §5.4）。

- :class:`Dispatcher`：请求 id 表（在途去重 → -32600）、并发执行、乱序响应、
  双向请求（``session/request_permission`` 等，超时按拒绝）、
  ``$/cancel_request`` 取消在途服务端请求、断连拒绝全部 pending；
- :class:`RPCService`：8 个方法（initialize / session.prompt / resume / wait /
  cancel / approvals.respond / tools.list / shutdown）与通知
  ``session.event`` / ``session.status``。

传输无关：stdio（``server.py``）与 HTTP（``http.py``）共用本模块。
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.core.error_mapping import (
    GENERIC_SERVER_ERROR,
    JSONRPC_INTERNAL_ERROR,
    JSONRPC_INVALID_PARAMS,
    JSONRPC_INVALID_REQUEST,
    JSONRPC_METHOD_NOT_FOUND,
    JSONRPC_NOT_FOUND,
    rpc_error_from_exception,
)
from app.core.log_safety import log_safe
from app.core.prometheus import RPC_REQUEST_DURATION, RPC_REQUESTS_TOTAL
from app.core.redis import RedisUnavailableError, redis_client

logger = logging.getLogger(__name__)

SendFn = Callable[[dict[str, Any]], Awaitable[None]]

# 幂等键 TTL（issue §5.4：24h）。
IDEMPOTENCY_TTL_SECONDS = 86400
# session/wait 默认与最大等待（毫秒）。
SESSION_WAIT_DEFAULT_MS = 30_000
SESSION_WAIT_MAX_MS = 300_000
# session/cancel 等待任务在 DB 收敛为 CANCELLED 的上限（秒，issue #493）。
CANCEL_CONVERGE_TIMEOUT_SECONDS = 5.0


class RPCError(Exception):
    """方法层错误：直接携带 JSON-RPC code（对外 message 不得含内部细节）。"""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        self.code = code
        self.message = message
        self.data = data
        super().__init__(message)


def _valid_id(value: Any) -> bool:
    return value is None or (isinstance(value, (str, int, float)) and not isinstance(value, bool))


@dataclass
class _PendingServerRequest:
    future: asyncio.Future
    method: str
    session_id: str | None


def _error(id_value: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": id_value, "error": error}


async def cancel_task_and_wait(
    task_id: str,
    *,
    user_id: int,
    organization_id: int | None = None,
    timeout: float = CANCEL_CONVERGE_TIMEOUT_SECONDS,
) -> bool:
    """置协作式取消旗并等待任务在 DB 收敛（issue #493）。

    - 返回 True：``timeout`` 内观察到任务状态 ``CANCELLED``；
    - 返回 False：任务先以其他终态收尾（取消输掉竞态，如实返回未生效）；
    - Redis 不可用（未初始化 / 连接失败）→ :class:`RedisUnavailableError`，
      调用方必须映射为 -32603，**不得**静默降级成"取消未生效"；
    - 旗已置但 ``timeout`` 内未收敛 → ``TimeoutError``：取消已受理，只是任务
      还没走到检查点，调用方可继续用 ``session/wait`` 等待。

    共享助手：ACP（issue #494）复用同一语义。
    """
    from app.agent import checkpoint
    from app.models.agent_task import TaskStatus

    if not await checkpoint.request_cancel(task_id):
        raise RedisUnavailableError("Redis 不可用，无法取消")

    deadline = time.monotonic() + max(0.0, float(timeout))
    while True:
        task = await checkpoint.load_task(
            task_id=task_id,
            user_id=user_id,
            organization_id=organization_id,
        )
        if task.status == TaskStatus.CANCELLED:
            return True
        if task.is_terminal:
            return False
        if time.monotonic() >= deadline:
            raise TimeoutError(f"取消已受理，但任务未在 {timeout:g} 秒内收敛为 cancelled")
        await asyncio.sleep(0.1)


class Dispatcher:
    """JSON-RPC 2.0 消息分发器（传输无关）。"""

    def __init__(
        self,
        methods: Mapping[str, Callable[[dict], Awaitable[Any]]],
        *,
        send: SendFn | None = None,
        on_disconnect: Callable[[], Awaitable[None]] | None = None,
        permission_timeout: float | None = None,
    ) -> None:
        self._methods = dict(methods)
        self._send = send
        self._on_disconnect = on_disconnect
        self._inflight: dict[Any, asyncio.Task] = {}
        self._pending_server: dict[str, _PendingServerRequest] = {}
        self._server_ids = itertools.count(1)
        self._tasks: set[asyncio.Task] = set()
        self._closed = False
        self.permission_timeout = (
            float(permission_timeout)
            if permission_timeout is not None
            else float(settings.RPC_PERMISSION_TIMEOUT_SECONDS)
        )

    # ── 生命周期 ──────────────────────────────────────────────────────────

    @property
    def supports_server_requests(self) -> bool:
        return self._send is not None and not self._closed

    @property
    def closed(self) -> bool:
        return self._closed

    async def close(self) -> None:
        """断连收尾：拒绝全部 pending 服务端请求、取消在途任务。"""
        if self._closed:
            return
        self._closed = True
        tasks = {t for t in self._tasks if isinstance(t, asyncio.Task)}
        tasks |= {t for t in self._inflight.values() if isinstance(t, asyncio.Task)}
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for entry in self._pending_server.values():
            if not entry.future.done():
                # 用 result 而非 exception：等待方即使已被取消也不会产生
                # "exception was never retrieved" 噪音；语义上等价拒绝。
                entry.future.set_result({"cancelled": True, "disconnected": True})
        self._pending_server.clear()
        if self._on_disconnect is not None:
            try:
                await self._on_disconnect()
            except Exception:  # noqa: BLE001 - 断连收尾不抛给事件循环
                logger.warning("RPC on_disconnect hook failed", exc_info=True)

    async def wait_idle(self, timeout: float = 10.0) -> None:
        """等待在途请求全部完成（shutdown / 测试用）。"""
        deadline = time.monotonic() + timeout
        while self._inflight or self._tasks:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            await asyncio.sleep(min(0.02, remaining))

    # ── 入站消息 ──────────────────────────────────────────────────────────

    def dispatch(self, message: Any) -> None:
        """stdio 入口：同步登记 id（重复检测无竞态），异步执行并回写响应。"""
        if self._closed:
            return
        if isinstance(message, dict) and "method" in message and "id" in message:
            mid = message["id"]
            if not _valid_id(mid) or not self._register(mid):
                self._spawn(self._send_invalid(mid if _valid_id(mid) else None))
                return
            task = asyncio.create_task(self._run_and_send(message, mid))
            self._track(task)
            return
        self._track(asyncio.create_task(self._process_and_send(message)))

    async def handle_message(self, message: Any) -> dict[str, Any] | None:
        """单条消息处理（HTTP / 测试用）：直接返回响应（通知返回 None）。"""
        if isinstance(message, dict) and "method" in message and "id" in message:
            mid = message["id"]
            if not _valid_id(mid) or not self._register(mid):
                return _error(
                    mid if _valid_id(mid) else None, JSONRPC_INVALID_REQUEST, "重复或非法请求 id"
                )
            try:
                return await self._execute_request(message)
            finally:
                self._inflight.pop(mid, None)
        return await self._process_nonrequest(message)

    # ── 服务端 → 客户端请求 ──────────────────────────────────────────────

    async def request_client(
        self,
        method: str,
        params: dict,
        *,
        session_id: str | None = None,
        timeout: float | None = None,
    ) -> dict:
        """向客户端发请求并等待响应；超时按拒绝处理（RPCError）。"""
        if not self.supports_server_requests or self._send is None:
            raise RPCError(JSONRPC_INTERNAL_ERROR, "当前传输不支持服务端发起的请求（请使用 stdio）")
        request_id = f"srv-{next(self._server_ids)}"
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending_server[request_id] = _PendingServerRequest(
            future=future, method=method, session_id=session_id
        )
        try:
            await self._send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params,
                }
            )
            try:
                result = await asyncio.wait_for(
                    future, timeout=timeout if timeout is not None else self.permission_timeout
                )
            except TimeoutError as exc:
                logger.warning("RPC server request %s timed out (%s)", method, request_id)
                raise RPCError(JSONRPC_INTERNAL_ERROR, "客户端响应超时，按拒绝处理") from exc
        finally:
            self._pending_server.pop(request_id, None)
        return result if isinstance(result, dict) else {"result": result}

    # ── 内部 ──────────────────────────────────────────────────────────────

    def _track(self, task: asyncio.Task) -> None:
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _register(self, request_id: Any) -> bool:
        if request_id in self._inflight:
            return False
        self._inflight[request_id] = None  # type: ignore[assignment] - 占位防竞态
        return True

    def _spawn(self, coro: Awaitable[Any]) -> None:
        self._track(asyncio.create_task(coro))  # type: ignore[arg-type]

    async def _process_and_send(self, message: Any) -> None:
        response = await self._process_nonrequest(message)
        if response is not None and self._send is not None:
            try:
                await self._send(response)
            except Exception:  # noqa: BLE001
                logger.warning("Failed to write RPC error response", exc_info=True)

    async def _send_invalid(self, request_id: Any) -> None:
        if self._send is not None:
            await self._send(_error(request_id, JSONRPC_INVALID_REQUEST, "非法请求"))

    async def _run_and_send(self, message: dict, mid: Any) -> None:
        try:
            response = await self._execute_request(message)
        except Exception:  # noqa: BLE001 - 兜底：绝不因单请求异常打断 stdio 循环
            logger.exception("RPC request crashed (id=%s)", log_safe(mid))
            response = _error(mid, JSONRPC_INTERNAL_ERROR, GENERIC_SERVER_ERROR)
        finally:
            self._inflight.pop(mid, None)
        if response is not None and self._send is not None:
            try:
                await self._send(response)
            except Exception:  # noqa: BLE001
                logger.warning("Failed to write RPC response (id=%s)", log_safe(mid), exc_info=True)

    async def _execute_request(self, message: dict) -> dict:
        mid = message.get("id")
        if message.get("jsonrpc") != "2.0":
            return _error(mid, JSONRPC_INVALID_REQUEST, 'jsonrpc 必须为 "2.0"')
        method = message.get("method")
        if not isinstance(method, str):
            return _error(mid, JSONRPC_INVALID_REQUEST, "method 必须是字符串")
        handler = self._methods.get(method)
        if handler is None:
            return _error(mid, JSONRPC_METHOD_NOT_FOUND, f"未知方法: {method}")
        params = message.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            return _error(mid, JSONRPC_INVALID_PARAMS, "params 必须是对象")

        started = time.perf_counter()
        try:
            result = handler(params)
            if asyncio.iscoroutine(result) or isinstance(result, Awaitable):
                result = await result
        except RPCError as exc:
            RPC_REQUESTS_TOTAL.labels(method=method, result="error").inc()
            return _error(mid, exc.code, exc.message, exc.data)
        except Exception as exc:  # noqa: BLE001
            code, public_message, data = rpc_error_from_exception(exc)
            if code == JSONRPC_INTERNAL_ERROR:
                logger.exception("RPC method failed: %s", log_safe(method))
            else:
                logger.info("RPC method rejected: %s (%s)", log_safe(method), type(exc).__name__)
            RPC_REQUESTS_TOTAL.labels(method=method, result="error").inc()
            return _error(mid, code, public_message, data)
        finally:
            RPC_REQUEST_DURATION.labels(method=method).observe(
                (time.perf_counter() - started) * 1000.0
            )
        RPC_REQUESTS_TOTAL.labels(method=method, result="success").inc()
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    async def _process_nonrequest(self, message: Any) -> dict | None:
        if not isinstance(message, dict):
            return _error(None, JSONRPC_INVALID_REQUEST, "请求必须是 JSON 对象")
        if "method" in message:
            method = message.get("method")
            params = message.get("params") or {}
            if method == "$/cancel_request":
                await self._handle_cancel_request(params)
                return None
            logger.debug("Ignoring unknown RPC notification: %s", log_safe(method))
            return None
        if "result" in message or "error" in message:
            self._handle_client_response(message)
            return None
        return _error(message.get("id"), JSONRPC_INVALID_REQUEST, "无法识别的 JSON-RPC 消息")

    async def _handle_cancel_request(self, params: Any) -> None:
        if not isinstance(params, dict):
            return
        request_id = params.get("requestId")
        entry = self._pending_server.pop(str(request_id), None)
        if entry is None:
            return
        if not entry.future.done():
            entry.future.set_result({"cancelled": True})
        if entry.session_id:
            cancel = self._methods.get("session/cancel")
            if cancel is not None:
                try:
                    result = cancel({"sessionId": entry.session_id, "reason": "cancel_request"})
                    if asyncio.iscoroutine(result) or isinstance(result, Awaitable):
                        await result
                except Exception:  # noqa: BLE001
                    logger.warning("Forward session/cancel failed", exc_info=True)

    def _handle_client_response(self, message: dict) -> None:
        request_id = message.get("id")
        entry = self._pending_server.get(str(request_id))
        if entry is None:
            logger.debug("Ignoring response for unknown server request id=%s", log_safe(request_id))
            return
        if entry.future.done():
            return
        if "error" in message:
            error = message.get("error") or {}
            try:
                code = int(error.get("code", JSONRPC_INTERNAL_ERROR))
            except (TypeError, ValueError):
                code = JSONRPC_INTERNAL_ERROR
            entry.future.set_exception(
                RPCError(code, str(error.get("message", "客户端返回错误")), error.get("data"))
            )
        else:
            entry.future.set_result(message.get("result"))


class RPCService:
    """8 个 JSON-RPC 方法 + 通知发射（stdio / HTTP 共用）。"""

    def __init__(
        self,
        *,
        organization_id: int | None,
        user_id: int,
        is_superuser: bool = False,
        db_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.organization_id = organization_id
        self.user_id = user_id
        self.is_superuser = is_superuser
        self._db_factory = db_factory or AsyncSessionLocal
        self._prompt_tasks: dict[str, asyncio.Task] = {}
        self._cancelled_sessions: set[str] = set()
        self._session_seq: dict[str, int] = {}
        self.dispatcher: Dispatcher | None = None
        self.shutdown_requested = False

    # ── 装配 ──────────────────────────────────────────────────────────────

    def method_table(self) -> dict[str, Callable[[dict], Awaitable[Any]]]:
        return {
            "initialize": self.initialize,
            "session/prompt": self.session_prompt,
            "session/resume": self.session_resume,
            "session/wait": self.session_wait,
            "session/cancel": self.session_cancel,
            "approvals/respond": self.approvals_respond,
            "tools/list": self.tools_list,
            "shutdown": self.shutdown,
        }

    def build_dispatcher(self, send: SendFn | None = None) -> Dispatcher:
        self.dispatcher = Dispatcher(
            self.method_table(), send=send, on_disconnect=self.on_disconnect
        )
        return self.dispatcher

    async def on_disconnect(self) -> None:
        """客户端断连：取消全部在跑 prompt/resume 任务（不留悬空 run）。"""
        tasks = [t for t in self._prompt_tasks.values() if not t.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._prompt_tasks.clear()

    # ── 方法 ──────────────────────────────────────────────────────────────

    async def initialize(self, params: dict) -> dict:
        return {
            "protocolVersion": 1,
            "serverInfo": {"name": "docmind", "version": settings.APP_VERSION},
            "methods": sorted(self.method_table()),
            "capabilities": {"streaming": True, "approvals": True},
        }

    async def session_prompt(self, params: dict) -> dict:
        prompt = params.get("prompt")
        client_message_id = params.get("clientMessageId")
        if not isinstance(prompt, str) or not prompt.strip():
            raise RPCError(JSONRPC_INVALID_PARAMS, "prompt 必须是非空字符串")
        if not isinstance(client_message_id, str) or not client_message_id.strip():
            raise RPCError(JSONRPC_INVALID_PARAMS, "clientMessageId 必须是非空字符串")

        # 幂等通道是硬依赖（issue #493）：Redis 未初始化时在**建会话/启动 run
        # 之前**就失败，避免留下无所属消息的孤儿会话行；连接期故障由
        # _check_idempotency 同样映射 -32603。
        if not redis_client:
            raise RPCError(JSONRPC_INTERNAL_ERROR, "Redis 不可用，无法保证幂等")

        session_id = params.get("sessionId")
        if session_id is not None and not isinstance(session_id, str):
            raise RPCError(JSONRPC_INVALID_PARAMS, "sessionId 必须是字符串")
        if session_id:
            await self._ensure_session_access(session_id)
        else:
            session_id = await self._create_session(prompt)

        message_id = str(uuid.uuid4())
        dedup_key = f"rpc:msg:{session_id}:{client_message_id}"
        dedup = await self._check_idempotency(dedup_key, message_id, session_id)
        if dedup is not None:
            return dedup

        self._cancelled_sessions.discard(session_id)
        task = asyncio.create_task(self._run_prompt(session_id, prompt, message_id))
        self._prompt_tasks[session_id] = task
        task.add_done_callback(lambda t: self._forget_task(session_id, t))
        return {"messageId": message_id, "sessionId": session_id}

    async def session_resume(self, params: dict) -> dict:
        session_id = params.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            raise RPCError(JSONRPC_INVALID_PARAMS, "sessionId 必须是字符串")
        task_id = await self._latest_task_id(session_id)
        from app.agent.service import agent_service

        await agent_service.prepare_resume(
            task_id,
            user_id=self.user_id,
            organization_id=self.organization_id,
        )
        self._cancelled_sessions.discard(session_id)
        task = asyncio.create_task(self._run_resume(session_id, task_id))
        self._prompt_tasks[session_id] = task
        task.add_done_callback(lambda t: self._forget_task(session_id, t))
        return {"sessionId": session_id, "status": "running"}

    async def session_wait(self, params: dict) -> dict:
        session_id = params.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            raise RPCError(JSONRPC_INVALID_PARAMS, "sessionId 必须是字符串")
        timeout_ms = params.get("timeoutMs", SESSION_WAIT_DEFAULT_MS)
        try:
            timeout_ms = int(timeout_ms)
        except (TypeError, ValueError) as exc:
            raise RPCError(JSONRPC_INVALID_PARAMS, "timeoutMs 必须是整数") from exc
        timeout_ms = max(0, min(timeout_ms, SESSION_WAIT_MAX_MS))

        from app.agent import checkpoint
        from app.models.agent_task import TaskStatus

        stops = {
            TaskStatus.COMPLETED: "done",
            TaskStatus.CANCELLED: "cancelled",
            TaskStatus.FAILED: "failed",
            TaskStatus.REJECTED: "plan_rejected",
            TaskStatus.ABANDONED: "abandoned",
            TaskStatus.WAITING_APPROVAL: "approval_required",
            TaskStatus.WAITING_PLAN_APPROVAL: "plan_approval_required",
        }
        task_id = await self._latest_task_id(session_id)
        deadline = time.monotonic() + timeout_ms / 1000.0
        status = TaskStatus.PENDING
        while True:
            from app.exceptions import AppError

            try:
                task = await checkpoint.load_task(
                    task_id=task_id,
                    user_id=self.user_id,
                    organization_id=self.organization_id,
                )
            except AppError as exc:
                raise RPCError(JSONRPC_NOT_FOUND, "任务不存在") from exc
            if task is None:
                raise RPCError(JSONRPC_NOT_FOUND, "任务不存在")
            status = task.status
            if status in stops:
                return {"status": status.value, "stopReason": stops[status]}
            if time.monotonic() >= deadline:
                return {"status": status.value, "stopReason": "timeout"}
            await asyncio.sleep(0.1)

    async def session_cancel(self, params: dict) -> dict:
        """取消会话当前任务（issue #493）。

        运行中任务：先置协作式取消旗，再轮询到 DB 收敛为 ``CANCELLED``（≤5s）
        才回 ``{"cancelled": true}``；Redis 不可用 → -32603（不得静默降级）。
        审批等待中的任务直接 DB 终态化；无任务 → -32004。
        """
        session_id = params.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            raise RPCError(JSONRPC_INVALID_PARAMS, "sessionId 必须是字符串")
        task_id = await self._latest_task_id(session_id)

        from app.agent import checkpoint
        from app.exceptions import AppError
        from app.models.agent_task import TaskStatus

        # 先登记：返回后该会话不再外发事件（issue §5.4）。
        self._cancelled_sessions.add(session_id)

        try:
            task = await checkpoint.load_task(
                task_id=task_id,
                user_id=self.user_id,
                organization_id=self.organization_id,
            )
        except AppError as exc:
            self._cancelled_sessions.discard(session_id)
            raise RPCError(JSONRPC_NOT_FOUND, "任务不存在") from exc
        if task is None:
            self._cancelled_sessions.discard(session_id)
            raise RPCError(JSONRPC_NOT_FOUND, "任务不存在")
        if task.status in (
            TaskStatus.WAITING_APPROVAL,
            TaskStatus.WAITING_PLAN_APPROVAL,
            # 这两个状态下没有活跃 run 会观察取消旗（waiting_human 是 legacy
            # 无写入者态；paused 只是 fork 克隆快照的起点），必须直接 DB
            # 终态化，否则会等到 5s 超时。
            TaskStatus.WAITING_HUMAN,
            TaskStatus.PAUSED,
        ):
            ok = await checkpoint.mark_terminal(
                task_id,
                TaskStatus.CANCELLED,
                error="用户取消（RPC）",
                error_type="user_cancelled",
            )
            if not ok:
                # 未真正取消：恢复事件外发（不能静默丢流）。
                self._cancelled_sessions.discard(session_id)
            return {"cancelled": bool(ok)}
        if task.is_terminal:
            self._cancelled_sessions.discard(session_id)
            return {"cancelled": task.status == TaskStatus.CANCELLED}
        try:
            cancelled = await cancel_task_and_wait(
                task_id,
                user_id=self.user_id,
                organization_id=self.organization_id,
            )
        except RedisUnavailableError as exc:
            # 取消通道不可用：如实报错，绝不再返回 {"cancelled": false} 让客户端
            # 误以为任务拒绝了取消。
            self._cancelled_sessions.discard(session_id)
            raise RPCError(JSONRPC_INTERNAL_ERROR, "Redis 不可用，无法取消") from exc
        except TimeoutError as exc:
            # 旗已置、取消已受理，只是尚未走到检查点；保留事件抑制（取消终将
            # 生效），如实返回错误让客户端继续用 session/wait 观察收敛。
            raise RPCError(JSONRPC_INTERNAL_ERROR, str(exc)) from exc
        if not cancelled:
            self._cancelled_sessions.discard(session_id)
        return {"cancelled": cancelled}

    async def approvals_respond(self, params: dict) -> dict:
        session_id = params.get("sessionId")
        approval_id = params.get("approvalId")
        decision = params.get("decision")
        if not isinstance(session_id, str) or not session_id:
            raise RPCError(JSONRPC_INVALID_PARAMS, "sessionId 必须是字符串")
        if decision not in ("allow", "deny"):
            raise RPCError(JSONRPC_INVALID_PARAMS, "decision 必须是 allow 或 deny")

        from app.agent import checkpoint

        task_id = await self._latest_task_id(session_id)
        pending = await checkpoint.get_pending_approval(task_id)
        if pending is None:
            raise RPCError(JSONRPC_NOT_FOUND, "没有待审批请求")
        if approval_id not in (task_id, pending.get("args_hash")):
            raise RPCError(JSONRPC_NOT_FOUND, "approvalId 不匹配")

        await checkpoint.apply_approval_decision(
            task_id=task_id,
            user_id=self.user_id,
            organization_id=self.organization_id,
            decision="approve" if decision == "allow" else "deny",
            note="remember" if params.get("remember") else None,
        )
        self._cancelled_sessions.discard(session_id)
        task = asyncio.create_task(self._run_resume(session_id, task_id))
        self._prompt_tasks[session_id] = task
        task.add_done_callback(lambda t: self._forget_task(session_id, t))
        return {"ok": True}

    async def tools_list(self, params: dict) -> dict:
        # 触发 core_tools / tools 包的注册副作用，确保 stdio 进程里清单完整。
        import app.agent.service  # noqa: F401
        from app.agent.registry import tool_registry

        return {"tools": tool_registry.get_manifest(include_hidden=True)}

    async def shutdown(self, params: dict) -> dict:
        self.shutdown_requested = True
        tasks = [t for t in self._prompt_tasks.values() if not t.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._prompt_tasks.clear()
        return {"ok": True}

    # ── 内部：会话 / 幂等 / 事件 ─────────────────────────────────────────

    def _forget_task(self, session_id: str, task: asyncio.Task) -> None:
        if self._prompt_tasks.get(session_id) is task:
            self._prompt_tasks.pop(session_id, None)

    async def _create_session(self, prompt: str) -> str:
        from app.models.chat import ChatSession, ChatSessionStatus

        session_id = str(uuid.uuid4())
        async with self._db_factory() as db:
            db.add(
                ChatSession(
                    id=session_id,
                    user_id=self.user_id,
                    title=(prompt.strip().splitlines()[0] if prompt.strip() else "SDK Session")[
                        :80
                    ],
                    status=ChatSessionStatus.ACTIVE,
                    organization_id=self.organization_id,
                    root_session_id=session_id,
                )
            )
            await db.commit()
        return session_id

    async def _ensure_session_access(self, session_id: str) -> None:
        from app.models.chat import ChatSession

        async with self._db_factory() as db:
            session = await db.get(ChatSession, session_id)
        if session is None or session.user_id != self.user_id:
            raise RPCError(JSONRPC_NOT_FOUND, "会话不存在或无权访问")
        if (
            not self.is_superuser
            and session.organization_id is not None
            and session.organization_id != self.organization_id
        ):
            raise RPCError(JSONRPC_NOT_FOUND, "会话不存在或无权访问")

    async def _latest_task_id(self, session_id: str) -> str:
        from app.services.session_service import get_latest_task_id

        async with self._db_factory() as db:
            task_id = await get_latest_task_id(db, session_id)
        if task_id is None:
            raise RPCError(JSONRPC_NOT_FOUND, "会话没有进行中的任务")
        return task_id

    async def _check_idempotency(self, key: str, message_id: str, session_id: str) -> dict | None:
        """Redis 幂等占位（issue #493）。

        Redis 不可用时必须抛 ``RPCError(-32603)``——静默降级会使同一
        ``clientMessageId`` 重复触发 run。正常时同 key 第二次返回同一 messageId。
        存储值损坏与 Redis 不可用分开报错，避免误导排障方向。
        """
        payload = json.dumps({"messageId": message_id, "sessionId": session_id})

        def _decode(raw: str) -> dict:
            try:
                data = json.loads(raw)
                return {"messageId": data["messageId"], "sessionId": data["sessionId"]}
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                logger.warning("RPC idempotency record corrupted: %s", log_safe(exc))
                raise RPCError(JSONRPC_INTERNAL_ERROR, "幂等记录损坏，拒绝执行") from exc

        try:
            existing = await redis_client.get(key)
            if existing:
                return _decode(existing)
            created = await redis_client.set(key, payload, nx=True, ex=IDEMPOTENCY_TTL_SECONDS)
            if created is None:
                existing = await redis_client.get(key)
                if existing:
                    return _decode(existing)
        except RPCError:
            raise
        except Exception as exc:  # noqa: BLE001 - 不可用必须如实失败，不得静默继续
            logger.warning("RPC idempotency unavailable: %s", log_safe(exc))
            raise RPCError(JSONRPC_INTERNAL_ERROR, "Redis 不可用，无法保证幂等") from exc
        return None

    async def _notify(self, method: str, params: dict) -> None:
        if self.dispatcher is None or not self.dispatcher.supports_server_requests:
            return
        send = self.dispatcher._send  # noqa: SLF001 - 同模块内协作
        if send is None:
            return
        try:
            await send({"jsonrpc": "2.0", "method": method, "params": params})
        except Exception:  # noqa: BLE001
            logger.warning("RPC notification failed: %s", method, exc_info=True)

    async def _emit_event(self, session_id: str, event: Any) -> None:
        if session_id in self._cancelled_sessions:
            return
        from app.core.error_mapping import public_agent_event_data

        seq = self._session_seq.get(session_id, 0) + 1
        self._session_seq[session_id] = seq
        await self._notify(
            "session.event",
            {"sessionId": session_id, "event": public_agent_event_data(event), "seq": seq},
        )

    async def _notify_status(self, session_id: str, status: str) -> None:
        await self._notify("session.status", {"sessionId": session_id, "status": status})

    @staticmethod
    def _status_for(last_type: str) -> str:
        """agent 事件终态 → session.status（不把 error/计划审批谎报成 done）。"""
        return {
            "approval_required": "waiting_approval",
            "plan_approval_required": "waiting_plan_approval",
            "run_cancelled": "cancelled",
            "error": "error",
        }.get(last_type, "done")

    async def _save_interaction(self, session_id: str, prompt: str, answer: str) -> None:
        from app.services.session_service import save_session_interaction

        try:
            await save_session_interaction(
                session_id,
                self.user_id,
                prompt,
                self.organization_id,
                answer,
                session_factory=self._db_factory,
                bypass_org=self.is_superuser,
            )
        except Exception:  # noqa: BLE001 - 落库失败如实记日志，不打断 RPC 流
            logger.error("RPC save interaction failed (session=%s)", session_id, exc_info=True)

    async def _stream_events(self, session_id: str, events: Any) -> tuple[str, str, bool]:
        """转发事件流；返回 (last_event_type, 累积回答, 是否已处理审批)。"""
        last_type = ""
        answer = ""
        permission_handled = False
        async for event in events:
            last_type = event.type
            if event.type == "chunk":
                answer += event.content
            await self._emit_event(session_id, event)
            # 双向请求（issue §5.3）：stdio 客户端可被服务端询问；HTTP 传输
            # 没有回推通道，客户端改走 approvals/respond。
            if (
                event.type == "approval_required"
                and self.dispatcher is not None
                and self.dispatcher.supports_server_requests
                and getattr(event, "task_id", None)
            ):
                permission_handled = True
                await self._request_permission(session_id, event)
        return last_type, answer, permission_handled

    async def _request_permission(self, session_id: str, event: Any) -> None:
        """发 ``session/request_permission`` 并按客户端决定审批 + 续跑。"""
        task_id = event.task_id
        assert self.dispatcher is not None
        try:
            result = await self.dispatcher.request_client(
                "session/request_permission",
                {
                    "sessionId": session_id,
                    "toolCallId": getattr(event, "tool_call_id", "") or "",
                    "toolName": getattr(event, "tool_name", "") or "",
                    "options": [
                        {"id": "allow_once", "label": "允许一次"},
                        {"id": "reject_once", "label": "拒绝"},
                    ],
                },
                session_id=session_id,
            )
        except RPCError as exc:
            logger.info("session/request_permission unavailable: %s（按拒绝处理）", exc.message)
            result = {}

        choice = result.get("optionId") or result.get("decision") or ""
        allow = choice in ("allow_once", "allow", "approve")
        from app.agent import checkpoint

        try:
            await checkpoint.apply_approval_decision(
                task_id=task_id,
                user_id=self.user_id,
                organization_id=self.organization_id,
                decision="approve" if allow else "deny",
                note="rpc:request_permission",
            )
            await self._run_resume(session_id, task_id)
        except Exception:  # noqa: BLE001 - 审批失败如实记录，由状态通知收敛
            logger.exception("RPC request_permission follow-up failed (task=%s)", task_id)

    async def _run_prompt(self, session_id: str, prompt: str, message_id: str) -> None:
        from app.agent.config import AgentConfig
        from app.agent.service import agent_service

        try:
            last_type, answer, permission_handled = await self._stream_events(
                session_id,
                agent_service.chat(
                    query=prompt,
                    history=[],
                    organization_id=self.organization_id,
                    user_id=self.user_id,
                    session_id=session_id,
                    config=AgentConfig(),
                ),
            )
            if permission_handled:
                return  # _request_permission 内部已续跑并发出终局状态
            if session_id in self._cancelled_sessions:
                # cancel 后即使底层 run 自然结束，也不落库、不谎报 done。
                await self._notify_status(session_id, "cancelled")
                return
            if last_type not in (
                "approval_required",
                "run_cancelled",
                "plan_approval_required",
            ):
                await self._save_interaction(session_id, prompt, answer)
            await self._notify_status(session_id, self._status_for(last_type))
        except asyncio.CancelledError:
            await self._notify_status(session_id, "cancelled")
            raise
        except Exception:  # noqa: BLE001
            logger.exception("RPC prompt failed (session=%s)", session_id)
            await self._notify_status(session_id, "error")

    async def _run_resume(self, session_id: str, task_id: str) -> None:
        from app.agent.config import AgentConfig
        from app.agent.service import agent_service

        try:
            last_type, answer, permission_handled = await self._stream_events(
                session_id,
                agent_service.resume_chat(
                    task_id,
                    user_id=self.user_id,
                    config=AgentConfig(),
                    claim=False,
                ),
            )
            if permission_handled:
                return  # 下一轮审批在 _request_permission 内部闭环
            if session_id in self._cancelled_sessions:
                await self._notify_status(session_id, "cancelled")
                return
            if last_type not in (
                "approval_required",
                "run_cancelled",
                "plan_approval_required",
            ):
                from app.agent import checkpoint

                async with self._db_factory() as db:
                    task = await checkpoint._load_task_row(db, task_id)
                if task is not None:
                    await self._save_interaction(session_id, task.query, answer)
            await self._notify_status(session_id, self._status_for(last_type))
        except asyncio.CancelledError:
            await self._notify_status(session_id, "cancelled")
            raise
        except Exception:  # noqa: BLE001
            logger.exception("RPC resume failed (session=%s task=%s)", session_id, task_id)
            await self._notify_status(session_id, "error")
