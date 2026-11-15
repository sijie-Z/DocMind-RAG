"""Auth API 端点集成测试 — 使用 TestClient + mock。"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app.main import app

    return TestClient(app)


class TestHealthEndpoint:
    def test_health_returns_200(self, client: TestClient):
        r = client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] in ("healthy", "degraded", "unhealthy")


class TestLoginEndpoint:
    def test_login_success(self, client: TestClient):
        """登录成功路径（form 提交），并验证注入的 mock 会话真的被端点使用。

        get_db 必须用 ``app.dependency_overrides`` 替换：路由在**注册时**就把
        ``Depends(get_db)`` 的函数对象捕获进 dependant，运行时改模块属性
        （``@patch("app.api.v1.endpoints.auth.get_db")``）对已注册依赖无效。
        另外 ``audit_service.log_activity`` 内部自开 ``AsyncSessionLocal()``、
        不理会调用方传入的 db，不 patch 它同样会绕过 override 去打真库。
        """
        from app.core.database import get_db
        from app.main import app

        mock_user = MagicMock()
        mock_user.id = 1
        mock_user.username = "testuser"
        mock_user.email = "testuser@example.com"
        mock_user.full_name = None
        mock_user.organization_id = None
        mock_user.role = "user"
        mock_user.preferences = None

        mock_db = MagicMock()
        mock_db.add = MagicMock()
        mock_db.flush = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.rollback = AsyncMock()
        # permissions 下发（RBAC 显隐批次）：get_user_permissions 内部
        # `await db.execute(select(Permission.name))` —— MagicMock 的 execute
        # 不是 awaitable，必须换成 AsyncMock 并返回可 scalars().all() 的形状。
        mock_db.execute = AsyncMock(return_value=MagicMock(scalars=lambda: MagicMock(all=lambda: [])))

        async def _override_get_db():
            yield mock_db

        with (
            patch("app.api.v1.endpoints.auth.auth_service") as mock_auth,
            patch("app.api.v1.endpoints.auth.audit_service") as mock_audit,
        ):
            mock_auth.authenticate_user = AsyncMock(return_value=mock_user)
            mock_auth.create_access_token.return_value = "access_token_123"
            mock_auth.create_refresh_token.return_value = "refresh_token_123"
            mock_audit.log_activity = AsyncMock()

            app.dependency_overrides[get_db] = _override_get_db
            try:
                r = client.post(
                    "/api/v1/auth/login",
                    data={"username": "testuser", "password": "ValidPass123!"},
                )
            finally:
                app.dependency_overrides.pop(get_db, None)

        assert r.status_code == 200, r.text
        body = r.json()
        assert body["success"] is True
        data = body["data"]
        assert data["access_token"] == "access_token_123"
        assert data["refresh_token"] == "refresh_token_123"
        assert data["token_type"] == "bearer"
        assert data["user_info"]["username"] == "testuser"

        # 有牙断言：登录成功路径必然经过 UserLoginSession 记录
        # （auth.py 的 db.add + await db.flush）。若 get_db override 失效、
        # 端点回退到真实会话，这里会立即失败，而不是静默打真库后照常通过。
        mock_db.flush.assert_awaited()
        mock_auth.authenticate_user.assert_awaited_once_with(mock_db, "testuser", "ValidPass123!")
        mock_audit.log_activity.assert_awaited_once()

    def test_login_missing_fields(self, client: TestClient):
        """缺少表单字段 → 422 请求校验（handler 不执行）。"""
        r = client.post("/api/v1/auth/login", data={})
        assert r.status_code == 422

    def test_login_empty_password(self, client: TestClient):
        """空密码表单 → 422 请求校验。

        FastAPI 对 Form 字段的空字符串按“未提供”处理
        （fastapi/dependencies/utils.py 的 _get_multidict_value），
        因此这里同样是 missing 校验错误，不会进入 handler 做鉴权。
        """
        r = client.post(
            "/api/v1/auth/login",
            data={"username": "testuser", "password": ""},
        )
        assert r.status_code == 422


class TestRegisterEndpoint:
    def test_register_short_password(self, client: TestClient):
        """Password < 8 chars should be rejected."""
        r = client.post(
            "/api/v1/auth/register",
            json={
                "username": "newuser",
                "email": "new@example.com",
                "password": "short",
                "full_name": "New User",
            },
        )
        assert r.status_code == 422

    def test_register_missing_username(self, client: TestClient):
        r = client.post(
            "/api/v1/auth/register",
            json={"email": "new@example.com", "password": "ValidPass123!"},
        )
        assert r.status_code == 422

    def test_register_invalid_email(self, client: TestClient):
        r = client.post(
            "/api/v1/auth/register",
            json={
                "username": "newuser",
                "email": "not-an-email",
                "password": "ValidPass123!",
            },
        )
        assert r.status_code == 422
