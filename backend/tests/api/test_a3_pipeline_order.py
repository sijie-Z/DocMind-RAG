"""A3-P0：重建/上传的顺序一致性——先落库再投递，状态更新带条件。

重建事故形态（修前）：job 只 flush 未 commit 就发 Kafka；worker 可能先处理完
并写 INDEXED，随后请求末尾的 ``doc.status = PENDING; commit()`` 把新终态覆盖
回 PENDING——文档永久停在 PENDING/PARSING。

修后顺序：job 先 commit → 条件 UPDATE（``WHERE status != 'INDEXED'``）并
commit → 最后才发 Kafka。上传路径把 Document 与 Job 放在同一事务里，
flush 取自增 job.id 后只联合 commit 一次，再投递。

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
        monkeypatch.setattr(knowledge_mod, "create_notification", AsyncMock(return_value=None))

        async def fake_send(topic, message):
            events.append("send")
            return True

        monkeypatch.setattr(knowledge_mod.kafka_producer, "send_message", fake_send)

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
        assert "status" not in vars(doc), "重建不得直接赋值 doc.status（会覆盖 worker 的新终态）"

        # 顺序：job 已 commit，且条件更新已 commit，之后才发 Kafka
        send_at = events.index("send")
        before_send = events[:send_at]
        assert any(
            isinstance(e, tuple) and e[0] == "add" and isinstance(e[1], KnowledgeProcessingJob)
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
        assert DocumentStatus.INDEXED in compiled.params.values(), "条件必须是 status != INDEXED"
        assert DocumentStatus.PENDING in compiled.params.values(), "条件更新应把状态写为 PENDING"

        job_obj = next(
            e[1]
            for e in events
            if isinstance(e, tuple) and e[0] == "add" and isinstance(e[1], KnowledgeProcessingJob)
        )
        assert job_obj.local_fallback is False, (
            "N4：Kafka 投递确认后必须清除本机兜底标记（否则重启会重复本地调度）"
        )

    @pytest.mark.asyncio
    async def test_job_commit_failure_happens_before_es_delete(self, monkeypatch):
        """预审补偿：job 落库失败时 ES 尚未删除——不得留下「INDEXED 但搜不到」。"""
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
        monkeypatch.setattr(knowledge_mod, "get_document_for_user", AsyncMock(return_value=doc))
        delete_es = AsyncMock(return_value=None)
        monkeypatch.setattr(knowledge_mod.knowledge_service, "delete_es_by_document_id", delete_es)
        monkeypatch.setattr(knowledge_mod, "create_notification", AsyncMock(return_value=None))

        db = MagicMock()
        db.add = MagicMock()
        db.flush = AsyncMock()  # 变异对照：旧顺序（flush→删 ES）也必须能被判别
        db.commit = AsyncMock(side_effect=RuntimeError("db down at job commit"))
        db.execute = AsyncMock(return_value=MagicMock())

        with pytest.raises(AppError, match="重建知识库失败"):
            await knowledge_mod.rebuild_document_knowledge(
                "doc-1", current_user=SimpleNamespace(id=1), db=db
            )

        # job 落库失败必须发生在删除 ES 之前；否则 INDEXED 文档会变成不可检索
        delete_es.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_rebuild_does_not_call_legacy_es_predelete(self, monkeypatch):
        """即使旧预删服务会失败，重建投递也不再经过该路径。"""
        import app.api.v1.endpoints.knowledge as knowledge_mod

        doc = SimpleNamespace(
            id="doc-1",
            filename="report.pdf",
            title="Report",
            file_path="1/md5.pdf",
            file_type=SimpleNamespace(value="pdf"),
            organization_id=1,
        )
        monkeypatch.setattr(knowledge_mod, "get_document_for_user", AsyncMock(return_value=doc))
        delete_es = AsyncMock(side_effect=RuntimeError("legacy predelete must not run"))
        monkeypatch.setattr(knowledge_mod.knowledge_service, "delete_es_by_document_id", delete_es)
        monkeypatch.setattr(knowledge_mod, "create_notification", AsyncMock(return_value=None))
        monkeypatch.setattr(
            knowledge_mod.kafka_producer, "send_message", AsyncMock(return_value=True)
        )

        db = MagicMock()
        db.add = MagicMock()
        db.commit = AsyncMock()
        db.execute = AsyncMock(return_value=MagicMock())

        result = await knowledge_mod.rebuild_document_knowledge(
            "doc-1", current_user=SimpleNamespace(id=1), db=db
        )

        assert result.success is True
        delete_es.assert_not_awaited()
        assert "status" not in vars(doc), "投递路径不得直接改写文档状态"

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

        monkeypatch.setattr(knowledge_mod, "get_document_for_user", AsyncMock(return_value=doc))
        monkeypatch.setattr(
            knowledge_mod.knowledge_service,
            "delete_es_by_document_id",
            AsyncMock(return_value=None),
        )
        monkeypatch.setattr(knowledge_mod, "create_notification", AsyncMock(return_value=None))
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
                        isinstance(o, KnowledgeProcessingJob) and o.local_fallback for o in captured
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


class TestUploadAtomicCommitBeforeSend:
    def test_upload_commits_document_and_job_once_before_kafka_send(self, client):
        """上传：Document+Job 同一事务 flush 后联合 commit 一次，再发 Kafka。"""
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
            with (
                patch("app.api.v1.endpoints.documents.minio_client") as mock_minio,
                patch(
                    "app.api.v1.endpoints.documents.kafka_producer.send_message",
                    fake_send,
                ),
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

        # Document 与 Job 必须先一起 flush，再联合 commit 一次；投递后仅有
        # 清除 local_fallback 的既有后续 commit，不得有 Document 独立预提交。
        doc_add_at = next(
            i
            for i, e in enumerate(events)
            if isinstance(e, tuple) and e[0] == "add" and e[1] is docs[0]
        )
        job_add_at = next(
            i
            for i, e in enumerate(events)
            if isinstance(e, tuple) and e[0] == "add" and e[1] is jobs[0]
        )
        send_at = events.index("send")
        assert doc_add_at < job_add_at < send_at
        assert events[:send_at].count("commit") == 1, "发送前只能有一次 Document+Job 联合 commit"
        joint_commit_at = events.index("commit")
        assert "flush" in events[job_add_at + 1 : joint_commit_at], (
            "联合 commit 前必须 flush，以取得自增 job.id"
        )
        flush_at = events.index("flush", job_add_at + 1, joint_commit_at)
        assert doc_add_at < job_add_at < flush_at < joint_commit_at < send_at
        assert jobs[0].local_fallback is False, "N4：Kafka 投递确认后必须清除本机兜底标记"
