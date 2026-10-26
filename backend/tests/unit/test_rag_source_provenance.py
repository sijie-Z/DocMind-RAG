"""PR1：只增加 original/retrieval 双正文持久化，不切换任何消费者。"""

from __future__ import annotations

import importlib.util
import sqlite3
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

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
