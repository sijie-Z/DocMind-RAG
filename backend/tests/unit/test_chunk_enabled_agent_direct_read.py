"""评审必修 1：Agent 直读 ES 路径必须复用分块启停过滤。

变异点：
- 把任一 summarize_document / extract_insights / cross_document_analysis 的
  ``with_chunk_enabled_filter`` 去掉 → 对应用例精确红（断言 must 含 must_not）；
- 把 compare_documents 自建查询的 ``with_chunk_enabled_filter`` 去掉 →
  ``test_compare_documents_...`` 红；
- 把 @引用直读兜底（agent.service）的 ``with_chunk_enabled_filter`` 去掉 →
  ``test_referenced_direct_read_...`` 红；
- 把共享 helper 改成空过滤 → hybrid + Agent 全部红。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent import core_tools
from app.agent.tools import data_analysis as data_analysis_tool
from app.core.elasticsearch import ElasticsearchTools
from app.rag.source_provenance import PARSER_ORIGINAL

CHUNK_DISABLED = {"bool": {"must_not": [{"term": {"is_chunk_enabled": False}}]}}


def _llm_pipeline():
    pipeline = MagicMock()

    async def _create(**_kwargs):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content='{"summary": "ok", "entities": {}}')
                )
            ],
            usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
            model="fake-llm",
        )

    pipeline.openai_client.chat.completions.create = _create
    pipeline.get_embedding = AsyncMock(return_value=[0.1, 0.2])
    return pipeline


def _hit(doc_id: str = "doc-1") -> dict:
    return {
        "_source": {
            "content": "第一条 付款期限为30天。",
            "original_text": "第一条 付款期限为30天。",
            "text_provenance": PARSER_ORIGINAL,
            "filename": f"{doc_id}.pdf",
            "document_id": doc_id,
        }
    }


def _assert_chunk_filter(query: dict) -> None:
    must = query["query"]["bool"]["must"]
    assert CHUNK_DISABLED in must, "禁用 chunk 必须被 Agent 直读查询排除"
    # ACL/tenant 条件仍须保留，不能被新 filter 顶掉。
    assert any("organization_id" in cond.get("term", {}) for cond in must if isinstance(cond, dict))


@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ["summarize", "insights", "cross"])
async def test_agent_direct_read_queries_exclude_disabled_chunks(tool_name):
    search = AsyncMock(return_value={"hits": {"hits": [_hit("doc-1"), _hit("doc-2")]}})
    with (
        patch("app.dependencies.get_rag_pipeline", return_value=_llm_pipeline()),
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

    assert search.await_count >= 1
    for call in search.await_args_list:
        _assert_chunk_filter(call.args[0])


@pytest.mark.asyncio
async def test_cross_document_filename_fallback_excludes_disabled_chunks():
    """文件名兜底腿是独立 query，也必须带分块启停 filter。"""
    calls: list[dict] = []

    async def _search(query):
        calls.append(query)
        # 第一次（document_id 查询）返回空，强制走 filename 兜底。
        if any("document_id" in cond.get("term", {}) for cond in query["query"]["bool"]["must"]):
            return {"hits": {"hits": []}}
        return {"hits": {"hits": [_hit("doc-1")]}}

    with (
        patch("app.dependencies.get_rag_pipeline", return_value=_llm_pipeline()),
        patch.object(ElasticsearchTools, "search_documents", AsyncMock(side_effect=_search)),
    ):
        await core_tools.cross_document_analysis(
            document_ids=["contract.pdf", "policy.pdf"], organization_id=1
        )

    assert len(calls) >= 3  # 两份文档各 2 个查询（id + filename 兜底）
    filename_queries = [
        q
        for q in calls
        if any("filename" in cond.get("term", {}) for cond in q["query"]["bool"]["must"])
    ]
    assert len(filename_queries) == 2
    for query in filename_queries:
        _assert_chunk_filter(query)


@pytest.mark.asyncio
async def test_agent_disabled_chunk_cannot_reach_summary_body():
    """行为级：ES 按 filter 只返回启用块时，摘要/分析不出现禁用块正文。"""
    secret = "DISABLED-CANARY-9917"
    enabled = {
        "_source": {
            "content": "可召回正文",
            "original_text": "可召回正文",
            "text_provenance": PARSER_ORIGINAL,
            "filename": "doc-1.pdf",
        }
    }
    captured = []

    pipeline = MagicMock()

    async def _create(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="摘要：可召回正文"))],
            usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
            model="fake-llm",
        )

    pipeline.openai_client.chat.completions.create = _create

    async def _search(query):
        _assert_chunk_filter(query)
        return {"hits": {"hits": [enabled]}}  # 禁用块正文 secret 不在返回集

    with (
        patch("app.dependencies.get_rag_pipeline", return_value=pipeline),
        patch.object(ElasticsearchTools, "search_documents", AsyncMock(side_effect=_search)),
    ):
        out = await core_tools.summarize_document(document_id="doc-1", organization_id=1)

    assert secret not in str(captured)
    assert secret not in str(out)


# ── compare_documents（评审 IMPORTANT 1）────────────────────────────────────


async def _allow_all(*_args, **_kwargs):
    return None


def _assert_chunk_filter_at_must(query: dict) -> None:
    """compare_documents 的 must 是 list（ACL 另行拼接前的形态）。"""
    must = query["query"]["bool"]["must"]
    assert isinstance(must, list)
    assert CHUNK_DISABLED in must, "compare_documents 自建查询必须排除禁用 chunk"


@pytest.mark.asyncio
async def test_compare_documents_queries_exclude_disabled_chunks():
    """compare_documents 自建 ES 查询必须带分块启停过滤（不绕 shared helper）。

    变异点：删除 data_analysis.compare_documents 里的 with_chunk_enabled_filter
    → 本用例在断言处精确红。
    """
    calls: list[dict] = []

    async def _search(query):
        calls.append(query)
        return {"hits": {"hits": [_hit("doc-1")]}}

    with (
        patch.object(core_tools, "_ensure_document_readable", _allow_all),
        patch.object(ElasticsearchTools, "search_documents", AsyncMock(side_effect=_search)),
    ):
        out = await data_analysis_tool.compare_documents(["doc-1", "doc-2"], organization_id=1)

    assert isinstance(out, str), out
    assert len(calls) == 2
    for query in calls:
        _assert_chunk_filter_at_must(query)


@pytest.mark.asyncio
async def test_compare_documents_disabled_chunk_never_reaches_preview():
    """行为级：ES 按 filter 只回启用块时，compare_documents 输出不含禁用块正文。"""
    secret = "DISABLED-COMPARE-CANARY-7712"
    enabled = {
        "_source": {
            "filename": "a.txt",
            "chunk_text": "可对比正文",
            "original_text": "可对比正文",
            "text_provenance": PARSER_ORIGINAL,
        }
    }

    async def _search(query):
        _assert_chunk_filter_at_must(query)
        return {"hits": {"hits": [enabled]}}

    with (
        patch.object(core_tools, "_ensure_document_readable", _allow_all),
        patch.object(ElasticsearchTools, "search_documents", AsyncMock(side_effect=_search)),
    ):
        out = await data_analysis_tool.compare_documents(["doc-1", "doc-2"], organization_id=1)

    assert secret not in str(out)
    assert "可对比正文" in str(out)
