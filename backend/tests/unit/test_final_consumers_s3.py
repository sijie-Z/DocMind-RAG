"""阶段 3：权威 final_answer 的直接消费方契约测试。"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.acp.server import AcpServer
from app.agent.events import AgentEvent, FinalAnswerAccumulator
from app.entrypoints import headless
from app.rpc.dispatcher import RPCError, RPCService
from app.services.webhook_service import (
    WebhookDeliveryError,
    WebhookDeliveryTerminalError,
    WebhookService,
)
from tests.unit.test_final_recovery_s3 import _valid_ready_payload


def _events(*events):
    async def _stream():
        for event in events:
            yield event

    return _stream()


def _successful_stream(task_id="task-authoritative"):
    return _events(
        AgentEvent(type="chunk", content="STREAM-PART", task_id=task_id),
        AgentEvent(
            type="done",
            content="",
            final_answer="AUTHORITATIVE",
            task_state="completed",
            task_id=task_id,
        ),
    )


def test_accumulator_priority_and_done_serialization():
    snapshot = _valid_ready_payload()
    answers = FinalAnswerAccumulator(snapshot)
    answers.add(AgentEvent(type="chunk", content="STREAM"))
    assert answers.answer == "READY"
    answers.add(AgentEvent(type="done", final_answer="TERMINAL", task_state="completed"))
    assert answers.answer == "TERMINAL"

    legacy = FinalAnswerAccumulator({"partial_output": "legacy-"})
    legacy.add(AgentEvent(type="chunk", content="chunk"))
    assert legacy.answer == "legacy-chunk"
    assert "final_answer" not in AgentEvent(type="done", task_state="failed").to_sse_dict()
    assert (
        AgentEvent(type="done", final_answer="OK", task_state="completed").to_sse_dict()[
            "final_answer"
        ]
        == "OK"
    )


def test_unverified_error_and_failed_answers_are_blocked():
    invalid = FinalAnswerAccumulator(
        {
            "answer_contract": "single_final/v1",
            "final_phase": "ready",
            "final_answer": "STALE-READY",
        }
    )
    assert invalid.answer == ""
    assert invalid.blocked is True
    assert invalid.can_save is False

    valid = FinalAnswerAccumulator(_valid_ready_payload())
    assert valid.answer == "READY"
    valid.add(AgentEvent(type="error", content="bad checkpoint"))
    valid.add(AgentEvent(type="done", final_answer="MUST-NOT-RECOVER", task_state="completed"))
    assert valid.answer == ""
    assert valid.terminal_answer == ""
    assert valid.can_save is False

    failed = FinalAnswerAccumulator()
    failed.add(AgentEvent(type="chunk", content="partial"))
    failed.add(AgentEvent(type="done", task_state="failed", final_answer="STALE"))
    assert failed.answer == ""
    assert failed.can_save is False


@pytest.mark.asyncio
@pytest.mark.parametrize("resume", [False, True], ids=["initial", "ready-resume"])
async def test_acp_done_answer_overrides_chunks_without_duplicate(resume):
    server = object.__new__(AcpServer)
    state = SimpleNamespace(
        cancel_event=asyncio.Event(),
        active_task_id="",
        update_seq=0,
        drain=lambda: 0,
        enqueue_update=lambda _update: None,
    )
    answer: list[str] = []

    stop = await server._drive(state, _successful_stream(), answer)

    assert stop == "end_turn"
    assert answer == ["AUTHORITATIVE"]
    assert state.active_task_id == "task-authoritative"


@pytest.mark.asyncio
async def test_rpc_stream_uses_authoritative_answer_and_task_id():
    service = object.__new__(RPCService)
    service._session_seq = {}
    service._cancelled_sessions = set()
    service.dispatcher = None
    answers = FinalAnswerAccumulator()

    last_type, answer, handled = await service._stream_events(
        "session-1", _successful_stream(), answers
    )

    assert last_type == "done"
    assert answer == "AUTHORITATIVE"
    assert answers.task_id == "task-authoritative"
    assert handled is False


@pytest.mark.asyncio
async def test_rpc_failed_done_is_error_and_has_no_answer():
    service = object.__new__(RPCService)
    service._session_seq = {}
    service._cancelled_sessions = set()
    service.dispatcher = None
    answers = FinalAnswerAccumulator()
    last_type, answer, _ = await service._stream_events(
        "session-1",
        _events(AgentEvent(type="done", task_state="failed", final_answer="STALE")),
        answers,
    )
    assert last_type == "error"
    assert answer == ""


@pytest.mark.asyncio
async def test_acp_failed_done_is_rejected():
    server = object.__new__(AcpServer)
    state = SimpleNamespace(
        cancel_event=asyncio.Event(),
        active_task_id="",
        update_seq=0,
        drain=lambda: 0,
        enqueue_update=lambda _update: None,
    )
    with pytest.raises(RPCError):
        await server._drive(
            state,
            _events(AgentEvent(type="done", task_state="failed", final_answer="STALE")),
            [],
        )


def test_headless_and_webhook_use_same_authoritative_answer(monkeypatch, capsys):
    from app.agent import service as service_module

    monkeypatch.setattr(
        service_module.agent_service, "chat", lambda **_kwargs: _successful_stream()
    )
    args = SimpleNamespace(query="q", user_id=1, org_id=1, dry_run=False, json=True)
    assert headless.run(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["answer"] == "AUTHORITATIVE"

    service = object.__new__(WebhookService)
    answers = FinalAnswerAccumulator()
    agent = SimpleNamespace(chat=lambda **_kwargs: _successful_stream())
    rule = SimpleNamespace(org_id=1, created_by=1)
    answer = asyncio.run(service._run_chat(agent, rule, "session-1", "q", [], answers=answers))
    assert answer == "AUTHORITATIVE"
    assert answers.task_id == "task-authoritative"


def test_headless_failed_done_is_nonzero_and_not_completed(monkeypatch, capsys):
    from app.agent import service as service_module

    monkeypatch.setattr(
        service_module.agent_service,
        "chat",
        lambda **_kwargs: _events(AgentEvent(type="done", task_state="failed", final_answer="")),
    )
    args = SimpleNamespace(query="q", user_id=1, org_id=1, dry_run=False, json=True)
    assert headless.run(args) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "failed"
    assert payload["answer"] == ""


@pytest.mark.parametrize(
    ("state", "error_type"),
    [
        ("failed", WebhookDeliveryError),
        ("cancelled", WebhookDeliveryTerminalError),
        ("waiting_approval", WebhookDeliveryTerminalError),
    ],
)
def test_webhook_failed_cancelled_or_paused_done_is_not_success(state, error_type):
    service = object.__new__(WebhookService)
    answers = FinalAnswerAccumulator()
    agent = SimpleNamespace(
        chat=lambda **_kwargs: _events(AgentEvent(type="done", task_state=state, final_answer=""))
    )
    rule = SimpleNamespace(org_id=1, created_by=1)
    with pytest.raises(error_type):
        asyncio.run(service._run_chat(agent, rule, "session-1", "q", [], answers=answers))
    assert answers.answer == ""


@pytest.mark.asyncio
async def test_agent_api_and_benchmark_use_authoritative_answer(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake")
    monkeypatch.setenv("DEEPSEEK_API_URL", "https://fake.invalid")
    import app.agent_api as agent_api
    import benchmark.run as benchmark_run

    class _Loop:
        stream = _successful_stream

        def __init__(self, *_args, **_kwargs):
            pass

        async def run(self, _query):
            async for event in type(self).stream():
                yield event

    class _Tracer:
        def __init__(self, **_kwargs):
            pass

        def start_phase(self, *_args):
            return None

        def end_phase(self, *_args):
            return None

        def log_step(self, **_kwargs):
            return None

        def log_tool_call(self, *_args):
            return None

        def add_dag_edge(self, *_args):
            return None

        def log_error(self, *_args):
            return None

        def finalize(self, **_kwargs):
            return SimpleNamespace(timing={}, dag_edges=[])

    class _Runner:
        def __init__(self, **_kwargs):
            pass

        async def run(self, coroutine):
            await coroutine()
            return SimpleNamespace(success=True, error_type=None, error=None, recoverable=False)

    monkeypatch.setattr(agent_api, "PERAgentLoop", _Loop)
    monkeypatch.setattr(agent_api, "AgentTracer", _Tracer)
    monkeypatch.setattr(agent_api, "StepRunner", _Runner)
    monkeypatch.setattr(agent_api, "AsyncOpenAI", lambda **_kwargs: object())
    monkeypatch.setattr(agent_api, "_resolve_demo_org_id", lambda: 1)
    monkeypatch.setattr(agent_api, "_trace_exporter", SimpleNamespace(save=lambda _trace: None))
    monkeypatch.setenv("DEEPSEEK_API_KEY", "fake")
    monkeypatch.setenv("AGENT_DEMO_USER_ID", "1")
    result = await agent_api.run_agent("q")
    assert result["answer"] == "AUTHORITATIVE"

    monkeypatch.setattr("app.agent.loop.PERAgentLoop", _Loop)
    monkeypatch.setattr("app.agent.service.agent_service", SimpleNamespace(client=object()))
    monkeypatch.setattr(benchmark_run, "_create_question_trace", lambda *_args: ("", ""))
    benchmark_result = await benchmark_run._run_agent(
        {"id": "q1", "question": "q", "difficulty": "easy", "subcategory": "test"},
        org_id=1,
        enable_experience=False,
    )
    assert benchmark_result.answer == "AUTHORITATIVE"

    _Loop.stream = staticmethod(
        lambda: _events(AgentEvent(type="done", task_state="failed", final_answer="STALE"))
    )
    failed_api = await agent_api.run_agent("q")
    assert failed_api["answer"] == ""
    assert failed_api["errors"][0]["type"] == "agent_failure"
    failed_benchmark = await benchmark_run._run_agent(
        {"id": "q2", "question": "q", "difficulty": "easy", "subcategory": "test"},
        org_id=1,
        enable_experience=False,
    )
    assert failed_benchmark.answer == "Error: Agent execution failed"
    assert failed_benchmark.tool_failures == 1
