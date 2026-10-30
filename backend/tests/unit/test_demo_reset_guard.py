"""Batch 8：demo 重置对「被核验案件引用的示例文档」跳过并如实报告。"""

import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.api.v1.endpoints import demo as demo_api
from app.core.database import Base
from app.models.document import Document, DocumentChunk, DocumentStatus, DocumentType
from app.models.knowledge_job import KnowledgeProcessingJob
from app.models.review import ReviewCase, ReviewDocumentMeta

ORG = 1
USER = 7
DEMO_DESC = "示例数据 - DocMind 首次体验"
REFERENCED = "demo-doc-referenced"
FREE = "demo-doc-free"


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'demo-reset.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        for doc_id in (REFERENCED, FREE):
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
            ReviewDocumentMeta(
                document_id=REFERENCED,
                organization_id=ORG,
                doc_role="contract",
                family_key="demo-fam",
                version_label="V1",
                version_seq=1,
                content_sha256="a" * 64,
                registered_by=USER,
            )
        )
        db.add(
            ReviewCase(
                id="demo-case-1",
                organization_id=ORG,
                title="引用示例文档的案件",
                checklist_version="v1",
                status="blocked",
                created_by=USER,
                lock_version=1,
                policy_document_id="other-policy",
                base_document_id=REFERENCED,
                revised_document_id="other-revised",
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
    assert [item["document_id"] for item in data["documents_skipped_referenced"]] == [REFERENCED]
    assert data["documents_skipped_referenced"][0]["case_count"] == 1
    assert "保留" in result["message"]
    # 只对未被引用的文档触发 ES 清理
    assert es_calls and all(not call.startswith(REFERENCED) for call in es_calls)

    async with factory() as db:
        assert await db.get(Document, REFERENCED) is not None, "被引用的示例文档必须保留"
        assert await db.get(Document, FREE) is None
        meta = await db.get(ReviewDocumentMeta, REFERENCED)
        assert meta is not None, "保留文档的核验身份不得被清理"
        chunks = (
            (await db.execute(select(DocumentChunk).where(DocumentChunk.document_id == REFERENCED)))
            .scalars()
            .all()
        )
        assert chunks, "保留文档的分块不得被清理"
        jobs = (
            (
                await db.execute(
                    select(KnowledgeProcessingJob).where(KnowledgeProcessingJob.document_id == FREE)
                )
            )
            .scalars()
            .all()
        )
        assert jobs == []
