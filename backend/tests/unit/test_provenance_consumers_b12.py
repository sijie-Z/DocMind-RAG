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
    assert "E-SENTINEL" not in data["content"]
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

    assert data["content"] == E_SENTINEL
    assert data["text_provenance"] == LEGACY_UNVERIFIED
    assert data["original_chunk_count"] == 0
    assert data["legacy_chunk_count"] == 1


@pytest.mark.asyncio
async def test_parse_upload_without_chunks_is_legacy(monkeypatch):
    async def _parse(_self, _path, organization_id):
        return {"chunks": [], "content": "PLAIN-CONTENT", "file_size": 5}

    _patch_org(monkeypatch)
    monkeypatch.setattr(DocumentParser, "parse_local_file", _parse)

    result = await chat_api.chat_parse_upload(
        file=_upload(), current_user=SimpleNamespace(id=1), db=SimpleNamespace()
    )
    data = result["data"]

    assert data["content"] == "PLAIN-CONTENT"
    assert data["text_provenance"] == LEGACY_UNVERIFIED
    assert data["original_chunk_count"] == 0
    assert data["legacy_chunk_count"] == 0


@pytest.mark.asyncio
async def test_parse_upload_ignores_unattested_original_text(monkeypatch):
    """有 original_text 但没有 provenance 标记：必须按 legacy 处理（marker gate）。"""

    async def _parse(_self, _path, organization_id):
        return {
            "chunks": [{"chunk_text": E_SENTINEL, "original_text": O_SENTINEL}],
            "file_size": 5,
        }

    _patch_org(monkeypatch)
    monkeypatch.setattr(DocumentParser, "parse_local_file", _parse)

    result = await chat_api.chat_parse_upload(
        file=_upload(), current_user=SimpleNamespace(id=1), db=SimpleNamespace()
    )
    data = result["data"]

    assert data["content"] == E_SENTINEL
    assert data["text_provenance"] == LEGACY_UNVERIFIED
    assert data["original_chunk_count"] == 0
    assert data["legacy_chunk_count"] == 1


@pytest.mark.asyncio
async def test_parse_upload_blank_original_with_marker_is_legacy(monkeypatch):
    async def _parse(_self, _path, organization_id):
        return {
            "chunks": [
                {
                    "chunk_text": E_SENTINEL,
                    "original_text": "   ",
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

    assert data["content"] == E_SENTINEL
    assert data["text_provenance"] == LEGACY_UNVERIFIED
    assert data["original_chunk_count"] == 0
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


@pytest.mark.asyncio
async def test_workflow_strict_mode_skips_unattested_legacy(monkeypatch):
    """strict 下即使上游漏过滤，节点也不得把未验证文本送进 LLM。"""
    monkeypatch.setattr(settings, "RAG_SOURCE_PROVENANCE_MODE", "strict")

    async def _search(self, query, organization_id, top_k=5, **kwargs):
        return [
            {
                "title": "合同",
                "score": 0.9,
                "content": E_SENTINEL,
                "original_text": O_SENTINEL,
                "text_provenance": PARSER_ORIGINAL,
            },
            {
                "title": "旧文档",
                "score": 0.8,
                "content": "E-LEGACY",
                "text_provenance": LEGACY_UNVERIFIED,
                "prompt_text": "",
            },
        ], None

    monkeypatch.setattr(RagServiceFacade, "search_knowledge_base", _search)

    executor = KnowledgeSearchNodeExecutor(_workflow_node())
    executor.organization_id = 3
    result = await executor.execute(_workflow_state())

    context = result["context"]["knowledge_context"]
    assert O_SENTINEL in context
    assert "E-LEGACY" not in context


# ── 3. compare_documents：预览优先原文，legacy 显式标记，strict 拒绝 ─────────


def _patch_es(monkeypatch, sources: list[dict]):
    from app.core.elasticsearch import ElasticsearchTools

    async def _search(_query):
        return {"hits": {"hits": [{"_source": source} for source in sources]}}

    monkeypatch.setattr(ElasticsearchTools, "search_documents", _search)


def _patch_es_by_doc(monkeypatch, by_doc: dict[str, list[dict]], failing: set[str] | None = None):
    from app.core.elasticsearch import ElasticsearchTools

    failing = failing or set()

    async def _search(query):
        doc_id = query["query"]["bool"]["must"][0]["term"]["document_id"]
        if doc_id in failing:
            raise RuntimeError("es down")
        hits = [{"_source": source} for source in by_doc.get(doc_id, [])]
        return {"hits": {"hits": hits}}

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
    assert "Sampled hits: 1" in out
    assert "Legacy hits ignored: 0" in out


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
    # 复用既有 ToolErrorType 契约；机器可读的 provenance 状态放在 raw
    assert out.error.code == "invalid_configuration"
    assert out.error.raw["code"] == "source_original_unavailable"
    assert out.error.raw["status"] == "needs_rebuild"
    assert out.error.raw["document_ids"] == ["doc-1", "doc-2"]


@pytest.mark.asyncio
async def test_compare_documents_mixed_hits_label_actual_content(monkeypatch):
    """同文档混合命中：预览只用 O；标签按实际返回内容，丢弃的 legacy 数量单列。"""
    _patch_es_by_doc(
        monkeypatch,
        {
            "doc-1": [
                {
                    "filename": "a.txt",
                    "chunk_text": E_SENTINEL,
                    "original_text": O_SENTINEL,
                    "text_provenance": PARSER_ORIGINAL,
                },
                {"filename": "a.txt", "chunk_text": "LEGACY-HIT"},
            ],
            "doc-2": [
                {
                    "filename": "b.txt",
                    "chunk_text": E_SENTINEL,
                    "original_text": O_SENTINEL,
                    "text_provenance": PARSER_ORIGINAL,
                }
            ],
        },
    )

    out = await compare_documents(["doc-1", "doc-2"], organization_id=1)

    assert isinstance(out, str)
    assert "O-SENTINEL 原文" in out
    assert "E-SENTINEL" not in out
    assert "LEGACY-HIT" not in out
    assert "Provenance: parser_original" in out
    # doc-2 全原文也会提供 parser_original——必须直接排除 mixed 标签，
    # 才能钉住 doc-1（混合命中）的标注语义（评审 B 的 M4d-oldlabel 变异）。
    assert "Provenance: mixed" not in out
    assert "Legacy hits ignored: 1" in out


@pytest.mark.asyncio
async def test_compare_documents_strict_partial_missing_original(monkeypatch):
    """strict：只要任一文档缺原文就拒绝（不是「全部缺失」才拒绝）。"""
    monkeypatch.setattr(settings, "RAG_SOURCE_PROVENANCE_MODE", "strict")
    _patch_es_by_doc(
        monkeypatch,
        {
            "doc-1": [
                {
                    "filename": "a.txt",
                    "chunk_text": E_SENTINEL,
                    "original_text": O_SENTINEL,
                    "text_provenance": PARSER_ORIGINAL,
                }
            ],
            "doc-2": [{"filename": "old.txt", "chunk_text": "LEGACY-CONTENT"}],
        },
    )

    out = await compare_documents(["doc-1", "doc-2"], organization_id=1)

    assert not isinstance(out, str)
    assert out.error.code == "invalid_configuration"
    assert out.error.raw["document_ids"] == ["doc-2"]
    assert "doc-2" in out.error.message


@pytest.mark.asyncio
async def test_compare_documents_strict_query_failure_is_structured(monkeypatch):
    """strict：ES 查询异常的文档也必须进入结构化失败，不得静默返回成功字符串。"""
    monkeypatch.setattr(settings, "RAG_SOURCE_PROVENANCE_MODE", "strict")
    _patch_es_by_doc(
        monkeypatch,
        {
            "doc-2": [
                {
                    "filename": "a.txt",
                    "chunk_text": E_SENTINEL,
                    "original_text": O_SENTINEL,
                    "text_provenance": PARSER_ORIGINAL,
                }
            ]
        },
        failing={"doc-1"},
    )

    out = await compare_documents(["doc-1", "doc-2"], organization_id=1)

    assert not isinstance(out, str)
    assert out.error.code == "invalid_configuration"
    assert out.error.raw["query_failed_document_ids"] == ["doc-1"]


@pytest.mark.asyncio
async def test_compare_documents_marker_gate_blocks_unattested_original(monkeypatch):
    """有 original_text 但无 provenance 标记：不得当作原文（marker gate）。"""
    monkeypatch.setattr(settings, "RAG_SOURCE_PROVENANCE_MODE", "compat")
    _patch_es(
        monkeypatch,
        [{"filename": "a.txt", "chunk_text": E_SENTINEL, "original_text": O_SENTINEL}],
    )

    out = await compare_documents(["doc-1", "doc-2"], organization_id=1)

    assert isinstance(out, str)
    assert "[legacy_unverified]" in out
    assert "Provenance: legacy_unverified" in out
    assert "Provenance: parser_original" not in out
