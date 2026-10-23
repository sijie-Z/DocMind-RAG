"""终端 HTTP + WebSocket 端点（issue #477 §5.4 / §5.5）。

HTTP：创建/列出/关闭会话、签发一次性 WS 票据。
WS：``/api/v1/terminal/ws/{terminal_id}``，握手用 ``Sec-WebSocket-Protocol:
auth.<ticket>``（票据绝不进 URL），失败一律 accept 后 ``close(1008,
"policy_violation")``，不发送任何终端数据。

WS 消息协议（JSON 文本帧；二进制帧按 UTF-8 解码）：

* client → server：``input`` / ``resize`` / ``ping``
* server → client：``output`` / ``exit`` / ``truncated`` / ``error`` / ``pong``

重连：连接建立后先补发 ring buffer（最近 TERMINAL_RING_BUFFER_BYTES 字节），
再进入实时流；客户端断开不杀容器，由空闲超时/显式 kill 回收。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, WebSocket
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.websockets import WebSocketDisconnect

from app.agent.terminal.manager import (
    TerminalBusy,
    TerminalError,
    TerminalExited,
    TerminalSession,
)
from app.core.database import get_db
from app.core.security import get_current_user, require_organization
from app.core.ws_auth import close_ws, extract_ws_subprotocol
from app.exceptions import (
    ConflictError,
    NotFoundError,
    ServiceUnavailableError,
    ValidationError,
)
from app.models.user import User
from app.services.terminal_service import terminal_service

logger = logging.getLogger(__name__)

router = APIRouter()

WS_POLICY_VIOLATION_CLOSE_CODE = 1008
WS_POLICY_VIOLATION_REASON = "policy_violation"

MAX_COLS = 1000
MAX_ROWS = 500

# 服务端 → 客户端发送单帧的超时（慢客户端不应无限拖住 pump 任务）。
_SEND_TIMEOUT_SECONDS = 10.0


class TerminalCreateRequest(BaseModel):
    session_id: str | None = None
    cols: int = 80
    rows: int = 24


# ── HTTP 错误映射 ────────────────────────────────────────────────────────────


def _raise_http(exc: TerminalError) -> None:
    """把 TerminalError 映射到统一 HTTP 契约（issue #477 §5.4）。"""
    code = getattr(exc, "code", "terminal_error")
    message = str(exc) or code
    if code in ("terminal_disabled", "docker_unavailable", "redis_unavailable"):
        raise ServiceUnavailableError(message, detail=code) from exc
    if code in ("terminal_quota_exceeded", "terminal_owned_elsewhere"):
        raise ConflictError(message, detail=code) from exc
    if code == "terminal_not_found":
        raise NotFoundError(message, detail=code) from exc
    if code in ("terminal_input_too_large",):
        raise ValidationError(message, detail=code) from exc
    # 其余（busy/exited/参数越界）按服务端状态冲突处理会误导；未知错误直接
    # 上抛，由全局异常处理器记录（500），绝不给客户端伪造成功。
    raise exc


def _require_enabled() -> None:
    if not terminal_service.enabled():
        raise ServiceUnavailableError("终端功能未启用", detail="terminal_disabled")


# ── HTTP 端点 ────────────────────────────────────────────────────────────────


@router.post("/sessions")
async def create_terminal_session(
    payload: TerminalCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """创建终端会话并签发首张 WS 票据（HTTP 创建 → WS 连接两段式）。"""
    if not 1 <= payload.cols <= MAX_COLS or not 1 <= payload.rows <= MAX_ROWS:
        raise ValidationError(
            f"cols 需在 1-{MAX_COLS}、rows 需在 1-{MAX_ROWS} 范围内",
            detail={"cols": [1, MAX_COLS], "rows": [1, MAX_ROWS]},
        )
    org_id = await require_organization(current_user, db)

    session: TerminalSession | None = None
    try:
        session = await terminal_service.create(
            user_id=current_user.id,
            org_id=org_id,
            session_id=payload.session_id,
            cols=payload.cols,
            rows=payload.rows,
        )
        ticket = await terminal_service.issue_ticket(
            user_id=current_user.id, terminal_id=session.terminal_id
        )
    except TerminalError as exc:
        if session is not None:
            # 票据失败时回收会话，绝不留半成品资源。
            with contextlib.suppress(Exception):
                await terminal_service.kill(session.terminal_id, user_id=current_user.id)
        _raise_http(exc)

    logger.info(
        "terminal session created via API id=%s user=%s org=%s",
        session.terminal_id,
        current_user.id,
        org_id,
    )
    return {
        "success": True,
        "data": {
            "terminal_id": session.terminal_id,
            "ws_path": f"/api/v1/terminal/ws/{session.terminal_id}",
            "ticket": ticket,
            "expires_in": terminal_service.ticket_ttl,
        },
    }


@router.get("/sessions")
async def list_terminal_sessions(
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """列出当前用户在**本 worker** 上的终端（多 worker 部署按 worker 分区）。"""
    _require_enabled()
    sessions = terminal_service.manager.list_for_user(current_user.id)
    return {
        "success": True,
        "data": {"sessions": [session.to_public_dict() for session in sessions]},
    }


@router.delete("/sessions/{terminal_id}")
async def kill_terminal_session(
    terminal_id: str,
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """显式关闭终端；不存在/非本人 → 404，归属其他 worker → 409。"""
    _require_enabled()
    try:
        await terminal_service.resolve_local(terminal_id, user_id=current_user.id)
    except TerminalError as exc:
        _raise_http(exc)
    killed = await terminal_service.kill(terminal_id, user_id=current_user.id)
    if not killed:
        raise NotFoundError("终端不存在或无权访问", detail="terminal_not_found")
    return {"success": True, "data": {"killed": True}}


@router.post("/sessions/{terminal_id}/ticket")
async def create_terminal_ticket(
    terminal_id: str,
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """签发新的 WS 票据（旧票据立即失效）。"""
    _require_enabled()
    try:
        await terminal_service.resolve_local(terminal_id, user_id=current_user.id)
        ticket = await terminal_service.issue_ticket(
            user_id=current_user.id, terminal_id=terminal_id
        )
    except TerminalError as exc:
        _raise_http(exc)
    return {
        "success": True,
        "data": {
            "terminal_id": terminal_id,
            "ws_path": f"/api/v1/terminal/ws/{terminal_id}",
            "ticket": ticket,
            "expires_in": terminal_service.ticket_ttl,
        },
    }


# ── WebSocket ────────────────────────────────────────────────────────────────


def _extract_ticket(websocket: WebSocket) -> str | None:
    """只接受 ``Sec-WebSocket-Protocol: auth.<ticket>``（禁止 URL 查询参数）。

    与 ``ws_auth.extract_ws_token`` 的解析思路一致，但不做 JWT 校验、也**不**
    回退到 ``?token=``——终端票据是短期一次性凭证，落 URL 会进访问日志。
    """
    protocols = websocket.headers.get("sec-websocket-protocol", "")
    for proto in protocols.split(","):
        proto = proto.strip()
        if proto.startswith("auth."):
            return proto[5:].strip().replace('"', "").replace("'", "")
    return None


def _origin_allowed(websocket: WebSocket) -> bool:
    """Origin 必须存在且在 ``ALLOWED_ORIGINS``（CSRF/DNS-rebinding 防线）。"""
    from app.core.config import settings

    origin = websocket.headers.get("origin")
    if not origin:
        return False
    return origin in (settings.ALLOWED_ORIGINS or [])


async def _reject_ws(websocket: WebSocket, subprotocol: str | None) -> None:
    """握手失败契约：accept（尽量回显子协议）后 close(1008)，不发终端数据。"""
    try:
        await websocket.accept(subprotocol=subprotocol)
    except Exception:
        logger.debug("terminal ws accept during rejection failed", exc_info=True)
    await close_ws(websocket, WS_POLICY_VIOLATION_CLOSE_CODE, WS_POLICY_VIOLATION_REASON)


async def _send(websocket: WebSocket, payload: dict[str, Any], lock: asyncio.Lock) -> None:
    text = json.dumps(payload, ensure_ascii=False)
    async with lock:
        await asyncio.wait_for(websocket.send_text(text), timeout=_SEND_TIMEOUT_SECONDS)


@router.websocket("/ws/{terminal_id}")
async def terminal_websocket(websocket: WebSocket, terminal_id: str) -> None:
    """终端 WS：一次性票据 → Origin → 归属 → 补发缓冲 → 双向流。"""
    subprotocol = extract_ws_subprotocol(websocket)

    # 先做无副作用的 Origin 校验（不消耗票据），再做 GETDEL —— 顺序与 issue
    # 列表一致，但避免「Origin 非法也烧掉票据」。
    if not _origin_allowed(websocket):
        await _reject_ws(websocket, subprotocol)
        return
    ticket = _extract_ticket(websocket)
    if not ticket:
        await _reject_ws(websocket, subprotocol)
        return
    try:
        consumed = await terminal_service.consume_ticket(ticket)
    except TerminalError:
        # Redis 不可用 / 票据校验失败：fail-closed，不泄露原因。
        await _reject_ws(websocket, subprotocol)
        return
    if consumed is None:
        await _reject_ws(websocket, subprotocol)
        return
    user_id, ticket_terminal_id = consumed
    if ticket_terminal_id != terminal_id:
        await _reject_ws(websocket, subprotocol)
        return
    try:
        session = await terminal_service.resolve_local(terminal_id, user_id=user_id)
    except TerminalError:
        await _reject_ws(websocket, subprotocol)
        return
    if session.status == "closed":
        await _reject_ws(websocket, subprotocol)
        return

    await websocket.accept(subprotocol=subprotocol)
    try:
        await _serve_session(websocket, session)
    except WebSocketDisconnect:
        pass
    finally:
        # 客户端断开不杀容器（空闲/寿命回收负责）；仅关闭连接收尾。
        with contextlib.suppress(Exception):
            await websocket.close()


async def _serve_session(websocket: WebSocket, session: TerminalSession) -> None:
    send_lock = asyncio.Lock()

    # 重连补发：先给 ring buffer 全量，再进入实时流。
    buffered, cursor, dropped = await session.snapshot()
    if dropped:
        await _send(
            websocket,
            {"type": "truncated", "dropped_bytes": dropped},
            send_lock,
        )
    if buffered:
        await _send(
            websocket,
            {"type": "output", "data": buffered.decode("utf-8", errors="replace")},
            send_lock,
        )

    receive_task = asyncio.create_task(
        _receive_loop(websocket, session, send_lock),
        name=f"terminal-ws-recv-{session.terminal_id}",
    )
    pump_task = asyncio.create_task(
        _pump_loop(websocket, session, cursor, send_lock),
        name=f"terminal-ws-pump-{session.terminal_id}",
    )
    done: set[asyncio.Task[Any]] = set()
    try:
        done, _pending = await asyncio.wait(
            {receive_task, pump_task}, return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        for task in (receive_task, pump_task):
            if not task.done():
                task.cancel()
        for task in (receive_task, pump_task):
            with contextlib.suppress(BaseException):
                await task
    # pump 正常结束（exit）→ 用正常关闭码收尾；客户端断开则无需再 close。
    if pump_task in done and not pump_task.cancelled() and pump_task.exception() is None:
        await close_ws(websocket, 1000, "terminal_exited")


async def _receive_loop(
    websocket: WebSocket, session: TerminalSession, send_lock: asyncio.Lock
) -> None:
    from app.core.config import settings

    max_input = int(settings.TERMINAL_MAX_INPUT_BYTES)
    while True:
        message = await websocket.receive()
        if message.get("type") == "websocket.disconnect":
            return
        text = message.get("text")
        raw = message.get("bytes")
        if raw is not None:
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                await _send(
                    websocket,
                    {
                        "type": "error",
                        "code": "invalid_utf8",
                        "message": "二进制帧必须是 UTF-8 文本",
                    },
                    send_lock,
                )
                continue
        if text is None:
            continue
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            await _send(
                websocket,
                {"type": "error", "code": "invalid_json", "message": "消息必须是 JSON"},
                send_lock,
            )
            continue
        if not isinstance(parsed, dict):
            await _send(
                websocket,
                {"type": "error", "code": "invalid_message", "message": "消息必须是 JSON 对象"},
                send_lock,
            )
            continue
        message_type = parsed.get("type")
        if message_type == "input":
            data = parsed.get("data")
            if not isinstance(data, str):
                await _send(
                    websocket,
                    {
                        "type": "error",
                        "code": "invalid_message",
                        "message": "input.data 必须是字符串",
                    },
                    send_lock,
                )
                continue
            encoded = data.encode("utf-8")
            if len(encoded) > max_input:
                await _send(
                    websocket,
                    {
                        "type": "error",
                        "code": "terminal_input_too_large",
                        "message": f"单次输入超过上限 {max_input} 字节",
                    },
                    send_lock,
                )
                continue
            try:
                await terminal_service.manager.send(
                    session.terminal_id, user_id=session.user_id, data=encoded
                )
            except TerminalBusy:
                await _send(
                    websocket,
                    {
                        "type": "error",
                        "code": TerminalBusy.code,
                        "message": "终端已有写入者，请稍后重试",
                    },
                    send_lock,
                )
            except TerminalExited:
                await _send(
                    websocket,
                    {
                        "type": "error",
                        "code": TerminalExited.code,
                        "message": "终端进程已退出",
                    },
                    send_lock,
                )
            except TerminalError as exc:
                await _send(
                    websocket,
                    {
                        "type": "error",
                        "code": getattr(exc, "code", "terminal_error"),
                        "message": str(exc),
                    },
                    send_lock,
                )
        elif message_type == "resize":
            cols, rows = parsed.get("cols"), parsed.get("rows")
            if (
                not isinstance(cols, int)
                or isinstance(cols, bool)
                or not isinstance(rows, int)
                or isinstance(rows, bool)
                or not 1 <= cols <= MAX_COLS
                or not 1 <= rows <= MAX_ROWS
            ):
                await _send(
                    websocket,
                    {
                        "type": "error",
                        "code": "invalid_message",
                        "message": f"resize 需要 1-{MAX_COLS} 的 cols 与 1-{MAX_ROWS} 的 rows",
                    },
                    send_lock,
                )
                continue
            try:
                await terminal_service.manager.resize(
                    session.terminal_id, user_id=session.user_id, cols=cols, rows=rows
                )
            except TerminalError as exc:
                await _send(
                    websocket,
                    {
                        "type": "error",
                        "code": getattr(exc, "code", "terminal_error"),
                        "message": str(exc),
                    },
                    send_lock,
                )
        elif message_type == "ping":
            await _send(websocket, {"type": "pong"}, send_lock)
        else:
            await _send(
                websocket,
                {
                    "type": "error",
                    "code": "unknown_message_type",
                    "message": f"未知消息类型: {message_type!r}",
                },
                send_lock,
            )


async def _pump_loop(
    websocket: WebSocket,
    session: TerminalSession,
    cursor: int,
    send_lock: asyncio.Lock,
) -> None:
    """把 ring buffer 的新增输出推给客户端；进程退出时发 exit 后返回。"""
    while True:
        data, cursor, lost = await session.snapshot_since(cursor)
        if lost:
            await _send(
                websocket,
                {"type": "truncated", "dropped_bytes": lost},
                send_lock,
            )
        if data:
            await _send(
                websocket,
                {"type": "output", "data": data.decode("utf-8", errors="replace")},
                send_lock,
            )
            continue
        if session.is_terminal and cursor >= session.total_written:
            code = session.exit_code if session.exit_code is not None else -1
            await _send(websocket, {"type": "exit", "code": code}, send_lock)
            return
        await session.wait_for_since(cursor, timeout=1.0)
