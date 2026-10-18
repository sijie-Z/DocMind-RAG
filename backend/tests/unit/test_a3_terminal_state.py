"""A3-P0：终态写入条件化（真实 SQLAlchemy/SQLite 语义验证）。

事故形态：失败的 run 晚到（重投/并发/兜底），把已经 INDEXED 的文档覆盖回
FAILED——「状态 = INDEXED ⟹ 可检索」的终态被回退，检索内容与状态脱节。

在内存 SQLite 上跑**真实的 UPDATE ... WHERE status != 'INDEXED'**，不用替身，
确保条件确实生效（语句级断言无法证明这一点）。对应迁移无需改动（无新列）。

变异点：把 ``_mark_failed`` / ``_mark_success`` 的 WHERE 条件去掉 → 本文件
INDEXED 用例红（状态被改写）。
"""

import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.knowledge_job import KnowledgeProcessingJob
from app.worker.doc_processor import DocumentProcessor


@pytest.fixture
async def session_factory(monkeypatch):
    import app.worker.doc_processor as dp_mod

    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool
    )
    tables = [Document.__table__, KnowledgeProcessingJob.__table__]
    async with engine.begin() as conn:
        await conn.run_sync(
            lambda sync_conn: Base.metadata.create_all(sync_conn, tables=tables)
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(dp_mod, "AsyncSessionLocal", factory)
    yield factory
    await engine.dispose()


async def _seed(factory, status: DocumentStatus, parse_error=None):
    doc_id = str(uuid.uuid4())
    async with factory() as session:
        session.add(
            Document(
                id=doc_id,
                filename="f.txt",
                file_path="p/f.txt",
                file_size=1,
                file_type=DocumentType.TXT,
                status=status,
                parse_error=parse_error,
                organization_id=1,
                uploaded_by=1,
            )
        )
        await session.commit()
    return doc_id


async def _load(factory, doc_id):
    async with factory() as session:
        return await session.get(Document, doc_id)


CHUNKS = [
    {"chunk_text": "hello", "chunk_index": 0, "chunk_length": 5},
    {"chunk_text": "world", "chunk_index": 1, "chunk_length": 5},
]


class TestFailedWriteConditional:
    @pytest.mark.asyncio
    async def test_late_failure_cannot_overwrite_indexed(self, session_factory):
        """INDEXED 之后到达的失败 run：状态与 parse_error 均不得改变。"""
        doc_id = await _seed(session_factory, DocumentStatus.INDEXED)

        await DocumentProcessor()._mark_failed(doc_id, "late failure", job_id=None)

        doc = await _load(session_factory, doc_id)
        assert doc.status == DocumentStatus.INDEXED, "终态回退：FAILED 覆盖了 INDEXED"
        assert doc.parse_error is None

    @pytest.mark.asyncio
    async def test_failure_still_marks_parsing_document(self, session_factory):
        """回归对照：非终态文档照常推进为 FAILED（含原因）。"""
        doc_id = await _seed(session_factory, DocumentStatus.PARSING)

        await DocumentProcessor()._mark_failed(doc_id, "boom", job_id=None)

        doc = await _load(session_factory, doc_id)
        assert doc.status == DocumentStatus.FAILED
        assert doc.parse_error == "boom"


class TestSuccessWriteConditional:
    @pytest.mark.asyncio
    async def test_success_writes_indexed_metadata(self, session_factory):
        doc_id = await _seed(session_factory, DocumentStatus.PARSING)

        await DocumentProcessor()._mark_success(doc_id, CHUNKS, job_id=None)

        doc = await _load(session_factory, doc_id)
        assert doc.status == DocumentStatus.INDEXED
        assert doc.chunk_count == 2
        assert doc.content_length == 10
        assert doc.indexed_at is not None
        assert doc.parse_error is None

    @pytest.mark.asyncio
    async def test_success_does_not_rewrite_existing_terminal(self, session_factory):
        """已 INDEXED 的文档不会被重复 run 的元数据覆盖（条件更新生效）。"""
        doc_id = await _seed(session_factory, DocumentStatus.INDEXED)
        async with session_factory() as session:
            doc = await session.get(Document, doc_id)
            doc.chunk_count = 99
            await session.commit()

        await DocumentProcessor()._mark_success(doc_id, CHUNKS, job_id=None)

        doc = await _load(session_factory, doc_id)
        assert doc.status == DocumentStatus.INDEXED
        assert doc.chunk_count == 99, "已 INDEXED 的终态数据被过期 run 改写"
