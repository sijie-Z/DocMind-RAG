"""Rebuilds keep the last indexed version until ES replacement starts.

The endpoint and worker run against real SQLite. Elasticsearch is an in-memory fake
whose delete, bulk, search, and verification operations mutate or inspect real fake
state, so these tests assert retrievability rather than only mocking calls.
"""

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
        self.indices = self

    async def delete_by_query(self, *, index, query, refresh):
        self.delete_calls.append({"index": index, "query": deepcopy(query)})
        doomed = [key for key, source in self.docs.items() if _matches(source, query)]
        for key in doomed:
            self.docs.pop(key)
        return {"failures": [], "deleted": len(doomed)}

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


def _wire_bulk(monkeypatch, fake_es, error=None):
    async def fake_bulk(es_docs):
        if error is not None:
            raise error
        for item in es_docs:
            fake_es.docs[item["_id"]] = deepcopy(item["_source"])

    monkeypatch.setattr(dp_mod.ElasticsearchTools, "bulk_index_documents", fake_bulk)


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
async def test_failure_after_es_delete_keeps_existing_failure_semantics(
    session_factory, fake_es, monkeypatch
):
    """Bulk failure is explicitly outside this phase's old-version guarantee."""
    await _seed_indexed_version(session_factory)
    job_id = await _submit_rebuild(session_factory, monkeypatch)
    processor = _make_processor(monkeypatch)
    _wire_bulk(monkeypatch, fake_es, RuntimeError("bulk failed after delete"))

    async def parse_ok(*args):
        return {"chunks": deepcopy(NEW_CHUNKS)}

    async def embed_ok(texts):
        return [[0.3, 0.4] for _ in texts]

    monkeypatch.setattr(processor.parser, "parse_document", parse_ok)
    monkeypatch.setattr(processor, "_get_embeddings", embed_ok)

    assert await processor.process(DOC_ID, job_id) is False

    doc, job, _ = await _load_state(session_factory, job_id)
    assert len(fake_es.delete_calls) == 1
    assert doc.status == DocumentStatus.FAILED
    assert job.status == KnowledgeJobStatus.FAILED
    assert "bulk failed after delete" in job.error_message
    assert await _search_texts(fake_es) == []
