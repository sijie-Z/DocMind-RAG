"""Batch 12：原文/增强文本消费方收口。

三条用户可见/提示词路径曾直接读取检索增强文本 E（`chunk_text = 【文档背景：摘要】+ 原文`）：

1. ``/chat/parse-upload`` 把 E 当作「从文件提取的内容」返回；
2. 工作流知识库搜索节点把 E 拼进 LLM 的 ``knowledge_context``（strict 只清
   ``prompt_text``，挡住 content 读取才能让 strict 语义成立）；
3. ``compare_documents`` 工具把 E 前 500 字作为 ``Preview`` 原样展示给用户。

统一口径：原文 O 只认 ``original_text`` + ``text_provenance=parser_original``；
缺标记/缺 O 一律 legacy_unverified，compat 下回退 E 必须显式标记，strict 下
不得把 E 冒充原文。
"""

import io
from types import SimpleNamespace

import pytest
from fastapi import UploadFile

import app.api.v1.endpoints.chat as chat_api
from app.agent.tools.data_analysis import compare_documents
from app.core.config import settings
from app.rag.source_provenance import LEGACY_UNVERIFIED, PARSER_ORIGINAL
from app.schemas.workflow import WorkflowNode
from app.services.document_parser import DocumentParser
from app.services.rag_service import RagServiceFacade
from app.services.semantic_chunker import SemanticChunker
from app.services.workflow_engine import KnowledgeSearchNodeExecutor

O_SENTINEL = "O-SENTINEL 原文"
E_SENTINEL = f"【文档背景：E-SENTINEL 背景】\n\n{O_SENTINEL}"


# ── 0. 解析器为 original_text 提供 provenance 标记 ──────────────────────────


@pytest.mark.asyncio
async def test_parser_attests_original_text_provenance(monkeypatch):
    parser = DocumentParser()

    async def _summary(_self, _text):
        return "E-SENTINEL 背景"

    async def _split(_self, text, page_ranges=None, **_kwargs):
        return [{"text": text, "metadata": {}}]

    monkeypatch.setattr(DocumentParser, "_generate_summary", _summary)
    monkeypatch.setattr(SemanticChunker, "split_text", _split)

    chunks = await parser._create_contextual_chunks(O_SENTINEL)

    assert chunks[0]["chunk_text"] == E_SENTINEL
    assert chunks[0]["original_text"] == O_SENTINEL
    assert chunks[0]["text_provenance"] == PARSER_ORIGINAL


# ── 1. /chat/parse-upload：返回原文而不是增强文本 ───────────────────────────


def _upload(content: bytes = b"hello", name: str = "a.txt") -> UploadFile:
    return UploadFile(filename=name, file=io.BytesIO(content))


def _patch_org(monkeypatch):
    async def _org(_user, _db):
        return 1

    monkeypatch.setattr(chat_api, "require_organization", _org)


@pytest.mark.asyncio
async def test_parse_upload_returns_original_not_enhanced(monkeypatch):
    async def _parse(_self, _path, organization_id):
        return {
            "chunks": [
                {
                    "chunk_text": E_SENTINEL,
                    "original_text": O_SENTINEL,
                    "text_provenance": PARSER_ORIGINAL,
                }
            ],
            "file_size": 5,
        }

    _patch_org(monkeypatch)
    monkeypatch.setattr(DocumentParser, "parse_local_file", _parse)

    result = await chat_api.chat_parse_upload(
        file=_upload(), current_user=SimpleNamespace(id=1), db=SimpleNamespace()
    )
    data = result["data"]

    assert data["content"] == O_SENTINEL
    assert "E-SENTINEL" not in data["content"]
    assert data["text_provenance"] == PARSER_ORIGINAL
    assert data["original_chunk_count"] == 1
    assert data["legacy_chunk_count"] == 0


@pytest.mark.asyncio
async def test_parse_upload_marks_legacy_and_mixed(monkeypatch):
    async def _parse(_self, _path, organization_id):
        return {
            "chunks": [
                {
                    "chunk_text": E_SENTINEL,
                    "original_text": O_SENTINEL,
                    "text_provenance": PARSER_ORIGINAL,
                },
                {"chunk_text": "LEGACY-CHUNK"},
            ],
            "file_size": 5,
        }

    _patch_org(monkeypatch)
    monkeypatch.setattr(DocumentParser, "parse_local_file", _parse)

    result = await chat_api.chat_parse_upload(
        file=_upload(), current_user=SimpleNamespace(id=1), db=SimpleNamespace()
    )
    data = result["data"]

    assert O_SENTINEL in data["content"] and "LEGACY-CHUNK" in data["content"]
    assert data["text_provenance"] == "mixed"
    assert data["original_chunk_count"] == 1
    assert data["legacy_chunk_count"] == 1


@pytest.mark.asyncio
async def test_parse_upload_all_legacy_is_marked(monkeypatch):
    async def _parse(_self, _path, organization_id):
        return {
            "chunks": [{"chunk_text": E_SENTINEL}],
            "file_size": 5,
        }

    _patch_org(monkeypatch)
    monkeypatch.setattr(DocumentParser, "parse_local_file", _parse)

    result = await chat_api.chat_parse_upload(
        file=_upload(), current_user=SimpleNamespace(id=1), db=SimpleNamespace()
    )
    data = result["data"]

    assert data["text_provenance"] == LEGACY_UNVERIFIED
    assert data["legacy_chunk_count"] == 1


# ── 2. 工作流知识库节点：上下文只取原文 O ────────────────────────────────────


def _workflow_node() -> WorkflowNode:
    return WorkflowNode(id="n1", type="tool_search", data={}, position={"x": 0, "y": 0})


def _workflow_state() -> dict:
    return {
        "input": {"text": "付款期限"},
        "messages": [],
        "current_node": None,
        "node_outputs": {},
        "context": {},
        "errors": [],
        "memory": {},
        "iteration_count": 0,
        "tool_results": [],
    }


@pytest.mark.asyncio
async def test_workflow_knowledge_node_uses_original_text(monkeypatch):
    async def _search(self, query, organization_id, top_k=5, **kwargs):
        return [
            {
                "title": "合同",
                "score": 0.9,
                "content": E_SENTINEL,
                "original_text": O_SENTINEL,
                "text_provenance": PARSER_ORIGINAL,
            },
            {"title": "旧文档", "score": 0.8, "content": "LEGACY-CONTENT"},
        ], None

    monkeypatch.setattr(RagServiceFacade, "search_knowledge_base", _search)

    executor = KnowledgeSearchNodeExecutor(_workflow_node())
    executor.organization_id = 3
    result = await executor.execute(_workflow_state())

    context = result["context"]["knowledge_context"]
    assert O_SENTINEL in context
    assert "E-SENTINEL" not in context
    sources = result["context"]["knowledge_sources"]
    assert sources[0]["textProvenance"] == PARSER_ORIGINAL
    assert sources[1]["textProvenance"] == LEGACY_UNVERIFIED


# ── 3. compare_documents：预览优先原文，legacy 显式标记，strict 拒绝 ─────────


def _patch_es(monkeypatch, sources: list[dict]):
    from app.core.elasticsearch import ElasticsearchTools

    async def _search(_query):
        return {"hits": {"hits": [{"_source": source} for source in sources]}}

    monkeypatch.setattr(ElasticsearchTools, "search_documents", _search)


@pytest.mark.asyncio
async def test_compare_documents_prefers_original_text(monkeypatch):
    _patch_es(
        monkeypatch,
        [
            {
                "filename": "a.txt",
                "chunk_text": E_SENTINEL,
                "original_text": O_SENTINEL,
                "text_provenance": PARSER_ORIGINAL,
            }
        ],
    )

    out = await compare_documents(["doc-1", "doc-2"], organization_id=1)

    assert isinstance(out, str)
    assert "O-SENTINEL 原文" in out
    assert "E-SENTINEL" not in out
    assert "Provenance: parser_original" in out


@pytest.mark.asyncio
async def test_compare_documents_marks_legacy_in_compat(monkeypatch):
    monkeypatch.setattr(settings, "RAG_SOURCE_PROVENANCE_MODE", "compat")
    _patch_es(monkeypatch, [{"filename": "old.txt", "chunk_text": "LEGACY-CONTENT"}])

    out = await compare_documents(["doc-1", "doc-2"], organization_id=1)

    assert isinstance(out, str)
    assert "[legacy_unverified] LEGACY-CONTENT" in out
    assert "Provenance: legacy_unverified" in out


@pytest.mark.asyncio
async def test_compare_documents_strict_refuses_legacy(monkeypatch):
    monkeypatch.setattr(settings, "RAG_SOURCE_PROVENANCE_MODE", "strict")
    _patch_es(monkeypatch, [{"filename": "old.txt", "chunk_text": "LEGACY-CONTENT"}])

    out = await compare_documents(["doc-1", "doc-2"], organization_id=1)

    assert not isinstance(out, str)
    assert out.success is False
    assert out.error.code == "source_original_unavailable"
