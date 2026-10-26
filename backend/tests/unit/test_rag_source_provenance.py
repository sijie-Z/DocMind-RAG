"""RAG 原文溯源：PR1 双正文持久化 + PR2 检索/证据与 cache 分离。"""

from __future__ import annotations

import hashlib
import importlib.util
import sqlite3
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

import app.worker.doc_processor as dp_mod
from alembic import command
from app.core.config import settings
from app.core.elasticsearch import (
    IndexConfigError,
    _holder,
    _knowledge_index_mapping,
    _source_provenance_mapping_properties,
)
from app.models.document import DocumentChunk
from app.rag.cache import RetrievalCache, SemanticCache
from app.rag.context_compressor import get_prompt_text
from app.rag.pipeline import RAGPipeline
from app.rag.reranker import _hit_passage
from app.rag.retriever import HybridRetriever, _keyword_arm_terms
from app.rag.source_provenance import (
    COMPAT,
    LEGACY_UNVERIFIED,
    PARSER_ORIGINAL,
    STRICT,
    SourceProvenanceUnavailableError,
    filter_original_results,
    normalize_source_metadata,
)
from app.services.chat_service import (
    _cached_answer_provenance_blocked,
    _provenance_error_event,
    build_sources_list,
)
from app.services.clause_refs import extract_clause_fields
from app.worker.doc_processor import DocumentProcessor

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PREV_REV = "017_agent_message_task_id"
NEW_REV = "018_rag_source_provenance"
ORIGINAL = "第3条 数据分级\n审计日志保留期限为五年。"
ENHANCED = "第9条 文档背景：本制度由银河公司于2099年颁布，保留期限为十年。\n\n" + ORIGINAL
VECTOR = [0.1, 0.2]


def _doc():
    return SimpleNamespace(
        id="doc-1",
        organization_id=7,
        filename="policy.md",
        file_path="policy.md",
        file_type="md",
        file_size=123,
        created_at=None,
        uploaded_by=1,
    )


def _chunk(*, include_original=True):
    chunk = {
        "chunk_text": ENHANCED,
        "chunk_index": 0,
        "chunk_length": len(ENHANCED),
        "metadata": {
            "type": "contextual_semantic_split",
            "has_summary": True,
            "section_title": "第3条 数据分级",
        },
    }
    if include_original:
        chunk["original_text"] = ORIGINAL
    return chunk


class _FakeSession:
    def __init__(self, captured):
        self.captured = captured

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    async def execute(self, *_args, **_kwargs):
        return None

    def add_all(self, rows):
        self.captured["rows"] = list(rows)

    async def commit(self):
        return None


class _FakeES:
    def __init__(self):
        self.indices = self

    async def delete_by_query(self, **_kwargs):
        return {"deleted": 0, "failures": []}

    async def count(self, **_kwargs):
        return {"count": 0}


async def _replace_and_capture(monkeypatch, chunk):
    captured = {}
    session = _FakeSession(captured)
    es = _FakeES()
    monkeypatch.setattr(dp_mod, "AsyncSessionLocal", lambda: session)
    monkeypatch.setattr(dp_mod, "create_index_if_not_exists", AsyncMock())
    monkeypatch.setattr(dp_mod, "get_elasticsearch", AsyncMock(return_value=es))
    monkeypatch.setattr(dp_mod, "_verify_indexed", AsyncMock())
    monkeypatch.setattr(RetrievalCache, "clear_for_org", AsyncMock())
    monkeypatch.setattr(SemanticCache, "clear_for_org", AsyncMock())

    async def _bulk(actions):
        captured["actions"] = list(actions)

    monkeypatch.setattr(
        dp_mod.ElasticsearchTools,
        "bulk_index_documents",
        staticmethod(_bulk),
    )
    await DocumentProcessor()._replace_document_state(_doc(), [chunk], [VECTOR])
    return captured


@pytest.mark.asyncio
async def test_db_and_es_preserve_real_original_and_retrieval_text(monkeypatch):
    chunk = _chunk()
    metadata_before = deepcopy(chunk["metadata"])
    captured = await _replace_and_capture(monkeypatch, chunk)

    row = captured["rows"][0]
    source = captured["actions"][0]["_source"]
    assert isinstance(row, DocumentChunk)
    assert row.chunk_text == ENHANCED
    assert row.original_text == ORIGINAL
    assert row.chunk_length == len(ENHANCED)
    assert row.meta_data["text_provenance"] == "parser_original"
    assert row.meta_data["has_summary"] is True

    assert source["content"] == ENHANCED
    assert source["chunk_text"] == ENHANCED
    assert source["original_text"] == ORIGINAL
    assert source["text_provenance"] == "parser_original"
    assert source["embedding"] == VECTOR
    assert source["clause_no"] == extract_clause_fields(ORIGINAL)["clause_no"]
    assert chunk["metadata"] == metadata_before, "不得就地改写 parser metadata"


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [None, "", "   "])
async def test_missing_original_is_legacy_and_never_copies_retrieval_text(monkeypatch, missing):
    chunk = _chunk(include_original=False)
    chunk["original_text"] = missing
    captured = await _replace_and_capture(monkeypatch, chunk)

    row = captured["rows"][0]
    source = captured["actions"][0]["_source"]
    assert row.chunk_text == ENHANCED
    assert row.original_text is None
    assert row.meta_data["text_provenance"] == "legacy_unverified"
    assert source["content"] == ENHANCED
    assert source["chunk_text"] == ENHANCED
    assert source["text_provenance"] == "legacy_unverified"
    assert "original_text" not in source, "缺 O 时不得把 E 写成 original"
    # 旧条款 fallback 仍可使用 E，但它与 original 持久化是两条独立路径。
    assert source["clause_no"] == extract_clause_fields(ENHANCED)["clause_no"]


@pytest.mark.asyncio
async def test_process_still_embeds_retrieval_text(monkeypatch):
    processor = DocumentProcessor()
    doc = _doc()
    monkeypatch.setattr(processor, "_acquire_processing_lock", AsyncMock(return_value=None))
    monkeypatch.setattr(processor, "_load_and_mark_parsing", AsyncMock(return_value=doc))
    monkeypatch.setattr(
        processor.parser,
        "parse_document",
        AsyncMock(return_value={"chunks": [_chunk()]}),
    )
    replace = AsyncMock()
    monkeypatch.setattr(processor, "_replace_document_state", replace)
    monkeypatch.setattr(processor, "_mark_success", AsyncMock())
    monkeypatch.setattr(processor, "_extract_graph_safely", AsyncMock())
    embedded = []

    async def _embed(texts):
        embedded.extend(texts)
        return [VECTOR for _ in texts]

    monkeypatch.setattr(processor, "_get_embeddings", _embed)

    assert await processor.process(doc.id, job_id=1) is True
    assert embedded == [ENHANCED]
    replace.assert_awaited_once()


def test_mapping_is_additive_and_retrieval_fields_are_unchanged():
    mappings = _knowledge_index_mapping()["mappings"]
    props = mappings["properties"]

    assert props["original_text"] == {"type": "text", "index": False}
    assert props["text_provenance"] == {"type": "keyword"}
    assert set(_source_provenance_mapping_properties()) == {
        "original_text",
        "text_provenance",
    }
    assert props["content"] == {
        "type": "text",
        "analyzer": _holder.es_analyzer,
        "search_analyzer": _holder.es_search_analyzer,
    }
    assert props["chunk_text"] == props["content"]
    assert props["embedding"] == {
        "type": "dense_vector",
        "dims": settings.VECTOR_DIMENSION,
        "index": True,
        "similarity": "cosine",
    }
    assert mappings["_meta"]["vector_dimension"] == settings.VECTOR_DIMENSION
    assert DocumentChunk.__table__.c.original_text.nullable is True


class _FakeMappingIndices:
    def __init__(self, properties, *, exists=True):
        self.properties = deepcopy(properties)
        self.exists_value = exists
        self.put_calls = []
        self.get_calls = 0

    async def exists(self, *, index):
        assert index == "knowledge-test"
        return self.exists_value

    async def get_mapping(self, *, index):
        assert index == "knowledge-test"
        self.get_calls += 1
        return {
            index: {
                "mappings": {
                    "properties": deepcopy(self.properties),
                }
            }
        }

    async def put_mapping(self, *, index, properties):
        assert index == "knowledge-test"
        self.put_calls.append(deepcopy(properties))
        self.properties.update(deepcopy(properties))
        return {"acknowledged": True}


class _FakeMappingClient:
    def __init__(self, properties, *, exists=True):
        self.indices = _FakeMappingIndices(properties, exists=exists)


def _mapping_script():
    path = BACKEND_ROOT / "scripts" / "add_rag_source_provenance_mapping.py"
    spec = importlib.util.spec_from_file_location("add_rag_source_provenance_mapping", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _legacy_mapping_properties():
    return {
        "content": {"type": "text", "analyzer": "cjk", "search_analyzer": "cjk"},
        "chunk_text": {"type": "text", "analyzer": "cjk", "search_analyzer": "cjk"},
        "embedding": {"type": "dense_vector", "dims": 2, "index": True},
    }


@pytest.mark.asyncio
async def test_existing_index_mapping_is_additive_idempotent_and_read_back():
    client = _FakeMappingClient(_legacy_mapping_properties())
    before = deepcopy(client.indices.properties)
    script = _mapping_script()

    added = await script.add_source_provenance_mapping(client, "knowledge-test")

    assert added == ["original_text", "text_provenance"]
    assert len(client.indices.put_calls) == 1
    assert client.indices.put_calls[0] == _source_provenance_mapping_properties()
    for field, definition in before.items():
        assert client.indices.properties[field] == definition

    assert await script.add_source_provenance_mapping(client, "knowledge-test") == []
    assert len(client.indices.put_calls) == 1, "第二次执行不得重复写 mapping"


@pytest.mark.asyncio
async def test_mapping_script_refuses_missing_index_or_conflicting_field():
    script = _mapping_script()
    missing = _FakeMappingClient(_legacy_mapping_properties(), exists=False)
    with pytest.raises(IndexConfigError, match="不会创建索引"):
        await script.add_source_provenance_mapping(missing, "knowledge-test")
    assert missing.indices.get_calls == 0
    assert missing.indices.put_calls == []

    properties = _legacy_mapping_properties()
    properties["original_text"] = {"type": "text", "index": True}
    conflicting = _FakeMappingClient(properties)
    with pytest.raises(IndexConfigError, match="不覆盖既有 mapping"):
        await script.add_source_provenance_mapping(conflicting, "knowledge-test")
    assert conflicting.indices.put_calls == []


def _cfg() -> Config:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return cfg


def _columns(db_path: Path) -> dict:
    with sqlite3.connect(str(db_path)) as conn:
        return {row[1]: row for row in conn.execute("PRAGMA table_info(document_chunks)")}


def _constraints(db_path: Path) -> dict:
    """Capture constraint semantics without SQLite's non-semantic enumeration order."""
    with sqlite3.connect(str(db_path)) as conn:
        # 丢弃 FK id（重建表时可变）；保留 table/from/to/actions/match 以及
        # 组合外键内部的 seq。set 只消除不同约束之间的枚举顺序。
        foreign_keys = {
            (row[2], row[3], row[4], row[5], row[6], row[7], row[1])
            for row in conn.execute("PRAGMA foreign_key_list(document_chunks)")
        }
        indexes = {}
        for row in conn.execute("PRAGMA index_list(document_chunks)"):
            _seq, name, unique, origin, partial = row
            # 索引之间的 seq 非语义；组合索引内部的列顺序仍是语义，必须保留。
            columns = tuple(
                conn.execute(
                    "SELECT cid, name FROM pragma_index_info(?) ORDER BY seqno",
                    (name,),
                )
            )
            indexes[name] = {
                "unique": unique,
                "origin": origin,
                "partial": partial,
                "columns": columns,
            }
    return {"foreign_keys": foreign_keys, "indexes": indexes}


def _legacy_chunk_snapshot(db_path: Path):
    with sqlite3.connect(str(db_path)) as conn:
        return conn.execute(
            "SELECT id, document_id, chunk_index, chunk_text, chunk_length, "
            "start_pos, end_pos, page_number, section_title, embedding_id, "
            "meta_data, created_at FROM document_chunks WHERE id = 'legacy-chunk'"
        ).fetchone()


def test_018_migration_single_head_nullable_data_and_constraints_roundtrip(tmp_path, monkeypatch):
    db_path = tmp_path / "rag-source-provenance-018.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = _cfg()
    script = ScriptDirectory.from_config(cfg)

    assert len(NEW_REV) <= 32
    assert set(script.get_heads()) == {NEW_REV}
    chain = [rev.revision for rev in script.iterate_revisions(NEW_REV, "base")]
    assert PREV_REV in chain

    command.upgrade(cfg, PREV_REV)
    assert "original_text" not in _columns(db_path)
    constraints = _constraints(db_path)
    assert constraints["foreign_keys"], "外键快照不能为空，否则无序比较会假绿"
    assert {fk[0] for fk in constraints["foreign_keys"]} == {"documents"}
    assert len(constraints["indexes"]) == 3
    assert all(definition["columns"] for definition in constraints["indexes"].values())
    assert any(
        definition["origin"] == "pk" and definition["unique"] == 1
        for definition in constraints["indexes"].values()
    )
    indexed_columns = {
        column_name
        for definition in constraints["indexes"].values()
        for _cid, column_name in definition["columns"]
    }
    assert {"id", "document_id", "embedding_id"} <= indexed_columns
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO document_chunks "
            "(id, document_id, chunk_index, chunk_text, chunk_length, meta_data) "
            "VALUES ('legacy-chunk', 'legacy-doc', 0, ?, ?, ?)",
            (ENHANCED, len(ENHANCED), '{"has_summary":true}'),
        )
        conn.commit()
    before = _legacy_chunk_snapshot(db_path)

    command.upgrade(cfg, "head")
    columns = _columns(db_path)
    assert columns["original_text"][2].upper() == "TEXT"
    assert columns["original_text"][3] == 0, "original_text 必须 nullable"
    with sqlite3.connect(str(db_path)) as conn:
        assert conn.execute(
            "SELECT original_text FROM document_chunks WHERE id = 'legacy-chunk'"
        ).fetchone() == (None,)
    assert _legacy_chunk_snapshot(db_path) == before
    assert _constraints(db_path) == constraints

    command.downgrade(cfg, PREV_REV)
    assert "original_text" not in _columns(db_path)
    assert _legacy_chunk_snapshot(db_path) == before
    assert _constraints(db_path) == constraints

    command.upgrade(cfg, "head")
    assert "original_text" in _columns(db_path)
    assert _legacy_chunk_snapshot(db_path) == before
    assert _constraints(db_path) == constraints


# ── PR2：E 只检索，O 只作证据；legacy/cache 必须诚实分离 ──────────────────

S2_QUERY = "审计日志保留期限"


def _s2_hit(*, original=True, chunk_id=""):
    source = {
        "content": ENHANCED,
        "filename": "policy.md",
        "document_id": "doc-1",
    }
    if original:
        source.update(
            {
                "original_text": ORIGINAL,
                "text_provenance": PARSER_ORIGINAL,
            }
        )
    return {
        "_id": chunk_id,
        "_score": 1.0,
        "has_keyword": True,
        "has_vector": True,
        "_source": source,
    }


def _s2_result(*, original=True):
    return HybridRetriever()._post_process(S2_QUERY, [_s2_hit(original=original)], top_k=1)[0]


def _s2_filter(results, *, mode):
    return filter_original_results(
        results,
        mode=mode,
        query=S2_QUERY,
        snippet_builder=HybridRetriever._build_snippet,
    )


class _S2FakeCompletions:
    def __init__(self, captured):
        self.captured = captured

    async def create(self, **kwargs):
        self.captured.append(kwargs)

        async def _stream():
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="回答"))])

        return _stream()


class _S2FakeClient:
    def __init__(self, captured):
        self.chat = SimpleNamespace(completions=_S2FakeCompletions(captured))

    def with_options(self, **_kwargs):
        return self


def test_s2_dual_fields_keep_scoring_on_e_and_prompt_snippet_on_o(monkeypatch):
    monkeypatch.setattr(settings, "RAG_SOURCE_PROVENANCE_MODE", COMPAT)
    result = _s2_result()

    assert result["retrieval_text"] == ENHANCED
    assert result["content"] == ENHANCED
    assert result["text"] == ENHANCED
    assert result["original_text"] == ORIGINAL
    assert result["text_provenance"] == PARSER_ORIGINAL
    assert result["prompt_text"] == ORIGINAL
    assert result["snippet"] == HybridRetriever._build_snippet(ORIGINAL, S2_QUERY)
    assert "银河公司" not in result["snippet"]
    assert "五年" in result["snippet"]

    expected_hash = hashlib.sha1(ENHANCED.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]
    assert result["chunk_id"] == f"doc-1:{expected_hash}"
    expected_overlap = HybridRetriever._compute_overlap(
        f"policy.md {ENHANCED[:1600]}", _keyword_arm_terms(S2_QUERY)
    )
    assert result["overlap_score"] == pytest.approx(expected_overlap)
    assert _hit_passage(result) == f"文件:policy.md\n{ENHANCED}"


def test_s2_legacy_compat_and_strict_differ_only_in_evidence_not_score(monkeypatch):
    monkeypatch.setattr(settings, "RAG_SOURCE_PROVENANCE_MODE", COMPAT)
    compat = _s2_result(original=False)
    monkeypatch.setattr(settings, "RAG_SOURCE_PROVENANCE_MODE", STRICT)
    strict = _s2_result(original=False)

    for key in (
        "score",
        "rank_score",
        "raw_score",
        "overlap_score",
        "chunk_id",
        "has_keyword",
        "has_vector",
        "fresh_factor",
    ):
        assert strict[key] == compat[key]
    assert compat["original_text"] is None
    assert compat["text_provenance"] == LEGACY_UNVERIFIED
    assert compat["prompt_text"] == ENHANCED
    assert "银河公司" in compat["snippet"]

    assert strict["original_text"] is None
    assert strict["text_provenance"] == LEGACY_UNVERIFIED
    assert strict["prompt_text"] == ""
    assert strict["snippet"] == ""
    assert strict["retrieval_text"] == ENHANCED


def test_s2_strict_gate_filters_or_raises_never_returns_fake_empty():
    clean = _s2_result()
    legacy = _s2_result(original=False)

    assert _s2_filter([clean, legacy], mode=STRICT) == [clean]
    assert _s2_filter([], mode=STRICT) == []
    assert _s2_filter([legacy], mode=COMPAT) == [legacy]

    with pytest.raises(SourceProvenanceUnavailableError) as exc_info:
        _s2_filter([legacy], mode=STRICT)
    error = exc_info.value
    assert error.code == "source_original_unavailable"
    assert error.status == "needs_rebuild"
    assert error.document_ids == ["doc-1"]
    assert error.unverified_count == 1

    historical = normalize_source_metadata({"documentId": "doc-old", "content": "mixed"})
    assert historical["textProvenance"] == LEGACY_UNVERIFIED


def test_s2_chat_sources_use_original_and_mark_legacy(monkeypatch):
    clean = _s2_result()
    legacy = _s2_result(original=False)

    monkeypatch.setattr(settings, "RAG_SOURCE_PROVENANCE_MODE", COMPAT)
    clean_source = build_sources_list([clean], query=S2_QUERY)[0]
    assert clean_source["content"] == clean["snippet"]
    assert clean_source["textProvenance"] == PARSER_ORIGINAL
    assert clean_source["chunkId"] == clean["chunk_id"]
    assert "银河公司" not in clean_source["content"]

    legacy_source = build_sources_list([legacy], query=S2_QUERY)[0]
    assert legacy_source["textProvenance"] == LEGACY_UNVERIFIED
    assert "银河公司" in legacy_source["content"]

    monkeypatch.setattr(settings, "RAG_SOURCE_PROVENANCE_MODE", STRICT)
    assert build_sources_list([legacy], query=S2_QUERY)[0]["content"] == ""


@pytest.mark.asyncio
async def test_s2_chat_prompt_contains_original_not_generated_background(monkeypatch):
    monkeypatch.setattr(settings, "RAG_SOURCE_PROVENANCE_MODE", COMPAT)
    captured = []
    pipeline = RAGPipeline(openai_client=_S2FakeClient(captured))
    _ = [
        chunk
        async for chunk in pipeline.chat_stream(
            S2_QUERY,
            [_s2_result()],
            enable_compression=False,
            enable_masking=False,
        )
    ]

    prompt = captured[0]["messages"][-1]["content"]
    assert ORIGINAL in prompt
    assert "银河公司" not in prompt
    assert "2099" not in prompt


@pytest.mark.asyncio
async def test_s2_direct_chat_stream_cannot_bypass_strict(monkeypatch):
    monkeypatch.setattr(settings, "RAG_SOURCE_PROVENANCE_MODE", STRICT)
    captured = []
    pipeline = RAGPipeline(openai_client=_S2FakeClient(captured))

    with pytest.raises(SourceProvenanceUnavailableError):
        _ = [
            chunk
            async for chunk in pipeline.chat_stream(
                S2_QUERY,
                [_s2_result(original=False)],
                enable_compression=False,
                enable_masking=False,
            )
        ]
    assert captured == []


@pytest.mark.asyncio
async def test_s2_exact_cache_strict_gate_cannot_be_bypassed(monkeypatch):
    monkeypatch.setattr(settings, "RAG_SOURCE_PROVENANCE_MODE", STRICT)
    pipeline = RAGPipeline()
    legacy = _s2_result(original=False)
    clean = _s2_result()
    pipeline.cache = SimpleNamespace(
        get=AsyncMock(return_value=[legacy, clean]),
        set=AsyncMock(),
    )
    pipeline.retriever = MagicMock()
    pipeline.retriever.retrieve = AsyncMock()

    results, _ = await pipeline.search_knowledge_base(S2_QUERY, 1, top_k=2)

    assert results == [clean]
    pipeline.retriever.retrieve.assert_not_called()

    pipeline.cache.get = AsyncMock(return_value=[legacy])
    with pytest.raises(SourceProvenanceUnavailableError):
        await pipeline.search_knowledge_base(S2_QUERY, 1, top_k=1)
    pipeline.cache.set.assert_not_called()


@pytest.mark.asyncio
async def test_s2_semantic_cache_strict_gate_cannot_be_bypassed(monkeypatch):
    monkeypatch.setattr(settings, "RAG_SOURCE_PROVENANCE_MODE", STRICT)
    pipeline = RAGPipeline()
    legacy = _s2_result(original=False)
    pipeline.cache = SimpleNamespace(get=AsyncMock(return_value=None), set=AsyncMock())
    pipeline.semantic_cache = SimpleNamespace(
        enabled=True,
        get=AsyncMock(return_value={"sources": [legacy]}),
        set=AsyncMock(),
    )
    pipeline.get_embedding = AsyncMock(return_value=[0.1, 0.2])
    pipeline.retriever = MagicMock()
    pipeline.retriever.retrieve = AsyncMock()

    with pytest.raises(SourceProvenanceUnavailableError):
        await pipeline.search_knowledge_base(S2_QUERY, 1, top_k=1)

    pipeline.cache.set.assert_not_called()
    pipeline.semantic_cache.set.assert_not_called()
    pipeline.retriever.retrieve.assert_not_called()


@pytest.mark.asyncio
async def test_s2_fresh_results_strict_raises_needs_rebuild_not_normal_empty(monkeypatch):
    monkeypatch.setattr(settings, "RAG_SOURCE_PROVENANCE_MODE", STRICT)
    monkeypatch.setattr(settings, "RAG_ENABLE_RERANKER", False)
    pipeline = RAGPipeline()
    legacy = _s2_result(original=False)
    pipeline.retriever.retrieve = AsyncMock(return_value=([legacy], [], None, []))

    with pytest.raises(SourceProvenanceUnavailableError) as exc_info:
        await pipeline.search_knowledge_base(
            S2_QUERY,
            1,
            top_k=1,
            document_ids=["doc-1"],
            search_mode="hybrid",
        )
    assert exc_info.value.status == "needs_rebuild"


def test_s2_answer_cache_strict_is_always_disabled(monkeypatch):
    legacy_source = normalize_source_metadata({"documentId": "doc-old", "content": "mixed"})
    original_source = normalize_source_metadata(
        {"documentId": "doc-new", "textProvenance": PARSER_ORIGINAL}
    )

    monkeypatch.setattr(settings, "RAG_SOURCE_PROVENANCE_MODE", STRICT)
    assert _cached_answer_provenance_blocked({"sources": [legacy_source]}) is True
    assert _cached_answer_provenance_blocked({"sources": [legacy_source, original_source]})
    assert _cached_answer_provenance_blocked({"sources": [original_source]}) is True
    assert _cached_answer_provenance_blocked({"sources": []}) is True

    monkeypatch.setattr(settings, "RAG_SOURCE_PROVENANCE_MODE", COMPAT)
    assert _cached_answer_provenance_blocked({"sources": [legacy_source]}) is False


def test_s2_provenance_error_is_structured_needs_rebuild_event():
    error = SourceProvenanceUnavailableError(["doc-2", "doc-1"], unverified_count=2)
    event = _provenance_error_event(error, "conversation-1")

    assert event.type == "error"
    assert event.conversation_id == "conversation-1"
    assert "需要重建" in event.content
    assert event.debug_data == {
        "code": "source_original_unavailable",
        "status": "needs_rebuild",
        "document_ids": ["doc-1", "doc-2"],
        "unverified_count": 2,
    }


# ── R2：预填证据/raw cache 不可信，模式切换不得共享结果子集 ─────────────


def _s2_corrupt_original(prompt_state):
    result = _s2_result()
    if prompt_state == "missing":
        result.pop("prompt_text", None)
    elif prompt_state == "empty":
        result["prompt_text"] = ""
    else:
        result["prompt_text"] = ENHANCED
    result["snippet"] = ENHANCED
    return result


@pytest.mark.parametrize("prompt_state", ["missing", "empty", "enhanced"])
def test_s2_r2_get_prompt_text_never_falls_back_to_e_for_original(prompt_state):
    item = _s2_corrupt_original(prompt_state)

    assert get_prompt_text(item) == ORIGINAL

    item["original_text"] = None
    assert get_prompt_text(item) == "", "original marker 缺 O 时也不得回退 E"


@pytest.mark.asyncio
@pytest.mark.parametrize("prompt_state", ["missing", "empty", "enhanced"])
async def test_s2_r2_direct_chat_stream_rebuilds_prompt_from_original(monkeypatch, prompt_state):
    monkeypatch.setattr(settings, "RAG_SOURCE_PROVENANCE_MODE", STRICT)
    captured = []
    pipeline = RAGPipeline(openai_client=_S2FakeClient(captured))

    _ = [
        chunk
        async for chunk in pipeline.chat_stream(
            S2_QUERY,
            [_s2_corrupt_original(prompt_state)],
            enable_compression=False,
            enable_masking=False,
        )
    ]

    prompt = captured[0]["messages"][-1]["content"]
    assert ORIGINAL in prompt
    assert "银河公司" not in prompt
    assert "2099" not in prompt


def test_s2_r2_source_snippet_is_recomputed_from_original_with_same_query(monkeypatch):
    monkeypatch.setattr(settings, "RAG_SOURCE_PROVENANCE_MODE", STRICT)
    source = build_sources_list([_s2_corrupt_original("enhanced")], query=S2_QUERY)[0]

    assert source["content"] == HybridRetriever._build_snippet(ORIGINAL, S2_QUERY)
    assert source["textProvenance"] == PARSER_ORIGINAL
    assert "银河公司" not in source["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize("cache_kind", ["exact", "semantic"])
async def test_s2_r2_raw_cache_is_normalized_before_evidence_use(monkeypatch, cache_kind):
    monkeypatch.setattr(settings, "RAG_SOURCE_PROVENANCE_MODE", STRICT)
    pipeline = RAGPipeline()
    corrupted = _s2_corrupt_original("enhanced")
    pipeline.retriever = MagicMock()
    pipeline.retriever.retrieve = AsyncMock()

    if cache_kind == "exact":
        pipeline.cache = SimpleNamespace(
            get=AsyncMock(return_value=[corrupted]),
            set=AsyncMock(),
        )
    else:
        pipeline.cache = SimpleNamespace(get=AsyncMock(return_value=None), set=AsyncMock())
        pipeline.semantic_cache = SimpleNamespace(
            enabled=True,
            get=AsyncMock(return_value={"sources": [corrupted]}),
            set=AsyncMock(),
        )
        pipeline.get_embedding = AsyncMock(return_value=[0.1, 0.2])

    results, _ = await pipeline.search_knowledge_base(S2_QUERY, 1, top_k=1)

    assert len(results) == 1
    assert results[0]["prompt_text"] == ORIGINAL
    assert results[0]["snippet"] == HybridRetriever._build_snippet(ORIGINAL, S2_QUERY)
    assert results[0]["retrieval_text"] == ENHANCED
    assert "银河公司" not in results[0]["snippet"]
    pipeline.retriever.retrieve.assert_not_called()


@pytest.mark.asyncio
async def test_s2_r2_strict_compat_exact_cache_roundtrip_preserves_full_candidates(monkeypatch):
    monkeypatch.setattr("app.rag.cache._get_redis", lambda: None)
    monkeypatch.setattr(settings, "RAG_ENABLE_CACHE", True)
    monkeypatch.setattr(settings, "SEMANTIC_CACHE_RETRIEVAL_ENABLED", False)
    monkeypatch.setattr(settings, "RAG_ENABLE_RERANKER", False)
    pipeline = RAGPipeline()
    pipeline.cache = RetrievalCache()
    legacy = _s2_result(original=False)
    clean = _s2_result()
    legacy.update({"chunk_id": "legacy-chunk", "document_id": "doc-legacy"})
    clean.update({"chunk_id": "clean-chunk", "document_id": "doc-clean"})
    pipeline.retriever.retrieve = AsyncMock(return_value=([legacy, clean], [], None, []))
    query = f"{S2_QUERY} r2-mode-isolation-unique"

    monkeypatch.setattr(settings, "RAG_SOURCE_PROVENANCE_MODE", STRICT)
    strict_results, _ = await pipeline.search_knowledge_base(
        query, 987, top_k=5, search_mode="hybrid"
    )
    assert [item["chunk_id"] for item in strict_results] == ["clean-chunk"]
    assert pipeline.retriever.retrieve.await_count == 1

    pipeline.retriever.retrieve.reset_mock()
    monkeypatch.setattr(settings, "RAG_SOURCE_PROVENANCE_MODE", COMPAT)
    compat_results, _ = await pipeline.search_knowledge_base(
        query, 987, top_k=5, search_mode="hybrid"
    )
    assert {item["chunk_id"] for item in compat_results} == {
        "legacy-chunk",
        "clean-chunk",
    }
    assert pipeline.retriever.retrieve.await_count == 1, (
        "strict→compat 必须重新取得完整候选，不能命中 strict filtered 子集"
    )

    pipeline.retriever.retrieve.reset_mock()
    monkeypatch.setattr(settings, "RAG_SOURCE_PROVENANCE_MODE", STRICT)
    strict_again, _ = await pipeline.search_knowledge_base(
        query, 987, top_k=5, search_mode="hybrid"
    )
    assert [item["chunk_id"] for item in strict_again] == ["clean-chunk"]
    pipeline.retriever.retrieve.assert_not_called()

    monkeypatch.setattr(settings, "RAG_SOURCE_PROVENANCE_MODE", COMPAT)
    compat_again, _ = await pipeline.search_knowledge_base(
        query, 987, top_k=5, search_mode="hybrid"
    )
    assert {item["chunk_id"] for item in compat_again} == {
        "legacy-chunk",
        "clean-chunk",
    }
    pipeline.retriever.retrieve.assert_not_called()


def test_s2_r2_semantic_cache_namespace_includes_provenance_mode():
    compat_prefix, compat_index = SemanticCache.retrieval_namespace(COMPAT)
    strict_prefix, strict_index = SemanticCache.retrieval_namespace(STRICT)

    assert compat_prefix != strict_prefix
    assert compat_index != strict_index
    assert SemanticCache.for_retrieval(COMPAT).prefix == compat_prefix
    assert SemanticCache.for_retrieval(STRICT).prefix == strict_prefix
