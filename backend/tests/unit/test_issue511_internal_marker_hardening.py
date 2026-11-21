"""Hardening of the issue #511 direct-read ACL fallbacks.

The production user path always constructs a server-side RunContext. Without
one, or with an unmarked one, a missing viewer must fail closed instead of
silently reading a private document at organization scope. The internal-system
marker is a private RunContext field created by a server-side factory; LLM tool
arguments and request payloads cannot construct it.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.agent.agents.executor_agent import ExecutorAgent
from app.agent.core_tools import (
    _ensure_document_readable,
    cross_document_analysis,
    extract_insights,
    get_document_info,
    summarize_document,
)
from app.agent.planner import PlanStep
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
    ],
    ids=[
        "no-run-context",
        "unmarked-run-context",
        "forged-extra-marker",
        "forged-context-object",
    ],
)
async def test_missing_viewer_never_gets_org_level_private_read(run_context):
    """Missing, unmarked, and impersonated contexts are all denied."""
    with patch(
        "app.core.database.AsyncSessionLocal",
        side_effect=AssertionError("ACL gate must not touch the document DB"),
    ):
        result = await _ensure_document_readable(PRIVATE_ID, ORG_ID, 0, run_context=run_context)
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


def _fake_denied_gate():
    async def fake_gate(document_id, organization_id, user_id, run_context=None):
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
        with patch("app.agent.core_tools._ensure_document_readable", _fake_denied_gate()):
            result = await handler(**args, organization_id=ORG_ID, user_id=0, run_context=ctx)
        _denied(result)


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
    assert ctx.is_internal_system_task
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


def test_executor_agent_rejects_internal_task_with_viewer():
    with pytest.raises(ValueError, match="internal_system_task"):
        ExecutorAgent(
            organization_id=ORG_ID,
            user_id=99,
            internal_system_task=True,
        )
