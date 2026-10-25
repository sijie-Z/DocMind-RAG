"""ES 索引删除失败时，文档删除必须 fail-closed。

使用真实 service/endpoint、内存 SQLite、fake ES 和真实 HybridRetriever：
- ES 异常、helper False、响应含 failures 时保留 DB 行和 job，不 commit、不记成功审计；
- batch 按文档分别统计，且成功审计不包含失败项；
- 只有 ES 明确成功才删 DB，删除后真实 retriever 不再返回旧 chunk；
- 没有旧 chunk 时 deleted=0 仍是成功，不把幂等删除误判为故障。

不连接真实 MySQL/ES/Redis/MinIO，也不删除任何真实数据。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.services.knowledge_service as ks_mod
from app.core.database import Base
from app.exceptions import ServiceUnavailableError
from app.models.document import Document, DocumentChunk, DocumentStatus, DocumentTag, DocumentType
from app.models.knowledge_job import KnowledgeProcessingJob
from app.rag.cache import RetrievalCache, SemanticCache

ORG_ID = 1
OLD_CONTENT = "政策：已删除的机密条款"


def _es_chunk(document_id):
    return {
        "_id": f"{document_id}-old-chunk",
        "_score": 8.0,
        "_source": {
            "content": OLD_CONTENT,
            "document_id": document_id,
            "filename": f"{document_id}.txt",
            "organization_id": str(ORG_ID),
        },
    }


class _FakeES:
    """模拟 deferred refresh：删除先落入存储，refresh=True 才改变搜索快照。"""

    def __init__(self, chunks=(), failure_by_document=None):
        self.chunks = {chunk["_id"]: chunk for chunk in chunks}
        self._stored_chunks = dict(self.chunks)
        self.failure_by_document = failure_by_document or {}
        self.delete_calls = []
        self.deleted_counts = []

    async def delete_by_query(self, **kwargs):
        self.delete_calls.append(kwargs)
        document_id = kwargs["query"]["term"]["document_id"]
        failure = self.failure_by_document.get(document_id)
        if failure == "exception":
            raise RuntimeError("injected ES delete outage")
        if failure == "response_failures":
            self.deleted_counts.append(0)
            return {"deleted": 0, "failures": [{"cause": "injected shard failure"}]}

        before = len(self._stored_chunks)
        self._stored_chunks = {
            chunk_id: chunk
            for chunk_id, chunk in self._stored_chunks.items()
            if chunk["_source"]["document_id"] != document_id
        }
        deleted = before - len(self._stored_chunks)
        self.deleted_counts.append(deleted)
        if kwargs.get("refresh") is True:
            self.chunks = dict(self._stored_chunks)
        return {"deleted": deleted, "failures": []}

    async def search(self, **_kwargs):
        return {"hits": {"hits": list(self.chunks.values())}}


@pytest.fixture
async def delete_harness(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    tables = [
        Document.__table__,
        DocumentChunk.__table__,
        DocumentTag.__table__,
        KnowledgeProcessingJob.__table__,
    ]
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=tables))

    factory = async_sessionmaker(engine, expire_on_commit=False)
    commits = []

    def tracking_factory():
        session = factory()
        real_commit = session.commit

        async def tracked_commit():
            commits.append("commit")
            await real_commit()

        session.commit = tracked_commit
        return session

    monkeypatch.setattr(ks_mod, "AsyncSessionLocal", tracking_factory)
    retrieval_clear = AsyncMock(return_value=None)
    semantic_clear = AsyncMock(return_value=None)
    monkeypatch.setattr(RetrievalCache, "clear_for_org", retrieval_clear)
    monkeypatch.setattr(SemanticCache, "clear_for_org", semantic_clear)

    yield SimpleNamespace(
        factory=factory,
        commits=commits,
        retrieval_clear=retrieval_clear,
        semantic_clear=semantic_clear,
    )
    await engine.dispose()


async def _seed_document(factory, document_id):
    async with factory() as session:
        session.add_all(
            [
                Document(
                    id=document_id,
                    filename=f"{document_id}.txt",
                    file_path="",
                    file_size=len(OLD_CONTENT),
                    file_type=DocumentType.TXT,
                    status=DocumentStatus.INDEXED,
                    organization_id=ORG_ID,
                    uploaded_by=1,
                ),
                DocumentChunk(
                    id=f"{document_id}-db-chunk",
                    document_id=document_id,
                    chunk_index=0,
                    chunk_text=OLD_CONTENT,
                    chunk_length=len(OLD_CONTENT),
                ),
                KnowledgeProcessingJob(
                    document_id=document_id,
                    organization_id=ORG_ID,
                    trigger_type="upload",
                ),
            ]
        )
        await session.commit()


async def _database_state(factory, document_id):
    async with factory() as session:
        document_count = (
            await session.execute(select(func.count(Document.id)).where(Document.id == document_id))
        ).scalar_one()

        async def child_count(model):
            return (
                await session.execute(
                    select(func.count()).select_from(model).where(model.document_id == document_id)
                )
            ).scalar_one()

        return {
            "documents": document_count,
            "chunks": await child_count(DocumentChunk),
            "jobs": await child_count(KnowledgeProcessingJob),
        }


def _wire_es(monkeypatch, fake_es):
    import app.core.elasticsearch as es_module

    monkeypatch.setattr(ks_mod, "get_elasticsearch", AsyncMock(return_value=fake_es))
    monkeypatch.setattr(es_module, "get_elasticsearch", AsyncMock(return_value=fake_es))


def _wire_single_endpoint(monkeypatch, service, document_id):
    from app.api.v1.endpoints import knowledge as knowledge_api

    document = SimpleNamespace(
        id=document_id,
        organization_id=ORG_ID,
        filename=f"{document_id}.txt",
    )
    audit = AsyncMock(return_value=None)
    monkeypatch.setattr(knowledge_api, "knowledge_service", service)
    monkeypatch.setattr(knowledge_api, "get_document_for_user", AsyncMock(return_value=document))
    monkeypatch.setattr(knowledge_api.audit_service, "log_activity", audit)
    return knowledge_api, audit


def _wire_batch_endpoint(monkeypatch, service):
    from app.api.v1.endpoints import knowledge as knowledge_api

    audit = AsyncMock(return_value=None)
    monkeypatch.setattr(knowledge_api, "knowledge_service", service)
    monkeypatch.setattr(knowledge_api, "require_organization", AsyncMock(return_value=ORG_ID))
    monkeypatch.setattr(knowledge_api.audit_service, "log_activity", audit)
    return knowledge_api, audit


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["exception", "helper_false", "response_failures"])
async def test_es_delete_failure_preserves_db_and_blocks_success(
    delete_harness, monkeypatch, failure
):
    document_id = f"doc-{failure}"
    await _seed_document(delete_harness.factory, document_id)
    failure_by_document = {} if failure == "helper_false" else {document_id: failure}
    fake_es = _FakeES([_es_chunk(document_id)], failure_by_document)
    _wire_es(monkeypatch, fake_es)
    service = ks_mod.KnowledgeService()
    if failure == "helper_false":
        monkeypatch.setattr(service, "delete_es_by_document_id", AsyncMock(return_value=False))
    knowledge_api, audit = _wire_single_endpoint(monkeypatch, service, document_id)

    with pytest.raises(ServiceUnavailableError) as exc_info:
        await knowledge_api.delete_knowledge_document(
            document_id,
            current_user=SimpleNamespace(id=1),
            db=SimpleNamespace(),
        )

    assert exc_info.value.status_code == 503
    assert "请重试" in exc_info.value.message
    assert delete_harness.commits == []
    assert await _database_state(delete_harness.factory, document_id) == {
        "documents": 1,
        "chunks": 1,
        "jobs": 1,
    }
    assert list(fake_es.chunks) == [f"{document_id}-old-chunk"]
    audit.assert_not_awaited()
    delete_harness.retrieval_clear.assert_not_awaited()
    delete_harness.semantic_clear.assert_not_awaited()


@pytest.mark.asyncio
async def test_successful_delete_removes_old_chunk_from_real_retriever(delete_harness, monkeypatch):
    document_id = "doc-deleted"
    await _seed_document(delete_harness.factory, document_id)
    fake_es = _FakeES([_es_chunk(document_id)])
    _wire_es(monkeypatch, fake_es)
    service = ks_mod.KnowledgeService()
    knowledge_api, audit = _wire_single_endpoint(monkeypatch, service, document_id)

    from app.core.config import settings
    from app.rag.retriever import HybridRetriever

    monkeypatch.setattr(settings, "RAG_ENABLE_QUERY_REWRITE", False)
    retriever = HybridRetriever()
    before, _, _, _ = await retriever.retrieve("政策", ORG_ID, top_k=5, search_mode="keyword")
    assert [result["document_id"] for result in before] == [document_id]

    response = await knowledge_api.delete_knowledge_document(
        document_id,
        current_user=SimpleNamespace(id=1),
        db=SimpleNamespace(),
    )

    assert response == {"success": True, "message": "删除成功"}
    audit.assert_awaited_once()
    after, _, _, _ = await retriever.retrieve("政策", ORG_ID, top_k=5, search_mode="keyword")
    assert after == []
    assert fake_es.delete_calls[0]["refresh"] is True
    assert fake_es.deleted_counts == [1]
    assert delete_harness.commits == ["commit"]
    assert await _database_state(delete_harness.factory, document_id) == {
        "documents": 0,
        "chunks": 0,
        "jobs": 0,
    }


@pytest.mark.asyncio
async def test_successful_delete_allows_zero_es_chunks(delete_harness, monkeypatch):
    document_id = "doc-without-chunks"
    await _seed_document(delete_harness.factory, document_id)
    fake_es = _FakeES()
    _wire_es(monkeypatch, fake_es)
    service = ks_mod.KnowledgeService()

    assert await service.delete_es_by_document_id(document_id) is True
    assert await service.delete_knowledge_thoroughly(document_id, ORG_ID) is True

    assert fake_es.deleted_counts == [0, 0]
    assert delete_harness.commits == ["commit"]
    assert await _database_state(delete_harness.factory, document_id) == {
        "documents": 0,
        "chunks": 0,
        "jobs": 0,
    }


@pytest.mark.asyncio
async def test_batch_separates_failures_and_audits_only_successes(delete_harness, monkeypatch):
    ok_id, bad_id = "doc-ok", "doc-bad"
    await _seed_document(delete_harness.factory, ok_id)
    await _seed_document(delete_harness.factory, bad_id)
    fake_es = _FakeES(
        [_es_chunk(ok_id), _es_chunk(bad_id)],
        {bad_id: "response_failures"},
    )
    _wire_es(monkeypatch, fake_es)
    service = ks_mod.KnowledgeService()
    knowledge_api, audit = _wire_batch_endpoint(monkeypatch, service)

    result = await knowledge_api.batch_delete_documents(
        request=SimpleNamespace(document_ids=[ok_id, bad_id], organization_id=ORG_ID),
        current_user=SimpleNamespace(id=1, is_superuser=True),
        db=SimpleNamespace(),
    )

    assert result["success_count"] == 1
    assert result["fail_count"] == 1
    assert len(result["errors"]) == 1
    assert result["errors"][0]["document_id"] == bad_id
    assert "请重试" in result["errors"][0]["reason"]
    assert delete_harness.commits == ["commit"]
    assert await _database_state(delete_harness.factory, ok_id) == {
        "documents": 0,
        "chunks": 0,
        "jobs": 0,
    }
    assert await _database_state(delete_harness.factory, bad_id) == {
        "documents": 1,
        "chunks": 1,
        "jobs": 1,
    }
    audit.assert_awaited_once()
    assert audit.await_args.kwargs["target_id"] == ok_id
    assert "1 个" in audit.await_args.kwargs["detail"]


@pytest.mark.asyncio
async def test_batch_all_failures_write_no_success_audit(delete_harness, monkeypatch):
    document_id = "doc-batch-failure"
    await _seed_document(delete_harness.factory, document_id)
    fake_es = _FakeES([_es_chunk(document_id)], {document_id: "exception"})
    _wire_es(monkeypatch, fake_es)
    service = ks_mod.KnowledgeService()
    knowledge_api, audit = _wire_batch_endpoint(monkeypatch, service)

    result = await knowledge_api.batch_delete_documents(
        request=SimpleNamespace(document_ids=[document_id], organization_id=ORG_ID),
        current_user=SimpleNamespace(id=1, is_superuser=True),
        db=SimpleNamespace(),
    )

    assert result["success_count"] == 0
    assert result["fail_count"] == 1
    assert result["errors"][0]["document_id"] == document_id
    assert delete_harness.commits == []
    assert await _database_state(delete_harness.factory, document_id) == {
        "documents": 1,
        "chunks": 1,
        "jobs": 1,
    }
    audit.assert_not_awaited()
