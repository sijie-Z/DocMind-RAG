"""Batch 14：原文 P2 消费方收口。

- GraphRAG 实体抽取（处理链 + 回填脚本）不得用检索增强文本 E 作为输入；
- 搜索联想优先原文派生片段；
- SQL 工具白名单提供 original_text，不再只能读 chunk_text；
- 旧解析写入路径补齐 original_text 与 provenance 标记（旧路径更严格：仅
  marker=parser_original 且非空字符串 O 才写列；worker 写入口按列非空判定，
  两者都满足「列非空 ⟹ 已证实」的不变式）。
"""

from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import sqlite

import app.services.document_parser as parser_mod
from app.agent.tools.code_execution import _is_safe_select_sql
from app.rag.source_provenance import LEGACY_UNVERIFIED, PARSER_ORIGINAL
from app.services.document_parser import DocumentParser
from app.services.graph_rag_service import GraphRAGService
from app.services.knowledge_service import KnowledgeService
from app.services.rag_service import RagServiceFacade
from app.worker.doc_processor import DocumentProcessor
from scripts.backfill_graph import fetch_document_text

O_SENTINEL = "O-SENTINEL 原文"
E_SENTINEL = f"【文档背景：E-SENTINEL 背景】\n\n{O_SENTINEL}"


# ── 1. GraphRAG 实体抽取：原文优先 ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_graph_extraction_prefers_original_text(monkeypatch):
    captured: dict = {}

    async def _ingest(self, *, document_id, organization_id=None, text, llm_client=None, **_kw):
        captured["text"] = text
        return {"status": "ok", "entities": 1}

    monkeypatch.setattr(GraphRAGService, "ingest_document", _ingest)
    monkeypatch.setattr(RagServiceFacade, "openai_client", property(lambda self: None))

    processor = DocumentProcessor()
    doc = SimpleNamespace(id="doc-1", organization_id=1)
    await processor._extract_graph_safely(
        doc,
        [
            {"chunk_text": E_SENTINEL, "original_text": O_SENTINEL},
            {"chunk_text": "LEGACY-CHUNK"},
        ],
    )

    assert O_SENTINEL in captured["text"]
    assert "E-SENTINEL" not in captured["text"]
    assert "LEGACY-CHUNK" in captured["text"]


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self):
        self.statement = None

    async def execute(self, stmt):
        self.statement = stmt
        # (chunk_text, original_text, meta_data)；只有 marker 证实且非空才用 O
        return _Rows(
            [
                (E_SENTINEL, O_SENTINEL, {"text_provenance": PARSER_ORIGINAL}),
                ("LEGACY-CHUNK", None, {"text_provenance": LEGACY_UNVERIFIED}),
                ("E-UNMARKED", "O-UNMARKED", {}),
                ("E-BLANK-O", "   ", {"text_provenance": PARSER_ORIGINAL}),
            ]
        )


@pytest.mark.asyncio
async def test_backfill_fetch_text_requires_attested_original():
    session = _FakeSession()
    text = await fetch_document_text(session, "doc-1")

    # SQL 本身必须同时取 original_text 与 meta_data（否则无法校验 marker），
    # 且列序固定为 (chunk_text, original_text, meta_data) —— 解包顺序依赖它
    compiled = str(session.statement.compile(dialect=sqlite.dialect()))
    assert "document_chunks.chunk_text" in compiled
    assert "document_chunks.original_text" in compiled
    assert "document_chunks.meta_data" in compiled
    assert (
        compiled.index("document_chunks.chunk_text")
        < compiled.index("document_chunks.original_text")
        < compiled.index("document_chunks.meta_data")
    )

    assert O_SENTINEL in text
    assert "E-SENTINEL" not in text
    assert "LEGACY-CHUNK" in text
    # 无 marker 的 original_text 与空白 O 都不得当作原文
    assert "O-UNMARKED" not in text
    assert "E-UNMARKED" in text
    assert "E-BLANK-O" in text


# ── 2. 搜索联想：走真实格式化层，优先 O 派生 content ───────────────────────


@pytest.mark.asyncio
async def test_search_suggestions_use_formatted_content(monkeypatch):
    """search_knowledge 的真实返回结构：content = O 派生 snippet，text = E。"""

    async def _search(self, query, organization_id, top_k=5, search_mode=None, **kwargs):
        return [
            {
                "document_id": "doc-1",
                "text": E_SENTINEL,
                "snippet": O_SENTINEL,
                "text_provenance": PARSER_ORIGINAL,
                "score": 1.0,
                "filename": "a.txt",
            },
            {
                "document_id": "doc-2",
                "text": "LEGACY-TEXT",
                "snippet": "",
                "text_provenance": LEGACY_UNVERIFIED,
                "score": 0.5,
                "filename": "b.txt",
            },
        ], None

    monkeypatch.setattr(RagServiceFacade, "search_knowledge_base", _search)

    suggestions = await KnowledgeService().get_search_suggestions("q", "1")

    assert suggestions[0] == O_SENTINEL[:50] + "..."
    assert "E-SENTINEL" not in suggestions[0]
    assert suggestions[1] == "LEGACY-TEXT..."


# ── 3. SQL 工具：白名单提供 original_text ───────────────────────────────────


def test_sql_whitelist_allows_original_text():
    ok, message = _is_safe_select_sql(
        "SELECT filename, original_text FROM documents "
        "JOIN document_chunks ON document_chunks.document_id = documents.id"
    )
    assert ok, message


def test_sql_whitelist_still_rejects_unknown_columns():
    ok, message = _is_safe_select_sql(
        "SELECT filename, no_such_col FROM documents "
        "JOIN document_chunks ON document_chunks.document_id = documents.id"
    )
    assert not ok
    assert "no_such_col" in message


# ── 4. 旧解析写入路径：补 original_text 与 provenance ──────────────────────


class _DocRow:
    status = None
    content_length = None
    chunk_count = None
    parsed_at = None
    parse_error = None
    file_path = "1/doc-1.txt"
    organization_id = 1


class _SingleResult:
    def __init__(self, doc):
        self._doc = doc

    def scalar_one_or_none(self):
        return self._doc


class _WriteSession:
    def __init__(self):
        self.added: list = []
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, *_args, **_kwargs):
        return _SingleResult(_DocRow())

    async def commit(self):
        self.committed = True

    async def rollback(self):
        return None

    def add(self, obj):
        self.added.append(obj)


@pytest.mark.asyncio
async def test_legacy_process_document_writes_original_and_provenance(monkeypatch):
    session = _WriteSession()
    monkeypatch.setattr(parser_mod, "AsyncSessionLocal", lambda: session)

    async def _parse(self, _path, _organization_id):
        return {
            "content_length": 10,
            "chunks": [
                {
                    "chunk_index": 0,
                    "chunk_text": E_SENTINEL,
                    "original_text": O_SENTINEL,
                    "text_provenance": PARSER_ORIGINAL,
                    "chunk_length": len(E_SENTINEL),
                    "start_pos": 0,
                    "end_pos": 10,
                    "metadata": {"page_number": 1},
                },
                {
                    "chunk_index": 1,
                    "chunk_text": "LEGACY-CHUNK",
                    "chunk_length": 12,
                    "start_pos": 10,
                    "end_pos": 22,
                    "metadata": {},
                },
                {
                    "chunk_index": 2,
                    "chunk_text": "E-UNMARKED",
                    "original_text": "O-UNMARKED",
                    "chunk_length": 10,
                    "start_pos": 22,
                    "end_pos": 32,
                    "metadata": {},
                },
                {
                    "chunk_index": 3,
                    "chunk_text": "E-BLANK-O",
                    "original_text": "   ",
                    "text_provenance": PARSER_ORIGINAL,
                    "chunk_length": 10,
                    "start_pos": 32,
                    "end_pos": 42,
                    "metadata": {},
                },
                {
                    "chunk_index": 4,
                    "chunk_text": "E-NONSTR-O",
                    "original_text": 123,
                    "text_provenance": PARSER_ORIGINAL,
                    "chunk_length": 10,
                    "start_pos": 42,
                    "end_pos": 52,
                    "metadata": {},
                },
            ],
        }

    monkeypatch.setattr(DocumentParser, "parse_document", _parse)

    ok = await DocumentParser().process_document("doc-1")
    assert ok is True
    assert session.committed is True

    attested, legacy, unmarked, blank_o, nonstr_o = session.added
    assert attested.original_text == O_SENTINEL
    assert attested.meta_data["text_provenance"] == PARSER_ORIGINAL
    # 「列非空 ⟹ 写入方已证实」不变式：未证实/空白/非字符串 O 一律不写列
    assert legacy.original_text is None
    assert legacy.meta_data["text_provenance"] == LEGACY_UNVERIFIED
    assert unmarked.original_text is None
    assert unmarked.meta_data["text_provenance"] == LEGACY_UNVERIFIED
    assert blank_o.original_text is None
    assert blank_o.meta_data["text_provenance"] == LEGACY_UNVERIFIED
    assert nonstr_o.original_text is None
    assert nonstr_o.meta_data["text_provenance"] == LEGACY_UNVERIFIED
