"""`_diagnose_document_scope` 的租户边界与故障降级（检索自测空结果诊断）。"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — 注册全部表
from app.api.v1.endpoints.knowledge import _diagnose_document_scope
from app.core.database import Base
from app.models.document import Document, DocumentStatus, DocumentType


def _doc(doc_id: str, org_id: int, *, title: str, status=DocumentStatus.INDEXED, chunks=3):
    return Document(
        id=doc_id,
        filename=f"{doc_id}.txt",
        file_path=f"{org_id}/{doc_id}.txt",
        file_size=1,
        file_type=DocumentType.TXT,
        status=status,
        organization_id=org_id,
        uploaded_by=1,
        title=title,
        chunk_count=chunks,
    )


@pytest.fixture
async def factory():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        session.add_all(
            [
                _doc("doc-org1", 1, title="Org1 合同"),
                _doc("doc-org2", 2, title="Org2 机密合同"),
                _doc(
                    "doc-pending", 1, title="解析中的合同", status=DocumentStatus.PENDING, chunks=0
                ),
            ]
        )
        await session.commit()
    yield maker
    await engine.dispose()


@pytest.mark.asyncio
async def test_scope_filters_by_organization_and_hides_foreign_titles(factory):
    async with factory() as session:
        scope = await _diagnose_document_scope(session, 1, ["doc-org1", "doc-org2", "doc-missing"])

    by_id = {item["document_id"]: item for item in scope}
    assert by_id["doc-org1"]["found"] is True
    assert by_id["doc-org1"]["title"] == "Org1 合同"
    assert by_id["doc-org1"]["status"] == "indexed"
    assert by_id["doc-org1"]["chunk_count"] == 3

    # 跨组织文档只能暴露「不存在」，不得回显标题/状态/chunk 数。
    assert by_id["doc-org2"] == {
        "document_id": "doc-org2",
        "found": False,
        "title": None,
        "status": None,
        "chunk_count": None,
    }
    assert by_id["doc-missing"]["found"] is False
    assert by_id["doc-missing"]["title"] is None


@pytest.mark.asyncio
async def test_scope_reports_real_status_for_pending_document(factory):
    async with factory() as session:
        scope = await _diagnose_document_scope(session, 1, ["doc-pending"])

    assert scope[0]["found"] is True
    assert scope[0]["status"] == "pending"
    assert scope[0]["chunk_count"] == 0


@pytest.mark.asyncio
async def test_scope_db_failure_does_not_break_search():
    db = MagicMock()
    db.execute = AsyncMock(side_effect=RuntimeError("db down"))

    assert await _diagnose_document_scope(db, 1, ["doc-1"]) == []
