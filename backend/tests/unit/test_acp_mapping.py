"""ACP 映射与协议骨架的单元测试（issue #481 §5.3 / §5.4 / §8）。

- fixture（``tests/fixtures/acp_v1_schema.json``）锁定 v1 方法/通知的 wire 形状；
- 事件映射逐类型审计：``EventType`` 的每个取值都必须有明确归宿（映射或
  仅日志），不允许静默 KeyError；
- 审批响应解析 / 取消丢弃 / 串行 sender / 并发 prompt -32009 /
  ``session/load`` -32601。
"""

import json
from pathlib import Path
from typing import get_args

import jsonschema
import pytest

from app.acp.mapping import (
    decision_from_permission_result,
    event_to_update,
    merge_plan_entry,
    permission_request_params,
    plan_entry_from_event,
    sanitized_error_content,
)
from app.acp.server import (
    AcpServer,
    extract_prompt_text,
)
from app.acp.session import AcpSessionState
from app.agent.events import AgentEvent, EventType
from app.rpc.dispatcher import Dispatcher, RPCError

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "acp_v1_schema.json"


@pytest.fixture(scope="module")
def schema() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _validate(instance, schema_fragment) -> None:
    jsonschema.validate(instance=instance, schema=schema_fragment)


def _server() -> AcpServer:
    # db_factory 传占位：本文件只测不触 DB 的路径。
    return AcpServer(organization_id=1, user_id=7, db_factory=lambda: None)


# ── initialize / fixture ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_initialize_matches_fixture(schema):
    server = _server()
    result = await server.initialize({"protocolVersion": 1, "clientCapabilities": {}})
    _validate(result, schema["methods"]["initialize"]["response"])
    assert result["protocolVersion"] == 1
    assert result["agentCapabilities"]["loadSession"] is False
    assert result["agentCapabilities"]["dshExtensions"] is True
    for capability in ("image", "audio", "embeddedContext"):
        assert result["agentCapabilities"]["promptCapabilities"][capability] is False
    assert result["agentInfo"]["name"] == "DocMind"


@pytest.mark.asyncio
async def test_authenticate_local_trust(schema):
    server = _server()
    result = await server.authenticate({"method": "none"})
    _validate(result, schema["methods"]["authenticate"]["response"])
    with pytest.raises(RPCError) as exc:
        await server.authenticate({"method": 123})
    assert exc.value.code == -32602


def test_fixture_metadata_locks_v1(schema):
    assert schema["schemaVersion"] == "v1"
    assert schema["source"] == "https://agentclientprotocol.com/protocol/v1/schema"
    assert schema["fetchedAt"] == "2026-10-22"
    assert schema["methods"]["session/load"] == {"supported": False, "errorCode": -32601}
    assert schema["methods"]["session/cancel"]["notification"] is True


# ── 事件映射：逐类型审计 ────────────────────────────────────────────────────


async def _noop_send(_message: dict) -> None:
    return None


def test_every_event_type_has_defined_mapping():
    """EventType 的每个取值都必须明确：→ update 或仅日志（None），绝不 KeyError。"""
    mapped = {
        "chunk",
        "thinking",
        "tool_call",
        "tool_result",
        "tool_error",
        "plan_step",
        "plan_complete",
        "command_result",
        "command_error",
    }
    terminal = {
        "done",
        "run_cancelled",
        "error",
        "approval_required",
        "plan_approval_required",
    }
    log_only = set(get_args(EventType)) - mapped - terminal
    seen_log_only = set()
    for event_type in get_args(EventType):
        event = AgentEvent(type=event_type, content="x", tool_name="t", tool_call_id="call-1")
        update = event_to_update(event, tool_call_id="call-1", plan_entries=None)
        if event_type in mapped:
            # plan_* 无 entries 时按"无内容可发"返回 None（首个 plan_step 之前）。
            if event_type in ("plan_step", "plan_complete"):
                assert update is None
            else:
                assert update is not None, event_type
        else:
            assert update is None, event_type
            if event_type not in terminal:
                seen_log_only.add(event_type)
    assert seen_log_only == log_only


def test_chunk_and_thinking_map_to_text_chunks(schema):
    chunk = event_to_update(AgentEvent(type="chunk", content="你好"))
    _validate(chunk, schema["sessionUpdates"]["agent_message_chunk"])
    assert chunk["content"] == {"type": "text", "text": "你好"}

    thought = event_to_update(AgentEvent(type="thinking", content="想想"))
    _validate(thought, schema["sessionUpdates"]["agent_thought_chunk"])
    assert thought["content"] == {"type": "text", "text": "想想"}


def test_tool_events_map_with_stable_tool_call_id(schema):
    call = event_to_update(
        AgentEvent(type="tool_call", tool_name="code_execution", tool_call_id="call-1"),
        tool_call_id="call-1",
    )
    _validate(call, schema["sessionUpdates"]["tool_call"])
    assert call["toolCallId"] == "call-1" and call["title"] == "code_execution"

    result = event_to_update(
        AgentEvent(
            type="tool_result", tool_name="code_execution", tool_call_id="call-1", content="42"
        ),
        tool_call_id="call-1",
    )
    _validate(result, schema["sessionUpdates"]["tool_call_update"])
    assert result["status"] == "completed"
    assert result["content"][0]["content"]["text"] == "42"

    error = event_to_update(
        AgentEvent(
            type="tool_error", tool_name="code_execution", tool_call_id="call-1", content="boom"
        ),
        tool_call_id="call-1",
    )
    _validate(error, schema["sessionUpdates"]["tool_call_update"])
    assert error["status"] == "failed"


def test_tool_result_content_truncated():
    from app.acp.mapping import TOOL_RESULT_CONTENT_MAX

    result = event_to_update(
        AgentEvent(
            type="tool_result", tool_call_id="c", content="x" * (TOOL_RESULT_CONTENT_MAX + 50)
        ),
        tool_call_id="c",
    )
    text = result["content"][0]["content"]["text"]
    assert len(text) < TOOL_RESULT_CONTENT_MAX + 50
    assert text.endswith("（已截断）")


@pytest.mark.asyncio
async def test_plan_entries_accumulate(schema):
    server = _server()
    state = AcpSessionState("s-1", send=_noop_send)
    try:
        first = server._event_update(
            state,
            AgentEvent(
                type="plan_step",
                content="检索资料",
                plan_id="p1",
                plan_step_status="pending",
                risk_level="low",
            ),
        )
        _validate(first, schema["sessionUpdates"]["plan"])
        assert first["entries"] == [{"content": "检索资料", "priority": "low", "status": "pending"}]
        second = server._event_update(
            state,
            AgentEvent(
                type="plan_step",
                content="写答案",
                plan_id="p1",
                plan_step_status="pending",
                risk_level="high",
            ),
        )
        assert [entry["content"] for entry in second["entries"]] == ["检索资料", "写答案"]
        complete = server._event_update(
            state,
            AgentEvent(type="plan_complete", content="计划已生成", plan_id="p1"),
        )
        _validate(complete, schema["sessionUpdates"]["plan"])
        assert len(complete["entries"]) == 2
    finally:
        await state.close()


@pytest.mark.asyncio
async def test_merge_plan_entry_updates_instead_of_duplicating():
    entries: list[dict] = []
    entry = plan_entry_from_event(
        AgentEvent(type="plan_step", content="步骤", plan_step_status="pending"), index=1
    )
    merge_plan_entry(entries, entry)
    merge_plan_entry(
        entries,
        plan_entry_from_event(
            AgentEvent(type="plan_step", content="步骤", plan_step_status="completed"), index=1
        ),
    )
    assert len(entries) == 1
    assert entries[0]["status"] == "completed"


def test_command_events_surface_as_message_chunks(schema):
    ok = event_to_update(AgentEvent(type="command_result", content="/status 结果"))
    _validate(ok, schema["sessionUpdates"]["agent_message_chunk"])
    err = event_to_update(AgentEvent(type="command_error", content="未知命令"))
    _validate(err, schema["sessionUpdates"]["agent_message_chunk"])


def test_warning_and_diagnostics_are_log_only():
    for event_type in (
        "warning",
        "plan_start",
        "retry_hint",
        "reflection",
        "execution_step_result",
        "run_report",
        "execution_context",
        "steer_applied",
        "command_start",
    ):
        assert event_to_update(AgentEvent(type=event_type, content="x")) is None


def test_error_content_is_sanitized():
    event = AgentEvent(type="error", content="Traceback (most recent call last): secret sk-abc")
    text = sanitized_error_content(event)
    assert "Traceback" not in text
    assert "sk-abc" not in text


# ── 审批：wire 形状与响应解析 ───────────────────────────────────────────────


def test_permission_request_shape_matches_fixture(schema):
    params = permission_request_params("s-1", tool_call_id="call-1", tool_name="code_execution")
    _validate(params, schema["serverRequests"]["session/request_permission"]["request"])
    assert params["toolCall"] == {
        "toolCallId": "call-1",
        "title": "code_execution",
        "status": "pending",
    }
    assert [option["optionId"] for option in params["options"]] == ["allow_once", "reject_once"]
    assert {option["kind"] for option in params["options"]} == {"allow_once", "reject_once"}


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ({"outcome": {"outcome": "selected", "optionId": "allow_once"}}, "allow"),
        ({"outcome": {"outcome": "selected", "optionId": "reject_once"}}, "deny"),
        ({"outcome": {"outcome": "selected", "optionId": "allow_always"}}, "deny"),
        ({"outcome": {"outcome": "cancelled"}}, "cancelled"),
        ({"cancelled": True}, "cancelled"),
        ({"cancelled": True, "disconnected": True}, "cancelled"),
        ({}, "deny"),
        (None, "cancelled"),
        ("garbage", "cancelled"),
        ({"optionId": "allow_once"}, "allow"),
    ],
)
def test_decision_from_permission_result(result, expected):
    assert decision_from_permission_result(result) == expected


def test_permission_response_shapes_validate(schema):
    response_schema = schema["serverRequests"]["session/request_permission"]["response"]
    _validate({"outcome": {"outcome": "selected", "optionId": "allow_once"}}, response_schema)
    _validate({"outcome": {"outcome": "cancelled"}}, response_schema)


# ── 方法面 / 协议错误 ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_session_load_returns_method_not_found():
    server = _server()
    dispatcher = Dispatcher(server.method_table())
    response = await dispatcher.handle_message(
        {"jsonrpc": "2.0", "id": 1, "method": "session/load", "params": {"sessionId": "s"}}
    )
    assert response["error"]["code"] == -32601


@pytest.mark.asyncio
async def test_unknown_method_returns_method_not_found():
    server = _server()
    dispatcher = Dispatcher(server.method_table())
    response = await dispatcher.handle_message(
        {"jsonrpc": "2.0", "id": 2, "method": "session/fork", "params": {}}
    )
    assert response["error"]["code"] == -32601


@pytest.mark.asyncio
async def test_prompt_requires_text_parts():
    server = _server()
    dispatcher = Dispatcher(server.method_table())
    for bad_prompt in (None, "", [], [{"type": "image", "data": "x"}]):
        response = await dispatcher.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "session/prompt",
                "params": {"sessionId": "s", "prompt": bad_prompt},
            }
        )
        assert response["error"]["code"] == -32602


def test_extract_prompt_text_joins_text_parts():
    parts = [{"type": "text", "text": "你"}, {"type": "text", "text": "好"}]
    assert extract_prompt_text(parts) == "你\n好"
    assert extract_prompt_text([{"type": "text", "text": "   "}]) is None
    assert extract_prompt_text([{"type": "image", "data": "x"}]) is None
    assert extract_prompt_text("plain string") is None


def test_protocol_stdout_reconfigured_to_utf8():
    """Windows 中文 locale（cp936）下 emoji 帧必须不炸：协议 stdout 重配 UTF-8。

    变异点：不调用 ``_ensure_utf8_stdout`` → cp936 写 ✅ 抛 UnicodeEncodeError
    → 本用例红。
    """
    import io

    from app.acp.server import _ensure_utf8_stdout

    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="cp936")
    _ensure_utf8_stdout(stream)
    stream.write("✅ 你好")
    stream.flush()
    assert buffer.getvalue() == "✅ 你好".encode()


@pytest.mark.asyncio
async def test_run_wires_utf8_stdout_reconfigure():
    """``AcpServer.run`` 必须真的调用重配（变异点：删掉调用 → 本用例红）。"""

    class _CapturingStdout:
        def __init__(self) -> None:
            self.reconfigures: list[dict] = []

        def reconfigure(self, **kwargs) -> None:
            self.reconfigures.append(kwargs)

        def write(self, text: str) -> int:
            return len(text)

        def flush(self) -> None:
            return None

    class _EofStdin:
        def readline(self, _limit: int = -1) -> bytes:
            return b""

    stdout = _CapturingStdout()
    server = AcpServer(
        organization_id=1,
        user_id=7,
        db_factory=lambda: None,
        stdin=_EofStdin(),
        stdout=stdout,
        redirect_stdout=False,
        install_signal_handlers=False,
    )
    assert await server.run() == 0
    assert stdout.reconfigures and stdout.reconfigures[0]["encoding"] == "utf-8"


# ── 会话状态：并发 / 取消 / 串行 sender ────────────────────────────────────


@pytest.mark.asyncio
async def test_sender_serializes_updates_in_order():
    sent: list[dict] = []
    state = AcpSessionState("s-1", send=lambda message: _collect(sent, message))
    try:
        state.enqueue_update({"sessionUpdate": "agent_message_chunk", "n": 1})
        state.enqueue_update({"sessionUpdate": "agent_message_chunk", "n": 2})
        await state.barrier()
        assert [message["params"]["update"]["n"] for message in sent] == [1, 2]
        assert all(message["method"] == "session/update" for message in sent)
        assert all(message["params"]["sessionId"] == "s-1" for message in sent)
    finally:
        await state.close()


async def _collect(sink: list[dict], message: dict) -> None:
    sink.append(message)


@pytest.mark.asyncio
async def test_cancel_drops_queued_and_late_updates():
    sent: list[dict] = []

    async def _send(message: dict) -> None:
        sent.append(message)

    state = AcpSessionState("s-1", send=_send)
    try:
        state.enqueue_update({"sessionUpdate": "agent_message_chunk", "n": 1})
        dropped = await state.cancel()
        assert dropped == 1, "取消必须丢弃队列中未发送的更新"
        assert state.enqueue_update({"sessionUpdate": "agent_message_chunk", "n": 2}) is False
        await state.barrier()
        assert all(message["params"]["update"].get("n") != 2 for message in sent), (
            "取消后不得再发迟到 update"
        )
    finally:
        await state.close()


@pytest.mark.asyncio
async def test_concurrent_prompt_returns_conflict():
    server = _server()
    state = server.sessions.ensure("s-1", send=_noop_send)
    try:
        async with state.lock:
            with pytest.raises(RPCError) as exc:
                await server.session_prompt(
                    {"sessionId": "s-1", "prompt": [{"type": "text", "text": "hi"}]}
                )
            assert exc.value.code == -32009
    finally:
        server.sessions.pop("s-1")
        await state.close()


@pytest.mark.asyncio
async def test_prompt_unknown_session_returns_not_found():
    server = _server()
    with pytest.raises(RPCError) as exc:
        await server.session_prompt(
            {"sessionId": "missing", "prompt": [{"type": "text", "text": "hi"}]}
        )
    assert exc.value.code == -32004


@pytest.mark.asyncio
async def test_cancel_unknown_session_returns_not_found():
    server = _server()
    with pytest.raises(RPCError) as exc:
        await server.session_cancel({"sessionId": "missing"})
    assert exc.value.code == -32004
