"""持久终端 Agent 工具（issue #477 §5.6）——与 DSH 六个终端工具对齐。

``terminal_open/send/read/signal/close/list``，全部 ``risk_level="high"``：
``TERMINAL_REQUIRE_APPROVAL=true``（默认）时，executor 对 ``terminal_*`` 强制走
人工审批门（即使 ``approval_mode="never"``）；没有审批通道时 fail-closed，
工具不会执行（见 ``app/agent/executor.py::_approval_precheck``）。

错误码契约（issue 明确要求）：

* 功能未启用 → ``terminal_disabled``
* 终端不存在 / 非本人 → ``terminal_not_found``
* 等待输出超时 → ``terminal_timeout``

其余错误映射到既有 ToolError 码（``sandbox_unavailable`` / ``rate_limited`` /
``payload_too_large``）或终端专用码（``terminal_busy`` / ``terminal_exited``）。
"""

from __future__ import annotations

import logging
from typing import Any

from app.agent.registry import ToolResult, register_tool

logger = logging.getLogger(__name__)

# terminal_read 等待新输出的时长（秒）。测试可 monkeypatch 调小。
_READ_WAIT_SECONDS = 5.0

_TERMINAL_SEMANTIC = {"type": "mutating", "idempotent": False}


def _tool_fail(exc: Exception) -> ToolResult:
    """TerminalError → ToolResult（统一错误码映射）。"""
    from app.agent.terminal.manager import (
        TerminalBusy,
        TerminalDisabled,
        TerminalDockerUnavailable,
        TerminalError,
        TerminalExited,
        TerminalInputTooLarge,
        TerminalNotFound,
        TerminalOwnedElsewhere,
        TerminalQuotaExceeded,
        TerminalRedisUnavailable,
        TerminalTimeout,
    )

    if isinstance(exc, TerminalDisabled):
        code = "terminal_disabled"
    elif isinstance(exc, (TerminalNotFound, TerminalOwnedElsewhere)):
        code = "terminal_not_found"
    elif isinstance(exc, TerminalTimeout):
        code = "terminal_timeout"
    elif isinstance(exc, TerminalQuotaExceeded):
        code = "rate_limited"
    elif isinstance(exc, TerminalInputTooLarge):
        code = "payload_too_large"
    elif isinstance(exc, TerminalBusy):
        code = "terminal_busy"
    elif isinstance(exc, TerminalExited):
        code = "terminal_exited"
    elif isinstance(exc, (TerminalDockerUnavailable, TerminalRedisUnavailable)):
        code = "sandbox_unavailable"
    elif isinstance(exc, TerminalError):
        code = getattr(exc, "code", "terminal_error")
    else:  # pragma: no cover — 非终端异常不在本映射内
        code = "unknown"
    return ToolResult.fail(code=code, message=str(exc), raw=exc)


def _disabled_result() -> ToolResult | None:
    """所有工具的统一开关门（禁用时任何操作都返回 terminal_disabled）。"""
    from app.services.terminal_service import terminal_service

    if not terminal_service.enabled():
        return ToolResult.fail(
            code="terminal_disabled",
            message="持久终端功能未启用（TERMINAL_ENABLED=false）",
        )
    return None


@register_tool(
    name="terminal_open",
    description=(
        "Open a persistent terminal session in an isolated Docker container. "
        "Returns terminal_id and ws_path for the web terminal panel. "
        "The shell survives across tool calls until closed or timed out."
    ),
    parameters={
        "type": "object",
        "properties": {
            "session_id": {
                "type": "string",
                "description": "Optional chat session id to associate with the terminal",
            },
            "cols": {
                "type": "integer",
                "description": "Terminal width in columns (1-1000, default 80)",
                "default": 80,
            },
            "rows": {
                "type": "integer",
                "description": "Terminal height in rows (1-500, default 24)",
                "default": 24,
            },
        },
    },
    tags=["terminal", "shell"],
    requires_auth=True,
    risk_level="high",
    category="terminal",
    semantic=_TERMINAL_SEMANTIC,
)
async def terminal_open(
    session_id: str | None = None,
    cols: int = 80,
    rows: int = 24,
    *,
    user_id: int = 0,
    organization_id: int | None = None,
    **_: Any,
) -> ToolResult:
    from app.agent.terminal.manager import TerminalError
    from app.services.terminal_service import terminal_service

    disabled = _disabled_result()
    if disabled is not None:
        return disabled
    try:
        session = await terminal_service.create(
            user_id=user_id,
            org_id=organization_id,
            session_id=session_id,
            cols=int(cols),
            rows=int(rows),
        )
    except TerminalError as exc:
        return _tool_fail(exc)
    return ToolResult.ok(
        {
            "terminal_id": session.terminal_id,
            "ws_path": f"/api/v1/terminal/ws/{session.terminal_id}",
        }
    )


@register_tool(
    name="terminal_send",
    description=(
        "Send input (keystrokes / command text) to a persistent terminal. "
        "Returns the number of bytes written. Writing is non-blocking: the "
        "command keeps running in the background; read output with terminal_read."
    ),
    parameters={
        "type": "object",
        "properties": {
            "terminal_id": {"type": "string", "description": "Terminal id"},
            "data": {
                "type": "string",
                "description": "Text to write to the PTY (append \\n to run a command)",
            },
            "run_in_background": {
                "type": "boolean",
                "description": "Accepted for DSH parity; PTY writes are always async",
                "default": False,
            },
        },
        "required": ["terminal_id", "data"],
    },
    tags=["terminal", "shell"],
    requires_auth=True,
    risk_level="high",
    category="terminal",
    semantic=_TERMINAL_SEMANTIC,
)
async def terminal_send(
    terminal_id: str,
    data: str,
    run_in_background: bool = False,
    *,
    user_id: int = 0,
    **_: Any,
) -> ToolResult:
    from app.agent.terminal.manager import TerminalError
    from app.services.terminal_service import terminal_service

    disabled = _disabled_result()
    if disabled is not None:
        return disabled
    if not isinstance(data, str):
        return ToolResult.fail(code="validation_error", message="data 必须是字符串")
    try:
        written = await terminal_service.manager.send(
            terminal_id, user_id=user_id, data=data.encode("utf-8")
        )
    except TerminalError as exc:
        return _tool_fail(exc)
    # 命令摘要审计（仅回车提交的行；审计失败不影响写入结果）。
    await terminal_service.audit_completed_lines(
        user_id=user_id, terminal_id=terminal_id, data=data
    )
    # run_in_background 仅作契约兼容：PTY 写入天然异步，不会等待命令结束。
    _ = run_in_background
    return ToolResult.ok({"bytes_written": written})


@register_tool(
    name="terminal_read",
    description=(
        "Read pending output from a persistent terminal. Waits a few seconds "
        "for new output when the buffer is empty. Returns {data, truncated, "
        "dropped_total}; truncated=true means more output remains beyond max_bytes."
    ),
    parameters={
        "type": "object",
        "properties": {
            "terminal_id": {"type": "string", "description": "Terminal id"},
            "max_bytes": {
                "type": "integer",
                "description": "Maximum bytes to return (capped by server config)",
            },
        },
        "required": ["terminal_id"],
    },
    tags=["terminal", "shell"],
    requires_auth=True,
    risk_level="high",
    category="terminal",
    semantic={"type": "read_only", "idempotent": True},
)
async def terminal_read(
    terminal_id: str,
    max_bytes: int | None = None,
    *,
    user_id: int = 0,
    **_: Any,
) -> ToolResult:
    from app.agent.terminal.manager import TerminalError
    from app.services.terminal_service import terminal_service

    disabled = _disabled_result()
    if disabled is not None:
        return disabled
    try:
        session = terminal_service.manager.get(terminal_id, user_id=user_id)
        if session is None:
            from app.agent.terminal.manager import TerminalNotFound

            raise TerminalNotFound("终端不存在或无权访问")
        data, truncated, dropped_total = await terminal_service.manager.read(
            terminal_id,
            user_id=user_id,
            max_bytes=max_bytes,
            timeout=_READ_WAIT_SECONDS,
        )
    except TerminalError as exc:
        return _tool_fail(exc)
    if not data and session.status == "running":
        return ToolResult.fail(
            code="terminal_timeout",
            message=f"{_READ_WAIT_SECONDS:.0f} 秒内没有新的终端输出",
        )
    return ToolResult.ok(
        {
            "data": data.decode("utf-8", errors="replace"),
            "truncated": truncated,
            "dropped_total": dropped_total,
        }
    )


@register_tool(
    name="terminal_signal",
    description=(
        "Send a signal to the persistent terminal process: INT (Ctrl-C), "
        "TERM or KILL. Use KILL when the shell is unresponsive."
    ),
    parameters={
        "type": "object",
        "properties": {
            "terminal_id": {"type": "string", "description": "Terminal id"},
            "signal": {
                "type": "string",
                "enum": ["INT", "TERM", "KILL"],
                "description": "Signal to send",
            },
        },
        "required": ["terminal_id", "signal"],
    },
    tags=["terminal", "shell"],
    requires_auth=True,
    risk_level="high",
    category="terminal",
    semantic={"type": "mutating", "idempotent": True},
)
async def terminal_signal(
    terminal_id: str, signal: str, *, user_id: int = 0, **_: Any
) -> ToolResult:
    from app.agent.terminal.manager import TerminalError, normalize_signal
    from app.services.terminal_service import terminal_service

    disabled = _disabled_result()
    if disabled is not None:
        return disabled
    if normalize_signal(signal) not in ("INT", "TERM", "KILL"):
        return ToolResult.fail(code="validation_error", message="signal 必须是 INT/TERM/KILL 之一")
    try:
        await terminal_service.manager.signal(terminal_id, user_id=user_id, sig=signal)
    except TerminalError as exc:
        return _tool_fail(exc)
    return ToolResult.ok({"ok": True})


@register_tool(
    name="terminal_close",
    description="Close a persistent terminal session and remove its container.",
    parameters={
        "type": "object",
        "properties": {
            "terminal_id": {"type": "string", "description": "Terminal id"},
        },
        "required": ["terminal_id"],
    },
    tags=["terminal", "shell"],
    requires_auth=True,
    risk_level="high",
    category="terminal",
    semantic=_TERMINAL_SEMANTIC,
)
async def terminal_close(terminal_id: str, *, user_id: int = 0, **_: Any) -> ToolResult:
    from app.agent.terminal.manager import TerminalError
    from app.services.terminal_service import terminal_service

    disabled = _disabled_result()
    if disabled is not None:
        return disabled
    try:
        killed = await terminal_service.kill(terminal_id, user_id=user_id)
    except TerminalError as exc:
        return _tool_fail(exc)
    if not killed:
        return ToolResult.fail(code="terminal_not_found", message="终端不存在或无权访问")
    return ToolResult.ok({"ok": True})


@register_tool(
    name="terminal_list",
    description="List your active persistent terminal sessions.",
    parameters={"type": "object", "properties": {}},
    tags=["terminal", "shell"],
    requires_auth=True,
    risk_level="high",
    category="terminal",
    semantic={"type": "read_only", "idempotent": True},
)
async def terminal_list(*, user_id: int = 0, **_: Any) -> ToolResult:
    from app.services.terminal_service import terminal_service

    disabled = _disabled_result()
    if disabled is not None:
        return disabled
    sessions = terminal_service.manager.list_for_user(user_id)
    return ToolResult.ok({"terminals": [session.to_public_dict() for session in sessions]})
