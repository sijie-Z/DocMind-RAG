"""Hardening of the issue #511 direct-read ACL fallbacks.

The production user path always constructs a server-side RunContext. Without
one, or with an unmarked one, a missing viewer must fail closed instead of
silently reading a private document at organization scope. The internal-system
marker is a server-side object registry created by a dedicated factory; LLM tool
arguments and request payloads cannot construct it.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import app.agent.service  # noqa: F401 — 注册内置工具，供 Executor 派发路径使用
from app.agent.agents.executor_agent import ExecutorAgent
from app.agent.config import AgentConfig
from app.agent.core_tools import (
    _ensure_document_readable,
    cross_document_analysis,
    extract_insights,
    get_document_info,
    summarize_document,
)
from app.agent.executor import Executor
from app.agent.memory_bridge import AgentMemoryBridge
from app.agent.orchestrator import Orchestrator
from app.agent.planner import Plan, PlanStep
from app.agent.registry import ToolResult, tool_registry
from app.agent.run_context import (
    RunContext,
    is_internal_system_task,
    run_context_for_internal_system_task,
)
from app.agent.tools.data_analysis import compare_documents

PRIVATE_ID = "private-doc-A"
ORG_ID = 7
DENIED_CODE = "not_found"


def _denied(result):
    assert result is not None
    assert result.success is False
    assert result.error.code == DENIED_CODE
    assert PRIVATE_ID in result.error.message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "run_context",
    [
        None,
        RunContext(user_id=0, organization_id=ORG_ID),
        RunContext(
            user_id=0,
            organization_id=ORG_ID,
            extra={"internal_system_task": True},
        ),
        SimpleNamespace(is_internal_system_task=True),
        RunContext(user_id=0, organization_id=ORG_ID, extra={}, config={}),
    ],
    ids=[
        "no-run-context",
        "unmarked-run-context",
        "forged-extra-marker",
        "forged-context-object",
        "forged-fields",
    ],
)
async def test_missing_viewer_never_gets_org_level_private_read(run_context):
    """Missing, unmarked, and impersonated contexts are all denied."""
    touched = []

    class _RecordingSession:
        async def __aenter__(self):
            touched.append(self)
            return self

        async def __aexit__(self, *_args):
            return False

    with patch("app.core.database.AsyncSessionLocal", _RecordingSession):
        result = await _ensure_document_readable(PRIVATE_ID, ORG_ID, 0, run_context=run_context)
    _denied(result)
    assert touched == [], "denied gate must not open a document DB session"


def test_constructor_cannot_mint_internal_marker():
    """No dataclass kwargs/JSON-shaped payload can produce a trusted marker."""
    with pytest.raises(TypeError):
        RunContext(
            user_id=0,
            organization_id=ORG_ID,
            _internal_system_task=object(),
        )

    ctx = RunContext(
        user_id=0,
        organization_id=ORG_ID,
        extra={"internal_system_task": True},
    )
    ctx.is_internal_system_task = True  # in-process attribute injection must not help
    assert not is_internal_system_task(ctx)


@pytest.mark.asyncio
async def test_internal_marker_requires_matching_organization():
    """A trusted marker for org A must not unlock org B's private document."""
    ctx = run_context_for_internal_system_task(organization_id=ORG_ID)
    result = await _ensure_document_readable(PRIVATE_ID, ORG_ID + 1, 0, run_context=ctx)
    _denied(result)


@pytest.mark.asyncio
async def test_explicit_internal_system_task_preserves_org_level_read():
    """A genuine background task keeps the historical organization-scope read."""
    ctx = run_context_for_internal_system_task(organization_id=ORG_ID)
    with patch(
        "app.core.database.AsyncSessionLocal",
        side_effect=AssertionError("internal task should not hit the ACL DB"),
    ):
        result = await _ensure_document_readable(PRIVATE_ID, ORG_ID, 0, run_context=ctx)
    assert result is None


def _recording_denied_gate(seen: list):
    async def fake_gate(document_id, organization_id, user_id, run_context=None):
        seen.append(run_context)
        return ToolResult.fail(
            code=DENIED_CODE,
            message=f"Document {document_id} not found or not accessible.",
        )

    return fake_gate


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler, args",
    [
        (summarize_document, {"document_id": PRIVATE_ID}),
        (extract_insights, {"document_id": PRIVATE_ID}),
        (get_document_info, {"document_id": PRIVATE_ID}),
        (cross_document_analysis, {"document_ids": [PRIVATE_ID, "doc-B"]}),
        (compare_documents, {"document_ids": [PRIVATE_ID, "doc-B"]}),
    ],
    ids=["summarize", "insights", "info", "cross", "compare"],
)
async def test_direct_read_tools_fail_closed_without_trusted_marker(handler, args):
    """A forged string marker must not unlock the private document body."""
    forged = RunContext(
        user_id=0,
        organization_id=ORG_ID,
        extra={"internal_system_task": True},
    )
    for ctx in (None, forged):
        seen = []
        with patch("app.agent.core_tools._ensure_document_readable", _recording_denied_gate(seen)):
            result = await handler(**args, organization_id=ORG_ID, user_id=0, run_context=ctx)
        _denied(result)
        assert seen, "ACL gate was never called"
        assert all(item is ctx for item in seen), "call site dropped the caller context"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler, args",
    [
        (summarize_document, {"document_id": PRIVATE_ID}),
        (extract_insights, {"document_id": PRIVATE_ID}),
        (get_document_info, {"document_id": PRIVATE_ID}),
        (cross_document_analysis, {"document_ids": [PRIVATE_ID, "doc-B"]}),
        (compare_documents, {"document_ids": [PRIVATE_ID, "doc-B"]}),
    ],
    ids=["summarize", "insights", "info", "cross", "compare"],
)
async def test_direct_read_tools_propagate_trusted_internal_marker(handler, args):
    """Removing the run_context argument at a call site must turn this test red."""
    ctx = run_context_for_internal_system_task(organization_id=ORG_ID)
    seen = []

    async def fake_gate(document_id, organization_id, user_id, run_context=None):
        seen.append(run_context)
        return ToolResult.fail(
            code=DENIED_CODE,
            message=f"Document {document_id} not found or not accessible.",
        )

    with patch("app.agent.core_tools._ensure_document_readable", fake_gate):
        result = await handler(**args, organization_id=ORG_ID, user_id=0, run_context=ctx)
    _denied(result)
    assert seen, "ACL gate was never called"
    assert all(item is ctx for item in seen), "trusted internal marker was lost"


@pytest.mark.asyncio
async def test_internal_task_context_is_not_constructible_from_extra():
    """The private marker survives serialization as an ordinary, untrusted dict."""
    ctx = run_context_for_internal_system_task(organization_id=ORG_ID)
    clone = RunContext(**ctx.to_dict())
    forged = RunContext(
        user_id=0,
        organization_id=ORG_ID,
        extra={"internal_system_task": True},
    )
    assert is_internal_system_task(ctx)
    assert not is_internal_system_task(clone)
    assert not is_internal_system_task(forged)
    assert not is_internal_system_task(SimpleNamespace(is_internal_system_task=True))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "internal_system_task, expect_internal",
    [(False, False), (True, True)],
    ids=["user-run", "internal-system-run"],
)
async def test_executor_agent_passes_a_real_run_context(internal_system_task, expect_internal):
    """ExecutorAgent must never dispatch ACL tools without a typed RunContext."""
    agent = ExecutorAgent(
        organization_id=ORG_ID,
        user_id=0,
        internal_system_task=internal_system_task,
    )
    step = PlanStep(id="s1", description="d", tool_hint="extract_keywords")
    seen = {}

    async def fake_execute_detailed(name, arguments, **context):
        seen.update(context)
        return ToolResult.ok({"ok": True})

    with patch.object(tool_registry, "execute_detailed", fake_execute_detailed):
        result, _record = await agent._call_tool(step)

    assert result.success
    assert seen["organization_id"] == ORG_ID
    assert seen["user_id"] == 0
    assert isinstance(seen["run_context"], RunContext)
    assert is_internal_system_task(seen["run_context"]) is expect_internal


def _client_with_tool_call(name: str):
    client = MagicMock()

    async def _create(**_kwargs):
        resp = MagicMock()
        choice = MagicMock()
        msg = MagicMock()
        call = MagicMock()
        call.id = "tc-1"
        call.function.name = name
        call.function.arguments = "{}"
        msg.tool_calls = [call]
        msg.content = ""
        msg.role = "assistant"
        choice.message = msg
        resp.choices = [choice]
        return resp

    client.chat.completions.create = _create
    return client


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "internal_system_task, expect_internal",
    [(False, False), (True, True)],
    ids=["user-run", "internal-system-run"],
)
async def test_main_executor_passes_a_real_run_context(internal_system_task, expect_internal):
    """The primary Executor path must mint a typed context with the loop's flag."""
    config = AgentConfig(enable_planning=False, enable_reflection=False, enable_memory=False)
    memory = AgentMemoryBridge(agent_id="t", organization_id=ORG_ID, user_id=0)
    executor = Executor(_client_with_tool_call("search_knowledge_base"), config, memory)
    executor.internal_system_task = internal_system_task
    step = PlanStep(id="s1", description="keywords", tool_hint=None)
    plan = Plan(id="p1", goal="g", steps=[step])
    seen = {}

    async def fake_execute_detailed(name, arguments, **context):
        seen.update(context)
        return ToolResult.ok("ok")

    with patch.object(tool_registry, "execute_detailed", fake_execute_detailed):
        async for _event in executor._execute_step_once(
            step=step,
            plan=plan,
            history=None,
            organization_id=ORG_ID,
            user_id=0,
            enable_thinking=False,
        ):
            pass

    assert isinstance(seen.get("run_context"), RunContext)
    assert seen["organization_id"] == ORG_ID
    assert is_internal_system_task(seen["run_context"]) is expect_internal


@pytest.mark.asyncio
async def test_main_executor_rejects_internal_task_with_viewer():
    config = AgentConfig(enable_planning=False, enable_reflection=False, enable_memory=False)
    memory = AgentMemoryBridge(agent_id="t", organization_id=ORG_ID, user_id=0)
    executor = Executor(MagicMock(), config, memory)
    executor.internal_system_task = True
    step = PlanStep(id="s1", description="keywords")
    plan = Plan(id="p1", goal="g", steps=[step])

    async def _must_not_run():
        async for _ in executor.execute(
            plan=plan,
            organization_id=ORG_ID,
            user_id=99,
        ):
            pass

    with pytest.raises(ValueError, match="internal_system_task"):
        await _must_not_run()


def test_orchestrator_rejects_internal_task_with_viewer():
    with pytest.raises(ValueError, match="internal_system_task"):
        Orchestrator(organization_id=ORG_ID, user_id=99, internal_system_task=True)


def test_executor_agent_rejects_internal_task_with_viewer():
    with pytest.raises(ValueError, match="internal_system_task"):
        ExecutorAgent(
            organization_id=ORG_ID,
            user_id=99,
            internal_system_task=True,
        )
