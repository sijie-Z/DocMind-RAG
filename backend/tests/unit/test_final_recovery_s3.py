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
    monkeypatch.setattr(checkpoint, "cancel_status", AsyncMock(return_value=False))


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


def _valid_ready_payload() -> dict:
    step = PlanStep(id="s1", description="step", status="completed", result="local")
    plan = Plan(id="p-ready", goal="g", steps=[step], completed_steps=1)
    base_messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "q"},
    ]
    from app.agent.final_synthesis import FinalInputPacket

    packet = FinalInputPacket.from_plan("q", plan, base_messages=base_messages)
    final_state = {
        "phase": "ready",
        "input": {
            "query": "q",
            "context": "",
            "history": [],
            "direct": False,
            "base_messages": base_messages,
        },
        "input_hash": checkpoint.final_input_hash(packet),
        "draft": "READY",
        "messages": base_messages,
        "answer": "READY",
    }
    return checkpoint.build_envelope(
        ctx=ExecutionContext(query="q", task_id="task-ready"),
        plan=plan,
        trigger="final_ready",
        config=h._config(),
        final_state=final_state,
    )


def _packet(payload):
    from app.agent.final_synthesis import FinalInputPacket

    data = payload["final_input"]
    plan = Plan.from_checkpoint_dict(payload["plan"])
    return FinalInputPacket.from_plan(
        data["query"],
        plan,
        context=data.get("context", ""),
        history=data.get("history", []),
        direct=data.get("direct", False),
        base_messages=data.get("base_messages", []),
    )


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
@pytest.mark.parametrize(
    "damage",
    [
        "missing_plan",
        "non_terminal",
        "bad_evidence",
        "bad_hash",
        "local_synthesis",
        "bad_messages",
        "nested_index",
        "ctx_tokens",
        "plan_goal",
        "attempt_bool",
        "source_index_bool",
        "stored_length_bool",
        "message_role",
        "message_content",
    ],
)
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
    elif damage == "local_synthesis":
        payload["plan"]["steps"][0]["local_synthesis"]["content"] = 123
        payload["final_input_hash"] = checkpoint.final_input_hash(_packet(payload))
    elif damage == "bad_messages":
        payload = copy.deepcopy(_phase(snapshots, "review_pending"))
        payload["final_messages"] = ["not-a-message-object"]
    elif damage == "nested_index":
        payload["plan"]["steps"][0]["evidence"][0]["evidence"][0]["index"] = "not-an-int"
        payload["final_input_hash"] = checkpoint.final_input_hash(_packet(payload))
    elif damage == "ctx_tokens":
        payload["ctx"]["total_tokens"] = "not-an-int"
    elif damage == "plan_goal":
        payload["plan"]["goal"] = {"not": "a-string"}
        payload["final_input_hash"] = checkpoint.final_input_hash(_packet(payload))
    elif damage == "attempt_bool":
        payload["plan"]["steps"][0]["evidence"][0]["attempt"] = True
    elif damage == "source_index_bool":
        payload["plan"]["steps"][0]["evidence"][0]["sources"][0]["index"] = True
    elif damage == "stored_length_bool":
        payload["plan"]["steps"][0]["evidence"][0]["stored_length"] = True
    elif damage == "message_role":
        payload = copy.deepcopy(_phase(snapshots, "review_pending"))
        payload["final_messages"] = [{"role": "tool", "content": "x"}]
    elif damage == "message_content":
        payload = copy.deepcopy(_phase(snapshots, "review_pending"))
        payload["final_messages"] = [{"role": "user", "content": 1}]
    else:
        payload["final_input_hash"] = "mismatch"

    with pytest.raises(checkpoint.FinalCheckpointError):
        checkpoint.final_packet_from_snapshot(payload)

    provider = h._FakeProvider("MUST-NOT-RUN")
    resumed = _loop(provider, reflection=False)
    events = [event async for event in resumed.run("q", task_id="task-damage", resume=payload)]

    _assert_no_execution(resumed, provider)
    assert resumed._reviewer.calls == []
    assert resumed.run_failed is True
    assert any(event.type == "error" for event in events)
    assert not any(event.type == "chunk" for event in events)
    assert not any(event.type == "done" for event in events)


@pytest.mark.asyncio
async def test_required_final_checkpoint_failure_prevents_ready_and_chunk(monkeypatch):
    async def _fail_ready(**kwargs):
        return kwargs["trigger"] != "final_ready"

    monkeypatch.setattr(checkpoint, "save_checkpoint", _fail_ready)
    monkeypatch.setattr(
        checkpoint,
        "read_final_delivery_state",
        AsyncMock(return_value=False),
    )
    loop = _loop(h._FakeProvider("NO-READY"))
    events = [event async for event in loop.run("q", task_id="task-write-failure")]

    assert loop.run_failed is True
    assert not any(event.type == "chunk" for event in events)
    assert not any(getattr(event, "final_answer", "") for event in events if event.type == "done")
    assert loop.checkpoint_live["partial_output"] == ""
    assert loop.checkpoint_live["final_state"]["phase"] != "ready"


@pytest.mark.asyncio
async def test_cancel_during_ready_write_commit_wins(monkeypatch):
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

    assert loop.run_cancelled is False
    assert [event.content for event in events if event.type == "chunk"] == ["NOT-DELIVERABLE"]
    assert _done(events).final_answer == "NOT-DELIVERABLE"
    assert loop.final_state_live["phase"] == "ready"
    assert snapshots[-1]["final_phase"] == "ready"
    assert snapshots[-1]["final_answer"] == "NOT-DELIVERABLE"


@pytest.mark.asyncio
async def test_late_cancel_and_rollback_write_failure_keep_committed_answer(monkeypatch):
    snapshots = []
    cancel_requested = False

    async def cancel_check() -> bool:
        return cancel_requested

    async def flaky_save(**kwargs):
        nonlocal cancel_requested
        if kwargs["trigger"] == "cancelled":
            return False
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
        if kwargs["trigger"] == "final_ready":
            cancel_requested = True
        return True

    monkeypatch.setattr(checkpoint, "save_checkpoint", flaky_save)
    monkeypatch.setattr(checkpoint, "request_cancel", AsyncMock(return_value=True))
    loop = _loop(h._FakeProvider("STALE-READY"), reflection=False)
    loop.cancel_check = cancel_check
    initial_events = [event async for event in loop.run("q", task_id="task-stale-ready")]
    assert [event.content for event in initial_events if event.type == "chunk"] == ["STALE-READY"]
    assert _done(initial_events).final_answer == "STALE-READY"
    persisted = copy.deepcopy(snapshots[-1])
    assert persisted["final_phase"] == "ready"
    assert loop.run_cancelled is False
    assert loop.run_failure is None

    monkeypatch.setattr(checkpoint, "cancel_status", AsyncMock(return_value=True))
    provider = h._FakeProvider("MUST-NOT-REPLAY")
    resumed = _loop(provider, reflection=False)
    events = [
        event async for event in resumed.run("q", task_id="task-stale-ready", resume=persisted)
    ]

    assert [event.content for event in events if event.type == "chunk"] == ["STALE-READY"]
    assert _done(events).final_answer == "STALE-READY"
    assert resumed.run_failed is False
    assert resumed.run_cancelled is False
    assert resumed._planner.calls == 0
    assert resumed._executor.executed_plans == []
    assert provider.calls == []


@pytest.mark.asyncio
async def test_ready_readback_commit_and_service_error_use_completed_not_failed(monkeypatch):
    _capture_snapshots(monkeypatch)
    original_save = checkpoint.save_checkpoint

    async def fail_only_ready(**kwargs):
        if kwargs["trigger"] == "final_ready":
            return False
        return await original_save(**kwargs)

    monkeypatch.setattr(checkpoint, "save_checkpoint", fail_only_ready)
    monkeypatch.setattr(checkpoint, "read_final_delivery_state", AsyncMock(return_value=True))
    loop = _loop(h._FakeProvider("COMMITTED-ANSWER"), reflection=False)
    events = [event async for event in loop.run("q", task_id="task-readback-commit")]

    assert loop.paused_for_final_commit is False
    assert loop.run_failed is False
    assert [event.content for event in events if event.type == "chunk"] == ["COMMITTED-ANSWER"]
    assert _done(events).final_answer == "COMMITTED-ANSWER"

    mark_terminal = AsyncMock(return_value=True)
    save_from_live = AsyncMock()
    notify = AsyncMock()
    monkeypatch.setattr(checkpoint, "mark_terminal", mark_terminal)
    monkeypatch.setattr(checkpoint, "save_from_live", save_from_live)
    monkeypatch.setattr("app.agent.service.notify_task_terminal", notify)
    loop.paused_for_final_commit = True
    service = AgentService()
    await service._checkpoint_error("task-readback-commit", loop, RuntimeError("late error"))

    assert save_from_live.await_count == 0
    assert mark_terminal.await_args.args[1] == TaskStatus.COMPLETED
    assert notify.await_args.args[1] == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_final_ready_unknown_outcome_pauses_without_terminal_failure(monkeypatch):
    _capture_snapshots(monkeypatch)
    original_save = checkpoint.save_checkpoint

    async def fail_only_ready(**kwargs):
        if kwargs["trigger"] == "final_ready":
            return False
        return await original_save(**kwargs)

    monkeypatch.setattr(checkpoint, "save_checkpoint", fail_only_ready)
    monkeypatch.setattr(
        checkpoint,
        "read_final_delivery_state",
        AsyncMock(return_value=None),
    )
    loop = _loop(h._FakeProvider("UNKNOWN-COMMIT"), reflection=False)
    events = [event async for event in loop.run("q", task_id="task-unknown-commit")]

    assert loop.paused_for_final_commit is True
    assert loop.run_failed is False
    assert loop.run_cancelled is False
    assert not any(event.type == "chunk" for event in events)
    assert not any(event.type == "done" for event in events)
    assert any(event.type == "error" for event in events)


@pytest.mark.asyncio
async def test_ready_delivery_state_matrix_allows_only_running_and_completed(monkeypatch):
    payload = _valid_ready_payload()
    service = AgentService()
    allowed = (TaskStatus.RUNNING, TaskStatus.COMPLETED)
    rejected = (
        TaskStatus.FAILED,
        TaskStatus.PAUSED,
        TaskStatus.CANCELLED,
        TaskStatus.ABANDONED,
        TaskStatus.REJECTED,
    )
    begin = AsyncMock()
    monkeypatch.setattr(checkpoint, "begin_resume", begin)

    for status in allowed:
        task = SimpleNamespace(
            id="task-ready",
            status=status,
            is_terminal=status == TaskStatus.COMPLETED,
            session_id="session-ready",
        )
        monkeypatch.setattr(
            checkpoint,
            "load_for_resume",
            AsyncMock(return_value=(task, payload)),
        )
        resumed_task, _ = await service.prepare_resume("task-ready", user_id=1)
        assert resumed_task is task
    assert begin.await_count == 0

    for status in rejected:
        task = SimpleNamespace(
            id="task-ready",
            status=status,
            is_terminal=status != TaskStatus.FAILED,
            session_id="session-ready",
        )
        monkeypatch.setattr(
            checkpoint,
            "load_for_resume",
            AsyncMock(return_value=(task, payload)),
        )
        with pytest.raises(checkpoint.TaskNotResumableError):
            await service.prepare_resume("task-ready", user_id=1)


@pytest.mark.asyncio
async def test_public_cancel_and_rpc_cancel_wait_are_noop_after_ready(monkeypatch):
    from app.api.v1.endpoints.agent import cancel_agent_task
    from app.rpc.dispatcher import cancel_task_and_wait

    task = SimpleNamespace(
        id="task-ready",
        status=TaskStatus.RUNNING,
        is_terminal=False,
        session_id="session-ready",
    )
    monkeypatch.setattr(checkpoint, "load_task", AsyncMock(return_value=task))
    monkeypatch.setattr(
        checkpoint,
        "read_final_delivery_state",
        AsyncMock(return_value=True),
    )
    request_cancel = AsyncMock()
    mark_terminal = AsyncMock()
    monkeypatch.setattr(checkpoint, "request_cancel", request_cancel)
    monkeypatch.setattr(checkpoint, "mark_terminal", mark_terminal)
    monkeypatch.setattr(
        "app.api.v1.endpoints.agent.require_organization",
        AsyncMock(return_value=1),
    )

    response = await cancel_agent_task(
        "task-ready",
        current_user=SimpleNamespace(id=1, organization_id=1),
        db=SimpleNamespace(),
    )
    assert response["data"]["cancel_requested"] is False
    assert response["data"]["state"] == "running"
    assert await cancel_task_and_wait("task-ready", user_id=1, organization_id=1) is False
    assert request_cancel.await_count == 0
    assert mark_terminal.await_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("readback", [True, False, None])
@pytest.mark.parametrize("persisted", [True, False])
@pytest.mark.parametrize("paused", [True, False])
async def test_checkpoint_error_readback_matrix(readback, persisted, paused, monkeypatch):
    agent = SimpleNamespace(
        final_state_live={"phase": "ready"},
        checkpoint_live=None,
        paused_for_final_commit=paused,
        _final_commit_persisted=persisted,
        final_delivery_started=False,
    )
    monkeypatch.setattr(
        checkpoint,
        "read_final_delivery_state",
        AsyncMock(return_value=readback),
    )
    save_from_live = AsyncMock()
    mark_terminal = AsyncMock(return_value=True)
    notify = AsyncMock()
    monkeypatch.setattr(checkpoint, "save_from_live", save_from_live)
    monkeypatch.setattr(checkpoint, "mark_terminal", mark_terminal)
    monkeypatch.setattr("app.agent.service.notify_task_terminal", notify)
    service = AgentService()

    await service._checkpoint_error("task-matrix", agent, RuntimeError("late error"))

    if readback is True:
        assert save_from_live.await_count == 0
        assert mark_terminal.await_args.args[1] == TaskStatus.COMPLETED
        assert notify.await_args.args[1] == TaskStatus.COMPLETED
    elif readback is None and (persisted or paused):
        assert save_from_live.await_count == 0
        assert mark_terminal.await_count == 0
        assert notify.await_count == 0
    else:
        assert save_from_live.await_count == 1
        assert mark_terminal.await_args.args[1] == TaskStatus.FAILED
        assert notify.await_args.args[1] == TaskStatus.FAILED


@pytest.mark.asyncio
async def test_confirmed_commit_readback_unknown_after_chunk_completes_without_live_write(
    monkeypatch,
):
    agent = SimpleNamespace(
        final_state_live={"phase": "ready"},
        checkpoint_live=None,
        paused_for_final_commit=False,
        _final_commit_persisted=True,
        final_delivery_started=True,
    )
    monkeypatch.setattr(
        checkpoint,
        "read_final_delivery_state",
        AsyncMock(return_value=None),
    )
    save_from_live = AsyncMock()
    mark_terminal = AsyncMock(return_value=True)
    notify = AsyncMock()
    monkeypatch.setattr(checkpoint, "save_from_live", save_from_live)
    monkeypatch.setattr(checkpoint, "mark_terminal", mark_terminal)
    monkeypatch.setattr("app.agent.service.notify_task_terminal", notify)
    service = AgentService()

    await service._checkpoint_error("task-after-chunk", agent, RuntimeError("late error"))

    assert save_from_live.await_count == 0
    assert mark_terminal.await_args.args[1] == TaskStatus.COMPLETED
    assert notify.await_args.args[1] == TaskStatus.COMPLETED


@pytest.mark.asyncio
@pytest.mark.parametrize("ready_state", [True, False, None])
async def test_acp_cancel_cleanup_honors_durable_ready(ready_state, monkeypatch):
    from app.acp.server import AcpServer
    from app.rpc.dispatcher import RPCError

    task = SimpleNamespace(id="task-acp-ready", status=TaskStatus.RUNNING, is_terminal=False)
    server = object.__new__(AcpServer)
    server._load_task_safe = AsyncMock(return_value=task)
    server._mark_task_cancelled = AsyncMock(return_value=True)
    monkeypatch.setattr(
        checkpoint,
        "read_final_delivery_state",
        AsyncMock(return_value=ready_state),
    )
    state = SimpleNamespace(active_task_id=task.id, session_id="session-acp")

    if ready_state is None:
        with pytest.raises(RPCError):
            await server._cancel_lost_race(task.id)
        with pytest.raises(RPCError):
            await server._ensure_task_cancelled(state)
    else:
        lost_race = await server._cancel_lost_race(task.id)
        await server._ensure_task_cancelled(state)
        assert lost_race is ready_state
    assert server._mark_task_cancelled.await_count == (0 if ready_state is not False else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("ready", [False, True])
async def test_disconnect_legacy_agent_without_final_commit_attribute(ready, monkeypatch):
    agent = SimpleNamespace(checkpoint_live=None)
    save_from_live = AsyncMock()
    converge = AsyncMock(return_value=True)
    release = AsyncMock()
    monkeypatch.setattr(checkpoint, "save_from_live", save_from_live)
    monkeypatch.setattr(checkpoint, "converge_disconnected", converge)
    monkeypatch.setattr(checkpoint, "release_task", release)
    monkeypatch.setattr(
        checkpoint,
        "read_final_delivery_state",
        AsyncMock(return_value=ready),
    )
    service = AgentService()

    await service._checkpoint_disconnect("task-legacy-double", agent, release_lease=True)

    assert save_from_live.await_count == 1
    assert converge.await_count == 1
    assert release.await_count == 1


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
    payload = _valid_ready_payload()
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
