"""核验生命周期的并发不变量（Batch 10，真实 MySQL）。

钉住两条 Batch 5/8 建立的锁纪律——SQLite 忽略 FOR UPDATE，只能用真实 MySQL 验证：

1. **建案侧**：建案在提交前按主键锁定三份文档行并复核存在性。并发删除持有文档
   行锁时，建案要么在锁上等待、要么在当前读里看到「行已消失」，最终都必须
   以 ``REVIEW_DOCUMENT_UNAVAILABLE`` 失败且**不落库**任何案件行。
2. **demo 清理侧**（Batch 8）：``clear_demo_data`` 锁定示例文档后当前读引用
   检查；并发建案同样只能二选一——等待后看到行被删（失败），或先提交让清理
   看到引用（跳过）。本用例覆盖「清理先提交」方向：建案必须拒绝且不落库。

两种交错下断言相同（missing_sides 含被删侧、无 review_case 行），因此不依赖
毫秒级时序；持锁/等待路径通过小 sleep 尽量触发，但不影响断言的确定性。
"""

import asyncio
import os
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.api.v1.endpoints import demo as demo_api
from app.core.config import settings
from app.core.database import Base
from app.exceptions import ConflictError
from app.models.document import Document, DocumentChunk, DocumentStatus, DocumentTag, DocumentType
from app.models.knowledge_job import KnowledgeProcessingJob
from app.models.organization import Organization
from app.models.review import (
    ReviewCase,
    ReviewDocumentMeta,
    ReviewEvidence,
    ReviewExport,
    ReviewItem,
)
from app.models.user import User
from app.models.user_audit import UserActivityLog
from app.services import review_case_service as case_service

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION") != "1",
    reason="需要真实 MySQL（设置 RUN_INTEGRATION=1）",
)

DEMO_DESC = "示例数据 - DocMind 首次体验"


def _doc(doc_id: str, org_id: int, user_id: int, *, description: str | None = None) -> Document:
    return Document(
        id=doc_id,
        filename=f"{doc_id}.txt",
        file_path=f"{org_id}/{doc_id}.txt",
        file_size=10,
        file_type=DocumentType.TXT,
        status=DocumentStatus.INDEXED,
        organization_id=org_id,
        uploaded_by=user_id,
        description=description,
        chunk_count=1,
    )


def _chunk(doc_id: str) -> DocumentChunk:
    return DocumentChunk(
        id=str(uuid.uuid4()),
        document_id=doc_id,
        chunk_index=0,
        chunk_text="第一条 付款期限为30天。",
        original_text="第一条 付款期限为30天。",
        chunk_length=14,
        page_number=1,
        meta_data={"text_provenance": "parser_original"},
    )


async def _seed(factory, suffix: str) -> tuple[int, int, dict[str, Document]]:
    async with factory() as db:
        user = User(
            username=f"conc_{suffix}",
            email=f"conc_{suffix}@example.com",
            hashed_password="not-a-real-hash",
            role="admin",
            is_active=True,
        )
        db.add(user)
        await db.flush()
        org = Organization(name=f"conc-{suffix}", owner_id=user.id)
        db.add(org)
        await db.flush()
        docs: dict[str, Document] = {}
        for side, doc_id in (
            ("policy", f"conc-policy-{suffix}"),
            ("base", f"conc-base-{suffix}"),
            ("revised", f"conc-revised-{suffix}"),
        ):
            doc = _doc(doc_id, org.id, user.id)
            db.add(doc)
            db.add(_chunk(doc_id))
            db.add(
                ReviewDocumentMeta(
                    document_id=doc_id,
                    organization_id=org.id,
                    doc_role="policy" if side == "policy" else "contract",
                    family_key=None if side == "policy" else f"fam-{suffix}",
                    version_label=None if side == "policy" else ("V1" if side == "base" else "V2"),
                    version_seq=None if side == "policy" else (1 if side == "base" else 2),
                    content_sha256="a" * 64,
                    registered_by=user.id,
                )
            )
            docs[side] = doc
        await db.commit()
        return user.id, org.id, docs


async def _cleanup(factory, org_id: int, user_id: int) -> None:
    """按外键顺序清干净（review_evidence/item/export → case → 文档子表 → 文档 → 组织/用户）。"""
    async with factory() as db:
        case_ids = (
            (await db.execute(select(ReviewCase.id).where(ReviewCase.organization_id == org_id)))
            .scalars()
            .all()
        )
        if case_ids:
            await db.execute(delete(ReviewEvidence).where(ReviewEvidence.case_id.in_(case_ids)))
            await db.execute(delete(ReviewItem).where(ReviewItem.case_id.in_(case_ids)))
            await db.execute(delete(ReviewExport).where(ReviewExport.case_id.in_(case_ids)))
            await db.execute(delete(ReviewCase).where(ReviewCase.id.in_(case_ids)))
        await db.execute(
            delete(ReviewDocumentMeta).where(ReviewDocumentMeta.organization_id == org_id)
        )
        await db.execute(
            delete(KnowledgeProcessingJob).where(KnowledgeProcessingJob.organization_id == org_id)
        )
        org_docs = select(Document.id).where(Document.organization_id == org_id)
        await db.execute(delete(DocumentTag).where(DocumentTag.document_id.in_(org_docs)))
        await db.execute(delete(DocumentChunk).where(DocumentChunk.document_id.in_(org_docs)))
        await db.execute(delete(Document).where(Document.organization_id == org_id))
        await db.execute(delete(UserActivityLog).where(UserActivityLog.user_id == user_id))
        await db.execute(delete(Organization).where(Organization.id == org_id))
        await db.execute(delete(User).where(User.id == user_id))
        await db.commit()


@pytest.mark.asyncio
async def test_create_case_rejects_document_deleted_while_lock_contended():
    if "mysql" not in settings.DATABASE_URL:
        pytest.skip("需要 MySQL（SQLite 忽略 FOR UPDATE）")

    engine = create_async_engine(settings.DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex[:8]
    user_id = org_id = None
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        user_id, org_id, docs = await _seed(factory, suffix)

        # 删除侧：先持有 base 文档的行锁（模拟 delete_knowledge_thoroughly 的第一阶段）
        async with factory() as deleter:
            await deleter.execute(
                select(Document).where(Document.id == docs["base"].id).with_for_update()
            )

            # 建案侧：携带先前载入的 Document 对象启动（文档此刻仍存在）
            async def _create() -> Exception | None:
                async with factory() as creator:
                    try:
                        await case_service.create_case(
                            creator,
                            docs=dict(docs),
                            title="concurrent delete",
                            checklist_version="v1",
                            created_by=user_id,
                        )
                    except ConflictError as exc:
                        return exc
                return None

            task = asyncio.create_task(_create())
            await asyncio.sleep(0.4)  # 让建案侧走到 FOR UPDATE 并阻塞在删除侧行锁上

            # 删除侧完成删除并提交：建案应在此后以「文档不可用」失败
            await deleter.execute(
                delete(DocumentChunk).where(DocumentChunk.document_id == docs["base"].id)
            )
            await deleter.execute(
                delete(ReviewDocumentMeta).where(ReviewDocumentMeta.document_id == docs["base"].id)
            )
            await deleter.execute(delete(Document).where(Document.id == docs["base"].id))
            await deleter.commit()

            try:
                error = await asyncio.wait_for(task, timeout=15)
            except TimeoutError:
                pytest.fail(
                    "建案任务 15s 未返回：可能阻塞在文档行锁上（死锁/锁等待），请检查 InnoDB 状态"
                )

        assert error is not None, "并发删除后建案必须失败而不是落库"
        assert error.error_code == "REVIEW_DOCUMENT_UNAVAILABLE"
        assert set(error.detail["missing_sides"]) == {"base"}
        async with factory() as db:
            case_count = (
                await db.execute(
                    select(func.count(ReviewCase.id)).where(ReviewCase.organization_id == org_id)
                )
            ).scalar()
            assert case_count == 0, "复核失败时不得落库任何案件行"
    finally:
        if org_id is not None:
            await _cleanup(factory, org_id, user_id)
        await engine.dispose()


@pytest.mark.asyncio
async def test_demo_clear_then_create_case_cannot_reference_deleted_docs(monkeypatch):
    if "mysql" not in settings.DATABASE_URL:
        pytest.skip("需要 MySQL（SQLite 忽略 FOR UPDATE）")

    engine = create_async_engine(settings.DATABASE_URL)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    suffix = uuid.uuid4().hex[:8]
    user_id = org_id = None
    es_calls: list[str] = []

    async def _fake_es(doc_id: str) -> bool:
        es_calls.append(doc_id)
        return True

    monkeypatch.setattr(demo_api.ElasticsearchTools, "delete_document", _fake_es)

    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        user_id, org_id, docs = await _seed(factory, suffix)
        # 三份文档都标记为用户上传的示例数据（clear_demo_data 的匹配口径）
        async with factory() as db:
            for side in ("policy", "base", "revised"):
                await db.execute(
                    Document.__table__.update()
                    .where(Document.id == docs[side].id)
                    .values(description=DEMO_DESC)
                )
            await db.commit()

        async with factory() as clearer:
            # 先由 clearer 事务持有三份文档行锁（clear_demo_data 自身也会做同样的事；
            # 同一事务内重复加锁安全），保证建案只能在其提交后继续 → 覆盖「清理先提交」方向。
            await clearer.execute(
                select(Document)
                .where(Document.id.in_([docs[side].id for side in ("policy", "base", "revised")]))
                .with_for_update()
            )

            async def _create() -> Exception | None:
                async with factory() as creator:
                    try:
                        await case_service.create_case(
                            creator,
                            docs=dict(docs),
                            title="clear vs create",
                            checklist_version="v1",
                            created_by=user_id,
                        )
                    except ConflictError as exc:
                        return exc
                return None

            task = asyncio.create_task(_create())
            await asyncio.sleep(0.4)
            result = await demo_api.clear_demo_data(
                current_user=SimpleNamespace(id=user_id), db=clearer
            )
            try:
                error = await asyncio.wait_for(task, timeout=15)
            except TimeoutError:
                pytest.fail(
                    "建案任务 15s 未返回：可能阻塞在文档行锁上（死锁/锁等待），请检查 InnoDB 状态"
                )

        assert result["data"]["documents_deleted"] == 3
        assert es_calls, "清理应触发 ES 删除"
        assert error is not None, "示例文档被清理后建案必须失败而不是落库"
        assert error.error_code == "REVIEW_DOCUMENT_UNAVAILABLE"
        assert set(error.detail["missing_sides"]) == {"policy", "base", "revised"}
        async with factory() as db:
            case_count = (
                await db.execute(
                    select(func.count(ReviewCase.id)).where(ReviewCase.organization_id == org_id)
                )
            ).scalar()
            assert case_count == 0, "复核失败时不得落库任何案件行"
    finally:
        if org_id is not None:
            await _cleanup(factory, org_id, user_id)
        await engine.dispose()
