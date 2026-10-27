"""PR3：Agent 检索、直读分析与结构化 evidence 的原文溯源。"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent import core_tools
from app.agent.events import AgentEvent
from app.agent.executor import AttemptOutcome
from app.agent.planner import PlanStep
from app.agent.service import AgentService, build_referenced_document_context
from app.core.config import settings
from app.core.elasticsearch import ElasticsearchTools
from app.rag.retriever import HybridRetriever
from app.rag.source_provenance import (
    LEGACY_UNVERIFIED,
    PARSER_ORIGINAL,
    STRICT,
    SourceProvenanceUnavailableError,
)

ORIGINAL = "第3条 审计留痕\n审计日志保留期限为五年。"
BACKGROUND = "【文档背景：本制度由银河公司于2099年颁布，保留期限为十年。】"
ENHANCED = f"{BACKGROUND}\n\n{ORIGINAL}"
QUERY = "审计日志保留期限"


def _result(*, original=True):
    result = {
        "score": 0.91,
        "relevance_score": 0.77,
        "rank_score": 0.91,
        "filename": "policy.pdf",
        "document_id": "doc-1",
        "chunk_id": "chunk-1",
        "retrieval_text": ENHANCED,
        "text": ENHANCED,
        "content": ENHANCED,
        "prompt_text": ENHANCED,
        "snippet": ENHANCED,
    }
    if original:
        result.update(
            {
                "original_text": ORIGINAL,
                "text_provenance": PARSER_ORIGINAL,
            }
        )
    return result


def _es_hit(*, original=True, document_id="doc-1"):
    source = {
        "content": ENHANCED,
        "filename": "policy.pdf",
        "document_id": document_id,
        "chunk_id": f"chunk-{document_id}",
    }
    if original:
        source.update(
            {
                "original_text": ORIGINAL,
                "text_provenance": PARSER_ORIGINAL,
            }
        )
    return {
        "_id": f"es-{document_id}",
        "_score": 1.31,
        "_source": source,
    }


def _llm_pipeline(captured, content='{"summary": "ok", "entities": {}}'):
    pipeline = MagicMock()

    async def _create(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
            model="fake-llm",
        )

    pipeline.openai_client.chat.completions.create = _create
    pipeline.get_embedding = AsyncMock(return_value=[0.1, 0.2])
    return pipeline


def _assert_no_background(value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    assert "银河公司" not in text
    assert "2099" not in text
    assert "十年" not in text


@pytest.mark.asyncio
async def test_hybrid_tool_content_evidence_and_sources_are_original_only(monkeypatch):
    monkeypatch.setattr(core_tools.ai_config, "unified_final_enabled", lambda: True)
    pipeline = MagicMock()
    pipeline.search_knowledge_base = AsyncMock(return_value=([_result()], None))

    with patch("app.dependencies.get_rag_pipeline", return_value=pipeline):
        raw = await core_tools.search_knowledge_base(QUERY, organization_id=1)

    payload = json.loads(raw)
    _assert_no_background(payload["content"])
    _assert_no_background(payload["evidence"])
    assert ORIGINAL in payload["content"]
    source = payload["sources"][0]
    assert source["documentId"] == "doc-1"
    assert source["index"] == 1
    assert source["score"] == pytest.approx(0.91)
    assert source["relevanceScore"] == pytest.approx(0.77)
    assert source["textProvenance"] == PARSER_ORIGINAL
    assert source["chunkId"] == "chunk-1"
    assert payload["evidence"][0]["content"] == ORIGINAL
    _assert_no_background(raw), "完整工具下载 JSON 同样不得含生成背景"


@pytest.mark.asyncio
async def test_vector_source_query_and_payload_use_original(monkeypatch):
    monkeypatch.setattr(core_tools.ai_config, "unified_final_enabled", lambda: True)
    pipeline = MagicMock()
    pipeline.get_embedding = AsyncMock(return_value=[0.1, 0.2])
    search = AsyncMock(return_value={"hits": {"hits": [_es_hit()]}})

    with (
        patch("app.dependencies.get_rag_pipeline", return_value=pipeline),
        patch.object(ElasticsearchTools, "search_documents", search),
    ):
        raw = await core_tools.search_knowledge_base(QUERY, mode="vector", organization_id=1)

    requested_source = search.await_args.args[0]["_source"]
    assert "original_text" in requested_source
    assert "text_provenance" in requested_source
    assert "chunk_id" in requested_source
    payload = json.loads(raw)
    _assert_no_background(payload["content"])
    _assert_no_background(payload["evidence"])
    assert payload["sources"][0]["documentId"] == "doc-1"
    assert payload["sources"][0]["textProvenance"] == PARSER_ORIGINAL
    assert payload["sources"][0]["chunkId"] == "chunk-doc-1"
    assert payload["sources"][0]["score"] == pytest.approx(1.31)
    assert payload["sources"][0]["relevanceScore"] is None


@pytest.mark.asyncio
async def test_vector_legacy_strict_is_structured_failure_compat_is_explicit(monkeypatch):
    pipeline = MagicMock()
    pipeline.get_embedding = AsyncMock(return_value=[0.1, 0.2])
    hits = {"hits": {"hits": [_es_hit(original=False)]}}

    monkeypatch.setattr(settings, "RAG_SOURCE_PROVENANCE_MODE", STRICT)
    with (
        patch("app.dependencies.get_rag_pipeline", return_value=pipeline),
        patch.object(ElasticsearchTools, "search_documents", AsyncMock(return_value=hits)),
    ):
        failed = await core_tools.search_knowledge_base(QUERY, mode="vector", organization_id=1)

    assert failed.success is False
    assert failed.error.code == "invalid_configuration"
    assert failed.error.raw["code"] == "source_original_unavailable"
    assert failed.error.raw["status"] == "needs_rebuild"
    assert failed.error.raw["document_ids"] == ["doc-1"]

    monkeypatch.setattr(settings, "RAG_SOURCE_PROVENANCE_MODE", "compat")
    with (
        patch("app.dependencies.get_rag_pipeline", return_value=pipeline),
        patch.object(ElasticsearchTools, "search_documents", AsyncMock(return_value=hits)),
    ):
        raw = await core_tools.search_knowledge_base(QUERY, mode="vector", organization_id=1)
    payload = json.loads(raw)
    assert payload["sources"][0]["textProvenance"] == LEGACY_UNVERIFIED
    assert ENHANCED in payload["content"]


@pytest.mark.asyncio
async def test_hybrid_strict_provenance_error_is_structured(monkeypatch):
    error = SourceProvenanceUnavailableError(["doc-1"], unverified_count=1)
    pipeline = MagicMock()
    pipeline.search_knowledge_base = AsyncMock(side_effect=error)

    with patch("app.dependencies.get_rag_pipeline", return_value=pipeline):
        failed = await core_tools.search_knowledge_base(QUERY, organization_id=1)

    assert failed.success is False
    assert failed.error.code == "invalid_configuration"
    assert failed.error.raw["status"] == "needs_rebuild"
    assert failed.error.raw["document_ids"] == ["doc-1"]


@pytest.mark.asyncio
async def test_summarize_strict_legacy_is_structured_failure(monkeypatch):
    monkeypatch.setattr(settings, "RAG_SOURCE_PROVENANCE_MODE", STRICT)
    hits = {"hits": {"hits": [_es_hit(original=False)]}}
    with patch.object(ElasticsearchTools, "search_documents", AsyncMock(return_value=hits)):
        failed = await core_tools.summarize_document(document_id="doc-1", organization_id=1)

    assert failed.success is False
    assert failed.error.code == "invalid_configuration"
    assert failed.error.raw["status"] == "needs_rebuild"
    assert failed.error.raw["document_ids"] == ["doc-1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["summarize", "insights", "compare"])
async def test_direct_analysis_llm_input_is_original_only(tool_name):
    captured = []
    pipeline = _llm_pipeline(captured)
    first = _es_hit(document_id="doc-1")
    second = _es_hit(document_id="doc-2")

    async def _search(query):
        document_id = (
            "doc-2"
            if query["query"]["bool"]["must"][0]["term"].get("document_id") == "doc-2"
            else "doc-1"
        )
        return {"hits": {"hits": [second if document_id == "doc-2" else first]}}

    search = AsyncMock(side_effect=_search)
    with (
        patch("app.dependencies.get_rag_pipeline", return_value=pipeline),
        patch.object(ElasticsearchTools, "search_documents", search),
    ):
        if tool_name == "summarize":
            await core_tools.summarize_document(document_id="doc-1", organization_id=1)
        elif tool_name == "insights":
            await core_tools.extract_insights(document_id="doc-1", organization_id=1)
        else:
            await core_tools.cross_document_analysis(
                document_ids=["doc-1", "doc-2"], organization_id=1
            )

    assert captured, f"{tool_name} 必须调用假 LLM"
    for call in captured:
        for message in call["messages"]:
            _assert_no_background(message["content"])
    for call in search.await_args_list:
        assert "original_text" in call.args[0]["_source"]
        assert "text_provenance" in call.args[0]["_source"]


class _FakeDB:
    def __init__(self, document):
        self.document = document

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def execute(self, *_args):
        return SimpleNamespace(
            scalars=lambda: SimpleNamespace(first=lambda: self.document),
        )


def _document():
    return SimpleNamespace(
        title="审计制度",
        filename="policy.pdf",
        organization_id=1,
        description="DESCRIPTION-NOT-EVIDENCE",
    )


@pytest.mark.asyncio
async def test_referenced_document_direct_fallback_uses_original(monkeypatch):
    document = _document()
    monkeypatch.setattr("app.core.database.AsyncSessionLocal", lambda: _FakeDB(document))
    monkeypatch.setattr(
        ElasticsearchTools,
        "search_documents",
        AsyncMock(return_value={"hits": {"hits": [_es_hit()]}}),
    )
    pipeline = MagicMock()
    pipeline.search_knowledge_base = AsyncMock(return_value=([], None))

    context, warnings = await build_referenced_document_context(
        ["doc-1"], QUERY, 1, pipeline=pipeline
    )

    assert context is not None
    assert ORIGINAL in context
    _assert_no_background(context)
    assert warnings == []


@pytest.mark.asyncio
async def test_referenced_document_strict_legacy_does_not_fall_back_to_description(monkeypatch):
    document = _document()
    monkeypatch.setattr("app.core.database.AsyncSessionLocal", lambda: _FakeDB(document))
    monkeypatch.setattr(settings, "RAG_SOURCE_PROVENANCE_MODE", STRICT)
    monkeypatch.setattr(
        ElasticsearchTools,
        "search_documents",
        AsyncMock(return_value={"hits": {"hits": [_es_hit(original=False)]}}),
    )
    pipeline = MagicMock()
    pipeline.search_knowledge_base = AsyncMock(return_value=([], None))

    context, warnings = await build_referenced_document_context(
        ["doc-1"], QUERY, 1, pipeline=pipeline
    )

    assert context is None
    assert len(warnings) == 1
    assert "需要重建" in warnings[0]
    assert "DESCRIPTION-NOT-EVIDENCE" not in warnings[0]


class _FakeLoop:
    calls = 0

    def __init__(self, **_kwargs):
        type(self).calls += 1

    async def run(self, *_args, **_kwargs):
        yield AgentEvent(type="chunk", content="done")


@pytest.mark.asyncio
async def test_search_and_chat_sources_are_original_and_strict_error_is_explicit(monkeypatch):
    monkeypatch.setattr("app.agent.service.PERAgentLoop", _FakeLoop)
    pipeline = MagicMock()
    pipeline.search_knowledge_base = AsyncMock(return_value=([_result()], None))
    service = AgentService(openai_client=object())

    with patch("app.dependencies.get_rag_pipeline", return_value=pipeline):
        events = [event async for event in service.search_and_chat(QUERY, organization_id=1)]

    context = events[0]
    assert context["type"] == "context"
    source = context["sources"][0]
    assert source["snippet"] == HybridRetriever._build_snippet(ORIGINAL, QUERY)[:200]
    assert source["textProvenance"] == PARSER_ORIGINAL
    assert source["chunkId"] == "chunk-1"
    _assert_no_background(source)

    error = SourceProvenanceUnavailableError(["doc-1"], unverified_count=1)
    pipeline.search_knowledge_base = AsyncMock(side_effect=error)
    _FakeLoop.calls = 0
    with patch("app.dependencies.get_rag_pipeline", return_value=pipeline):
        failure_events = [
            event async for event in service.search_and_chat(QUERY, organization_id=1)
        ]
    assert failure_events[0]["type"] == "error"
    assert failure_events[0]["debug_data"]["status"] == "needs_rebuild"
    assert _FakeLoop.calls == 0


def test_executor_evidence_preserves_provenance_chunk_and_source_key():
    entries = [
        (
            0.91,
            0.77,
            "policy.pdf",
            "doc-1",
            ORIGINAL,
            PARSER_ORIGINAL,
            "chunk-1",
        )
    ]
    raw = core_tools._build_search_payload(entries, include_evidence=True)
    outcome = AttemptOutcome(step_id="s1", attempt=2)
    outcome.observe(
        AgentEvent(
            type="tool_result",
            content=raw,
            tool_name="search_knowledge_base",
            tool_call_id="call-7",
        )
    )

    record = outcome.evidence[0]
    evidence = record["evidence"][0]
    assert evidence["key"] == "call-7#1"
    assert evidence["content"] == ORIGINAL
    assert evidence["textProvenance"] == PARSER_ORIGINAL
    assert evidence["chunkId"] == "chunk-1"
    assert record["sources"][0]["documentId"] == "doc-1"
    _assert_no_background(evidence)

    step = PlanStep(id="s1", description="检索", evidence=outcome.evidence)
    step.evidence_schema = "docmind.step_evidence/v1"
    restored = PlanStep.from_checkpoint_dict(step.to_checkpoint_dict())
    assert restored.evidence[0]["evidence"][0]["textProvenance"] == PARSER_ORIGINAL


def test_executor_legacy_evidence_defaults_to_legacy_without_changing_source_key():
    raw = json.dumps(
        {
            "kind": core_tools.SEARCH_RESULTS_PAYLOAD_KIND,
            "sources": [{"index": 1, "filename": "old.pdf", "documentId": "doc-old"}],
            "evidence": [{"index": 1, "content": "legacy content"}],
        }
    )
    outcome = AttemptOutcome(step_id="s1", attempt=1)
    outcome.observe(AgentEvent(type="tool_result", content=raw, tool_call_id="call-old"))

    evidence = outcome.evidence[0]["evidence"][0]
    assert evidence["key"] == "call-old#1"
    assert evidence["content"] == "legacy content"
    assert evidence["textProvenance"] == LEGACY_UNVERIFIED
    assert evidence["chunkId"] == ""


def test_legacy_five_field_entries_remain_legacy_not_original():
    entries = [(0.9, None, "old.pdf", "doc-old", "legacy snippet")]
    payload = json.loads(core_tools._build_search_payload(entries, include_evidence=True))

    assert payload["sources"][0]["textProvenance"] == LEGACY_UNVERIFIED
    assert payload["sources"][0]["chunkId"] == ""
    assert payload["evidence"] == [{"index": 1, "content": "legacy snippet"}]
