"""删除文档的子表清理回归（issue #174）。

背景：`delete_knowledge_thoroughly` 原先只清了 `document_chunks` 一张子表，而
`documents.id` 实测被 **3 张表**引用（document_chunks / document_tags /
knowledge_processing_jobs，外键均**无 ON DELETE CASCADE**）——每次上传必然写入一条
`knowledge_processing_jobs` 行，于是**上传过的文档基本删不掉**：
`DELETE /api/v1/knowledge/document/{id}` 必然被 MySQL 1451 外键错误挡住 → 500。

本文件钉住两条不变式（需要真实 MySQL + 已迁移 schema，``RUN_INTEGRATION=1`` 门控）：

1. **清单完整性**（防「下次加子表又漏」）：代码里删除路径的清理清单
   （`_DOCUMENT_CHILD_MODELS`）必须与 information_schema 里**真实引用 documents
   的表**逐表一致。新增一张引用 documents 的子表（模型 + 迁移）而忘了加清理时，
   本用例变红 —— 清单不再靠记忆，而是与库核对出来的。
2. **行为**：一个文档在 3 张子表都有引用行时，删除必须成功（2xx），且删完后
   三张表里引用它的行数全为 0。

覆盖面：服务层（delete_knowledge_thoroughly）、批量路径（batch_delete_knowledge）、
以及从上传到删除的完整 HTTP 链路（upload → delete，pin 掉 MinIO/Kafka/文档处理/
ES/Redis 缓存等外部依赖，DB 是唯一真实写入面）。
"""

import os
import uuid

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import app.services.knowledge_service as ks_mod
from app.core.config import settings
from app.core.security import get_current_user
from app.models.document import (
    Document,
    DocumentChunk,
    DocumentStatus,
    DocumentTag,
    DocumentType,
    Tag,
)
from app.models.knowledge_job import KnowledgeProcessingJob
from app.models.organization import Organization
from app.models.user import User
from app.models.user_audit import UserActivityLog

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION") != "1",
    reason="需要真实 MySQL（设置 RUN_INTEGRATION=1，并把 DATABASE_URL 指向已迁移的测试库）",
)

# NullPool：pytest-asyncio 每用例一个新事件循环，池中连接跨 loop 复用会报错
# （同 tests/integration/test_ws_auth_lifecycle.py 的处理）。
_engine = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
_Session = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture(autouse=True)
async def _nullpool_app_sessions(monkeypatch):
    """把应用侧会用到本测试路径的会话工厂都换成 NullPool 版。

    nullpool 版 = 同一个真实库、同一条 SQL 路径，只是不复用连接 —— 应用默认引擎
    带连接池，TestClient 每请求 / pytest 每用例都会新开 event loop，池中连接跨
    loop 复用会以与真实症状无关的错误炸掉（'NoneType' object has no attribute 'send'）。
    monkeypatch 保证用例结束后自动还原。

    覆盖：get_db（读全局 AsyncSessionLocal）、knowledge_service 与 audit_service
    （按值 import 了 AsyncSessionLocal）。
    """
    import app.core.database as db_mod
    import app.services.audit_service as audit_mod

    eng = create_async_engine(settings.DATABASE_URL, poolclass=NullPool)
    maker = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "AsyncSessionLocal", maker)
    monkeypatch.setattr(ks_mod, "AsyncSessionLocal", maker)
    monkeypatch.setattr(audit_mod, "AsyncSessionLocal", maker)
    yield
    await eng.dispose()


# information_schema：谁引用了 documents（就是 issue #174 用来列清单的那条查询）
_REFERENCING_TABLES_SQL = text(
    "SELECT kcu.TABLE_NAME FROM information_schema.KEY_COLUMN_USAGE kcu "
    "WHERE kcu.REFERENCED_TABLE_SCHEMA = DATABASE() "
    "AND kcu.REFERENCED_TABLE_NAME = 'documents'"
)


async def _documents_table_exists(session: AsyncSession) -> bool:
    return bool(
        (
            await session.execute(
                text(
                    "SELECT COUNT(*) FROM information_schema.tables "
                    "WHERE table_schema = DATABASE() AND table_name = 'documents'"
                )
            )
        ).scalar()
    )


async def _counts(doc_id: str) -> dict[str, int]:
    """数 4 张表里引用该文档的行数。"""
    async with _Session() as db:

        async def n(model):
            return (
                await db.execute(
                    select(func.count()).select_from(model).where(model.document_id == doc_id)
                )
            ).scalar()

        return {
            "documents": (
                await db.execute(
                    select(func.count()).select_from(Document).where(Document.id == doc_id)
                )
            ).scalar(),
            "document_chunks": await n(DocumentChunk),
            "document_tags": await n(DocumentTag),
            "knowledge_processing_jobs": await n(KnowledgeProcessingJob),
        }


async def _cleanup_document_graph(
    doc_id: str, org_id: int, user_id: int, tag_id: str | None = None
) -> None:
    """按依赖序兜底清理（测试中途失败也不留残留）。

    users 与 organizations 互为外键（user.organization_id / org.owner_id）：
    先解开 user → org 的引用，再删 org（此时已无引用它），最后删 user。
    """
    async with _Session() as db:
        await db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == doc_id))
        await db.execute(delete(DocumentTag).where(DocumentTag.document_id == doc_id))
        await db.execute(
            delete(KnowledgeProcessingJob).where(KnowledgeProcessingJob.document_id == doc_id)
        )
        await db.execute(delete(Document).where(Document.id == doc_id))
        if tag_id:
            await db.execute(delete(Tag).where(Tag.id == tag_id))
        await db.execute(delete(UserActivityLog).where(UserActivityLog.user_id == user_id))
        await db.execute(update(User).where(User.id == user_id).values(organization_id=None))
        await db.execute(delete(Organization).where(Organization.id == org_id))
        await db.execute(delete(User).where(User.id == user_id))
        await db.commit()


@pytest_asyncio.fixture
async def seeded_document():
    """真实 org+user + 一个在 3 张子表都有引用行的文档。"""
    suffix = uuid.uuid4().hex[:8]
    doc_id, tag_id = str(uuid.uuid4()), str(uuid.uuid4())
    async with _Session() as db:
        user = User(
            username=f"ws174_children_{suffix}",
            email=f"ws174_children_{suffix}@example.com",
            hashed_password="not-a-real-bcrypt-hash",
            role="admin",
            is_active=True,
            is_superuser=True,
        )
        db.add(user)
        await db.flush()
        org = Organization(
            name=f"ws174_children_org_{suffix}",
            description="issue #174 children cleanup test",
            color="#18a058",
            is_private=False,
            parent_id=None,
            level=1,
            sort_order=0,
            owner_id=user.id,
        )
        db.add(org)
        await db.flush()
        doc = Document(
            id=doc_id,
            filename="ws174-children.txt",
            file_path=f"1/ws174-{suffix}.txt",
            file_size=1,
            file_type=DocumentType.TXT,
            md5_hash=suffix.ljust(32, "0"),
            status=DocumentStatus.PENDING,
            organization_id=org.id,
            uploaded_by=user.id,
        )
        tag = Tag(id=tag_id, name=f"ws174_children_tag_{suffix}", color="#123456")
        db.add_all([doc, tag])
        await db.flush()
        db.add_all(
            [
                DocumentChunk(
                    id=str(uuid.uuid4()),
                    document_id=doc_id,
                    chunk_index=0,
                    chunk_text="content",
                    chunk_length=7,
                ),
                DocumentTag(id=str(uuid.uuid4()), document_id=doc_id, tag_id=tag.id),
                KnowledgeProcessingJob(
                    document_id=doc_id, organization_id=org.id, trigger_type="upload"
                ),
            ]
        )
        await db.commit()

    yield {"doc_id": doc_id, "tag_id": tag_id, "org_id": org.id, "user_id": user.id}

    await _cleanup_document_graph(doc_id, org.id, user.id, tag_id)


# ── 1. 清单完整性：清理清单 == information_schema 里引用 documents 的表 ──────


@pytest.mark.asyncio
async def test_cleanup_list_matches_information_schema():
    """删除路径的清理清单必须与库中真实引用 documents 的表逐表一致（issue #174）。

    - 从 information_schema 查出引用 documents 的所有表（ground truth）；
    - 与代码里删除路径实际使用的清单（_DOCUMENT_CHILD_MODELS）比对；
    - 新增子表而漏加清理 → 本用例红；清单里出现库里已不存在的表 → 也红。
    """
    async with _Session() as session:
        if not await _documents_table_exists(session):
            pytest.skip("当前库没有 documents 表（schema 未迁移），跳过清单核对")
        referencing = set((await session.execute(_REFERENCING_TABLES_SQL)).scalars().all())

    cleaned = {model.__tablename__ for model in ks_mod._DOCUMENT_CHILD_MODELS}

    assert referencing, (
        "information_schema 查不到任何引用 documents 的表 —— 库 schema 不完整或查询失效，"
        "此时本断言会假绿，故直接失败"
    )
    assert cleaned == referencing, (
        "删除路径的清理清单与 information_schema 不一致（issue #174 的清单漂移）：\n"
        f"  库中引用 documents 的表: {sorted(referencing)}\n"
        f"  清理清单（_DOCUMENT_CHILD_MODELS）: {sorted(cleaned)}\n"
        f"  漏清: {sorted(referencing - cleaned)}\n"
        f"  多清（库中已无此引用）: {sorted(cleaned - referencing)}\n"
        "新增引用 documents 的子表时必须同步加进 _DOCUMENT_CHILD_MODELS"
    )


# ── 2. 行为：三张子表都有引用时删除必须全清 ────────────────────────────────


@pytest.mark.asyncio
async def test_delete_clears_all_three_child_tables(seeded_document):
    """服务层：三张子表都有引用行的文档删除成功，且引用行全清（issue #174 核心）。"""
    doc_id = seeded_document["doc_id"]
    org_id = seeded_document["org_id"]

    before = await _counts(doc_id)
    assert before == {
        "documents": 1,
        "document_chunks": 1,
        "document_tags": 1,
        "knowledge_processing_jobs": 1,
    }, f"种子不符（测试自身问题，非被测代码）: {before}"

    outcome = await ks_mod.knowledge_service.delete_knowledge_thoroughly(doc_id, org_id)
    assert outcome.success is True
    assert outcome.file_cleanup_pending is False

    after = await _counts(doc_id)
    assert after == {
        "documents": 0,
        "document_chunks": 0,
        "document_tags": 0,
        "knowledge_processing_jobs": 0,
    }, f"删除后仍有引用残留（外键没清干净就会这样）: {after}"


@pytest.mark.asyncio
async def test_batch_delete_same_path_clears_children(seeded_document):
    """批量删除走同一条路径：同样把三张子表清干净（issue #174 §顺带检查）。"""
    doc_id = seeded_document["doc_id"]
    org_id = seeded_document["org_id"]

    result = await ks_mod.knowledge_service.batch_delete_knowledge([doc_id], org_id)

    assert result["success_count"] == 1 and result["fail_count"] == 0, result
    after = await _counts(doc_id)
    assert after == {
        "documents": 0,
        "document_chunks": 0,
        "document_tags": 0,
        "knowledge_processing_jobs": 0,
    }, f"批量删除后仍有引用残留: {after}"


# ── 3. 端到端：真实 upload 端点传一个文档 → 真实 delete 端点删掉（2xx）────────


@pytest.fixture(autouse=True)
def _pinned_externals(monkeypatch):
    """钉掉上传/删除链路的外部依赖（与生产语义无关，只为确定性与「不碰真实服务」；
    DB 保持真实）：
    - MinIO：stat 报不存在、put/remove 空操作（不读写任何对象存储）；
    - Kafka：不初始化（TestClient 不跑 lifespan）→ 端点走进程内兜底；
    - 文档处理：空操作（本测试只关心行级效果，不触发真实解析/embedding）；
    - ES：内存替身明确返回成功且无 failures（不接触真实 ES；失败契约由单测覆盖）；
    - RAG 缓存：不走 Redis。
    autouse：服务级用例同样走「删文件 + 清 ES 索引」这两步，不钉会把
    remove_object / ES 查询打到开发机真实服务上。
    """
    from app.core import elasticsearch as es_module
    from app.core.minio_client import minio_client
    from app.worker.doc_processor import processor

    async def _noop_initialize():
        return None

    class _FakeES:
        """严格删除契约：delete_by_query 成功响应 + refresh/回读 count==0。"""

        def __init__(self):
            self.indices = self

        async def delete_by_query(self, **_kwargs):
            return {
                "deleted": 0,
                "failures": [],
                "timed_out": False,
                "_shards": {"failed": 0, "failures": []},
            }

        async def count(self, **_kwargs):
            return {
                "count": 0,
                "failures": [],
                "timed_out": False,
                "_shards": {"failed": 0, "failures": []},
            }

        async def refresh(self, **_kwargs):
            pass

    monkeypatch.setattr(es_module._holder, "initialize", _noop_initialize)
    monkeypatch.setattr(es_module._holder, "_client", _FakeES())

    class _FakeMinio:
        def stat_object(self, *a, **k):
            raise Exception("not found (pinned by test)")

        def put_object(self, *a, **k):
            return None

        def remove_object(self, *a, **k):
            return None

        def bucket_exists(self, *a, **k):
            return True

    monkeypatch.setattr(minio_client, "_client", _FakeMinio())

    async def _fake_process(*a, **k):
        return True

    monkeypatch.setattr(processor, "process", _fake_process)

    import app.rag.cache as cache_mod

    monkeypatch.setattr(cache_mod, "_get_redis", lambda: None)


@pytest.mark.asyncio
async def test_api_upload_then_delete_end_to_end(client: TestClient):
    """端到端（issue #174 验收）：传一个文档 → 能成功删掉（2xx）；
    删完后 document_chunks / document_tags / knowledge_processing_jobs 引用全为 0。"""
    from app.main import app

    suffix = uuid.uuid4().hex[:8]
    async with _Session() as db:
        user = User(
            username=f"ws174_e2e_{suffix}",
            email=f"ws174_e2e_{suffix}@example.com",
            hashed_password="not-a-real-bcrypt-hash",
            role="admin",
            is_active=True,
            is_superuser=True,
        )
        db.add(user)
        await db.flush()
        org = Organization(
            name=f"ws174_e2e_org_{suffix}",
            description="issue #174 e2e",
            color="#18a058",
            is_private=False,
            parent_id=None,
            level=1,
            sort_order=0,
            owner_id=user.id,
        )
        db.add(org)
        await db.flush()
        user.organization_id = org.id  # 让 require_organization 稳定解析到本测试的组织
        await db.commit()
        user_id, org_id = user.id, org.id

    async def _as_user():
        async with _Session() as db:
            return await db.get(User, user_id)

    app.dependency_overrides[get_current_user] = _as_user
    doc_id = None
    try:
        # 传一个文档（真实 upload 端点：写 documents + knowledge_processing_jobs）
        r = client.post(
            "/api/v1/documents/upload",
            files={"file": ("ws174-e2e.txt", b"hello issue 174", "text/plain")},
            data={"title": "ws174 e2e"},
            headers={"Authorization": "Bearer ws174-e2e"},
        )
        assert r.status_code == 201, f"上传失败: {r.status_code} {r.text}"
        doc_id = r.json()["data"]["id"]

        # 上传本身就是删除失败的必要条件：它建出了 job 行（issue #174 的根因）
        before = await _counts(doc_id)
        assert before["knowledge_processing_jobs"] == 1, f"上传后应有 job 行: {before}"
        assert before["documents"] == 1, f"上传后应有文档行: {before}"

        # 删掉它 —— 修复前这里必然 500（1451 外键）
        r = client.delete(
            f"/api/v1/knowledge/document/{doc_id}",
            headers={"Authorization": "Bearer ws174-e2e"},
        )
        assert r.status_code == 200, f"删除失败: {r.status_code} {r.text}"

        after = await _counts(doc_id)
        assert after == {
            "documents": 0,
            "document_chunks": 0,
            "document_tags": 0,
            "knowledge_processing_jobs": 0,
        }, f"删除后子表还有引用行: {after}"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        if doc_id:
            await _cleanup_document_graph(doc_id, org_id, user_id)
        else:
            await _cleanup_document_graph(str(uuid.uuid4()), org_id, user_id)
