"""最终综合阶段 3：final-phase 恢复与权威答案判别测试。

只使用 fake provider、内存执行替身和 SQLite；不启动真实模型或服务。
"""

from __future__ import annotations

import copy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agent import checkpoint
from app.agent.events import AgentEvent
from app.agent.exec_context import ExecutionContext
from app.agent.executor import Executor
from app.agent.planner import Plan, PlanStep
from app.agent.service import AgentService
from app.core.config import ai as ai_config
from app.models.agent_task import TaskStatus
from tests.unit import test_final_synthesis_s2 as h


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    monkeypatch.setattr(ai_config, "unified_final_enabled", lambda: True, raising=False)
    monkeypatch.setattr(ExecutionContext, "save", AsyncMock())
    monkeypatch.setattr(h.ExecutionContext, "save", AsyncMock())


def _capture_snapshots(monkeypatch) -> list[dict]:
    snapshots: list[dict] = []

    async def _save(**kwargs):
        envelope = checkpoint.build_envelope(
            ctx=kwargs["ctx"],
            plan=kwargs["plan"],
            trigger=kwargs["trigger"],
            partial_output=kwargs.get("partial_output", ""),
            history=kwargs.get("history"),
            config=kwargs.get("config"),
            run_state=kwargs.get("run_state"),
            session_id=kwargs.get("session_id", ""),
            final_state=kwargs.get("final_state"),
        )
        snapshots.append(envelope)
        return True

    monkeypatch.setattr(checkpoint, "save_checkpoint", _save)
    return snapshots


def _loop(provider, *, reflection=True, statuses=("completed",)):
    loop = h._loop(provider, statuses=statuses, reflection=reflection)
    loop.config.enable_checkpoint = True
    return loop


def _done(events) -> AgentEvent:
    return next(event for event in events if event.type == "done")


def _phase(snapshots, phase: str) -> dict:
    return next(snapshot for snapshot in snapshots if snapshot.get("final_phase") == phase)


def _assert_no_execution(loop, provider):
    assert loop._planner.calls == 0
    assert loop._executor.executed_plans == []
    assert provider.calls == []


@pytest.mark.asyncio
async def test_ready_resume_replays_authoritative_answer_with_zero_llm_and_tools(monkeypatch):
    snapshots = _capture_snapshots(monkeypatch)
    first = _loop(h._FakeProvider("READY-AUTHORITATIVE"))
    first_events = [event async for event in first.run("q", task_id="task-ready")]
    ready = _phase(snapshots, "ready")

    assert _done(first_events).final_answer == "READY-AUTHORITATIVE"
    assert ready["answer_contract"] == "single_final/v1"
    assert ready["final_answer"] == "READY-AUTHORITATIVE"
    assert ready["partial_output"] == ""

    provider = h._FakeProvider("MUST-NOT-BE-CALLED")
    resumed = _loop(provider)
    resumed._final_synthesizer = SimpleNamespace(
        synthesize=AsyncMock(side_effect=AssertionError("ready 不得重跑最终综合"))
    )
    resumed._reviewer = SimpleNamespace(
        review_final=AsyncMock(side_effect=AssertionError("ready 不得重跑 Reviewer"))
    )
    events = [event async for event in resumed.run("q", task_id="task-ready", resume=ready)]

    _assert_no_execution(resumed, provider)
    assert resumed._final_synthesizer.synthesize.await_count == 0
    assert resumed._reviewer.review_final.await_count == 0
    assert [event.content for event in events if event.type == "chunk"] == ["READY-AUTHORITATIVE"]
    assert _done(events).final_answer == "READY-AUTHORITATIVE"


@pytest.mark.asyncio
async def test_review_pending_reuses_draft_and_skips_final_synthesis(monkeypatch):
    snapshots = _capture_snapshots(monkeypatch)
    first = _loop(h._FakeProvider("DRAFT-ONLY"))
    [event async for event in first.run("q", task_id="task-review")]
    pending = _phase(snapshots, "review_pending")

    provider = h._FakeProvider("MUST-NOT-SYNTHESIZE")
    resumed = _loop(provider)
    resumed._final_synthesizer = SimpleNamespace(
        synthesize=AsyncMock(side_effect=AssertionError("review_pending 不得重跑综合"))
    )
    events = [event async for event in resumed.run("q", task_id="task-review", resume=pending)]

    _assert_no_execution(resumed, provider)
    assert resumed._final_synthesizer.synthesize.await_count == 0
    assert len(resumed._reviewer.calls) == 1
    assert [event.content for event in events if event.type == "chunk"] == ["DRAFT-ONLY"]
    assert _done(events).final_answer == "DRAFT-ONLY"


@pytest.mark.asyncio
async def test_synthesis_pending_retries_only_final_llm_without_tool_replay(monkeypatch):
    snapshots = _capture_snapshots(monkeypatch)
    first = _loop(h._FakeProvider("FIRST-DRAFT"))
    [event async for event in first.run("q", task_id="task-synthesis")]
    pending = _phase(snapshots, "synthesis_pending")

    provider = h._FakeProvider("RESUMED-FINAL")
    resumed = _loop(provider)
    events = [event async for event in resumed.run("q", task_id="task-synthesis", resume=pending)]

    assert resumed._planner.calls == 0
    assert resumed._executor.executed_plans == []
    assert len(provider.calls) == 1, "draft 落盘前崩溃允许重试最终 LLM，但不宣称 exactly-once"
    assert [event.content for event in events if event.type == "chunk"] == ["RESUMED-FINAL"]
    assert _done(events).final_answer == "RESUMED-FINAL"


@pytest.mark.asyncio
@pytest.mark.parametrize("provider_error", [RuntimeError("boom"), None], ids=["error", "empty"])
async def test_failed_final_provider_has_no_answer_and_resume_does_not_replay_tools(
    monkeypatch,
    provider_error,
):
    snapshots = _capture_snapshots(monkeypatch)
    provider = h._FakeProvider(
        content="" if provider_error is None else "unused", error=provider_error
    )
    first = _loop(provider, reflection=False)
    events = [event async for event in first.run("q", task_id="task-provider")]

    assert first.run_failed is True
    assert not any(event.type == "chunk" for event in events)
    assert all(not getattr(_done(events), "final_answer", "") for _ in [0])
    assert not any(snapshot.get("final_phase") == "ready" for snapshot in snapshots)

    resumed_provider = h._FakeProvider("RECOVERED")
    resumed = _loop(resumed_provider, reflection=False)
    pending = _phase(snapshots, "synthesis_pending")
    resumed_events = [
        event async for event in resumed.run("q", task_id="task-provider", resume=pending)
    ]

    assert resumed._planner.calls == 0
    assert resumed._executor.executed_plans == []
    assert [event.content for event in resumed_events if event.type == "chunk"] == ["RECOVERED"]
    assert _done(resumed_events).final_answer == "RECOVERED"


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["missing_plan", "non_terminal", "bad_evidence", "bad_hash"])
async def test_final_checkpoint_damage_fails_closed_without_planning_or_tools(
    monkeypatch,
    damage,
):
    snapshots = _capture_snapshots(monkeypatch)
    first = _loop(h._FakeProvider("VALID"))
    [event async for event in first.run("q", task_id="task-damage")]
    payload = copy.deepcopy(_phase(snapshots, "synthesis_pending"))
    if damage == "missing_plan":
        payload["plan"] = None
    elif damage == "non_terminal":
        payload["plan"]["steps"][0]["status"] = "pending"
    elif damage == "bad_evidence":
        payload["plan"]["steps"][0]["evidence"] = "broken"
    else:
        payload["final_input_hash"] = "mismatch"

    provider = h._FakeProvider("MUST-NOT-RUN")
    resumed = _loop(provider, reflection=False)
    events = [event async for event in resumed.run("q", task_id="task-damage", resume=payload)]

    _assert_no_execution(resumed, provider)
    assert resumed.run_failed is True
    assert any(event.type == "error" for event in events)
    assert not any(event.type == "chunk" for event in events)
    assert not any(event.type == "done" for event in events)


@pytest.mark.asyncio
async def test_required_final_checkpoint_failure_prevents_ready_and_chunk(monkeypatch):
    async def _fail_ready(**kwargs):
        return kwargs["trigger"] != "final_ready"

    monkeypatch.setattr(checkpoint, "save_checkpoint", _fail_ready)
    loop = _loop(h._FakeProvider("NO-READY"))
    events = [event async for event in loop.run("q", task_id="task-write-failure")]

    assert loop.run_failed is True
    assert not any(event.type == "chunk" for event in events)
    assert not any(getattr(event, "final_answer", "") for event in events if event.type == "done")
    assert loop.checkpoint_live["partial_output"] == ""
    assert loop.checkpoint_live["final_state"]["phase"] != "ready"


@pytest.mark.asyncio
async def test_cancel_during_ready_write_rolls_back_delivery_before_chunk(monkeypatch):
    snapshots = _capture_snapshots(monkeypatch)
    original_save = checkpoint.save_checkpoint
    cancel_requested = False

    async def cancel_check() -> bool:
        return cancel_requested

    async def save_and_cancel(**kwargs):
        nonlocal cancel_requested
        saved = await original_save(**kwargs)
        if kwargs["trigger"] == "final_ready":
            cancel_requested = True
        return saved

    monkeypatch.setattr(checkpoint, "save_checkpoint", save_and_cancel)
    loop = _loop(h._FakeProvider("NOT-DELIVERABLE"), reflection=False)
    loop.cancel_check = cancel_check
    events = [event async for event in loop.run("q", task_id="task-ready-cancel")]

    assert loop.run_cancelled is True
    assert not any(event.type == "chunk" for event in events)
    assert not any(event.type == "done" for event in events)
    assert loop.final_state_live["phase"] == "review_pending"
    assert snapshots[-1]["final_phase"] == "review_pending"
    assert "final_answer" not in snapshots[-1]


@pytest.mark.asyncio
async def test_legacy_v1_completed_steps_do_not_replay_and_partial_is_not_final(monkeypatch):
    _capture_snapshots(monkeypatch)
    step = PlanStep(
        id="legacy",
        description="旧步骤",
        status="completed",
        result="旧局部结果",
        evidence_schema="legacy",
    )
    plan = Plan(id="legacy-plan", goal="g", steps=[step], completed_steps=1)
    payload = checkpoint.build_envelope(
        ctx=ExecutionContext(query="q", task_id="task-legacy"),
        plan=plan,
        trigger="after_execute_turn",
        partial_output="LEGACY-PARTIAL-MUST-NOT-LEAK",
        config=h._config(),
    )
    provider = h._FakeProvider("NEW-FINAL")
    resumed = _loop(provider, reflection=False)
    resumed._executor = Executor(provider, resumed.config, resumed.memory)
    resumed._executor._execute_single_step = AsyncMock(
        side_effect=AssertionError("legacy completed 步骤不得重跑")
    )
    events = [event async for event in resumed.run("q", task_id="task-legacy", resume=payload)]

    assert resumed._planner.calls == 0
    assert resumed._executor._execute_single_step.await_count == 0
    assert [event.content for event in events if event.type == "chunk"] == ["NEW-FINAL"]
    assert "LEGACY-PARTIAL-MUST-NOT-LEAK" not in _done(events).final_answer
    report = next(event.content for event in events if event.type == "run_report")
    assert "legacy: True" in report


@pytest.mark.asyncio
async def test_ready_prepare_resume_is_read_only_and_does_not_claim(monkeypatch):
    task = SimpleNamespace(
        id="task-ready",
        status=TaskStatus.COMPLETED,
        is_terminal=True,
        session_id="session-1",
    )
    payload = {"answer_contract": "single_final/v1", "final_phase": "ready"}
    monkeypatch.setattr(
        checkpoint,
        "load_for_resume",
        AsyncMock(return_value=(task, payload)),
    )
    begin = AsyncMock()
    monkeypatch.setattr(checkpoint, "begin_resume", begin)
    service = AgentService()

    resumed_task, resumed_payload = await service.prepare_resume("task-ready", user_id=1)

    assert resumed_task is task
    assert resumed_payload is payload
    assert begin.await_count == 0
