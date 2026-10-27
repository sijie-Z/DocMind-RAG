"""Rebuilds keep the last indexed version until ES replacement starts.

The endpoint and worker run against real SQLite. Elasticsearch is an in-memory fake
whose delete, bulk, search, and verification operations mutate or inspect real fake
state, so these tests assert retrievability rather than only mocking calls.
"""

import logging
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.api.v1.endpoints.knowledge as knowledge_mod
import app.services.knowledge_service as knowledge_service_mod
import app.worker.doc_processor as dp_mod
from app.core.database import Base
from app.models.document import Document, DocumentChunk, DocumentStatus, DocumentType
from app.models.knowledge_job import KnowledgeJobStatus, KnowledgeProcessingJob
from app.rag.cache import RetrievalCache, SemanticCache
from app.worker.doc_processor import DocumentProcessor

DOC_ID = "doc-rebuild-preserve"
OLD_TEXT = "old indexed version"
NEW_CHUNKS = [
    {"chunk_text": "new version first", "chunk_index": 0, "chunk_length": 17},
    {"chunk_text": "new version second", "chunk_index": 1, "chunk_length": 18},
]


def _matches(source, query):
    if "term" in query:
        field, value = next(iter(query["term"].items()))
        return source.get(field) == value
    if "exists" in query:
        return source.get(query["exists"]["field"]) is not None
    if "bool" in query:
        return all(_matches(source, clause) for clause in query["bool"]["filter"])
    raise AssertionError(f"Unsupported fake ES query: {query}")


class FakeES:
    def __init__(self):
        old_source = {
            "content": OLD_TEXT,
            "chunk_text": OLD_TEXT,
            "document_id": DOC_ID,
            "organization_id": "7",
            "chunk_id": "old-chunk",
            "chunk_index": 0,
            "embedding": [0.1, 0.2],
        }
        self.docs = {"old-es-id": old_source}
        self.delete_calls = []
        self.bulk_calls = []
        self.count_calls = []
        self.count_faults = {}
        self.delete_fault = None
        self.indices = self

    async def delete_by_query(self, *, index, query, refresh):
        self.delete_calls.append({"index": index, "query": deepcopy(query)})
        if self.delete_fault == "exception":
            raise RuntimeError("delete exploded")
        doomed = [key for key, source in self.docs.items() if _matches(source, query)]
        if self.delete_fault != "partial":
            for key in doomed:
                self.docs.pop(key)
        deleted = 0 if self.delete_fault == "partial" else len(doomed)
        if self.delete_fault == "false":
            return False
        if self.delete_fault == "failures":
            return {
                "failures": [{"cause": "injected shard failure"}],
                "deleted": deleted,
                "timed_out": False,
            }
        if self.delete_fault == "shard_failures":
            return {
                "failures": [],
                "deleted": deleted,
                "timed_out": False,
                "_shards": {"total": 2, "successful": 1, "failed": 1},
            }
        if self.delete_fault == "timed_out":
            return {"failures": [], "deleted": deleted, "timed_out": True}
        return {"failures": [], "deleted": deleted, "timed_out": False}

    async def search(self, *, index, query):
        sources = [source for source in self.docs.values() if _matches(source, query)]
        sources.sort(key=lambda source: source["chunk_index"])
        return {
            "hits": {
                "hits": [
                    {"_id": key, "_source": deepcopy(source)}
                    for key, source in self.docs.items()
                    if source in sources
                ]
            }
        }

    async def count(self, *, index, query):
        call_index = len(self.count_calls)
        self.count_calls.append(deepcopy(query))
        if call_index in self.count_faults:
            return deepcopy(self.count_faults[call_index])
        return {"count": sum(_matches(source, query) for source in self.docs.values())}

    async def refresh(self, *, index):
        return {"_shards": {"failed": 0}}


@pytest.fixture
async def session_factory(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    tables = [Document.__table__, DocumentChunk.__table__, KnowledgeProcessingJob.__table__]
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=tables))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(dp_mod, "AsyncSessionLocal", factory)
    yield factory
    await engine.dispose()


@pytest.fixture
def fake_es(monkeypatch):
    fake = FakeES()
    monkeypatch.setattr(knowledge_service_mod, "get_elasticsearch", AsyncMock(return_value=fake))
    monkeypatch.setattr(dp_mod, "get_elasticsearch", AsyncMock(return_value=fake))
    monkeypatch.setattr(dp_mod, "create_index_if_not_exists", AsyncMock(return_value=None))
    monkeypatch.setattr(RetrievalCache, "clear_for_org", AsyncMock(return_value=None))
    monkeypatch.setattr(SemanticCache, "clear_for_org", AsyncMock(return_value=None))
    return fake


async def _seed_indexed_version(factory):
    async with factory() as session:
        session.add(
            Document(
                id=DOC_ID,
                filename="report.txt",
                file_path="7/report.txt",
                file_size=len(OLD_TEXT),
                file_type=DocumentType.TXT,
                status=DocumentStatus.INDEXED,
                chunk_count=1,
                content_length=len(OLD_TEXT),
                organization_id=7,
                uploaded_by=1,
            )
        )
        session.add(
            DocumentChunk(
                id="old-chunk",
                document_id=DOC_ID,
                chunk_index=0,
                chunk_text=OLD_TEXT,
                chunk_length=len(OLD_TEXT),
            )
        )
        await session.commit()


async def _submit_rebuild(factory, monkeypatch):
    sent = {}

    async def fake_send(topic, message):
        sent.update(message)
        return True

    monkeypatch.setattr(knowledge_mod.kafka_producer, "send_message", fake_send)
    monkeypatch.setattr(knowledge_mod, "create_notification", AsyncMock(return_value=None))
    async with factory() as session:
        doc = await session.get(Document, DOC_ID)
        monkeypatch.setattr(knowledge_mod, "get_document_for_user", AsyncMock(return_value=doc))
        response = await knowledge_mod.rebuild_document_knowledge(
            DOC_ID,
            current_user=SimpleNamespace(id=1),
            db=session,
        )
    assert response.success is True
    return sent["job_id"]


def _make_processor(monkeypatch):
    processor = DocumentProcessor()
    monkeypatch.setattr(processor, "_acquire_processing_lock", AsyncMock(return_value=None))
    monkeypatch.setattr(processor, "_extract_graph_safely", AsyncMock(return_value=None))
    return processor


def _wire_bulk(monkeypatch, fake_es, error=None, limit=None, drop_embedding=False):
    async def fake_bulk(es_docs):
        fake_es.bulk_calls.append(deepcopy(es_docs))
        if error is not None:
            raise error
        docs_to_write = es_docs if limit is None else es_docs[:limit]
        for item in docs_to_write:
            source = deepcopy(item["_source"])
            if drop_embedding and item is docs_to_write[-1]:
                source.pop("embedding")
            fake_es.docs[item["_id"]] = source

    monkeypatch.setattr(dp_mod.ElasticsearchTools, "bulk_index_documents", fake_bulk)


def _wire_parse_and_embedding(monkeypatch, processor):
    async def parse_ok(*args):
        return {"chunks": deepcopy(NEW_CHUNKS)}

    async def embed_ok(texts):
        return [[0.3, 0.4] for _ in texts]

    monkeypatch.setattr(processor.parser, "parse_document", parse_ok)
    monkeypatch.setattr(processor, "_get_embeddings", embed_ok)


async def _prepare_rebuild(session_factory, fake_es, monkeypatch, *, bulk_limit=None):
    await _seed_indexed_version(session_factory)
    job_id = await _submit_rebuild(session_factory, monkeypatch)
    processor = _make_processor(monkeypatch)
    _wire_bulk(monkeypatch, fake_es, limit=bulk_limit)
    _wire_parse_and_embedding(monkeypatch, processor)
    return job_id, processor


async def _search_texts(fake_es):
    result = await fake_es.search(index="fake-index", query={"term": {"document_id": DOC_ID}})
    return [hit["_source"]["content"] for hit in result["hits"]["hits"]]


async def _load_state(factory, job_id):
    async with factory() as session:
        doc = await session.get(Document, DOC_ID)
        job = await session.get(KnowledgeProcessingJob, job_id)
        chunks = (
            (
                await session.execute(
                    select(DocumentChunk)
                    .where(DocumentChunk.document_id == DOC_ID)
                    .order_by(DocumentChunk.chunk_index)
                )
            )
            .scalars()
            .all()
        )
        return doc, job, chunks


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fault", "expected_error"),
    [
        ("parser", "parser exploded"),
        ("empty", "无有效文本内容"),
        ("embedding", "embedding unavailable"),
        ("count", "向量生成数量"),
        ("db", "UNIQUE constraint failed"),
    ],
)
async def test_pre_es_rebuild_failures_preserve_indexed_version(
    session_factory, fake_es, monkeypatch, fault, expected_error
):
    """Each pre-delete failure keeps old DB/ES data and fails only the new job."""
    await _seed_indexed_version(session_factory)
    original_es = deepcopy(fake_es.docs)
    job_id = await _submit_rebuild(session_factory, monkeypatch)
    processor = _make_processor(monkeypatch)
    _wire_bulk(monkeypatch, fake_es)

    async def parse_ok(*args):
        return {"chunks": deepcopy(NEW_CHUNKS)}

    async def embed_ok(texts):
        return [[0.3, 0.4] for _ in texts]

    if fault == "parser":

        async def parse_ok(*args):
            raise RuntimeError("parser exploded")

    elif fault == "empty":

        async def parse_ok(*args):
            return {"chunks": [{"chunk_text": " \n", "chunk_index": 0, "chunk_length": 0}]}

    elif fault == "embedding":

        async def embed_ok(texts):
            raise RuntimeError("embedding unavailable")

    elif fault == "count":

        async def embed_ok(texts):
            return [[0.3, 0.4]]

    elif fault == "db":
        # Both new rows get one primary key. SQLite flushes the delete and inserts,
        # then the second insert aborts commit; session close must roll everything back.
        monkeypatch.setattr(dp_mod, "uuid", SimpleNamespace(uuid4=lambda: "duplicate-new-chunk"))

    monkeypatch.setattr(processor.parser, "parse_document", parse_ok)
    monkeypatch.setattr(processor, "_get_embeddings", embed_ok)

    assert await processor.process(DOC_ID, job_id) is False

    doc, job, chunks = await _load_state(session_factory, job_id)
    assert doc.status == DocumentStatus.INDEXED
    assert doc.parse_error is None
    assert job.status == KnowledgeJobStatus.FAILED
    assert expected_error in job.error_message
    assert [(row.id, row.chunk_text) for row in chunks] == [("old-chunk", OLD_TEXT)]
    assert fake_es.delete_calls == []
    assert fake_es.docs == original_es
    assert await _search_texts(fake_es) == [OLD_TEXT]


@pytest.mark.asyncio
async def test_successful_rebuild_exposes_only_new_version(session_factory, fake_es, monkeypatch):
    await _seed_indexed_version(session_factory)
    job_id = await _submit_rebuild(session_factory, monkeypatch)
    processor = _make_processor(monkeypatch)
    _wire_bulk(monkeypatch, fake_es)

    async def parse_ok(*args):
        return {"chunks": deepcopy(NEW_CHUNKS)}

    async def embed_ok(texts):
        return [[0.3, 0.4] for _ in texts]

    monkeypatch.setattr(processor.parser, "parse_document", parse_ok)
    monkeypatch.setattr(processor, "_get_embeddings", embed_ok)

    assert await processor.process(DOC_ID, job_id) is True

    doc, job, chunks = await _load_state(session_factory, job_id)
    assert doc.status == DocumentStatus.INDEXED
    assert doc.chunk_count == len(NEW_CHUNKS)
    assert job.status == KnowledgeJobStatus.SUCCESS
    assert job.error_message is None
    assert len(fake_es.delete_calls) == 1
    assert await _search_texts(fake_es) == [chunk["chunk_text"] for chunk in NEW_CHUNKS]
    assert [row.chunk_text for row in chunks] == [chunk["chunk_text"] for chunk in NEW_CHUNKS]
    assert "old-es-id" not in fake_es.docs


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["bulk", "verify", "count", "embedding"])
async def test_failure_after_es_delete_keeps_existing_failure_semantics(
    session_factory, fake_es, monkeypatch, fault
):
    """Post-delete indexing failures converge to FAILED, never partial SUCCESS."""
    await _seed_indexed_version(session_factory)
    job_id = await _submit_rebuild(session_factory, monkeypatch)
    processor = _make_processor(monkeypatch)
    if fault == "bulk":
        _wire_bulk(monkeypatch, fake_es, RuntimeError("bulk failed after delete"))
    elif fault == "count":
        _wire_bulk(monkeypatch, fake_es, limit=1)
    elif fault == "embedding":
        _wire_bulk(monkeypatch, fake_es, drop_embedding=True)
    else:
        _wire_bulk(monkeypatch, fake_es)
        monkeypatch.setattr(
            dp_mod,
            "_verify_indexed",
            AsyncMock(side_effect=RuntimeError("verify unavailable")),
        )
    _wire_parse_and_embedding(monkeypatch, processor)

    result = await processor.process(DOC_ID, job_id)

    doc, job, chunks = await _load_state(session_factory, job_id)
    texts = await _search_texts(fake_es)
    expected_error = {
        "bulk": "bulk failed after delete",
        "verify": "verify unavailable",
        "count": "索引校验失败",
        "embedding": "可检索性不完整",
    }[fault]
    expected_es = {
        "bulk": [],
        "verify": [chunk["chunk_text"] for chunk in NEW_CHUNKS],
        "count": [NEW_CHUNKS[0]["chunk_text"]],
        "embedding": [chunk["chunk_text"] for chunk in NEW_CHUNKS],
    }[fault]
    assert result is False, f"{fault} was reported as success; ES texts={texts}"
    assert len(fake_es.delete_calls) == 1
    assert doc.status == DocumentStatus.FAILED
    assert expected_error in doc.parse_error
    assert job.status == KnowledgeJobStatus.FAILED
    assert expected_error in job.error_message
    assert [row.chunk_text for row in chunks] == [chunk["chunk_text"] for chunk in NEW_CHUNKS]
    assert texts == expected_es


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["create_index", "get_client"])
async def test_post_commit_es_initialization_failure_cannot_restore_indexed(
    session_factory, fake_es, monkeypatch, fault
):
    """Real SQLite proves the DB was replaced before this post-commit failure."""
    await _seed_indexed_version(session_factory)
    original_es = deepcopy(fake_es.docs)
    job_id = await _submit_rebuild(session_factory, monkeypatch)
    processor = _make_processor(monkeypatch)
    _wire_bulk(monkeypatch, fake_es)
    _wire_parse_and_embedding(monkeypatch, processor)
    error_message = f"{fault} failed after DB commit"
    if fault == "create_index":
        monkeypatch.setattr(
            dp_mod,
            "create_index_if_not_exists",
            AsyncMock(side_effect=RuntimeError(error_message)),
        )
    else:
        monkeypatch.setattr(
            dp_mod,
            "get_elasticsearch",
            AsyncMock(side_effect=RuntimeError(error_message)),
        )

    result = await processor.process(DOC_ID, job_id)

    doc, job, chunks = await _load_state(session_factory, job_id)
    assert result is False
    assert [row.chunk_text for row in chunks] == [chunk["chunk_text"] for chunk in NEW_CHUNKS], (
        "fault injection must happen after the real SQLite chunk commit"
    )
    assert fake_es.docs == original_es
    assert fake_es.delete_calls == []
    assert doc.status == DocumentStatus.FAILED, (
        "the DB no longer contains the old version; old ES + new DB must not be INDEXED"
    )
    assert doc.parse_error == error_message
    assert job.status == KnowledgeJobStatus.FAILED
    assert job.error_message == error_message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fault", "expected_error"),
    [
        ("exception", "delete exploded"),
        ("false", "empty/false response"),
        ("failures", "failures="),
        ("shard_failures", "failed_shards=1"),
        ("timed_out", "timed_out=True"),
        ("partial", "1 old chunk(s) remain"),
    ],
)
async def test_delete_faults_never_report_success_or_mix_versions(
    session_factory, fake_es, monkeypatch, fault, expected_error
):
    """Delete exceptions, false/failed responses, and partial deletion all fail closed."""
    await _seed_indexed_version(session_factory)
    job_id = await _submit_rebuild(session_factory, monkeypatch)
    processor = _make_processor(monkeypatch)
    # partial 同时限制 bulk 为 1 条：旧版 1 + 新版 1 恰好等于预期总数 2，
    # 因而能证伪「只做最终条数校验就足够」的实现。
    _wire_bulk(monkeypatch, fake_es, limit=1 if fault == "partial" else None)
    _wire_parse_and_embedding(monkeypatch, processor)
    fake_es.delete_fault = fault

    result = await processor.process(DOC_ID, job_id)

    doc, job, chunks = await _load_state(session_factory, job_id)
    texts = await _search_texts(fake_es)
    assert result is False, f"{fault} was reported as success; ES texts={texts}"
    assert doc.status == DocumentStatus.FAILED
    assert expected_error in doc.parse_error
    assert job.status == KnowledgeJobStatus.FAILED
    assert expected_error in job.error_message
    assert [row.chunk_text for row in chunks] == [chunk["chunk_text"] for chunk in NEW_CHUNKS]
    assert fake_es.bulk_calls == [], "an unconfirmed delete must not be followed by bulk"
    expected_es = [OLD_TEXT] if fault in {"exception", "partial"} else []
    assert texts == expected_es


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fault", "response"),
    [
        (
            "shard_failure_details",
            {"count": 0, "_shards": {"failed": 0, "failures": [{"reason": "partial"}]}},
        ),
        ("failures", {"count": 0, "failures": [{"reason": "partial"}]}),
        ("timeout", {"count": 0, "timed_out": True}),
        ("false", False),
        ("empty", {}),
        ("missing_count", {"_shards": {"failed": 0}}),
        ("invalid_count", {"count": "0"}),
        ("invalid_shards", {"count": 0, "_shards": {"failed": "0"}}),
    ],
)
async def test_pre_bulk_count_response_must_be_trustworthy(
    session_factory, fake_es, monkeypatch, fault, response
):
    """An unconfirmed pre-bulk count must fail before any bulk write."""
    job_id, processor = await _prepare_rebuild(session_factory, fake_es, monkeypatch)
    fake_es.count_faults[0] = response

    result = await processor.process(DOC_ID, job_id)

    doc, job, chunks = await _load_state(session_factory, job_id)
    texts = await _search_texts(fake_es)
    assert result is False, f"pre-bulk {fault} was reported as success; ES texts={texts}"
    assert doc.status == DocumentStatus.FAILED
    assert doc.parse_error
    assert job.status == KnowledgeJobStatus.FAILED
    assert job.error_message == doc.parse_error
    assert [row.chunk_text for row in chunks] == [chunk["chunk_text"] for chunk in NEW_CHUNKS]
    assert fake_es.bulk_calls == []
    assert len(fake_es.count_calls) == 1
    assert texts == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fault", "response"),
    [
        (
            "shard_failure_details",
            {"count": 2, "_shards": {"failed": 0, "failures": [{"reason": "partial"}]}},
        ),
        ("failures", {"count": 2, "failures": [{"reason": "partial"}]}),
        ("timeout", {"count": 2, "timed_out": True}),
        ("false", False),
        ("empty", {}),
        ("missing_count", {"_shards": {"failed": 0}}),
        ("invalid_count", {"count": "2"}),
    ],
)
async def test_final_count_response_must_be_trustworthy(
    session_factory, fake_es, monkeypatch, fault, response
):
    """The first final count uses the same strict parser as the pre-bulk count."""
    job_id, processor = await _prepare_rebuild(session_factory, fake_es, monkeypatch)
    fake_es.count_faults[1] = response

    result = await processor.process(DOC_ID, job_id)

    doc, job, chunks = await _load_state(session_factory, job_id)
    texts = await _search_texts(fake_es)
    assert result is False, f"final {fault} was reported as success; ES texts={texts}"
    assert doc.status == DocumentStatus.FAILED
    assert doc.parse_error
    assert job.status == KnowledgeJobStatus.FAILED
    assert job.error_message == doc.parse_error
    assert [row.chunk_text for row in chunks] == [chunk["chunk_text"] for chunk in NEW_CHUNKS]
    assert len(fake_es.bulk_calls) == 1
    assert len(fake_es.count_calls) == 2
    assert texts == [chunk["chunk_text"] for chunk in NEW_CHUNKS]


@pytest.mark.asyncio
@pytest.mark.parametrize("count_index", [0, 1, 2])
async def test_every_final_count_rejects_failed_shards(
    session_factory, fake_es, monkeypatch, count_index
):
    """Total, embedding, and organization count responses are all fail-closed."""
    job_id, processor = await _prepare_rebuild(session_factory, fake_es, monkeypatch)
    fake_es.count_faults[1 + count_index] = {
        "count": 2,
        "_shards": {"failed": 1},
    }

    result = await processor.process(DOC_ID, job_id)

    doc, job, _ = await _load_state(session_factory, job_id)
    assert result is False
    assert doc.status == DocumentStatus.FAILED
    assert "failed_shards=1" in doc.parse_error
    assert job.status == KnowledgeJobStatus.FAILED
    assert "failed_shards=1" in job.error_message
    assert len(fake_es.bulk_calls) == 1
    assert len(fake_es.count_calls) == 2 + count_index


@pytest.mark.asyncio
async def test_partial_pre_bulk_count_zero_cannot_mask_mixed_versions(
    session_factory, fake_es, monkeypatch
):
    """R3 review: old 1 + new 1 has the expected total but must never succeed."""
    job_id, processor = await _prepare_rebuild(session_factory, fake_es, monkeypatch, bulk_limit=1)
    fake_es.delete_fault = "partial"
    fake_es.count_faults[0] = {"count": 0, "_shards": {"failed": 1}}

    result = await processor.process(DOC_ID, job_id)

    doc, job, chunks = await _load_state(session_factory, job_id)
    texts = await _search_texts(fake_es)
    assert result is False, f"partial count masked mixed versions; ES texts={texts}"
    assert doc.status == DocumentStatus.FAILED
    assert "failed_shards=1" in doc.parse_error
    assert job.status == KnowledgeJobStatus.FAILED
    assert "failed_shards=1" in job.error_message
    assert [row.chunk_text for row in chunks] == [chunk["chunk_text"] for chunk in NEW_CHUNKS]
    assert fake_es.bulk_calls == []
    assert len(fake_es.count_calls) == 1
    assert texts == [OLD_TEXT]


@pytest.mark.asyncio
async def test_restore_indexed_log_escapes_document_id(monkeypatch, caplog):
    """The new restore log must not let a user document ID forge log lines."""
    processor = _make_processor(monkeypatch)
    doc = SimpleNamespace(
        file_path="unused.txt",
        organization_id=7,
        _reprocess_previous_status=DocumentStatus.INDEXED,
        _db_chunks_committed=False,
        _es_delete_started=False,
    )

    async def fake_load(_document_id, _job_id):
        return doc

    async def failing_parse(*args):
        raise RuntimeError("parser exploded")

    mark_failed = AsyncMock(return_value=None)
    monkeypatch.setattr(processor, "_load_and_mark_parsing", fake_load)
    monkeypatch.setattr(processor.parser, "parse_document", failing_parse)
    monkeypatch.setattr(processor, "_mark_failed", mark_failed)
    hostile_id = "doc-rebuild-preserve\n[FORGED] INDEXED"
    caplog.set_level(logging.INFO, logger=dp_mod.__name__)

    assert await processor.process(hostile_id, 321) is False

    messages = [
        record.getMessage()
        for record in caplog.records
        if "restoring INDEXED" in record.getMessage()
    ]
    assert len(messages) == 1
    assert "\n" not in messages[0] and "\r" not in messages[0]
    assert "doc-rebuild-preserve\\n[FORGED] INDEXED" in messages[0]
    mark_failed.assert_awaited_once_with(hostile_id, "parser exploded", 321, preserve_indexed=True)
