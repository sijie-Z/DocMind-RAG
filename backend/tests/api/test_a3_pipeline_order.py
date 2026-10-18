"""A3-P0：重建/上传的顺序一致性——先落库再投递，状态更新带条件。

重建事故形态（修前）：job 只 flush 未 commit 就发 Kafka；worker 可能先处理完
并写 INDEXED，随后请求末尾的 ``doc.status = PENDING; commit()`` 把新终态覆盖
回 PENDING——文档永久停在 PENDING/PARSING。

修后顺序：job 先 commit → 条件 UPDATE（``WHERE status != 'INDEXED'``）并
commit → 最后才发 Kafka。上传路径同样先 commit job 再投递。

变异点：恢复「flush + 状态直接赋值 + 最后统一 commit」→ 本文件第一个用例红
（事件顺序与语句条件都不满足）。
"""

import io
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.document import Document, DocumentStatus
from app.models.knowledge_job import KnowledgeProcessingJob


@pytest.fixture
def client():
    from app.main import app

    return TestClient(app)


class TestRebuildOrdering:
    @pytest.mark.asyncio
    async def test_job_committed_and_status_conditional_before_send(self, monkeypatch):
        """顺序：job commit → 条件 UPDATE commit → send；不得直接赋值 doc.status。"""
        import app.api.v1.endpoints.knowledge as knowledge_mod

        doc = SimpleNamespace(
            id="doc-1",
            filename="report.pdf",
            title="Report",
            file_path="1/md5.pdf",
            file_type=SimpleNamespace(value="pdf"),
            organization_id=1,
        )
        events = []
        captured_statements = []

        async def fake_get_document(_db, _user, _doc_id):
            return doc

        monkeypatch.setattr(knowledge_mod, "get_document_for_user", fake_get_document)

        async def fake_delete_es(_doc_id):
            events.append("delete_es")

        monkeypatch.setattr(
            knowledge_mod.knowledge_service,
            "delete_es_by_document_id",
            fake_delete_es,
        )
        monkeypatch.setattr(
            knowledge_mod, "create_notification", AsyncMock(return_value=None)
        )

        async def fake_send(topic, message):
            events.append("send")
            return True

        monkeypatch.setattr(
            knowledge_mod.kafka_producer, "send_message", fake_send
        )

        db = MagicMock()
        db.add = MagicMock(side_effect=lambda obj: events.append(("add", obj)))
        db.flush = AsyncMock()
        db.commit = AsyncMock(side_effect=lambda: events.append("commit"))

        async def fake_execute(stmt):
            captured_statements.append(stmt)
            events.append("execute")
            return MagicMock()

        db.execute = AsyncMock(side_effect=fake_execute)

        result = await knowledge_mod.rebuild_document_knowledge(
            "doc-1",
            current_user=SimpleNamespace(id=1),
            db=db,
        )

        assert result.success is True
        assert "status" not in vars(doc), (
            "重建不得直接赋值 doc.status（会覆盖 worker 的新终态）"
        )

        # 顺序：job 已 commit，且条件更新已 commit，之后才发 Kafka
        send_at = events.index("send")
        before_send = events[:send_at]
        assert any(
            isinstance(e, tuple) and e[0] == "add"
            and isinstance(e[1], KnowledgeProcessingJob)
            for e in before_send
        ), "job 必须已在发送前写入"
        assert before_send.count("commit") >= 2, (
            "发送前必须完成两次 commit：job 落库 + 文档条件更新"
        )
        assert "execute" in before_send and before_send.index("execute") < send_at

        # 条件更新：WHERE status != INDEXED，禁止把已 INDEXED 覆盖回 PENDING
        assert captured_statements, "缺少条件更新语句"
        compiled = captured_statements[0].compile()
        rendered = str(compiled)
        assert "documents.status !=" in rendered, rendered
        assert DocumentStatus.INDEXED in compiled.params.values(), (
            "条件必须是 status != INDEXED"
        )
        assert DocumentStatus.PENDING in compiled.params.values(), (
            "条件更新应把状态写为 PENDING"
        )

    @pytest.mark.asyncio
    async def test_rebuild_fallback_marker_persisted_before_schedule(self, monkeypatch):
        """Kafka 不可用：调度前必须已有「job 带 local_fallback=True」的 commit。"""
        import app.api.v1.endpoints.knowledge as knowledge_mod
        from app.exceptions import AppError

        doc = SimpleNamespace(
            id="doc-1",
            filename="report.pdf",
            title="Report",
            file_path="1/md5.pdf",
            file_type=SimpleNamespace(value="pdf"),
            organization_id=1,
        )
        events = []
        captured = []

        monkeypatch.setattr(
            knowledge_mod, "get_document_for_user", AsyncMock(return_value=doc)
        )
        monkeypatch.setattr(
            knowledge_mod.knowledge_service,
            "delete_es_by_document_id",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(
            knowledge_mod, "create_notification", AsyncMock(return_value=None)
        )
        monkeypatch.setattr(
            knowledge_mod.kafka_producer,
            "send_message",
            AsyncMock(return_value=False),  # Kafka 不可用
        )

        db = MagicMock()
        db.add = MagicMock(
            side_effect=lambda obj: (events.append(("add", obj)), captured.append(obj))
        )
        db.commit = AsyncMock(
            side_effect=lambda: events.append(
                (
                    "commit",
                    any(
                        isinstance(o, KnowledgeProcessingJob) and o.local_fallback
                        for o in captured
                    ),
                )
            )
        )
        db.execute = AsyncMock(return_value=MagicMock())

        dispatch = {}

        def _boom(coro):
            dispatch["events"] = list(events)
            coro.close()
            raise RuntimeError("no running event loop")

        monkeypatch.setattr(knowledge_mod.asyncio, "create_task", _boom)

        with pytest.raises(AppError):
            await knowledge_mod.rebuild_document_knowledge(
                "doc-1", current_user=SimpleNamespace(id=1), db=db
            )

        jobs = [o for o in captured if isinstance(o, KnowledgeProcessingJob)]
        assert len(jobs) == 1
        assert jobs[0].local_fallback is True
        assert ("commit", True) in dispatch["events"], (
            "调度前必须提交过带本机标记的 job（进程重启对账依据）"
        )


class TestUploadJobPersistedBeforeSend:
    def test_upload_commits_job_before_kafka_send(self, client):
        """上传：job 先 commit，再发 Kafka；成功发送不得走本地兜底。"""
        from app.main import app

        events = []
        captured = []

        user = MagicMock()
        user.id = 1
        user.username = "testuser"
        user.organization_id = 1
        user.is_superuser = True
        user.is_active = True

        db = MagicMock()
        db.add = MagicMock(
            side_effect=lambda obj: (events.append(("add", obj)), captured.append(obj))
        )
        db.flush = AsyncMock(side_effect=lambda: events.append("flush"))
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

        async def fake_send(topic, message):
            events.append("send")
            return True

        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_current_user] = _user

        try:
            with patch("app.api.v1.endpoints.documents.minio_client") as mock_minio, \
                 patch(
                     "app.api.v1.endpoints.documents.kafka_producer.send_message",
                     fake_send,
                 ):
                mock_minio.stat_object.side_effect = Exception("not found")
                mock_minio.put_object = MagicMock()

                response = client.post(
                    "/api/v1/documents/upload",
                    files={
                        "file": (
                            "test.pdf",
                            io.BytesIO(b"%PDF-1.4 fake"),
                            "application/pdf",
                        )
                    },
                    data={"title": "Ordering"},
                    headers={"Authorization": "Bearer test_token"},
                )
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)

        assert response.status_code == 201, response.text
        assert response.json()["success"] is True

        jobs = [o for o in captured if isinstance(o, KnowledgeProcessingJob)]
        docs = [o for o in captured if isinstance(o, Document)]
        assert len(jobs) == 1 and len(docs) == 1

        # 判别力（独立对抗评审 M1）：必须是 job add 之后、send 之前发生过
        # commit；修复前是 flush → send → commit，基线代码在本断言下必红。
        job_add_at = next(
            i
            for i, e in enumerate(events)
            if isinstance(e, tuple) and e[0] == "add" and e[1] is jobs[0]
        )
        send_at = events.index("send")
        assert job_add_at < send_at, "job 必须在发送前写入"
        assert "commit" in events[job_add_at + 1:send_at], (
            "job 必须在发送前 commit（仅 flush 不算）"
        )
