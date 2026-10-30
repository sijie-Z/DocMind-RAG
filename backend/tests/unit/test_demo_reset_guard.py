"""Batch 8：demo 重置对「被核验案件引用的示例文档」跳过并如实报告。

覆盖：引用三列（policy/base/revised）+ 已归档案件；会话消息批量删除；
processing job 随之清理；ES 仅对未引用文档触发；全部跳过时的文案。
"""

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.api.v1.endpoints import demo as demo_api
from app.core.database import Base
from app.models.chat import (
    ChatMessage,
    ChatSession,
    ChatSessionStatus,
    DeletedChatSession,
    MessageType,
)
from app.models.document import (
    Document,
    DocumentChunk,
    DocumentStatus,
    DocumentTag,
    DocumentType,
)
from app.models.knowledge_job import KnowledgeProcessingJob
from app.models.review import ReviewCase, ReviewDocumentMeta

ORG = 1
USER = 7
DEMO_DESC = "示例数据 - DocMind 首次体验"
# 三份分别被 policy / base / revised 引用；FREE 未被引用
POLICY_REF = "demo-doc-policy-ref"
BASE_REF = "demo-doc-base-ref"
REVISED_REF = "demo-doc-revised-ref"
FREE = "demo-doc-free"
DEMO_TITLE = demo_api.DEMO_CONVERSATION["title"]

DEMO_DOCS = (POLICY_REF, BASE_REF, REVISED_REF, FREE)


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'demo-reset.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        for doc_id in DEMO_DOCS:
            db.add(
                Document(
                    id=doc_id,
                    filename=f"{doc_id}.txt",
                    file_path=f"{ORG}/{doc_id}.txt",
                    file_size=10,
                    file_type=DocumentType.TXT,
                    status=DocumentStatus.INDEXED,
                    organization_id=ORG,
                    uploaded_by=USER,
                    description=DEMO_DESC,
                    chunk_count=1,
                )
            )
            db.add(
                DocumentChunk(
                    id=str(uuid.uuid4()),
                    document_id=doc_id,
                    chunk_index=0,
                    chunk_text="第1条 示例。",
                    original_text="第1条 示例。",
                    chunk_length=8,
                    page_number=1,
                )
            )
            db.add(
                KnowledgeProcessingJob(
                    document_id=doc_id,
                    organization_id=ORG,
                    trigger_type="upload",
                )
            )
        for doc_id in DEMO_DOCS:
            db.add(DocumentTag(id=f"tag-row-{doc_id}", document_id=doc_id, tag_id="tag-1"))
        db.add(
            ReviewDocumentMeta(
                document_id=POLICY_REF,
                organization_id=ORG,
                doc_role="policy",
                content_sha256="p" * 64,
                registered_by=USER,
            )
        )
        # 活跃案件：三列分别指向三份被引用文档
        db.add(
            ReviewCase(
                id="demo-case-active",
                organization_id=ORG,
                title="引用示例文档的活跃案件",
                checklist_version="v1",
                status="ready",
                created_by=USER,
                lock_version=1,
                policy_document_id=POLICY_REF,
                base_document_id=BASE_REF,
                revised_document_id=REVISED_REF,
            )
        )
        # 已归档案件：经 revised 列再引用 POLICY_REF（归档也算引用，保守跳过）
        db.add(
            ReviewCase(
                id="demo-case-archived",
                organization_id=ORG,
                title="引用示例文档的归档案件",
                checklist_version="v1",
                status="blocked",
                created_by=USER,
                lock_version=1,
                policy_document_id="other-policy",
                base_document_id="other-base",
                revised_document_id=POLICY_REF,
                archived_at=datetime.now(UTC),
                archived_by=USER,
            )
        )
        session_id = str(uuid.uuid4())
        db.add(
            ChatSession(
                id=session_id,
                user_id=USER,
                organization_id=ORG,
                title=DEMO_TITLE,
                status=ChatSessionStatus.ACTIVE,
                message_count=2,
                root_session_id=session_id,
            )
        )
        for seq in (1, 2):
            db.add(
                ChatMessage(
                    id=str(uuid.uuid4()),
                    session_id=session_id,
                    seq=seq,
                    content=f"示例消息 {seq}",
                    message_type=MessageType.USER if seq == 1 else MessageType.ASSISTANT,
                )
            )
        await db.commit()
    yield maker
    await engine.dispose()


@pytest.mark.asyncio
async def test_clear_demo_skips_referenced_documents_and_reports(factory, monkeypatch):
    es_calls: list[str] = []

    async def _fake_es_delete(doc_id: str) -> bool:
        es_calls.append(doc_id)
        return True

    monkeypatch.setattr(demo_api.ElasticsearchTools, "delete_document", _fake_es_delete)

    async with factory() as db:
        result = await demo_api.clear_demo_data(current_user=SimpleNamespace(id=USER), db=db)

    assert result["success"] is True
    data = result["data"]
    assert data["documents_deleted"] == 1
    skipped = {
        item["document_id"]: item["case_count"] for item in data["documents_skipped_referenced"]
    }
    assert skipped == {POLICY_REF: 2, BASE_REF: 1, REVISED_REF: 1}
    assert "保留" in result["message"]
    # ES 仅对未被引用的文档触发（chunk_count=1 → 一次删除）
    assert es_calls == [f"{FREE}_0"]

    async with factory() as db:
        for doc_id in (POLICY_REF, BASE_REF, REVISED_REF):
            assert await db.get(Document, doc_id) is not None, "被引用的示例文档必须保留"
            chunks = (
                (await db.execute(select(DocumentChunk).where(DocumentChunk.document_id == doc_id)))
                .scalars()
                .all()
            )
            assert chunks, "保留文档的分块不得被清理"
            jobs = (
                (
                    await db.execute(
                        select(KnowledgeProcessingJob).where(
                            KnowledgeProcessingJob.document_id == doc_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            assert jobs, "保留文档的 processing job 不得被清理"
            tags = (
                (await db.execute(select(DocumentTag).where(DocumentTag.document_id == doc_id)))
                .scalars()
                .all()
            )
            assert tags, "保留文档的标签不得被清理"
        assert await db.get(ReviewDocumentMeta, POLICY_REF) is not None, (
            "保留文档的核验身份不得被清理"
        )
        assert await db.get(Document, FREE) is None
        assert (
            await db.execute(
                select(KnowledgeProcessingJob).where(KnowledgeProcessingJob.document_id == FREE)
            )
        ).scalars().all() == []
        assert (
            await db.execute(select(DocumentTag).where(DocumentTag.document_id == FREE))
        ).scalars().all() == []
        # 消息与会话被批量删除，墓碑行写入
        assert (
            await db.execute(select(ChatMessage).where(ChatMessage.session_id.is_not(None)))
        ).scalars().all() == []
        assert (
            await db.execute(select(ChatSession).where(ChatSession.title == DEMO_TITLE))
        ).scalars().all() == []
        tombstones = (await db.execute(select(DeletedChatSession))).scalars().all()
        assert len(tombstones) == 1


@pytest.mark.asyncio
async def test_clear_demo_all_skipped_message_is_honest(factory, monkeypatch):
    async def _fake_es_delete(doc_id: str) -> bool:
        return True

    monkeypatch.setattr(demo_api.ElasticsearchTools, "delete_document", _fake_es_delete)

    async with factory() as db:
        db.add(
            ReviewCase(
                id="demo-case-free",
                organization_id=ORG,
                title="把 FREE 也引用上",
                checklist_version="v1",
                status="ready",
                created_by=USER,
                lock_version=1,
                policy_document_id="other-policy",
                base_document_id="other-base",
                revised_document_id=FREE,
            )
        )
        await db.commit()
        result = await demo_api.clear_demo_data(current_user=SimpleNamespace(id=USER), db=db)

    assert result["data"]["documents_deleted"] == 0
    assert len(result["data"]["documents_skipped_referenced"]) == 4
    assert "未删除" in result["message"]
