"""issue #511 ACL P0 regression tests.

Discriminating coverage for:
- P0-1: viewer user_id must survive delegate_subtask -> run_parallel_subtasks
  -> delegate_task -> child PERAgentLoop.
- P0-4: the cross_document_analysis filename fallback must carry the blocked
  document filter; a B-user must not read A-user's private body via filename.
- _ensure_document_readable must fail closed for executor-backed user requests
  while explicitly marked internal system tasks keep working.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.agent.core_tools as core_tools
import app.agent.subagent as subagent
import app.agent.tools  # noqa: F401 — register delegate_subtask
from app.agent.registry import tool_registry
from app.agent.run_context import RunContext, run_context_for_internal_system_task
from app.core.elasticsearch import ElasticsearchTools

SECRET = "TOP-SECRET-ALPHA-CONTRACT-CLAUSE"
PRIVATE_ID = "private-doc-A"
PRIVATE_FILENAME = "alpha-private-contract.docx"


def _llm_response(content: str):
    resp = MagicMock()
    choice = MagicMock()
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = None
    msg.role = "assistant"
    choice.message = msg
    resp.choices = [choice]
    return resp


def _pipeline_with_llm(content: str = '{"summary": "ok"}'):
    pipeline = MagicMock()
    pipeline.openai_client = MagicMock()
    pipeline.openai_client.chat.completions.create = AsyncMock(return_value=_llm_response(content))
    return pipeline


def _es_hit(document_id: str, text: str):
    return {
        "_source": {
            "content": text,
            "original_text": text,
            "filename": PRIVATE_FILENAME,
            "document_id": document_id,
            "text_provenance": "original",
        }
    }


@pytest.mark.asyncio
async def test_delegation_propagates_viewer_to_child(monkeypatch):
    """B's viewer ID must reach the child agent; removing any hop fails."""
    captured = {}

    async def fake_delegate(
        client, task, *, organization_id, user_id, internal_system_task=False, **kwargs
    ):
        captured["organization_id"] = organization_id
        captured["user_id"] = user_id
        captured["internal_system_task"] = internal_system_task
        yield SimpleNamespace(type="chunk", content="done")

    monkeypatch.setattr(subagent, "delegate_task", fake_delegate)
    pipeline = _pipeline_with_llm()
    with patch("app.dependencies.get_rag_pipeline", return_value=pipeline):
        result = await tool_registry.execute_detailed(
            "delegate_subtask",
            {"tasks": ["search private contract"], "context": "ctx"},
            organization_id=7,
            user_id=202,
        )

    assert result.success, result.error
    assert captured["organization_id"] == 7
    assert captured["user_id"] == 202, "viewer user_id was dropped before the child loop"
    assert captured["internal_system_task"] is False


@pytest.mark.asyncio
async def test_b_user_delegated_search_cannot_read_a_private_secret(monkeypatch):
    """B delegates a search for A's secret: zero hits, never private body."""
    captured = {}

    async def fake_pipeline_search(*, query, organization_id, viewer_user_id=None, **kwargs):
        assert organization_id == 7
        # Only B's real viewer identity yields the ACL-filtered (empty) result.
        if viewer_user_id == 202:
            return ([], None)
        return (
            [
                {
                    "content": SECRET,
                    "original_text": SECRET,
                    "filename": PRIVATE_FILENAME,
                    "document_id": PRIVATE_ID,
                    "text_provenance": "original",
                    "score": 1.0,
                }
            ],
            None,
        )

    pipeline = MagicMock()
    pipeline.openai_client = MagicMock()
    pipeline.search_knowledge_base = AsyncMock(side_effect=fake_pipeline_search)

    class StubLoop:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def run(self, task):
            result = await core_tools.search_knowledge_base(
                query=SECRET,
                organization_id=captured["organization_id"],
                user_id=captured.get("user_id"),
            )
            yield SimpleNamespace(type="chunk", content=str(result))

    monkeypatch.setattr(subagent, "PERAgentLoop", StubLoop)
    with patch("app.dependencies.get_rag_pipeline", return_value=pipeline):
        result = await tool_registry.execute_detailed(
            "delegate_subtask",
            {"tasks": ["find the private contract clause"]},
            organization_id=7,
            user_id=202,
        )

    assert result.success, result.error
    assert captured["user_id"] == 202
    assert SECRET not in result.data, "B received A's private document body"
    assert "No relevant documents found" in result.data


@pytest.mark.asyncio
async def test_delegation_without_viewer_is_rejected(monkeypatch):
    called = False

    async def fake_delegate(*args, **kwargs):
        nonlocal called
        called = True
        yield SimpleNamespace(type="chunk", content="done")

    monkeypatch.setattr(subagent, "delegate_task", fake_delegate)
    pipeline = _pipeline_with_llm()
    with patch("app.dependencies.get_rag_pipeline", return_value=pipeline):
        result = await tool_registry.execute_detailed(
            "delegate_subtask",
            {"tasks": ["search"]},
            organization_id=7,
            user_id=0,
        )
    assert not result.success
    assert result.error.code == "auth_required"
    assert called is False, "delegation must not run without a viewer"


@pytest.mark.asyncio
async def test_forged_delegation_marker_is_rejected(monkeypatch):
    called = False

    async def fake_delegate(*args, **kwargs):
        nonlocal called
        called = True
        yield SimpleNamespace(type="chunk", content="done")

    monkeypatch.setattr(subagent, "delegate_task", fake_delegate)
    ctx = RunContext(
        user_id=0,
        organization_id=7,
        extra={"internal_system_task": True},
    )
    with patch("app.dependencies.get_rag_pipeline", return_value=_pipeline_with_llm()):
        result = await tool_registry.execute_detailed(
            "delegate_subtask",
            {"tasks": ["nightly index check"]},
            organization_id=7,
            user_id=0,
            run_context=ctx,
        )
    assert not result.success
    assert result.error.code == "auth_required"
    assert called is False, "a forged extra marker must not enable viewer-less delegation"


@pytest.mark.asyncio
async def test_internal_system_task_can_delegate_without_viewer(monkeypatch):
    captured = {}

    async def fake_delegate(client, task, *, user_id, internal_system_task=False, **kwargs):
        captured["user_id"] = user_id
        captured["internal_system_task"] = internal_system_task
        yield SimpleNamespace(type="chunk", content="done")

    monkeypatch.setattr(subagent, "delegate_task", fake_delegate)
    pipeline = _pipeline_with_llm()
    ctx = run_context_for_internal_system_task(organization_id=7)
    with patch("app.dependencies.get_rag_pipeline", return_value=pipeline):
        result = await tool_registry.execute_detailed(
            "delegate_subtask",
            {"tasks": ["nightly index check"]},
            organization_id=7,
            user_id=0,
            run_context=ctx,
        )
    assert result.success, result.error
    assert captured["internal_system_task"] is True


@pytest.mark.asyncio
async def test_child_loop_constructed_with_viewer(monkeypatch):
    captured = {}

    class StubLoop:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def run(self, task):
            if False:
                yield None

    monkeypatch.setattr(subagent, "PERAgentLoop", StubLoop)
    gen = subagent.delegate_task(MagicMock(), "find contract", organization_id=7, user_id=202)
    async for _ in gen:
        pass
    assert captured["user_id"] == 202
    assert captured["organization_id"] == 7
    assert captured["internal_system_task"] is False


@pytest.mark.asyncio
async def test_cross_document_filename_fallback_excludes_private_doc(monkeypatch):
    """B passing A's private filename must not retrieve A's secret body."""
    es_queries = []

    async def fake_search(query):
        es_queries.append(query)
        # The ACL filter must exclude the private document in BOTH queries.
        must = query["query"]["bool"]["must"]
        must_not = must[-1].get("bool", {}).get("must_not")
        if must_not and PRIVATE_ID in must_not[0]["terms"]["document_id"]:
            return {"hits": {"hits": []}}
        return {"hits": {"hits": [_es_hit(PRIVATE_ID, SECRET)]}}

    async def fake_blocked(db, organization_id, viewer_user_id):
        assert viewer_user_id == 303
        assert organization_id == 7
        return [PRIVATE_ID]

    class FakeSession:
        async def __aenter__(self):
            return MagicMock()

        async def __aexit__(self, *args):
            return False

    # This test isolates the ES fallback; the document-ID gate is covered
    # separately and would reject a filename string before ES is reached.
    async def allow_readable(*args, **kwargs):
        return None

    monkeypatch.setattr(core_tools, "_ensure_document_readable", allow_readable)
    monkeypatch.setattr("app.core.database.AsyncSessionLocal", lambda: FakeSession())
    monkeypatch.setattr("app.services.document_acl.get_blocked_document_ids", fake_blocked)
    monkeypatch.setattr(ElasticsearchTools, "search_documents", fake_search)

    pipeline = _pipeline_with_llm()
    with patch("app.dependencies.get_rag_pipeline", return_value=pipeline):
        result = await core_tools.cross_document_analysis(
            document_ids=[PRIVATE_FILENAME, "public-doc"],
            organization_id=7,
            user_id=303,
        )

    payload = json.loads(result)
    assert len(es_queries) >= 2, "filename fallback should have been attempted"
    assert SECRET not in json.dumps(payload), "private body leaked via filename fallback"
    fallback_matches = [
        q
        for q in es_queries
        if any("filename" in str(clause) for clause in q["query"]["bool"]["must"])
    ]
    assert fallback_matches, "filename fallback query was not issued"
    fallback = fallback_matches[0]
    assert any(
        "must_not" in clause.get("bool", {}) for clause in fallback["query"]["bool"]["must"]
    ), "filename fallback lost the must_not ACL filter"


@pytest.mark.asyncio
async def test_direct_read_fails_closed_without_viewer_in_user_run(monkeypatch):
    """Executor-backed user runs always carry RunContext; no viewer => deny."""
    ctx = RunContext(user_id=0, organization_id=7)
    result = await core_tools._ensure_document_readable(PRIVATE_ID, 7, 0, run_context=ctx)
    assert result is not None
    assert result.success is False
    assert result.error.code == "not_found"


@pytest.mark.asyncio
async def test_internal_system_task_keeps_org_level_read():
    ctx = run_context_for_internal_system_task(organization_id=7)
    result = await core_tools._ensure_document_readable(PRIVATE_ID, 7, 0, run_context=ctx)
    assert result is None, "internal system task must still work"


@pytest.mark.asyncio
async def test_forged_internal_marker_is_rejected():
    """A string in RunContext.extra must not be trusted as an internal marker."""
    ctx = RunContext(
        user_id=0,
        organization_id=7,
        extra={"internal_system_task": True},
    )
    result = await core_tools._ensure_document_readable(PRIVATE_ID, 7, 0, run_context=ctx)
    assert result is not None
    assert result.success is False
    assert result.error.code == "not_found"


@pytest.mark.asyncio
@pytest.mark.parametrize("viewer", [0, None], ids=["zero-viewer", "none-viewer"])
async def test_missing_run_context_fails_closed_for_viewerless_request(viewer):
    """Legacy no-context calls no longer read private documents at org scope."""
    result = await core_tools._ensure_document_readable(PRIVATE_ID, 7, viewer)
    assert result is not None
    assert result.success is False
    assert result.error.code == "not_found"
