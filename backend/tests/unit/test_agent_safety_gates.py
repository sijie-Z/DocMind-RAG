"""P0/P1: missing approval channels and unsafe tool retries must fail closed."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from app.agent import executor as executor_module
from app.agent import registry as registry_module
from app.agent.config import AgentConfig
from app.agent.executor import Executor
from app.agent.planner import Plan, PlanStep
from app.agent.registry import ToolRegistry, ToolResult


class UnusedMemory:
    """Memory is disabled in these tests, so no bridge methods are needed."""


class ScriptedLLM:
    """Return scripted execution responses and a local text-only synthesis response."""

    def __init__(self, script):
        self.script = list(script)
        self.main_calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, *, tools=None, **_kwargs):
        if tools is None:
            return _response(content="local synthesis")
        outcome = self.script[min(self.main_calls, len(self.script) - 1)]
        self.main_calls += 1
        if isinstance(outcome, BaseException):
            raise outcome
        if isinstance(outcome, str):
            return _response(content=outcome)
        calls = [
            SimpleNamespace(
                id=call_id,
                function=SimpleNamespace(name=name, arguments=json.dumps(args)),
            )
            for name, args, call_id in outcome
        ]
        return _response(tool_calls=calls)


def _response(*, content="", tool_calls=None):
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)],
        usage=None,
        model="fake-local",
    )


def _config(**overrides):
    values = {
        "enable_tools": True,
        "enable_planning": False,
        "enable_reflection": False,
        "enable_memory": False,
        "enable_skills": False,
        "enable_experience": False,
        "enable_thinking": False,
        "disabled_tools": [],
        "max_retries_per_step": 1,
        "approval_mode": "never",
    }
    values.update(overrides)
    return AgentConfig(**values)


def _step_and_plan(tool_name):
    step = PlanStep(id="s1", description="agent safety probe", tool_hint=tool_name)
    step.status = "running"
    return step, Plan(id="p1", goal="agent safety", steps=[step])


async def _run_once(executor, tool_name):
    step, plan = _step_and_plan(tool_name)
    return [
        event
        async for event in executor._execute_step_once(
            step=step,
            plan=plan,
            history=None,
            organization_id=1,
            user_id=7,
            enable_thinking=False,
            ctx=None,
        )
    ]


async def _run_with_retry(executor, tool_name):
    step, plan = _step_and_plan(tool_name)
    events = [
        event
        async for event in executor._execute_step_with_retry(
            step=step,
            plan=plan,
            history=None,
            organization_id=1,
            user_id=7,
            enable_thinking=False,
            ctx=None,
        )
    ]
    return events, step


def _retry_events(events):
    return [event for event in events if event.type == "thinking" and "重试步骤" in event.content]


def _register_counting_tool(
    registry,
    name,
    *,
    outcomes=("ok",),
    semantic=None,
    risk_level="",
):
    state = {"handler_calls": 0, "side_effects": 0}

    async def handler():
        state["handler_calls"] += 1
        state["side_effects"] += 1
        outcome = outcomes[min(state["handler_calls"] - 1, len(outcomes) - 1)]
        if outcome == "timeout":
            return ToolResult.fail(code="timeout", message="response timed out")
        return ToolResult.ok(data=f"{name}-ok")

    entry = registry.register(
        name=name,
        description="local counting probe",
        parameters={"type": "object", "properties": {}},
        handler=handler,
        semantic=semantic,
        risk_level=risk_level,
    )
    return state, entry


@pytest.fixture
def local_registry(monkeypatch):
    registry = ToolRegistry()
    monkeypatch.setattr(executor_module, "tool_registry", registry)
    # approval.py imports the registry lazily for canonical risk and terminal tags.
    monkeypatch.setattr(registry_module, "tool_registry", registry)
    monkeypatch.setattr(executor_module, "compute_backoff", lambda *_args: 0.0)
    return registry


@pytest.mark.asyncio
@pytest.mark.parametrize(("mode", "risk_level"), [("always", "low"), ("risky", "high")])
async def test_required_approval_without_channel_is_unavailable_and_never_dispatches(
    local_registry, mode, risk_level
):
    tool_name = f"approval_{mode}_{risk_level}"
    state, _ = _register_counting_tool(local_registry, tool_name, risk_level=risk_level)
    llm = ScriptedLLM([[(tool_name, {}, "approval-call")]])
    executor = Executor(llm, _config(approval_mode=mode), UnusedMemory())
    executor.approval_check = None

    events = await _run_once(executor, tool_name)

    errors = [event for event in events if event.type == "tool_error"]
    assert errors and errors[0].error_type == "approval_unavailable"
    assert state == {"handler_calls": 0, "side_effects": 0}
    assert not any(event.type == "approval_required" for event in events)
    assert executor.approval_decisions == [], "unavailable must not become a fake user denial"


@pytest.mark.asyncio
async def test_never_mode_without_channel_still_executes(local_registry):
    tool_name = "approval_never_high"
    state, _ = _register_counting_tool(local_registry, tool_name, risk_level="high")
    llm = ScriptedLLM([[(tool_name, {}, "never-call")]])
    executor = Executor(llm, _config(approval_mode="never"), UnusedMemory())
    executor.approval_check = None

    events = await _run_once(executor, tool_name)

    assert state["handler_calls"] == 1
    assert any(event.type == "tool_result" for event in events)
    assert not any(
        event.type == "tool_error"
        and event.error_type in {"approval_unavailable", "approval_denied"}
        for event in events
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("semantic", "expected_calls", "expected_retries"),
    [
        ({"type": "mutating", "idempotent": False, "retry_safe": False}, 1, 0),
        ({"type": "read_only", "idempotent": True, "retry_safe": False}, 1, 0),
        ({"type": "mutating", "idempotent": False, "retry_safe": True}, 2, 1),
        ({"type": "mutating", "idempotent": False}, 1, 0),
    ],
)
async def test_dispatched_tool_retry_metadata_gates_step_replay(
    local_registry, semantic, expected_calls, expected_retries
):
    tool_name = "retry_metadata_probe"
    state, entry = _register_counting_tool(
        local_registry,
        tool_name,
        outcomes=("timeout", "ok"),
        semantic=semantic,
    )
    call = (tool_name, {}, "retry-call")
    llm = ScriptedLLM([[call], [call]])
    executor = Executor(llm, _config(), UnusedMemory())

    events, _step = await _run_with_retry(executor, tool_name)

    assert state["handler_calls"] == expected_calls
    assert state["side_effects"] == expected_calls
    assert len(_retry_events(events)) == expected_retries
    assert llm.main_calls == expected_calls
    assert entry.semantic is semantic


@pytest.mark.asyncio
async def test_unsafe_sibling_blocks_replay_when_another_tool_fails(local_registry):
    unsafe_name = "unsafe_sibling"
    safe_name = "safe_timeout_sibling"
    unsafe_state, _ = _register_counting_tool(
        local_registry,
        unsafe_name,
        semantic={"type": "mutating", "idempotent": False, "retry_safe": False},
    )
    safe_state, _ = _register_counting_tool(
        local_registry,
        safe_name,
        outcomes=("timeout", "ok"),
        semantic={"type": "read_only", "idempotent": True, "retry_safe": True},
    )
    calls = [(unsafe_name, {}, "unsafe-call"), (safe_name, {}, "safe-call")]
    llm = ScriptedLLM([calls, calls])
    executor = Executor(llm, _config(), UnusedMemory())

    events, _step = await _run_with_retry(executor, unsafe_name)

    assert unsafe_state == {"handler_calls": 1, "side_effects": 1}
    assert safe_state == {"handler_calls": 1, "side_effects": 1}
    assert _retry_events(events) == []
    assert llm.main_calls == 1


@pytest.mark.asyncio
async def test_llm_failure_before_any_tool_dispatch_still_retries(local_registry):
    tool_name = "unused_safe_tool"
    state, _ = _register_counting_tool(
        local_registry,
        tool_name,
        semantic={"type": "read_only", "idempotent": True, "retry_safe": True},
    )
    llm = ScriptedLLM([TimeoutError("provider timeout"), "recovered without tools"])
    executor = Executor(llm, _config(), UnusedMemory())

    events, step = await _run_with_retry(executor, tool_name)

    assert llm.main_calls == 2
    assert state == {"handler_calls": 0, "side_effects": 0}
    assert len(_retry_events(events)) == 1
    assert step.status != "failed"
    assert any(
        event.type == "chunk" and event.content == "recovered without tools" for event in events
    )
