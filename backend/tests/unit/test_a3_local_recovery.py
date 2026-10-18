"""A3-P0：进程内 fallback 持久化——先落 DB 标记，进程重启后启动对账回收。

事故形态：Kafka 不可用时只用 ``asyncio.create_task`` 调度，进程在任务完成前
退出 = 任务凭空消失，文档永久 PENDING/PARSING（没有任何恢复路径）。

修后：调度前先把 ``knowledge_processing_jobs.local_fallback=True`` 落库；
进程启动时按「local_fallback 且状态 ∈ {QUEUED, PROCESSING}」对账，重新调度。
任务终局由 ``_mark_success`` / ``_mark_failed`` 写入，终局任务不再被回收。

变异点：去掉上传/重建/文件服务里的 local_fallback 标记 → 标记用例红；去掉
启动对账调用/查询条件 → 对账用例红。
"""

import asyncio
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.knowledge_job import KnowledgeJobStatus, KnowledgeProcessingJob


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _Session:
    def __init__(self, rows, captured):
        self._rows = rows
        self._captured = captured

    async def execute(self, stmt):
        self._captured.append(stmt)
        return _Result(self._rows)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class TestStartupReconciliation:
    @pytest.mark.asyncio
    async def test_dispatch_pending_local_jobs_and_filter_query(self):
        """模拟进程重启：对账查询命中未终局本机任务并逐条重新调度。"""
        from app.services.local_processing import reconcile_local_jobs

        jobs = [
            SimpleNamespace(document_id="doc-1", id=11),
            SimpleNamespace(document_id="doc-2", id=12),
        ]
        captured = []
        processed = []

        async def fake_process(document_id, job_id):
            processed.append((document_id, job_id))
            return True

        recovered = await reconcile_local_jobs(
            process=fake_process,
            session_factory=lambda: _Session(jobs, captured),
        )
        await asyncio.sleep(0)  # 让 create_task 调度出的协程真正执行

        assert recovered == 2
        assert processed == [("doc-1", 11), ("doc-2", 12)]

        assert captured, "对账必须查询知识任务表"
        compiled = captured[0].compile()
        rendered = str(compiled)
        assert "knowledge_processing_jobs.local_fallback" in rendered
        assert "is true" in rendered.lower() or "is 1" in rendered.lower(), (
            f"查询必须限定 local_fallback = true: {rendered}"
        )
        params = compiled.params.values()
        statuses = [
            v for v in params
            if isinstance(v, list)
        ]
        assert statuses and set(statuses[0]) == {
            KnowledgeJobStatus.QUEUED,
            KnowledgeJobStatus.PROCESSING,
        }, "只回收未终局状态：QUEUED / PROCESSING"

    @pytest.mark.asyncio
    async def test_terminal_jobs_are_not_dispatched_by_query(self):
        """回归对照：查询条件本身排除终局任务（SQL 侧过滤，不靠调度侧兜底）。"""
        from app.services.local_processing import reconcile_local_jobs

        captured = []

        async def fake_process(document_id, job_id):  # pragma: no cover
            raise AssertionError("终局任务不应被调度")

        recovered = await reconcile_local_jobs(
            process=fake_process,
            session_factory=lambda: _Session([], captured),
        )

        assert recovered == 0
        compiled = captured[0].compile()
        statuses = [v for v in compiled.params.values() if isinstance(v, list)]
        assert KnowledgeJobStatus.SUCCESS not in statuses[0]
        assert KnowledgeJobStatus.FAILED not in statuses[0]


def _upload_user():
    user = MagicMock()
    user.id = 1
    user.username = "testuser"
    user.organization_id = 1
    user.is_superuser = True
    user.is_active = True
    return user


class TestUploadFallbackMarker:
    def test_marker_persisted_before_scheduling_task(self):
        """上传兜底：create_task 时 job.local_fallback 已为 True 且已 commit。"""
        from app.main import app

        events = []
        captured = []
        user = _upload_user()

        db = MagicMock()
        db.add = MagicMock(
            side_effect=lambda obj: (events.append(("add", obj)), captured.append(obj))
        )
        db.flush = AsyncMock()
        db.commit = AsyncMock(side_effect=lambda: events.append("commit"))
        db.refresh = AsyncMock()
        db.rollback = AsyncMock()
        db.close = AsyncMock()
        db.merge = AsyncMock(return_value=user)
        db.get = AsyncMock(return_value=user)
        db.execute = AsyncMock(return_value=MagicMock())

        async def _db():
            yield db

        async def _user():
            return user

        snapshot = {}

        def _boom(coro):
            jobs = [o for o in captured if isinstance(o, KnowledgeProcessingJob)]
            snapshot["local_fallback"] = jobs[0].local_fallback if jobs else None
            snapshot["events"] = list(events)
            coro.close()
            raise RuntimeError("no running event loop")

        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_current_user] = _user

        try:
            with patch("app.api.v1.endpoints.documents.minio_client") as mock_minio, \
                 patch(
                     "app.api.v1.endpoints.documents.kafka_producer.send_message",
                     AsyncMock(return_value=False),  # Kafka 不可用 → 本机兜底
                 ), \
                 patch(
                     "app.api.v1.endpoints.documents.asyncio.create_task", _boom
                 ):
                mock_minio.stat_object.side_effect = Exception("not found")
                mock_minio.put_object = MagicMock()

                response = TestClient(app).post(
                    "/api/v1/documents/upload",
                    files={
                        "file": (
                            "test.pdf",
                            io.BytesIO(b"%PDF-1.4 fake"),
                            "application/pdf",
                        )
                    },
                    data={"title": "Marker"},
                    headers={"Authorization": "Bearer test_token"},
                )
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)

        assert response.status_code == 201
        assert snapshot["local_fallback"] is True, (
            "调度前必须已把本机处理标记写到 job 上（进程重启对账的依据）"
        )
        assert "commit" in snapshot["events"], (
            "标记必须在 create_task 之前 commit 落库"
        )


class TestFileServiceMarker:
    @pytest.mark.asyncio
    async def test_upload_persists_local_job_before_async_shell(self):
        """file_service 上传：先落 local_fallback job，再调度处理壳。"""
        import app.services.file_service as fs_mod

        added = []
        events = []

        class _FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def execute(self, *a, **k):
                return MagicMock()

            def add(self, obj):
                added.append(obj)

            async def commit(self):
                events.append("commit")

            async def refresh(self, obj):
                from datetime import UTC, datetime

                if getattr(obj, "created_at", None) is None:
                    obj.created_at = datetime.now(UTC)

        service = fs_mod.FileUploadService()
        shell_calls = []

        async def fake_shell(document_id, job_id=None):
            shell_calls.append((document_id, job_id))

        file = MagicMock()
        file.filename = "doc.txt"
        file.content_type = "text/plain"
        file.read = AsyncMock(return_value=b"hello world")
        file.file = io.BytesIO(b"hello world")

        with patch.object(fs_mod, "AsyncSessionLocal", lambda: _FakeSession()), \
             patch.object(service, "_upload_to_minio", AsyncMock(return_value="1/x.txt")), \
             patch.object(
                 fs_mod.FileUploadService,
                 "_process_document_async",
                 side_effect=fake_shell,
             ):
            result = await service.upload_file(
                file=file, user_id=1, organization_id=1
            )

        assert result["document_id"]
        await asyncio.sleep(0)  # 让 create_task 调度出的处理壳真正执行
        jobs = [o for o in added if isinstance(o, KnowledgeProcessingJob)]
        assert len(jobs) == 1, "必须先创建一个本机任务标记"
        assert jobs[0].local_fallback is True
        assert events and events[0] == "commit", "标记必须先 commit"
        assert len(shell_calls) == 1, "随后才调度处理壳"
