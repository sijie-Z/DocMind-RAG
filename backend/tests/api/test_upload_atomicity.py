"""Upload Document + Job atomicity, using the real endpoint and ORM with SQLite."""

import asyncio
import io
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.datastructures import UploadFile

import app.models  # noqa: F401 — register every table for create_all
from app.api.v1.endpoints import documents as documents_api
from app.core.database import Base
from app.exceptions import AppError
from app.models.document import Document, DocumentStatus
from app.models.knowledge_job import KnowledgeJobStatus, KnowledgeProcessingJob
from app.models.organization import Organization
from app.models.user import User


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'upload_atomicity.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    yield session_factory
    await engine.dispose()


def _stub_minio(monkeypatch):
    monkeypatch.setattr(
        documents_api.minio_client,
        "stat_object",
        MagicMock(side_effect=RuntimeError("missing")),
    )
    monkeypatch.setattr(documents_api.minio_client, "put_object", MagicMock(return_value=None))


async def _seed_user(db):
    db.add_all(
        [
            Organization(id=1, name="upload-atomic-org", owner_id=1),
            User(
                id=1,
                username="upload-atomic-user",
                email="upload-atomic@example.invalid",
                hashed_password="not-used",
                organization_id=1,
            ),
        ]
    )
    await db.commit()
    return await db.get(User, 1)


async def _upload(db, user):
    upload = UploadFile(io.BytesIO(b"policy original text\n"), filename="policy.txt")
    return await documents_api.upload_document(
        file=upload,
        title=None,
        description=None,
        tags=None,
        db=db,
        current_user=user,
    )


@pytest.mark.asyncio
async def test_joint_commit_failure_persists_nothing_and_never_sends(factory, monkeypatch):
    """A failed Document+Job commit must not leave a recoverable-looking PENDING doc."""
    _stub_minio(monkeypatch)
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(documents_api.kafka_producer, "send_message", send)

    commit_kinds = []
    async with factory() as db:
        user = await _seed_user(db)
        added = []
        real_add = db.add

        def track_add(obj):
            added.append(obj)
            return real_add(obj)

        monkeypatch.setattr(db, "add", track_add)
        real_commit = db.commit

        async def fail_joint_commit():
            has_doc = any(isinstance(obj, Document) for obj in added)
            has_job = any(isinstance(obj, KnowledgeProcessingJob) for obj in added)
            if has_doc and has_job:
                commit_kinds.append("document+job")
                raise RuntimeError("injected joint-commit outage")
            commit_kinds.append("partial")
            await real_commit()

        monkeypatch.setattr(db, "commit", fail_joint_commit)
        with pytest.raises(AppError, match="上传失败"):
            await _upload(db, user)
        send.assert_not_awaited()
        await db.rollback()

    async with factory() as check:
        doc_count = await check.scalar(select(func.count()).select_from(Document))
        job_count = await check.scalar(select(func.count()).select_from(KnowledgeProcessingJob))

    print(
        "UPLOAD_ATOMIC_FAILURE",
        {"commit_kinds": commit_kinds, "doc_count": doc_count, "job_count": job_count},
    )
    assert doc_count == 0
    assert job_count == 0
    assert commit_kinds == ["document+job"], "Document must not have an independent pre-commit"


@pytest.mark.asyncio
async def test_single_joint_commit_is_visible_before_kafka_send(factory, monkeypatch):
    """The one initial commit contains both rows; Kafka sees their committed IDs and path."""
    _stub_minio(monkeypatch)
    commit_calls = 0
    at_send = {}

    async def send_message(_topic, message):
        at_send["commit_calls"] = commit_calls
        at_send["message"] = dict(message)
        async with factory() as check:
            doc = await check.get(Document, message["document_id"])
            job = await check.get(KnowledgeProcessingJob, message["job_id"])
        at_send["document"] = (
            None
            if doc is None
            else {"id": doc.id, "file_path": doc.file_path, "status": doc.status}
        )
        at_send["job"] = (
            None
            if job is None
            else {
                "id": job.id,
                "document_id": job.document_id,
                "status": job.status,
                "local_fallback": job.local_fallback,
            }
        )
        return True

    monkeypatch.setattr(documents_api.kafka_producer, "send_message", send_message)

    async with factory() as db:
        user = await _seed_user(db)
        real_commit = db.commit

        async def recording_commit():
            nonlocal commit_calls
            await real_commit()
            commit_calls += 1

        monkeypatch.setattr(db, "commit", recording_commit)
        result = await _upload(db, user)

    assert result["success"] is True
    assert at_send["commit_calls"] == 1, "exactly one joint commit may precede Kafka send"
    message = at_send["message"]
    visible_doc = at_send["document"]
    visible_job = at_send["job"]
    assert visible_doc is not None and visible_job is not None
    assert message["document_id"] == visible_doc["id"] == visible_job["document_id"]
    assert message["job_id"] == visible_job["id"]
    assert message["file_path"] == visible_doc["file_path"]
    assert visible_doc["status"] is DocumentStatus.PENDING
    assert visible_job["status"] is KnowledgeJobStatus.QUEUED
    assert visible_job["local_fallback"] is True

    async with factory() as check:
        final_doc = await check.get(Document, message["document_id"])
        final_job = await check.get(KnowledgeProcessingJob, message["job_id"])
    assert final_doc is not None and final_job is not None
    assert final_job.local_fallback is False, "Kafka acknowledgement must clear local fallback"
    assert commit_calls == 2, "the later fallback-clearing commit retains its existing semantics"


@pytest.mark.asyncio
async def test_kafka_failure_schedules_committed_local_fallback(factory, monkeypatch):
    """Kafka failure still schedules locally, after a fallback-marked job is committed."""
    _stub_minio(monkeypatch)
    send = AsyncMock(return_value=False)
    monkeypatch.setattr(documents_api.kafka_producer, "send_message", send)
    commit_calls = 0
    at_schedule = {}

    def fake_create_task(coro):
        at_schedule["commit_calls"] = commit_calls
        coro.close()
        task = asyncio.get_running_loop().create_future()
        task.set_result(None)
        return task

    real_create_task = documents_api.asyncio.create_task
    monkeypatch.setattr(documents_api.asyncio, "create_task", fake_create_task)

    async with factory() as db:
        user = await _seed_user(db)
        real_commit = db.commit

        async def recording_commit():
            nonlocal commit_calls
            await real_commit()
            commit_calls += 1

        monkeypatch.setattr(db, "commit", recording_commit)
        try:
            result = await _upload(db, user)
        finally:
            # SQLAlchemy may use asyncio.create_task while closing the session.
            monkeypatch.setattr(documents_api.asyncio, "create_task", real_create_task)
        doc_id = result["data"]["id"]

    assert result["success"] is True
    send.assert_awaited_once()
    assert at_schedule["commit_calls"] == 1
    assert commit_calls == 1
    async with factory() as check:
        doc = await check.get(Document, doc_id)
        job = (await check.execute(select(KnowledgeProcessingJob))).scalars().one()
    assert doc.status is DocumentStatus.PENDING
    assert job.status is KnowledgeJobStatus.QUEUED
    assert job.local_fallback is True


@pytest.mark.asyncio
async def test_local_schedule_failure_still_terminalizes_doc_and_job(factory, monkeypatch):
    """The existing honest FAILED response and terminal DB states must not regress."""
    _stub_minio(monkeypatch)
    monkeypatch.setattr(
        documents_api.kafka_producer,
        "send_message",
        AsyncMock(return_value=False),
    )

    def fail_create_task(coro):
        coro.close()
        raise RuntimeError("injected scheduler outage")

    real_create_task = documents_api.asyncio.create_task
    monkeypatch.setattr(documents_api.asyncio, "create_task", fail_create_task)

    async with factory() as db:
        user = await _seed_user(db)
        try:
            result = await _upload(db, user)
        finally:
            monkeypatch.setattr(documents_api.asyncio, "create_task", real_create_task)
        doc_id = result["data"]["id"]

    assert result["success"] is False
    assert "处理任务启动失败" in result["message"]
    assert result["data"]["status"] == "failed"
    async with factory() as check:
        doc = await check.get(Document, doc_id)
        job = (await check.execute(select(KnowledgeProcessingJob))).scalars().one()
    assert doc.status is DocumentStatus.FAILED
    assert "处理任务启动失败" in doc.parse_error
    assert job.status is KnowledgeJobStatus.FAILED
    assert "处理任务启动失败" in job.error_message
    assert job.finished_at is not None
