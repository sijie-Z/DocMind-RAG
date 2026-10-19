"""W0-4：DELETE /api/v1/files/{id} 必须走统一 knowledge 删除链路（#416 / W0-4）。

修复前：端点走 ``file_service.delete_document`` —— 只删 ``documents`` 行（ORM
级联 chunks），不清 ``document_tags`` / ``knowledge_processing_jobs``（MySQL 下
两张表的 ``document_id`` 外键均为 NO ACTION → 1451 被挡），也不清 ES 索引与
RAG 缓存（RetrievalCache / SemanticCache）。

本文件钉住三件事（纯打桩，不连 DB/ES/Redis）：

1. 端点把删除委托给 ``knowledge_service.delete_knowledge``（与
   ``DELETE /api/v1/knowledge/document/{id}`` 同一条统一链路，内部即
   ``delete_knowledge_thoroughly``），入参携带文档所属 org；旧
   ``file_service.delete_document`` 不再被调用。
2. 统一链路的清理序列：document_chunks → document_tags →
   knowledge_processing_jobs → documents，随后 commit；ES 按 document_id
   删索引；RetrievalCache / SemanticCache 按 org 清缓存。
3. ``FileDeleteResponse`` 契约（success / message）不变；AppError 失败原因
   原样上抛，不被 ``except Exception`` 重包；NotFoundError 保持 404 类型。
"""
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException
from sqlalchemy import Delete

import app.rag.cache as cache_mod
import app.services.knowledge_service as ks_mod
from app.exceptions import AppError, AuthorizationError, NotFoundError

DOC_ID = "doc-w0-4"
ORG_ID = 7


# ── 统一链路：子表 / ES / 缓存清理序列 ─────────────────────────────────────


class _DocResult:
    def __init__(self, doc):
        self._doc = doc

    def scalar_one_or_none(self):
        return self._doc


class _CountResult:
    def __init__(self, count):
        self._count = count

    def scalar(self):
        return self._count


class _RecordingSession:
    """记录 execute 的语句类型与顺序；第一条 select 返回目标文档，
    其后出现的 select 是共享对象引用计数（返回 ``other_refs``）。"""

    def __init__(self, doc, *, other_refs=0):
        self._doc = doc
        self._other_refs = other_refs
        self.statements = []
        self.selects = []
        self.deletes = []
        self.commit_count = 0
        self.deletes_before_commit = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def execute(self, stmt, *args, **kwargs):
        self.statements.append(stmt)
        if isinstance(stmt, Delete):
            self.deletes.append(stmt)
            return MagicMock()
        self.selects.append(stmt)
        if len(self.selects) == 1:
            return _DocResult(self._doc)
        return _CountResult(self._other_refs)

    async def commit(self):
        self.commit_count += 1
        self.deletes_before_commit = len(self.deletes)


class _FakeES:
    def __init__(self):
        self.delete_calls = []

    async def delete_by_query(self, **kwargs):
        self.delete_calls.append(kwargs)


def _recording_cache(records):
    class _Cache:
        def __init__(self):
            pass

        async def clear_for_org(self, organization_id):
            records.append(organization_id)

    return _Cache


@pytest.mark.asyncio
async def test_unified_chain_cleans_children_es_and_caches_in_order(monkeypatch):
    """delete_knowledge_thoroughly：四张表按序清、ES 按 id 清、缓存按 org 清。"""
    svc = ks_mod.KnowledgeService()
    doc = MagicMock()
    doc.file_path = "7/shared-object.pdf"
    doc.organization_id = ORG_ID
    session = _RecordingSession(doc)
    fake_es = _FakeES()
    retrieval_clears: list[int] = []
    semantic_clears: list[int] = []

    monkeypatch.setattr(ks_mod, "AsyncSessionLocal", lambda: session)
    monkeypatch.setattr(ks_mod, "get_elasticsearch", AsyncMock(return_value=fake_es))
    monkeypatch.setattr(cache_mod, "RetrievalCache", _recording_cache(retrieval_clears))
    monkeypatch.setattr(cache_mod, "SemanticCache", _recording_cache(semantic_clears))
    delete_file = AsyncMock(return_value=True)
    monkeypatch.setattr(svc.file_upload_service, "delete_file", delete_file)

    assert await svc.delete_knowledge_thoroughly(DOC_ID, ORG_ID) is True

    # 1. 查文档时就带上 org 过滤（入参 org 真的被消费，而不是只传给缓存）
    assert len(session.statements) >= 1
    select_params = session.statements[0].compile().params
    assert DOC_ID in select_params.values()
    assert ORG_ID in select_params.values()

    # 1b. 共享对象引用计数查询：同一 file_path、排除当前文档
    assert len(session.selects) == 2
    ref_params = session.selects[1].compile().params
    assert "7/shared-object.pdf" in ref_params.values()
    assert DOC_ID in ref_params.values()
    # 谓词方向也要钉住（只查同一 file_path 的「其他」文档）——否则把
    # `==` 写成 `!=`、或漏掉 `id !=` 时参数里同样有这两个值（假绿）。
    ref_sql = str(session.selects[1])
    assert "documents.file_path = " in ref_sql
    assert "documents.id != " in ref_sql

    # 2. 子表清单与顺序 —— 漏掉任一张都会在 MySQL 被 NO ACTION 外键挡住（#174/W0-4）
    deleted_tables = [stmt.table.name for stmt in session.deletes]
    assert deleted_tables == [
        "document_chunks",
        "document_tags",
        "knowledge_processing_jobs",
        "documents",
    ]
    # 3. 全部子表删完才 commit
    assert session.commit_count == 1
    assert session.deletes_before_commit == 4

    # 4. ES 按 document_id 清索引
    assert fake_es.delete_calls == [
        {"index": svc.index_name, "query": {"term": {"document_id": DOC_ID}}}
    ]

    # 5. RAG 缓存按文档所属 org 清（两个命名空间都要清）
    assert retrieval_clears == [ORG_ID]
    assert semantic_clears == [ORG_ID]

    # 6. 物理对象删除使用文档行里的原始 file_path
    delete_file.assert_awaited_once_with("7/shared-object.pdf")


@pytest.mark.asyncio
async def test_unified_chain_keeps_minio_object_shared_by_other_documents(monkeypatch):
    """md5 去重共享对象：仍有其他文档引用同一 file_path 时不得物理删除。

    这是 /files 旧链路的既有保护（md5 相同的上传共享 ``{org}/{md5}.{ext}``
    MinIO 对象）；/files 删除并入统一链路后，该保护必须在统一链路里继续生效。
    """
    svc = ks_mod.KnowledgeService()
    doc = MagicMock()
    doc.file_path = "7/shared-object.pdf"
    doc.organization_id = ORG_ID
    session = _RecordingSession(doc, other_refs=1)

    monkeypatch.setattr(ks_mod, "AsyncSessionLocal", lambda: session)
    monkeypatch.setattr(ks_mod, "get_elasticsearch", AsyncMock(return_value=_FakeES()))
    monkeypatch.setattr(cache_mod, "RetrievalCache", _recording_cache([]))
    monkeypatch.setattr(cache_mod, "SemanticCache", _recording_cache([]))
    delete_file = AsyncMock(return_value=True)
    monkeypatch.setattr(svc.file_upload_service, "delete_file", delete_file)

    assert await svc.delete_knowledge_thoroughly(DOC_ID, ORG_ID) is True

    delete_file.assert_not_awaited()
    # DB 侧照常删干净
    assert [stmt.table.name for stmt in session.deletes][-1] == "documents"
    assert session.commit_count == 1


@pytest.mark.asyncio
async def test_unified_chain_survives_es_outage(monkeypatch):
    """ES 不可用是 best-effort：不得挡住 DB 侧删除（否则 /files 删除会 500）。"""
    svc = ks_mod.KnowledgeService()
    doc = MagicMock()
    doc.file_path = None
    doc.organization_id = ORG_ID
    session = _RecordingSession(doc)

    monkeypatch.setattr(ks_mod, "AsyncSessionLocal", lambda: session)
    monkeypatch.setattr(
        ks_mod, "get_elasticsearch", AsyncMock(side_effect=RuntimeError("es down"))
    )
    monkeypatch.setattr(cache_mod, "RetrievalCache", _recording_cache([]))
    monkeypatch.setattr(cache_mod, "SemanticCache", _recording_cache([]))

    assert await svc.delete_knowledge_thoroughly(DOC_ID, ORG_ID) is True
    assert session.commit_count == 1
    assert [stmt.table.name for stmt in session.deletes][-1] == "documents"


# ── 端点：委托统一链路、入参 org、契约与异常透传 ────────────────────────────


def _endpoint_mocks(monkeypatch, *, resolver=None, unified=None):
    from app.api.v1.endpoints import files as files_api
    from app.services.knowledge_service import knowledge_service as ks_singleton

    resolver = resolver or AsyncMock(return_value=_doc())
    unified = unified or AsyncMock(return_value=True)
    legacy = AsyncMock(return_value=True)
    audit = AsyncMock()

    # 钉的是行为而不是「模块里恰好有某个名字」：修复前 get_document_for_user
    # 尚未接入 / knowledge_service 尚未委托，断言会以「未被 await」失败，
    # 而不是在打桩阶段抛 AttributeError。
    monkeypatch.setattr(files_api, "get_document_for_user", resolver, raising=False)
    monkeypatch.setattr(ks_singleton, "delete_knowledge", unified)
    monkeypatch.setattr(files_api.file_service, "delete_document", legacy)
    monkeypatch.setattr(files_api.audit_service, "log_activity", audit)
    return files_api, resolver, unified, legacy, audit


def _doc():
    doc = MagicMock()
    doc.organization_id = ORG_ID
    doc.filename = "w0-4.pdf"
    return doc


async def _call_delete(files_api, *, document_id=DOC_ID):
    user = MagicMock()
    user.id = 42
    return await files_api.delete_document(
        request=MagicMock(),
        document_id=document_id,
        current_user=user,
        db=MagicMock(),
    )


@pytest.mark.asyncio
async def test_files_delete_delegates_to_knowledge_chain_with_org(monkeypatch):
    files_api, resolver, unified, legacy, audit = _endpoint_mocks(monkeypatch)

    response = await _call_delete(files_api)

    # 归属 + org 校验先做（越权/不存在在进入删除前分流）
    resolver.assert_awaited_once()
    assert resolver.await_args.args[2] == DOC_ID
    # 删除委托统一链路，且 org 入参来自文档行
    unified.assert_awaited_once_with(DOC_ID, ORG_ID)
    # 旧链路不得再被触碰（它不清 tags/jobs/ES/缓存）
    legacy.assert_not_awaited()
    audit.assert_awaited_once()

    # FileDeleteResponse 契约不变
    assert response.success is True
    assert response.message == "删除成功"


@pytest.mark.asyncio
async def test_files_delete_surfaces_unified_chain_failure_reason(monkeypatch):
    """统一链路失败 → AppError 原样上抛（500 可定位），且不记成功审计。"""
    failure = AppError("删除文档失败: IntegrityError: (1451, 'foreign key')")
    files_api, _resolver, _unified, legacy, audit = _endpoint_mocks(
        monkeypatch, unified=AsyncMock(side_effect=failure)
    )

    with pytest.raises(AppError) as exc_info:
        await _call_delete(files_api)

    assert "IntegrityError" in exc_info.value.message
    assert "1451" in exc_info.value.message
    legacy.assert_not_awaited()
    audit.assert_not_awaited()


@pytest.mark.asyncio
async def test_files_delete_passes_through_not_found(monkeypatch):
    """文档不存在 → NotFoundError（404），不得被端点的 except Exception 重包成 500。"""
    files_api, _resolver, unified, _legacy, audit = _endpoint_mocks(
        monkeypatch, resolver=AsyncMock(side_effect=NotFoundError("文档不存在"))
    )

    with pytest.raises(NotFoundError) as exc_info:
        await _call_delete(files_api)

    assert exc_info.value.status_code == 404
    unified.assert_not_awaited()
    audit.assert_not_awaited()


@pytest.mark.asyncio
async def test_files_delete_reports_service_false_without_audit(monkeypatch):
    """统一链路返回 False（当前不可达，契约兜底）→ success=false 且不记审计。

    旧端点的 FileDeleteResponse 语义是「200 + success 布尔」；消费返回值
    可避免将来 service 出现 False 分支时响应谎报成功。
    """
    files_api, _resolver, _unified, legacy, audit = _endpoint_mocks(
        monkeypatch, unified=AsyncMock(return_value=False)
    )

    response = await _call_delete(files_api)

    assert response.success is False
    assert response.message == "删除失败"
    legacy.assert_not_awaited()
    audit.assert_not_awaited()


@pytest.mark.asyncio
async def test_files_delete_passes_through_cross_org_authorization(monkeypatch):
    """跨组织 → AuthorizationError（403，与 /knowledge/document/{id} 同语义）。"""
    files_api, _resolver, unified, _legacy, audit = _endpoint_mocks(
        monkeypatch, resolver=AsyncMock(side_effect=AuthorizationError("无权访问该文档"))
    )

    with pytest.raises(AuthorizationError) as exc_info:
        await _call_delete(files_api)

    assert exc_info.value.status_code == 403
    unified.assert_not_awaited()
    audit.assert_not_awaited()


# ── 旧 service 方法：兼容包装也必须并入统一链路 ─────────────────────────────


@pytest.mark.asyncio
async def test_file_service_delete_document_delegates_to_unified_chain(monkeypatch):
    """file_service.delete_document 不再保留只删主表的旧实现（防未来误用）。"""
    from app.services.file_service import file_service as fs_singleton
    from app.services.knowledge_service import knowledge_service as ks_singleton

    unified = AsyncMock(return_value=True)
    monkeypatch.setattr(ks_singleton, "delete_knowledge_thoroughly", unified)

    result = await fs_singleton.delete_document(DOC_ID, "7", user_id="42")

    assert result is True
    unified.assert_awaited_once_with(DOC_ID, 7)


@pytest.mark.asyncio
async def test_file_service_delete_document_rejects_missing_org(monkeypatch):
    """org 缺失/非法时保持旧语义（404），不得退化成无 org 过滤的删除。"""
    from app.services.file_service import file_service as fs_singleton
    from app.services.knowledge_service import knowledge_service as ks_singleton

    unified = AsyncMock(return_value=True)
    monkeypatch.setattr(ks_singleton, "delete_knowledge_thoroughly", unified)

    with pytest.raises(HTTPException) as exc_info:
        await fs_singleton.delete_document(DOC_ID, None, user_id="42")

    assert exc_info.value.status_code == 404
    unified.assert_not_awaited()
