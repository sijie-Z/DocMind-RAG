"""issue #301 批 D API 层回归：#22（agent 落库失败信号）/ #23（上传调度失败如实）。

复现原文归档：`_orchestrate/round2/_docs301/repro_22_baseline.txt` / `repro_23_baseline.txt`。

- **#22**：修前 `/agent/chat` 落库失败（commit 抛异常）被吞成 None —— SSE 里
  chunk + done 照发，done 无 message_id、无 error/warning、无任何提示；刷新后
  本轮问答凭空消失。现在：warning 事件 + done.saved=false + ERROR 日志。
- **#23**：修前本地处理调度失败 → job=FAILED、doc 永远卡 PENDING、响应仍是
  success:true「上传成功」。现在：doc 同步 FAILED（带 parse_error）、响应
  success=false + 失败文案（HTTP 仍 201：文件确实已保存）。

变异验证（tools/mutate.py，行内注明对应点）：
- #22 把 _save_session_interaction 的异常恢复成「吞掉 return None」→ 失败用例红；
- #23 去掉调度失败分支里对 doc 的 FAILED 赋值/如实响应 → 对应用例红。
"""

import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.document import Document, DocumentStatus
from app.models.knowledge_job import KnowledgeJobStatus, KnowledgeProcessingJob


def _user():
    return SimpleNamespace(id=1, organization_id=1, is_superuser=False, role="user", is_active=True)


def _mock_user_for_permission():
    u = MagicMock()
    u.id = 1
    u.is_superuser = True
    u.organization_id = 1
    return u


class _Ctx:
    def __init__(self, obj):
        self._obj = obj

    async def __aenter__(self):
        return self._obj

    async def __aexit__(self, *a):
        return False


class _CountingSession:
    """查询恒「无」、commit 可控（成功/失败）的落库替身。"""

    def __init__(self, commit_fails=False):
        self.added = []
        self._commit_fails = commit_fails

    async def execute(self, stmt):
        res = MagicMock()
        res.scalar_one_or_none = MagicMock(return_value=None)
        return res

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        if self._commit_fails:
            raise RuntimeError("simulated DB write failure")


def _override_http(user=None, db=None):
    from app.main import app

    async def _db():
        return db if db is not None else MagicMock()

    async def _u():
        return user

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = _u

    def _cleanup():
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)

    return _cleanup


def _sse_events(r) -> list[dict]:
    events = []
    for line in r.text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[6:]))
    return events


def _fake_chat(agent_mod):
    async def _chat(**kwargs):
        yield SimpleNamespace(
            type="chunk",
            content="这是回答正文",
            to_sse_dict=lambda: {"type": "chunk", "content": "这是回答正文"},
        )
        yield SimpleNamespace(
            type="done",
            content="",
            task_id="task-state-honesty",
            task_state="completed",
            to_sse_dict=lambda: {
                "type": "done",
                "content": "",
                "task_id": "task-state-honesty",
                "task_state": "completed",
            },
        )

    return _chat


# ══════════════════════════════════════════════════════════════════════════
# #22 — /agent/chat 落库失败必须显式信号
# ══════════════════════════════════════════════════════════════════════════


class TestAgentChatSaveFailureSignals:
    def test_save_failure_emits_warning_and_done_saved_false(self, client: TestClient, monkeypatch):
        """修前（复现 22）：done 照发、无 message_id、无任何失败信号。

        变异点：_save_session_interaction 恢复「except → return None」或删掉
        warning 事件的发射 → 本用例红。
        """
        import app.api.v1.endpoints.agent as agent_mod

        failing = _CountingSession(commit_fails=True)
        monkeypatch.setattr(agent_mod, "AsyncSessionLocal", lambda: _Ctx(failing))
        monkeypatch.setattr(agent_mod.agent_service, "chat", _fake_chat(agent_mod))

        _cleanup = _override_http(user=_user())
        try:
            r = client.post(
                "/api/v1/agent/chat",
                json={"query": "你好", "session_id": "sess-22"},
            )
        finally:
            _cleanup()

        assert r.status_code == 200
        events = _sse_events(r)
        assert any(e.get("type") == "chunk" for e in events), "回答应已流给用户"

        warnings = [e for e in events if e.get("type") == "warning"]
        assert warnings, f"落库失败必须有 warning 事件: {events}"
        assert "保存失败" in warnings[0]["content"]

        done = [e for e in events if e.get("type") == "done"][-1]
        assert done.get("saved") is False, "done.saved 必须为 False"
        assert "message_id" not in done, "落库失败不得携带 message_id"
        assert not any(e.get("type") == "error" for e in events), (
            "回答本身成功，不得伪装成 Agent 执行错误"
        )

    def test_save_success_marks_saved_true_with_message_id(self, client: TestClient, monkeypatch):
        """回归对照：落库成功 → done.saved=true + message_id + 无 warning。"""
        import app.api.v1.endpoints.agent as agent_mod

        ok_session = _CountingSession(commit_fails=False)
        monkeypatch.setattr(agent_mod, "AsyncSessionLocal", lambda: _Ctx(ok_session))
        monkeypatch.setattr(agent_mod.agent_service, "chat", _fake_chat(agent_mod))

        _cleanup = _override_http(user=_user())
        try:
            r = client.post(
                "/api/v1/agent/chat",
                json={"query": "你好", "session_id": "sess-22-ok"},
            )
        finally:
            _cleanup()

        events = _sse_events(r)
        assert not any(e.get("type") == "warning" for e in events)
        done = [e for e in events if e.get("type") == "done"][-1]
        assert done.get("saved") is True
        assert done.get("message_id"), "落库成功应携带 message_id"

    def test_no_session_id_omits_saved_flag(self, client: TestClient, monkeypatch):
        """无 session_id：没有落库动作，done 不带 saved 字段（不臆造状态）。"""
        import app.api.v1.endpoints.agent as agent_mod

        monkeypatch.setattr(agent_mod.agent_service, "chat", _fake_chat(agent_mod))

        _cleanup = _override_http(user=_user())
        try:
            r = client.post("/api/v1/agent/chat", json={"query": "你好"})
        finally:
            _cleanup()

        done = [e for e in _sse_events(r) if e.get("type") == "done"][-1]
        assert "saved" not in done


# ══════════════════════════════════════════════════════════════════════════
# #23 — 上传：本地处理调度失败 → doc 状态与响应如实
# ══════════════════════════════════════════════════════════════════════════


class TestUploadScheduleFailureHonesty:
    def test_schedule_failure_marks_doc_failed_and_response_honest(self, client: TestClient):
        """修前（复现 23）：job=FAILED 但 doc 卡 pending、响应 success:true「上传成功」。

        变异点：去掉 except 分支里的 `new_doc.status = DocumentStatus.FAILED`
        （或让响应走回 success:true 成功形状）→ 本用例红。
        """
        from app.main import app

        captured: list = []
        uploader = _mock_user_for_permission()  # 超管主体：一次性通过上传权限门
        mock_db = MagicMock()
        mock_db.add = MagicMock(side_effect=captured.append)
        mock_db.flush = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()
        mock_db.rollback = AsyncMock()
        mock_db.close = AsyncMock()
        mock_db.merge = AsyncMock(return_value=uploader)
        mock_db.get = AsyncMock(return_value=uploader)
        mock_db.execute = AsyncMock(return_value=MagicMock())

        async def _db():
            yield mock_db

        async def _u():
            return uploader

        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_current_user] = _u

        def _boom(*a, **k):
            raise RuntimeError("no running event loop")

        try:
            with (
                patch("app.api.v1.endpoints.documents.minio_client") as mock_minio,
                patch("app.api.v1.endpoints.documents.kafka_producer") as mock_kafka,
                patch("app.api.v1.endpoints.documents.asyncio.create_task", _boom),
            ):
                mock_minio.stat_object.side_effect = Exception("not found")
                mock_minio.put_object = MagicMock()
                mock_kafka.send_message = AsyncMock(return_value=None)  # Kafka 不可用

                r = client.post(
                    "/api/v1/documents/upload",
                    files={"file": ("t.pdf", io.BytesIO(b"%PDF-1.4 fake"), "application/pdf")},
                    data={"title": "调度失败用例"},
                    headers={"Authorization": "Bearer test_token"},
                )
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)

        assert r.status_code == 201
        body = r.json()
        assert body["success"] is False, "调度失败不得再报 success:true"
        assert "处理任务启动失败" in body["message"]
        assert body["data"]["status"] == "failed"
        assert "处理任务启动失败" in (body["data"]["parse_error"] or "")

        docs = [o for o in captured if isinstance(o, Document)]
        jobs = [o for o in captured if isinstance(o, KnowledgeProcessingJob)]
        assert len(docs) == 1 and len(jobs) == 1
        assert docs[0].status == DocumentStatus.FAILED, "doc 必须同步置 FAILED，不得卡 PENDING"
        assert "处理任务启动失败" in (docs[0].parse_error or "")
        assert jobs[0].status == KnowledgeJobStatus.FAILED
