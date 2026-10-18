"""admin 判定契约（T3）：/users/me 与登录 payload 必须下发 is_superuser。

现场：后端鉴权普遍按 `is_superuser or role == "admin"` 判定管理员，但
`/users/me`（users.py）与两条登录路径（form / JSON，auth.py）只下发 `role`，
不下发 `is_superuser`。前端因此只能看到 role —— 「is_superuser=true 但
role=user」的超管看不到管理菜单，路由守卫也会把它重定向回 Dashboard。

本文件钉死契约（修复前红、修复后绿）：
  1. GET /users/me 响应顶层包含布尔 is_superuser，且与用户行一致；
  2. POST /auth/login 与 /auth/login/json 的 data.user_info 包含布尔
     is_superuser，且与用户行一致；
  3. GET /auth/me（同为 UserInfoResponse 契约）同样如实反映 is_superuser。

用例统一用 `role="user"` + `is_superuser=True` 的组合，正是修复前丢失
判定能力的那一类账号。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import get_current_user
from app.main import app
from app.models.user import User
from app.services.auth_service import auth_service


def _superuser_probe(is_superuser: bool) -> User:
    """真模型实例（非 MagicMock）—— /users/me 会逐字段序列化。"""
    return User(
        id=42,
        username="claim_probe",
        email="claim_probe@example.com",
        hashed_password="not-a-real-bcrypt-hash",
        full_name="Claim Probe",
        organization_id=None,
        role="user",
        is_active=True,
        is_superuser=is_superuser,
    )


@pytest.mark.parametrize("is_superuser", [True, False])
def test_users_me_exposes_is_superuser(is_superuser: bool):
    """GET /users/me 必须下发布尔 is_superuser（而不是只给 role）。"""
    user = _superuser_probe(is_superuser)
    db = MagicMock()
    db.get = AsyncMock(return_value=user)

    async def _override_db():
        yield db

    async def _override_user():
        return user

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[auth_service.get_current_user] = _override_user
    try:
        r = TestClient(app).get("/api/v1/users/me")
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(auth_service.get_current_user, None)

    assert r.status_code == 200, f"/users/me 未成功返回：{r.text}"
    body = r.json()
    assert body["role"] == "user", "前提不成立：探针用户应保持 role=user"
    assert "is_superuser" in body, "/users/me 响应缺少 is_superuser 字段"
    assert body["is_superuser"] is is_superuser


_LOGIN_ROUTES = ["/api/v1/auth/login", "/api/v1/auth/login/json"]


@pytest.mark.parametrize("route", _LOGIN_ROUTES)
@pytest.mark.parametrize("is_superuser", [True, False])
def test_login_payload_exposes_is_superuser(route: str, is_superuser: bool):
    """两条登录路径的 data.user_info 都必须携带布尔 is_superuser。"""
    user = _superuser_probe(is_superuser)
    db = MagicMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()

    async def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db
    try:
        with patch("app.api.v1.endpoints.auth.auth_service") as mock_auth_service:
            mock_auth_service.authenticate_user = AsyncMock(return_value=user)
            mock_auth_service.create_access_token.return_value = "access-token"
            mock_auth_service.create_refresh_token.return_value = "refresh-token"

            client = TestClient(app)
            if route.endswith("/login"):
                r = client.post(
                    route,
                    data={"username": user.username, "password": "ValidPass123!"},
                )
            else:
                r = client.post(
                    route,
                    json={"username": user.username, "password": "ValidPass123!"},
                )
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert r.status_code == 200, f"{route} 登录未成功：{r.text}"
    user_info = r.json()["data"]["user_info"]
    assert "is_superuser" in user_info, f"{route} 的 user_info 缺少 is_superuser 字段"
    assert user_info["is_superuser"] is is_superuser


def test_auth_me_exposes_is_superuser():
    """GET /auth/me 与其余身份端点同一契约：is_superuser 如实下发。"""
    user = _superuser_probe(True)

    async def _override_user():
        return user

    app.dependency_overrides[get_current_user] = _override_user
    try:
        r = TestClient(app).get("/api/v1/auth/me")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert r.status_code == 200, f"/auth/me 未成功返回：{r.text}"
    data = r.json()["data"]
    assert "is_superuser" in data, "/auth/me 响应缺少 is_superuser 字段"
    assert data["is_superuser"] is True
