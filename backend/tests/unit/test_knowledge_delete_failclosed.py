"""ES 索引删除失败时，文档删除必须 fail-closed（P1-1 / P2-3）。

使用真实 service/endpoint、内存 SQLite、fake ES 和真实 HybridRetriever：
- ES 异常、falsy/空响应、timeout、shard 失败、显式 failures、非法元数据、
  refresh/回读失败或回读 count>0 时：保留 DB 行和 job，不 commit、不删文件、
  不记成功审计；
- batch 按文档分别统计，且成功审计不包含失败项；
- 只有 ES 严格成功 + refresh 回读 count==0 才删 DB，删除后真实 retriever 不再
  返回旧 chunk；
- 物理文件删除在 DB commit 之后 best-effort 执行；失败时响应/审计如实写
  ``file_cleanup_pending``，不谎报完全清理；共享 file_path 的其他文档受保护；
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
from app.exceptions import AppError, ServiceUnavailableError
from app.models.document import Document, DocumentChunk, DocumentStatus, DocumentTag, DocumentType
from app.models.knowledge_job import KnowledgeProcessingJob
from app.models.review import ReviewCase, ReviewDocumentMeta
from app.rag.cache import RetrievalCache, SemanticCache
from app.services.document_status import require_delete_by_query_response

ORG_ID = 1
OLD_CONTENT = "政策：已删除的机密条款"

# delete_by_query 的失败形态矩阵：前 9 个是响应本身异常（严格解析必须拒绝），
# 后 3 个是响应看似成功但 refresh 回读证明删除不可见/回读不可信。
ES_FAILURE_MODES = [
    "exception",
    "none",
    "false",
    "empty",
    "timed_out",
    "shard_failed",
    "shard_failures",
    "response_failures",
    "bad_deleted",
    "partial",
    "count_failure",
    "refresh_failure",
]


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


def _strict_delete_meta(**overrides):
    response = {
        "deleted": 0,
        "failures": [],
        "timed_out": False,
        "_shards": {"total": 1, "successful": 1, "failed": 0, "failures": []},
    }
    response.update(overrides)
    return response


def _strict_count(count):
    return {
        "count": count,
        "failures": [],
        "timed_out": False,
        "_shards": {"total": 1, "successful": 1, "failed": 0, "failures": []},
    }


def test_delete_response_without_shards_is_accepted_with_readback_proof():
    """ES delete_by_query 规范响应可能不含 ``_shards``。

    该宽容是显式契约：缺 ``_shards`` 时仍要求 ``deleted``/``failures``/
    ``timed_out`` 齐全，并由调用方 refresh + count==0 回读作为删除可见的最终
    证明（见 helper docstring）。
    """
    assert (
        require_delete_by_query_response(
            {"deleted": 0, "failures": [], "timed_out": False}, context="test"
        )
        == 0
    )


@pytest.mark.parametrize(
    "response",
    [
        {"deleted": 0, "failures": []},  # 缺 timed_out
        {"deleted": 0, "timed_out": False},  # 缺 failures
        {"deleted": 0},  # 缺 failures + timed_out
        {"failures": [], "timed_out": False},  # 缺 deleted
        {"deleted": 0, "failures": [], "timed_out": False, "_shards": {"failed": 1}},
    ],
)
def test_delete_response_missing_or_failing_required_metadata_rejected(response):
    """缺失关键元数据（deleted/failures/timed_out）或 shard 失败必须拒绝。"""
    with pytest.raises(RuntimeError):
        require_delete_by_query_response(response, context="test")


class _FakeES:
    """模拟 deferred refresh：删除先落入存储，refresh=True 才改变搜索快照。

    ``failure_by_document`` 支持的注入形态见 ``ES_FAILURE_MODES``。正常/partial
    会走完 delete_by_query；partial 故意不删旧 chunk、却返回成功响应，用于证明
    「响应成功 ≠ 删除可见」必须由 refresh 回读兜住。
    """

    def __init__(self, chunks=(), failure_by_document=None):
        self.chunks = {chunk["_id"]: chunk for chunk in chunks}
        self._stored_chunks = dict(self.chunks)
        self.failure_by_document = failure_by_document or {}
        self.delete_calls = []
        self.deleted_counts = []
        self.count_calls = []
        self.refresh_calls = []
        self._refresh_failures = set()
        self.indices = self

    async def refresh(self, **kwargs):
        self.refresh_calls.append(kwargs)
        self.chunks = dict(self._stored_chunks)
        if self._refresh_failures:
            raise RuntimeError("injected ES refresh failure")

    async def delete_by_query(self, **kwargs):
        self.delete_calls.append(kwargs)
        document_id = kwargs["query"]["term"]["document_id"]
        failure = self.failure_by_document.get(document_id)
        if failure == "exception":
            raise RuntimeError("injected ES delete outage")
        if failure == "none":
            return None
        if failure == "false":
            return False
        if failure == "empty":
            return {}
        if failure == "timed_out":
            return _strict_delete_meta(timed_out=True)
        if failure == "shard_failed":
            return _strict_delete_meta(_shards={"failed": 1, "failures": []})
        if failure == "shard_failures":
            return _strict_delete_meta(_shards={"failed": 0, "failures": [{"reason": "injected"}]})
        if failure == "response_failures":
            return _strict_delete_meta(failures=[{"cause": "injected shard failure"}])
        if failure == "bad_deleted":
            return _strict_delete_meta(deleted="not-an-int")

        before = len(self._stored_chunks)
        if failure != "partial":
            self._stored_chunks = {
                chunk_id: chunk
                for chunk_id, chunk in self._stored_chunks.items()
                if chunk["_source"]["document_id"] != document_id
            }
        deleted = before - len(self._stored_chunks)
        self.deleted_counts.append(deleted)
        if kwargs.get("refresh") is True:
            self.chunks = dict(self._stored_chunks)
        if failure == "refresh_failure":
            self._refresh_failures.add(document_id)
        return _strict_delete_meta(deleted=deleted)

    async def count(self, **kwargs):
        self.count_calls.append(kwargs)
        document_id = kwargs["query"]["term"]["document_id"]
        if self.failure_by_document.get(document_id) == "count_failure":
            return {"count": 0, "failures": [], "timed_out": True, "_shards": {"failed": 0}}
        visible = sum(
            1 for chunk in self.chunks.values() if chunk["_source"]["document_id"] == document_id
        )
        return _strict_count(visible)

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
        ReviewDocumentMeta.__table__,
        # Batch 5：删除前会检查核验案件引用
        ReviewCase.__table__,
    ]
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.create_all(sync_conn, tables=tables))

    factory = async_sessionmaker(engine, expire_on_commit=False)
    state = SimpleNamespace(commits=[], events=[], fail_commits=0)

    def tracking_factory():
        session = factory()
        real_commit = session.commit

        async def tracked_commit():
            if state.fail_commits > 0:
                state.fail_commits -= 1
                state.events.append("commit_failed")
                raise RuntimeError("injected db commit failure")
            state.commits.append("commit")
            state.events.append("commit")
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
        state=state,
        retrieval_clear=retrieval_clear,
        semantic_clear=semantic_clear,
    )
    await engine.dispose()


async def _seed_document(factory, document_id, *, file_path=""):
    async with factory() as session:
        session.add_all(
            [
                Document(
                    id=document_id,
                    filename=f"{document_id}.txt",
                    file_path=file_path,
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


def _wire_file_delete(service, events, *, result=True):
    """替换物理删除实现并记录事件（用于顺序断言与失败注入）。"""

    async def _delete_file(file_path):
        events.append(("file_delete", file_path))
        if isinstance(result, Exception):
            raise result
        return result

    mock = AsyncMock(side_effect=_delete_file)
    service.file_upload_service.delete_file = mock
    return mock


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [*ES_FAILURE_MODES, "helper_false"])
async def test_es_delete_failure_preserves_db_and_blocks_success(
    delete_harness, monkeypatch, failure
):
    document_id = f"doc-{failure}"
    file_path = f"{ORG_ID}/{document_id}.pdf"
    await _seed_document(delete_harness.factory, document_id, file_path=file_path)
    failure_by_document = {} if failure == "helper_false" else {document_id: failure}
    fake_es = _FakeES([_es_chunk(document_id)], failure_by_document)
    _wire_es(monkeypatch, fake_es)
    service = ks_mod.KnowledgeService()
    delete_file = _wire_file_delete(service, delete_harness.state.events)
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
    assert delete_harness.state.commits == []
    assert delete_harness.state.events == []  # 无 commit、无文件删除
    assert await _database_state(delete_harness.factory, document_id) == {
        "documents": 1,
        "chunks": 1,
        "jobs": 1,
    }
    delete_file.assert_not_awaited()
    audit.assert_not_awaited()
    delete_harness.retrieval_clear.assert_not_awaited()
    delete_harness.semantic_clear.assert_not_awaited()
    if failure not in {"count_failure", "refresh_failure"}:
        # 删除未被证明可见：旧 chunk 必须仍可检索（P1-1 用户场景）。
        assert list(fake_es.chunks) == [f"{document_id}-old-chunk"]


@pytest.mark.asyncio
async def test_successful_delete_removes_old_chunk_from_real_retriever(delete_harness, monkeypatch):
    document_id = "doc-deleted"
    file_path = f"{ORG_ID}/{document_id}.pdf"
    await _seed_document(delete_harness.factory, document_id, file_path=file_path)
    fake_es = _FakeES([_es_chunk(document_id)])
    _wire_es(monkeypatch, fake_es)
    service = ks_mod.KnowledgeService()
    delete_file = _wire_file_delete(service, delete_harness.state.events)
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
    # 删除后必须显式 refresh + 回读 count(document_id)==0 才放行 DB 删除
    assert fake_es.refresh_calls, "delete 后必须显式 refresh"
    assert fake_es.count_calls and fake_es.count_calls[0]["query"] == {
        "term": {"document_id": document_id}
    }
    assert delete_harness.state.commits == ["commit"]
    delete_file.assert_awaited_once_with(file_path)
    # P2-3：物理删除必须发生在 DB commit 之后。
    assert delete_harness.state.events.index("commit") < delete_harness.state.events.index(
        ("file_delete", file_path)
    )
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

    first = await service.delete_es_by_document_id(document_id)
    assert first is True
    outcome = await service.delete_knowledge_thoroughly(document_id, ORG_ID)
    assert outcome.success is True
    assert outcome.file_cleanup_pending is False

    assert fake_es.deleted_counts == [0, 0]
    assert len(fake_es.count_calls) == 2
    assert len(fake_es.refresh_calls) == 2
    assert delete_harness.state.commits == ["commit"]
    assert await _database_state(delete_harness.factory, document_id) == {
        "documents": 0,
        "chunks": 0,
        "jobs": 0,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("delete_result", [False, RuntimeError("minio down")])
async def test_file_cleanup_failure_reports_pending_honestly(
    delete_harness, monkeypatch, delete_result
):
    """DB/ES 删除已提交后文件清理失败：删除仍成功，但必须如实标记待清理。"""
    document_id = "doc-file-fail"
    file_path = f"{ORG_ID}/{document_id}.pdf"
    await _seed_document(delete_harness.factory, document_id, file_path=file_path)
    fake_es = _FakeES([_es_chunk(document_id)])
    _wire_es(monkeypatch, fake_es)
    service = ks_mod.KnowledgeService()
    delete_file = _wire_file_delete(service, delete_harness.state.events, result=delete_result)
    knowledge_api, audit = _wire_single_endpoint(monkeypatch, service, document_id)

    response = await knowledge_api.delete_knowledge_document(
        document_id,
        current_user=SimpleNamespace(id=1),
        db=SimpleNamespace(),
    )

    assert response == {
        "success": True,
        "message": "删除成功，文件清理待重试",
        "file_cleanup_pending": True,
    }
    delete_file.assert_awaited_once_with(file_path)
    audit.assert_awaited_once()
    assert "file_cleanup_pending" in audit.await_args.kwargs["detail"]
    # DB/ES 的删除是已提交事实，不得因文件失败回滚或谎报未删除
    assert delete_harness.state.commits == ["commit"]
    assert fake_es.chunks == {}
    assert await _database_state(delete_harness.factory, document_id) == {
        "documents": 0,
        "chunks": 0,
        "jobs": 0,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stat_mode", ["network_error", "no_such_bucket", "bare_404", "object_present"]
)
async def test_minio_failure_with_local_copy_removed_still_reports_pending(
    delete_harness, monkeypatch, tmp_path, stat_mode
):
    """R2/R3 评审回归：MinIO 报错 + 本地副本删除成功，不得谎报完全清理。

    走真实 ``FileUploadService.delete_file``（不替换 service 方法）：
    ``remove_object`` 抛连接错误、本地相对路径文件被删除；stat 要么本身失败
    （网络异常 / NoSuchBucket / 裸 404），要么证明对象仍存在 —— 这些都不能
    证明「这个对象已删」→ delete_file 返回 False → 知识删除链必须标记
    ``file_cleanup_pending``，审计不得声称完全清理。
    """
    import app.services.file_service as fs_mod
    from app.core.config import settings as app_settings

    document_id = "doc-minio-local"
    relative_path = f"{ORG_ID}/{document_id}.pdf"
    local_file = tmp_path / relative_path
    local_file.parent.mkdir(parents=True)
    local_file.write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setattr(app_settings, "UPLOAD_DIR", str(tmp_path))

    class _FailingMinio:
        def remove_object(self, *_args, **_kwargs):
            raise RuntimeError("minio connection refused")

        def stat_object(self, *_args, **_kwargs):
            if stat_mode == "object_present":
                return object()
            exc = RuntimeError(f"stat unavailable ({stat_mode})")
            if stat_mode == "no_such_bucket":
                exc.code = "NoSuchBucket"
            elif stat_mode == "bare_404":
                exc.response = SimpleNamespace(status=404)
            raise exc

    monkeypatch.setattr(fs_mod, "minio_client", _FailingMinio())

    await _seed_document(delete_harness.factory, document_id, file_path=relative_path)
    fake_es = _FakeES([_es_chunk(document_id)])
    _wire_es(monkeypatch, fake_es)
    service = ks_mod.KnowledgeService()
    knowledge_api, audit = _wire_single_endpoint(monkeypatch, service, document_id)

    response = await knowledge_api.delete_knowledge_document(
        document_id,
        current_user=SimpleNamespace(id=1),
        db=SimpleNamespace(),
    )

    assert response == {
        "success": True,
        "message": "删除成功，文件清理待重试",
        "file_cleanup_pending": True,
    }
    assert not local_file.exists(), "本地副本应已被删除（但远端状态未证明）"
    audit.assert_awaited_once()
    detail = audit.await_args.kwargs["detail"]
    assert "file_cleanup_pending" in detail
    assert "完全清理" not in detail
    assert await _database_state(delete_harness.factory, document_id) == {
        "documents": 0,
        "chunks": 0,
        "jobs": 0,
    }


@pytest.mark.asyncio
async def test_shared_file_path_is_not_deleted(delete_harness, monkeypatch):
    """md5 去重共享对象：仍有其他文档引用同一 file_path 时不得物理删除。"""
    shared_path = f"{ORG_ID}/shared-md5.pdf"
    target_id, other_id = "doc-shared-a", "doc-shared-b"
    await _seed_document(delete_harness.factory, target_id, file_path=shared_path)
    await _seed_document(delete_harness.factory, other_id, file_path=shared_path)
    fake_es = _FakeES([_es_chunk(target_id)])
    _wire_es(monkeypatch, fake_es)
    service = ks_mod.KnowledgeService()
    delete_file = _wire_file_delete(service, delete_harness.state.events)

    outcome = await service.delete_knowledge_thoroughly(target_id, ORG_ID)

    assert outcome.success is True
    assert outcome.file_cleanup_pending is False
    delete_file.assert_not_awaited()
    assert await _database_state(delete_harness.factory, target_id) == {
        "documents": 0,
        "chunks": 0,
        "jobs": 0,
    }
    assert await _database_state(delete_harness.factory, other_id) == {
        "documents": 1,
        "chunks": 1,
        "jobs": 1,
    }


@pytest.mark.asyncio
async def test_cross_org_delete_is_side_effect_free(delete_harness, monkeypatch):
    """带 org 过滤的删除对跨 org 的 document_id 是幂等成功且零副作用。"""
    document_id = "doc-other-org"
    await _seed_document(
        delete_harness.factory, document_id, file_path=f"{ORG_ID}/{document_id}.pdf"
    )
    fake_es = _FakeES([_es_chunk(document_id)])
    _wire_es(monkeypatch, fake_es)
    service = ks_mod.KnowledgeService()
    delete_file = _wire_file_delete(service, delete_harness.state.events)

    outcome = await service.delete_knowledge_thoroughly(document_id, ORG_ID + 1)

    assert outcome.success is True  # 幂等：目标 org 下本就不存在
    assert fake_es.delete_calls == []
    delete_file.assert_not_awaited()
    assert delete_harness.state.commits == []
    assert await _database_state(delete_harness.factory, document_id) == {
        "documents": 1,
        "chunks": 1,
        "jobs": 1,
    }


@pytest.mark.asyncio
async def test_db_commit_failure_keeps_file_and_retry_converges(delete_harness, monkeypatch):
    """DB commit 失败：文件必须仍在（反向半删除），重试可收敛。

    已知剩余边界（任务书要求如实测试）：ES 删除已确认后 DB commit 失败，文档行
    随事务回滚保留，而索引已删 —— 文档暂不可检索但可下载；重试删除时 ES 对已
    清零文档幂等成功，DB 与文件清理随后完成。
    """
    document_id = "doc-commit-fail"
    file_path = f"{ORG_ID}/{document_id}.pdf"
    await _seed_document(delete_harness.factory, document_id, file_path=file_path)
    fake_es = _FakeES([_es_chunk(document_id)])
    _wire_es(monkeypatch, fake_es)
    service = ks_mod.KnowledgeService()
    delete_file = _wire_file_delete(service, delete_harness.state.events)

    delete_harness.state.fail_commits = 1
    with pytest.raises(AppError) as exc_info:
        await service.delete_knowledge_thoroughly(document_id, ORG_ID)

    assert "injected db commit failure" in exc_info.value.message
    delete_file.assert_not_awaited()
    assert await _database_state(delete_harness.factory, document_id) == {
        "documents": 1,
        "chunks": 1,
        "jobs": 1,
    }
    assert fake_es.chunks == {}, "已知边界：ES 已删而 DB 行仍在"
    assert delete_harness.state.commits == []

    outcome = await service.delete_knowledge_thoroughly(document_id, ORG_ID)

    assert outcome.success is True
    assert outcome.file_cleanup_pending is False
    delete_file.assert_awaited_once_with(file_path)
    assert await _database_state(delete_harness.factory, document_id) == {
        "documents": 0,
        "chunks": 0,
        "jobs": 0,
    }
    assert delete_harness.state.commits == ["commit"]


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
    assert "file_cleanup_pending_ids" not in result
    assert delete_harness.state.commits == ["commit"]
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
async def test_batch_reports_file_cleanup_pending_ids(delete_harness, monkeypatch):
    """批量中单条文件清理失败：仍计删除成功，但结果/审计如实标记待清理。"""
    ok_id, pending_id = "doc-batch-ok", "doc-batch-pending"
    await _seed_document(delete_harness.factory, ok_id, file_path=f"{ORG_ID}/{ok_id}.pdf")
    await _seed_document(delete_harness.factory, pending_id, file_path=f"{ORG_ID}/{pending_id}.pdf")
    fake_es = _FakeES([_es_chunk(ok_id), _es_chunk(pending_id)])
    _wire_es(monkeypatch, fake_es)
    service = ks_mod.KnowledgeService()
    events = delete_harness.state.events

    async def _delete_file(file_path):
        events.append(("file_delete", file_path))
        return file_path != f"{ORG_ID}/{pending_id}.pdf"

    monkeypatch.setattr(
        service.file_upload_service, "delete_file", AsyncMock(side_effect=_delete_file)
    )
    knowledge_api, audit = _wire_batch_endpoint(monkeypatch, service)

    result = await knowledge_api.batch_delete_documents(
        request=SimpleNamespace(document_ids=[ok_id, pending_id], organization_id=ORG_ID),
        current_user=SimpleNamespace(id=1, is_superuser=True),
        db=SimpleNamespace(),
    )

    assert result["success_count"] == 2
    assert result["fail_count"] == 0
    assert result["file_cleanup_pending_ids"] == [pending_id]
    assert "文件清理待重试" in result["message"]
    audit.assert_awaited_once()
    assert "file_cleanup_pending" in audit.await_args.kwargs["detail"]
    for doc_id in (ok_id, pending_id):
        assert await _database_state(delete_harness.factory, doc_id) == {
            "documents": 0,
            "chunks": 0,
            "jobs": 0,
        }


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
    assert delete_harness.state.commits == []
    assert await _database_state(delete_harness.factory, document_id) == {
        "documents": 1,
        "chunks": 1,
        "jobs": 1,
    }
    audit.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_removes_review_meta(delete_harness, monkeypatch):
    """核验身份是 documents 的子表：删除文档必须一并清理，否则 FK 会挡住或留下孤儿。"""
    document_id = "doc-with-review-meta"
    await _seed_document(delete_harness.factory, document_id)
    async with delete_harness.factory() as session:
        session.add(
            ReviewDocumentMeta(
                document_id=document_id,
                organization_id=ORG_ID,
                doc_role="contract",
                family_key="f",
                version_label="v1",
                version_seq=1,
                content_sha256="a" * 64,
                registered_by=1,
            )
        )
        await session.commit()

    fake_es = _FakeES()
    _wire_es(monkeypatch, fake_es)
    service = ks_mod.KnowledgeService()
    outcome = await service.delete_knowledge_thoroughly(document_id, ORG_ID)

    assert outcome.success is True
    async with delete_harness.factory() as session:
        assert await session.get(ReviewDocumentMeta, document_id) is None
