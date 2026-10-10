"""Chat API 集成测试 — 会话与消息流程。"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import get_current_user


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


class _AsyncCtxMgr:
    """支持 async with 的 mock 上下文管理器。"""
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False


def _make_mock_user():
    user = MagicMock()
    user.id = 1
    user.username = "testuser"
    user.email = "test@example.com"
    user.full_name = "Test User"
    user.role = "user"
    user.organization_id = 1
    user.is_superuser = False
    user.is_active = True
    return user


def _make_mock_db_user():
    """用于 db.get(User, ...) 返回的超管用户，绕过权限检查。"""
    u = MagicMock()
    u.id = 1
    u.is_superuser = True
    u.role = "admin"
    return u


def _make_mock_session(session_id="conv-123"):
    session = MagicMock()
    session.id = session_id
    session.title = "Test Conversation"
    session.status = MagicMock()
    session.status.value = "active"
    session.user_id = 1
    session.organization_id = 1
    session.settings = {}
    session.created_at = None
    session.updated_at = None
    return session


def _override_get_db(mock_db=None):
    if mock_db is None:
        mock_db = MagicMock()
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()
        mock_db.rollback = AsyncMock()
        mock_db.close = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock())
        mock_db.merge = MagicMock(return_value=_make_mock_user())
        mock_db.get = AsyncMock(return_value=_make_mock_db_user())
        mock_db.delete = AsyncMock()
        mock_db.begin_nested = MagicMock(return_value=_AsyncCtxMgr(mock_db))

    async def _override():
        yield mock_db
        await mock_db.close()

    return _override, mock_db


class TestConversations:
    def test_list_conversations(self, client: TestClient):
        """已认证用户应能获取会话列表。"""
        from app.main import app
        mock_user = _make_mock_user()
        mock_session = _make_mock_session()

        override_func, mock_db = _override_get_db()

        # Mock DB chain: execute -> scalars -> all
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [mock_session]

        mock_result = MagicMock()
        mock_result.scalar.return_value = 1  # total count
        mock_result.scalars.return_value = mock_scalars

        mock_db.execute = AsyncMock(return_value=mock_result)

        async def _override_user():
            return mock_user

        app.dependency_overrides[get_db] = override_func
        app.dependency_overrides[get_current_user] = _override_user
        try:
            r = client.get(
                "/api/v1/chat/conversations",
                headers={"Authorization": "Bearer test_token"}
            )
            assert r.status_code == 200
            data = r.json()
            assert data["success"] is True
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)

    def test_list_conversations_empty(self, client: TestClient):
        """无会话时应返回空列表。"""
        from app.main import app
        mock_user = _make_mock_user()

        override_func, mock_db = _override_get_db()

        mock_scalars = MagicMock()
        mock_scalars.all.return_value = []

        mock_result = MagicMock()
        mock_result.scalar.return_value = 0
        mock_result.scalars.return_value = mock_scalars

        mock_db.execute = AsyncMock(return_value=mock_result)

        async def _override_user():
            return mock_user

        app.dependency_overrides[get_db] = override_func
        app.dependency_overrides[get_current_user] = _override_user
        try:
            r = client.get(
                "/api/v1/chat/conversations",
                headers={"Authorization": "Bearer test_token"}
            )
            assert r.status_code == 200
            data = r.json()
            assert data["data"]["total"] == 0
            assert data["data"]["data"] == []
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)

    def test_list_conversations_no_auth(self, client: TestClient):
        """未认证请求应返回 401/403。"""
        r = client.get("/api/v1/chat/conversations")
        assert r.status_code in (401, 403)

    def test_create_conversation(self, client: TestClient):
        """POST /chat/conversations 应创建新会话。"""
        from app.main import app
        mock_user = _make_mock_user()

        override_func, mock_db = _override_get_db()
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        async def _override_user():
            return mock_user

        app.dependency_overrides[get_db] = override_func
        app.dependency_overrides[get_current_user] = _override_user
        try:
            r = client.post(
                "/api/v1/chat/conversations",
                json={"title": "New Chat"},
                headers={"Authorization": "Bearer test_token"}
            )
            assert r.status_code == 200
            data = r.json()
            assert data["success"] is True
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)

    def test_delete_conversation(self, client: TestClient):
        """DELETE /chat/conversations/{id} 应删除会话。"""
        from app.main import app
        mock_user = _make_mock_user()
        mock_session = _make_mock_session()

        override_func, mock_db = _override_get_db()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_session
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.delete = AsyncMock()
        mock_db.commit = AsyncMock()

        async def _override_user():
            return mock_user

        app.dependency_overrides[get_db] = override_func
        app.dependency_overrides[get_current_user] = _override_user
        try:
            r = client.delete(
                "/api/v1/chat/conversations/conv-123",
                headers={"Authorization": "Bearer test_token"}
            )
            assert r.status_code == 200
            data = r.json()
            assert data["success"] is True
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)

    def test_delete_conversation_not_found(self, client: TestClient):
        """不存在的会话删除应返回 404。"""
        from app.main import app
        mock_user = _make_mock_user()

        override_func, mock_db = _override_get_db()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_result)

        async def _override_user():
            return mock_user

        app.dependency_overrides[get_db] = override_func
        app.dependency_overrides[get_current_user] = _override_user
        try:
            r = client.delete(
                "/api/v1/chat/conversations/nonexistent",
                headers={"Authorization": "Bearer test_token"}
            )
            assert r.status_code == 404
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)


class TestConversationMessages:
    def test_get_conversation_messages(self, client: TestClient):
        """获取会话消息应返回消息列表。"""
        from app.main import app
        mock_user = _make_mock_user()
        mock_session = _make_mock_session()

        mock_msg = MagicMock()
        mock_msg.id = "msg-1"
        mock_msg.content = "Hello"
        mock_msg.message_type = MagicMock()
        mock_msg.message_type.value = "user"
        mock_msg.created_at = None
        mock_msg.feedback = None
        mock_msg.feedback_note = None
        mock_msg.meta_data = None

        override_func, mock_db = _override_get_db()

        # First execute call returns session, second returns messages
        mock_session_result = MagicMock()
        mock_session_result.scalar_one_or_none.return_value = mock_session

        mock_msgs_scalars = MagicMock()
        mock_msgs_scalars.all.return_value = [mock_msg]
        mock_msgs_result = MagicMock()
        mock_msgs_result.scalars.return_value = mock_msgs_scalars

        mock_db.execute = AsyncMock(side_effect=[mock_session_result, mock_msgs_result])

        async def _override_user():
            return mock_user

        app.dependency_overrides[get_db] = override_func
        app.dependency_overrides[get_current_user] = _override_user
        try:
            r = client.get(
                "/api/v1/chat/conversations/conv-123",
                headers={"Authorization": "Bearer test_token"}
            )
            assert r.status_code == 200
            data = r.json()
            assert data["success"] is True
            assert len(data["data"]["messages"]) == 1
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)

    def test_get_messages_no_auth(self, client: TestClient):
        """未认证获取消息应返回 401/403。"""
        r = client.get("/api/v1/chat/conversations/conv-123")
        assert r.status_code in (401, 403)

    def test_get_conversation_messages_includes_bound_document_settings(self, client: TestClient):
        """B35：详情响应必须带 settings.bound_document_ids ——
        这是前端 isBoundMode（「仅文档模式」标签/解绑入口）的唯一真值来源；
        旧响应缺 settings 导致标签与解绑入口永不出现。"""
        from app.main import app
        mock_user = _make_mock_user()
        mock_session = _make_mock_session()
        mock_session.settings = {"bound_document_ids": ["doc-1", "doc-2"]}

        override_func, mock_db = _override_get_db()

        mock_session_result = MagicMock()
        mock_session_result.scalar_one_or_none.return_value = mock_session

        mock_msgs_scalars = MagicMock()
        mock_msgs_scalars.all.return_value = []
        mock_msgs_result = MagicMock()
        mock_msgs_result.scalars.return_value = mock_msgs_scalars

        mock_db.execute = AsyncMock(side_effect=[mock_session_result, mock_msgs_result])

        async def _override_user():
            return mock_user

        app.dependency_overrides[get_db] = override_func
        app.dependency_overrides[get_current_user] = _override_user
        try:
            r = client.get(
                "/api/v1/chat/conversations/conv-123",
                headers={"Authorization": "Bearer test_token"}
            )
            assert r.status_code == 200
            data = r.json()
            assert data["success"] is True
            assert data["data"]["settings"]["bound_document_ids"] == ["doc-1", "doc-2"]
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)

    def test_get_conversation_messages_settings_non_dict_falls_back_to_empty(self, client: TestClient):
        """settings 为 None/非 dict 时如实回退 {}（防御脏数据，不抛错）。"""
        from app.main import app
        mock_user = _make_mock_user()
        mock_session = _make_mock_session()
        mock_session.settings = None

        override_func, mock_db = _override_get_db()

        mock_session_result = MagicMock()
        mock_session_result.scalar_one_or_none.return_value = mock_session

        mock_msgs_scalars = MagicMock()
        mock_msgs_scalars.all.return_value = []
        mock_msgs_result = MagicMock()
        mock_msgs_result.scalars.return_value = mock_msgs_scalars

        mock_db.execute = AsyncMock(side_effect=[mock_session_result, mock_msgs_result])

        async def _override_user():
            return mock_user

        app.dependency_overrides[get_db] = override_func
        app.dependency_overrides[get_current_user] = _override_user
        try:
            r = client.get(
                "/api/v1/chat/conversations/conv-123",
                headers={"Authorization": "Bearer test_token"}
            )
            assert r.status_code == 200
            assert r.json()["data"]["settings"] == {}
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)


class TestMessageFeedback:
    def test_update_feedback_success(self, client: TestClient):
        """POST /chat/messages/{id}/feedback 应成功。"""
        from app.main import app
        mock_user = _make_mock_user()
        mock_msg = MagicMock()
        mock_msg.id = "msg-1"
        mock_msg.feedback = None
        mock_msg.feedback_note = None

        override_func, mock_db = _override_get_db()
        mock_scalar = MagicMock()
        mock_scalar.scalar_one_or_none.return_value = mock_msg
        mock_db.execute = AsyncMock(return_value=mock_scalar)
        mock_db.commit = AsyncMock()

        async def _override_user():
            return mock_user

        app.dependency_overrides[get_db] = override_func
        app.dependency_overrides[get_current_user] = _override_user
        try:
            r = client.post(
                "/api/v1/chat/messages/msg-1/feedback",
                json={"feedback": 1, "note": "Great answer!"},
                headers={"Authorization": "Bearer test_token"}
            )
            assert r.status_code == 200
            data = r.json()
            assert data["success"] is True
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)

    def test_update_feedback_not_found(self, client: TestClient):
        """不存在的消息应返回 404。"""
        from app.main import app
        mock_user = _make_mock_user()

        override_func, mock_db = _override_get_db()
        mock_scalar = MagicMock()
        mock_scalar.scalar_one_or_none.return_value = None
        mock_db.execute = AsyncMock(return_value=mock_scalar)

        async def _override_user():
            return mock_user

        app.dependency_overrides[get_db] = override_func
        app.dependency_overrides[get_current_user] = _override_user
        try:
            r = client.post(
                "/api/v1/chat/messages/nonexistent/feedback",
                json={"feedback": 1},
                headers={"Authorization": "Bearer test_token"}
            )
            assert r.status_code == 404
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)


class TestAgentSSEForwarding:
    """issue #222：chat 的 SSE 出口不得静默丢弃白名单外的事件类型。

    修复前 chat.py 的转发是白名单式 if/elif，PER 循环真的会发出的
    `run_report` 与 `execution_context` 没有 case，就此消失；而 /agent 出口
    是全量转发的——同一个 run 从两个入口看到的流不一致。
    """

    def test_run_report_and_execution_context_are_forwarded(self, client: TestClient):
        from app.agent.events import AgentEvent
        from app.agent.service import agent_service
        from app.main import app

        mock_user = _make_mock_user()
        override_func, _ = _override_get_db()

        async def _override_user():
            return mock_user

        async def _fake_chat(**kwargs):
            yield AgentEvent(type="chunk", content="你好")
            yield AgentEvent(type="run_report", content="RUN_REPORT_MARKER")
            yield AgentEvent(
                type="execution_context",
                content={"steps": ["s1"]},
                plan_id="p1",
                plan_progress=1.0,
            )

        app.dependency_overrides[get_db] = override_func
        app.dependency_overrides[get_current_user] = _override_user
        try:
            with patch.object(agent_service, "chat", new=_fake_chat):
                r = client.post(
                    "/api/v1/chat/stream",
                    json={"content": "你好", "useAgent": True},
                )
        finally:
            app.dependency_overrides.pop(get_db, None)
            app.dependency_overrides.pop(get_current_user, None)

        assert r.status_code == 200, r.text
        text = r.text
        assert "event: run_report" in text, f"run_report 被丢弃，实际流: {text[:500]}"
        assert "RUN_REPORT_MARKER" in text
        assert "event: execution_context" in text, f"execution_context 被丢弃，实际流: {text[:500]}"
        assert '"p1"' in text
