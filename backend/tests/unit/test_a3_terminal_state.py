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
from app.models.knowledge_job import KnowledgeJobStatus, KnowledgeProcessingJob
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


async def _seed_job(factory, doc_id, status: KnowledgeJobStatus):
    job = KnowledgeProcessingJob(
        document_id=doc_id,
        organization_id=1,
        trigger_type="upload",
        status=status,
        retry_count=0,
    )
    async with factory() as session:
        session.add(job)
        await session.commit()
        return job.id


async def _load_job(factory, job_id):
    async with factory() as session:
        return await session.get(KnowledgeProcessingJob, job_id)


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

    @pytest.mark.asyncio
    async def test_failure_does_not_regress_successful_job(self, session_factory):
        """预审建议②：job 已 SUCCESS 时，晚到的失败不得把任务终态回退为 FAILED。"""
        doc_id = await _seed(session_factory, DocumentStatus.PARSING)
        job_id = await _seed_job(
            session_factory, doc_id, KnowledgeJobStatus.SUCCESS
        )

        await DocumentProcessor()._mark_failed(doc_id, "late failure", job_id=job_id)

        doc = await _load(session_factory, doc_id)
        job = await _load_job(session_factory, job_id)
        assert doc.status == DocumentStatus.FAILED
        assert job.status == KnowledgeJobStatus.SUCCESS, (
            "并发成功 run 的 job 终态被晚到的失败覆盖"
        )
        assert job.error_message is None


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


# ══════════════════════════════════════════════════════════════════════════
# 端到端重投回归（独立对抗评审 B2 / M4 / M7）
# ══════════════════════════════════════════════════════════════════════════

class TestStaleRedeliveryEndToEnd:
    """别只测 _mark_failed——真实重投会先经过 _load_and_mark_parsing。"""

    @pytest.mark.asyncio
    async def test_stale_redelivery_after_success_keeps_indexed(
        self, session_factory, monkeypatch
    ):
        """job 已 SUCCESS 的重投：不得进入 PARSING，更不得被打成 FAILED。

        复现评审 B2：成功提交后、偏移提交前崩溃 → 消息重投 → 重投 run 若
        解析失败，修前会把 INDEXED 覆盖为 FAILED（因为 PARSING 已无条件写入）。
        """
        doc_id = await _seed(session_factory, DocumentStatus.INDEXED)
        job_id = await _seed_job(
            session_factory, doc_id, KnowledgeJobStatus.SUCCESS
        )
        processor = DocumentProcessor()
        parsed = []

        async def fake_lock(_document_id):
            return True

        async def fake_release(_document_id):
            pass

        async def failing_parse(*_a, **_k):
            parsed.append(True)
            raise RuntimeError("embedding service 503")

        monkeypatch.setattr(processor, "_acquire_processing_lock", fake_lock)
        monkeypatch.setattr(processor, "_release_processing_lock", fake_release)
        monkeypatch.setattr(processor.parser, "parse_document", failing_parse)

        outcome = await processor.process(doc_id, job_id)

        assert outcome is True, "已完成 job 的重投应视为已了结（可提交偏移）"
        assert parsed == [], "陈旧重投必须在进入 PARSING/解析之前被识别并跳过"
        doc = await _load(session_factory, doc_id)
        assert doc.status == DocumentStatus.INDEXED, (
            "陈旧重投把 INDEXED 终态打回了非索引状态"
        )
        assert doc.parse_error is None

    @pytest.mark.asyncio
    async def test_new_job_reprocess_may_fail_intentionally(
        self, session_factory, monkeypatch
    ):
        """回归对照：重建创建的新 QUEUED job 不受陈旧判定影响，失败如实 FAILED。"""
        doc_id = await _seed(session_factory, DocumentStatus.INDEXED)
        job_id = await _seed_job(
            session_factory, doc_id, KnowledgeJobStatus.QUEUED
        )
        processor = DocumentProcessor()

        async def fake_lock(_document_id):
            return True

        async def fake_release(_document_id):
            pass

        async def failing_parse(*_a, **_k):
            raise RuntimeError("rebuild parse failed")

        monkeypatch.setattr(processor, "_acquire_processing_lock", fake_lock)
        monkeypatch.setattr(processor, "_release_processing_lock", fake_release)
        monkeypatch.setattr(processor.parser, "parse_document", failing_parse)

        outcome = await processor.process(doc_id, job_id)

        assert outcome is False
        doc = await _load(session_factory, doc_id)
        assert doc.status == DocumentStatus.FAILED, (
            "有意重建失败应如实 FAILED（其旧索引已被重建端点删除）"
        )

    @pytest.mark.asyncio
    async def test_superseded_failed_job_redelivery_keeps_indexed(
        self, session_factory, monkeypatch
    ):
        """N1：失败 job 的重投，若期间已有更新的 job 成功 → 旧 run 不得再碰文档。

        旧 job 状态是 FAILED（失败重试是设计内路径），因此不能只看 job 状态；
        必须比较 job 代次：存在更新 job 时，旧消息已被取代。
        """
        doc_id = await _seed(session_factory, DocumentStatus.INDEXED)
        old_job_id = await _seed_job(
            session_factory, doc_id, KnowledgeJobStatus.FAILED
        )
        await _seed_job(session_factory, doc_id, KnowledgeJobStatus.SUCCESS)
        processor = DocumentProcessor()
        parsed = []

        async def fake_lock(_document_id):
            return True

        async def fake_release(_document_id):
            pass

        async def failing_parse(*_a, **_k):
            parsed.append(True)
            raise RuntimeError("embedding service 503")

        monkeypatch.setattr(processor, "_acquire_processing_lock", fake_lock)
        monkeypatch.setattr(processor, "_release_processing_lock", fake_release)
        monkeypatch.setattr(processor.parser, "parse_document", failing_parse)

        outcome = await processor.process(doc_id, old_job_id)

        assert outcome is True, "被更新 job 取代的旧消息应视为已了结"
        assert parsed == [], "被取代的旧 run 不得进入解析"
        doc = await _load(session_factory, doc_id)
        assert doc.status == DocumentStatus.INDEXED

    @pytest.mark.asyncio
    async def test_latest_failed_job_still_retries(
        self, session_factory, monkeypatch
    ):
        """回归对照：没有更新 job 时，FAILED job 的重投必须继续重试（不能一刀切跳过）。"""
        doc_id = await _seed(session_factory, DocumentStatus.FAILED)
        job_id = await _seed_job(
            session_factory, doc_id, KnowledgeJobStatus.FAILED
        )
        processor = DocumentProcessor()
        parsed = []

        async def fake_lock(_document_id):
            return True

        async def fake_release(_document_id):
            pass

        async def failing_parse(*_a, **_k):
            parsed.append(True)
            raise RuntimeError("retry still failing")

        monkeypatch.setattr(processor, "_acquire_processing_lock", fake_lock)
        monkeypatch.setattr(processor, "_release_processing_lock", fake_release)
        monkeypatch.setattr(processor.parser, "parse_document", failing_parse)

        outcome = await processor.process(doc_id, job_id)

        assert outcome is False, "最新 FAILED job 的重投必须真正重试并如实报告失败"
        assert parsed == [True]

    @pytest.mark.asyncio
    async def test_missing_document_terminalizes_orphan_job(
        self, session_factory, monkeypatch
    ):
        """文档不存在：消息终局提交，同时把孤儿 job 收敛为 FAILED（不再每次启动重调）。"""
        job_id = await _seed_job(
            session_factory, "doc-missing", KnowledgeJobStatus.QUEUED
        )
        processor = DocumentProcessor()

        async def fake_lock(_document_id):
            return True

        async def fake_release(_document_id):
            pass

        monkeypatch.setattr(processor, "_acquire_processing_lock", fake_lock)
        monkeypatch.setattr(processor, "_release_processing_lock", fake_release)

        assert await processor.process("doc-missing", job_id) is True

        job = await _load_job(session_factory, job_id)
        assert job.status == KnowledgeJobStatus.FAILED
        assert "文档不存在" in (job.error_message or "")


class TestSuccessJobWrite:
    @pytest.mark.asyncio
    async def test_mark_success_marks_job_success(self, session_factory):
        """`_mark_success` 必须同时把 job 收敛为 SUCCESS（e68f500 的原子性改动）。"""
        doc_id = await _seed(session_factory, DocumentStatus.PARSING)
        job_id = await _seed_job(
            session_factory, doc_id, KnowledgeJobStatus.PROCESSING
        )

        await DocumentProcessor()._mark_success(doc_id, CHUNKS, job_id=job_id)

        doc = await _load(session_factory, doc_id)
        job = await _load_job(session_factory, job_id)
        assert doc.status == DocumentStatus.INDEXED
        assert job.status == KnowledgeJobStatus.SUCCESS
        assert job.finished_at is not None
