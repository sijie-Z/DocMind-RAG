"""API-level security regression tests for the fix batches.

Uses FastAPI TestClient with mocked DB/Redis/auth dependencies, following
the existing tests/integration/test_api_auth.py pattern.
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import get_current_user, optional_current_user


class _AsyncCtxMgr:
    """Async context manager adapter for mocked sessions."""

    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False


def _make_user(is_superuser=False, role="user", organization_id=1, user_id=1):
    u = MagicMock()
    u.id = user_id
    u.username = "testuser"
    u.email = "test@example.com"
    u.full_name = "Test User"
    u.role = role
    u.organization_id = organization_id
    u.is_superuser = is_superuser
    u.is_active = True
    u.preferences = None
    u.hashed_password = "$2b$12$abcdefghijklmnopqrstuv"
    return u


def _mock_db(user=None):
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.rollback = AsyncMock()
    db.close = AsyncMock()
    db.merge = MagicMock(return_value=db)
    db.begin_nested = MagicMock(return_value=_AsyncCtxMgr(db))
    db.get = AsyncMock(return_value=user or _make_user())
    return db


def _override_get_db(db):
    async def _override():
        yield db
        await db.close()

    return _override


def _override_auth(user):
    async def _override_auth_impl():
        return user

    return _override_auth_impl


@pytest.fixture
def client():
    from app.main import app

    return TestClient(app)


class TestLlmConfigAuthorization:
    """llm_config write endpoints require admin (fixed in batch 1)."""

    def test_create_forbidden_for_regular_user(self, client):
        user = _make_user(is_superuser=False, role="user")
        db = _mock_db(user)
        app_ = client.app
        app_.dependency_overrides[get_db] = _override_get_db(db)
        app_.dependency_overrides[get_current_user] = _override_auth(user)
        try:
            resp = client.post("/api/v1/llm-config", json={
                "config_name": "t", "provider": "deepseek",
                "api_key": "sk-test", "base_url": "https://api.deepseek.com",
                "model": "deepseek-v4-flash",
            })
            assert resp.status_code == 403
        finally:
            app_.dependency_overrides.clear()

    def test_create_allowed_for_admin(self, client):
        user = _make_user(is_superuser=False, role="admin")
        db = _mock_db(user)
        app_ = client.app
        app_.dependency_overrides[get_db] = _override_get_db(db)
        app_.dependency_overrides[get_current_user] = _override_auth(user)
        # Redis unavailable -> endpoints fail closed with 500/400 (no crash)
        with patch("app.api.v1.endpoints.llm_config.redis_client", None):
            try:
                resp = client.post("/api/v1/llm-config", json={
                    "config_name": "t", "provider": "deepseek",
                    "api_key": "sk-test", "base_url": "https://api.deepseek.com",
                    "model": "deepseek-v4-flash",
                })
                # Not 403: authorization passed; Redis missing raises ValidationError (400)
                assert resp.status_code != 403
            finally:
                app_.dependency_overrides.clear()


class TestWorkflowExecutionIdor:
    """Execution detail must be owned by the caller (fixed in batch 1)."""

    def test_foreign_execution_returns_404(self, client):
        user = _make_user(is_superuser=False, role="user", user_id=1)
        db = _mock_db(user)

        # execute() first returns the execution row; then the workflow ownership query
        fake_execution = MagicMock()
        fake_execution.id = 99
        fake_execution.workflow_id = 7
        fake_workflow = MagicMock()
        fake_workflow.created_by = 999  # owned by another user
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(side_effect=[fake_execution, fake_workflow])
        db.execute = AsyncMock(return_value=result)

        app_ = client.app
        app_.dependency_overrides[get_db] = _override_get_db(db)
        app_.dependency_overrides[get_current_user] = _override_auth(user)
        try:
            resp = client.get("/api/v1/workflows/executions/99")
            assert resp.status_code == 404
        finally:
            app_.dependency_overrides.clear()


class TestRegisterOrgPolicy:
    """Registration must not honor a client-supplied organization_id."""

    def test_register_ignores_client_org_id(self, client):
        user = _make_user(is_superuser=False, role="user")
        db = _mock_db(user)
        # No existing user/email -> register proceeds
        result = MagicMock()
        result.scalar_one_or_none = MagicMock(return_value=None)
        db.execute = AsyncMock(return_value=result)

        app_ = client.app
        app_.dependency_overrides[get_db] = _override_get_db(db)
        try:
            with patch("app.api.v1.endpoints.auth.auth_service.create_user", new=AsyncMock()) as m:
                with patch("app.api.v1.endpoints.auth.auth_service.create_access_token", return_value="a"):
                    with patch("app.api.v1.endpoints.auth.auth_service.create_refresh_token", return_value="r"):
                        resp = client.post("/api/v1/auth/register", json={
                            "username": "newuser",
                            "email": "new@example.com",
                            "password": "password123",
                            "organization_id": 5,
                        })
                        assert resp.status_code == 200
                        kwargs = m.call_args.kwargs
                        assert kwargs.get("organization_id") is None, "client org_id must be ignored"
        finally:
            app_.dependency_overrides.clear()


class TestFilesProxyOrgBoundary:
    """/files/{path} must reject objects from other organizations."""

    def test_foreign_org_object_rejected(self, client):
        user = _make_user(is_superuser=False, role="user", organization_id=3)
        app_ = client.app
        app_.dependency_overrides[optional_current_user] = _override_auth(user)
        try:
            with patch("app.main.minio_client.get_object") as get_obj:
                resp = client.get("/files/documents/5/abc.pdf")  # org 5 != user org 3
                assert resp.status_code == 403
                get_obj.assert_not_called()
        finally:
            app_.dependency_overrides.clear()

    def test_own_org_object_passes_org_check(self, client):
        user = _make_user(is_superuser=False, role="user", organization_id=3)
        app_ = client.app
        app_.dependency_overrides[optional_current_user] = _override_auth(user)
        try:
            with patch("app.main.minio_client.get_object") as get_obj:
                get_obj.return_value = MagicMock()
                resp = client.get("/files/documents/3/abc.pdf")
                # org matches: proceed to MinIO (will 404 if object missing -> still not 403)
                assert resp.status_code != 403
                get_obj.assert_called_once_with("documents/3/abc.pdf")
        finally:
            app_.dependency_overrides.clear()


class TestWsRejectsRefreshToken:
    """Chat WS must close when presented with a refresh token (batch 1 fix)."""

    def test_refresh_token_closed(self, client):
        from app.services.auth_service import auth_service

        refresh = auth_service.create_refresh_token({"sub": "u", "user_id": 1})
        import starlette.websockets as ws_mod

        # TestClient raises WebSocketDisconnect when the server closes during handshake
        with pytest.raises(ws_mod.WebSocketDisconnect) as exc_info:
            with client.websocket_connect(
                "/api/v1/chat/ws",
                subprotocols=[f"auth.{refresh}"],
            ) as ws:
                ws.receive_text()
        assert exc_info.value.code == 4001
