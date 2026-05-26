"""Auth API 集成测试 — 完整的认证流程测试。"""
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


def _make_mock_db_user():
    """创建一个用于 db.get(User, ...) 返回的 mock 用户，is_superuser=True 以绕过权限检查。"""
    u = MagicMock()
    u.id = 1
    u.is_superuser = True
    u.role = "admin"
    return u


def _override_get_db(mock_db=None):
    """返回一个覆盖 get_db 的依赖，提供 mock_db 会话。"""
    if mock_db is None:
        mock_db = MagicMock()
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()
        mock_db.rollback = AsyncMock()
        mock_db.close = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock())
        mock_db.merge = MagicMock(return_value=mock_db)
        mock_db.begin_nested = MagicMock(return_value=_AsyncCtxMgr(mock_db))
        # db.get(User, id) 返回超管用户（绕过权限检查）
        mock_db.get = AsyncMock(return_value=_make_mock_db_user())

    async def _override():
        yield mock_db
        await mock_db.close()

    return _override, mock_db


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
    user.preferences = None
    user.last_login_at = None
    user.created_at = None
    user.updated_at = None
    user.phone = None
    user.avatar_url = None
    user.department = None
    user.position = None
    return user


class TestHealthEndpoint:
    def test_health_returns_200(self, client: TestClient):
        """GET /health 应返回 200。"""
        r = client.get("/health")
        assert r.status_code == 200
        data = r.json()
        # status may be "healthy" or "degraded" depending on service availability
        assert "status" in data


class TestRegisterEndpoint:
    def test_register_success(self, client: TestClient):
        """注册成功应返回 access_token 和 refresh_token。"""
        from app.main import app
        override_func, mock_db = _override_get_db()

        mock_user = _make_mock_user()
        mock_user.id = 1
        mock_user.username = "newuser"
        mock_user.organization_id = None

        # Mock auth_service methods
        with patch("app.api.v1.endpoints.auth.auth_service") as mock_auth_service:
            mock_auth_service.get_user_by_username = AsyncMock(return_value=None)
            mock_auth_service.get_user_by_email = AsyncMock(return_value=None)
            mock_auth_service.create_user = AsyncMock(return_value=mock_user)
            mock_auth_service.create_access_token.return_value = "mock_access_token"
            mock_auth_service.create_refresh_token.return_value = "mock_refresh_token"

            # Use dependency_overrides for get_db
            app.dependency_overrides[get_db] = override_func

            try:
                r = client.post("/api/v1/auth/register", json={
                    "username": "newuser",
                    "email": "new@example.com",
                    "password": "ValidPass123!",
                    "full_name": "New User"
                })
                assert r.status_code == 200, f"Register failed: {r.json()}"
                data = r.json()
                assert data["success"] is True
                assert "access_token" in data["data"]
                assert "refresh_token" in data["data"]
            finally:
                app.dependency_overrides.pop(get_db, None)

    def test_register_duplicate_username(self, client: TestClient):
        """重复用户名应返回 409 Conflict。"""
        from app.main import app
        override_func, mock_db = _override_get_db()

        mock_existing = _make_mock_user()

        with patch("app.api.v1.endpoints.auth.auth_service") as mock_auth_service:
            mock_auth_service.get_user_by_username = AsyncMock(return_value=mock_existing)

            app.dependency_overrides[get_db] = override_func
            try:
                r = client.post("/api/v1/auth/register", json={
                    "username": "existing",
                    "email": "new@example.com",
                    "password": "ValidPass123!"
                })
                assert r.status_code == 409
            finally:
                app.dependency_overrides.pop(get_db, None)

    def test_register_duplicate_email(self, client: TestClient):
        """重复邮箱应返回 409 Conflict。"""
        from app.main import app
        override_func, mock_db = _override_get_db()

        mock_existing = _make_mock_user()

        with patch("app.api.v1.endpoints.auth.auth_service") as mock_auth_service:
            mock_auth_service.get_user_by_username = AsyncMock(return_value=None)
            mock_auth_service.get_user_by_email = AsyncMock(return_value=mock_existing)

            app.dependency_overrides[get_db] = override_func
            try:
                r = client.post("/api/v1/auth/register", json={
                    "username": "newuser",
                    "email": "used@example.com",
                    "password": "ValidPass123!"
                })
                assert r.status_code == 409
            finally:
                app.dependency_overrides.pop(get_db, None)

    def test_register_short_password(self, client: TestClient):
        """密码少于 8 位应返回 422。"""
        r = client.post("/api/v1/auth/register", json={
            "username": "newuser",
            "email": "new@example.com",
            "password": "short"
        })
        assert r.status_code == 422

    def test_register_missing_username(self, client: TestClient):
        """缺少 username 应返回 422。"""
        r = client.post("/api/v1/auth/register", json={
            "email": "new@example.com",
            "password": "ValidPass123!"
        })
        assert r.status_code == 422

    def test_register_invalid_email(self, client: TestClient):
        """无效邮箱应返回 422。"""
        r = client.post("/api/v1/auth/register", json={
            "username": "newuser",
            "email": "not-an-email",
            "password": "ValidPass123!"
        })
        assert r.status_code == 422

    def test_register_empty_body(self, client: TestClient):
        """空请求体应返回 422。"""
        r = client.post("/api/v1/auth/register", json={})
        assert r.status_code == 422


class TestLoginEndpoint:
    def test_login_success(self, client: TestClient):
        """有效凭据登录应返回 access_token。"""
        from app.main import app
        override_func, mock_db = _override_get_db()

        mock_user = _make_mock_user()

        with patch("app.api.v1.endpoints.auth.auth_service") as mock_auth_service:
            mock_auth_service.authenticate_user = AsyncMock(return_value=mock_user)
            mock_auth_service.create_access_token.return_value = "access_token_123"
            mock_auth_service.create_refresh_token.return_value = "refresh_token_123"

            app.dependency_overrides[get_db] = override_func
            try:
                r = client.post("/api/v1/auth/login", data={
                    "username": "testuser",
                    "password": "ValidPass123!"
                })
                assert r.status_code == 200, f"Login failed: {r.json()}"
                data = r.json()
                assert data["success"] is True
                assert data["data"]["access_token"] == "access_token_123"
            finally:
                app.dependency_overrides.pop(get_db, None)

    def test_login_wrong_password(self, client: TestClient):
        """错误密码应返回 401。"""
        from app.main import app
        override_func, mock_db = _override_get_db()

        with patch("app.api.v1.endpoints.auth.auth_service") as mock_auth_service:
            mock_auth_service.authenticate_user = AsyncMock(return_value=None)

            app.dependency_overrides[get_db] = override_func
            try:
                r = client.post("/api/v1/auth/login", data={
                    "username": "testuser",
                    "password": "WrongPass123!"
                })
                assert r.status_code == 401
            finally:
                app.dependency_overrides.pop(get_db, None)

    def test_login_missing_fields(self, client: TestClient):
        """缺少字段应返回 422。"""
        r = client.post("/api/v1/auth/login", data={})
        assert r.status_code == 422

    def test_login_empty_password(self, client: TestClient):
        """空密码应返回 422。"""
        r = client.post("/api/v1/auth/login", data={
            "username": "testuser",
            "password": ""
        })
        assert r.status_code == 422


class TestLoginJsonEndpoint:
    def test_login_json_success(self, client: TestClient):
        """JSON 登录应成功。"""
        from app.main import app
        override_func, mock_db = _override_get_db()

        mock_user = _make_mock_user()

        with patch("app.api.v1.endpoints.auth.auth_service") as mock_auth_service:
            mock_auth_service.authenticate_user = AsyncMock(return_value=mock_user)
            mock_auth_service.create_access_token.return_value = "access_token_123"
            mock_auth_service.create_refresh_token.return_value = "refresh_token_123"

            app.dependency_overrides[get_db] = override_func
            try:
                r = client.post("/api/v1/auth/login/json", json={
                    "username": "testuser",
                    "password": "ValidPass123!"
                })
                assert r.status_code == 200
                data = r.json()
                assert data["success"] is True
            finally:
                app.dependency_overrides.pop(get_db, None)


class TestRefreshEndpoint:
    def test_refresh_success(self, client: TestClient):
        """有效的 refresh_token 应返回新的 access_token。"""
        from app.main import app
        override_func, mock_db = _override_get_db()

        mock_user = _make_mock_user()

        with patch("app.api.v1.endpoints.auth.auth_service") as mock_auth_service:
            mock_auth_service.verify_token.return_value = {
                "sub": "testuser", "user_id": 1, "type": "refresh"
            }
            mock_auth_service.get_user_by_id = AsyncMock(return_value=mock_user)
            mock_auth_service.create_access_token.return_value = "new_access_token"

            app.dependency_overrides[get_db] = override_func
            try:
                r = client.post("/api/v1/auth/refresh", json={
                    "refresh_token": "valid_refresh_token"
                })
                assert r.status_code == 200
                data = r.json()
                assert data["data"]["access_token"] == "new_access_token"
            finally:
                app.dependency_overrides.pop(get_db, None)

    def test_refresh_invalid_token(self, client: TestClient):
        """无效的 refresh_token 应返回 401。"""
        with patch("app.api.v1.endpoints.auth.auth_service") as mock_auth_service:
            mock_auth_service.verify_token.return_value = None

            r = client.post("/api/v1/auth/refresh", json={
                "refresh_token": "invalid_token"
            })
            assert r.status_code == 401

    def test_refresh_empty_token(self, client: TestClient):
        """缺少 token 应返回 422。"""
        r = client.post("/api/v1/auth/refresh", json={})
        assert r.status_code == 422


class TestMeEndpoint:
    def test_get_current_user_success(self, client: TestClient):
        """有效 token 应返回用户信息。"""
        from app.main import app
        mock_user = _make_mock_user()

        async def _override_user():
            return mock_user

        app.dependency_overrides[get_current_user] = _override_user
        try:
            r = client.get("/api/v1/auth/me", headers={"Authorization": "Bearer valid_token"})
            assert r.status_code == 200
            data = r.json()
            assert data["success"] is True
            assert data["data"]["username"] == "testuser"
        finally:
            app.dependency_overrides.pop(get_current_user, None)

    def test_get_current_user_unauthorized(self, client: TestClient):
        """无 token 应返回 401。"""
        r = client.get("/api/v1/auth/me")
        assert r.status_code == 401


class TestLogoutEndpoint:
    def test_logout_success(self, client: TestClient):
        """登出应返回 200。"""
        from app.main import app
        mock_user = _make_mock_user()

        async def _override_user():
            return mock_user

        app.dependency_overrides[get_current_user] = _override_user
        with patch("app.api.v1.endpoints.auth.auth_service") as mock_auth_service:
            mock_auth_service.blacklist_token = AsyncMock(return_value=None)

            try:
                r = client.post("/api/v1/auth/logout", headers={"Authorization": "Bearer valid_token"})
                assert r.status_code == 200
                mock_auth_service.blacklist_token.assert_called_once_with("valid_token")
            finally:
                app.dependency_overrides.pop(get_current_user, None)

    def test_logout_unauthorized(self, client: TestClient):
        """无 token 登出应返回 401（oauth2_scheme 要求认证）。"""
        r = client.post("/api/v1/auth/logout")
        assert r.status_code == 401


class TestPermissionsEndpoint:
    def test_permissions_requires_auth(self, client: TestClient):
        """受保护端点需要认证。"""
        r = client.get("/api/v1/users/me")
        assert r.status_code == 401
