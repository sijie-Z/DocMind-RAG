"""认证失败响应契约（issue #82）。

**契约**：`user_not_found` 与 `user_inactive` 的**公开响应必须完全一致** ——
状态码、响应体、`WWW-Authenticate` 头全部相同，只有服务端日志可区分。

依据：OWASP Authentication Cheat Sheet 要求应用对「账号不存在」与「账号被锁定/禁用」
返回同一个 generic 响应，否则构成 discrepancy factor，攻击者拿一个过期 token 轮询
即可枚举有效用户 id。

此前的实际行为是**三种答案**：DB 路径返回 404 `"用户不存在"`、禁用返回 401
`"账号已被禁用"`、而 `permission_required` 路径直接 500 —— 每一条都可区分。

额外覆盖一个容易漏掉的点：`WWW-Authenticate` 曾被 `app/main.py` 的 handler **静默丢弃**
（构造 `JSONResponse` 时没传 `headers=exc.headers`），而 RFC 9110 规定 401 **MUST**
携带该头。所以下面的测试同时断言「响应头真的到得了客户端」。
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.services.auth_service import _AUTH_FAILED_DETAIL, AuthService, _auth_failed


@pytest.fixture
def auth_service():
    with patch("app.services.auth_service.settings") as mock_settings:
        mock_settings.JWT_SECRET_KEY = "test-secret-key-for-unit-tests-only"
        mock_settings.JWT_ALGORITHM = "HS256"
        mock_settings.ACCESS_TOKEN_EXPIRE_MINUTES = 30
        mock_settings.REFRESH_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7
        yield AuthService()


def _credentials(auth_service: AuthService) -> HTTPAuthorizationCredentials:
    return HTTPAuthorizationCredentials(
        scheme="Bearer", credentials=auth_service.create_access_token({"user_id": 1})
    )


async def _raise_for_deleted_user(auth_service: AuthService) -> HTTPException:
    """用户行已从 DB 消失（DB 路径：缓存未命中 → 回源查不到）。"""
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db.execute.return_value = result

    with patch("app.services.auth_service.RedisTools") as mock_redis:
        mock_redis.exists = AsyncMock(return_value=False)  # 未在黑名单
        mock_redis.get_cache = AsyncMock(return_value=None)  # 缓存未命中
        with pytest.raises(HTTPException) as exc:
            await auth_service.get_current_user(credentials=_credentials(auth_service), db=db)
    return exc.value


async def _raise_for_disabled_user(auth_service: AuthService) -> HTTPException:
    """用户存在但 is_active=False（缓存路径即可判定，无需回源）。"""
    cached = json.dumps({"id": 1, "username": "u", "role": "user", "is_active": False})
    db = AsyncMock()

    with patch("app.services.auth_service.RedisTools") as mock_redis:
        mock_redis.exists = AsyncMock(return_value=False)
        mock_redis.get_cache = AsyncMock(return_value=cached)
        mock_redis.delete_cache = AsyncMock()
        with pytest.raises(HTTPException) as exc:
            await auth_service.get_current_user(credentials=_credentials(auth_service), db=db)
    return exc.value


# ── 核心契约：两者不可区分 ──────────────────────────────────

@pytest.mark.asyncio
async def test_deleted_and_disabled_are_indistinguishable(auth_service: AuthService):
    deleted = await _raise_for_deleted_user(auth_service)
    disabled = await _raise_for_disabled_user(auth_service)

    assert type(deleted) is type(disabled)  # noqa: E721 — 契约要求同一类型，不是子类关系
    assert deleted.status_code == disabled.status_code == 401
    assert deleted.detail == disabled.detail
    assert deleted.headers == disabled.headers


@pytest.mark.asyncio
async def test_auth_failure_detail_is_generic(auth_service: AuthService):
    """两个分支的公开文案都不得泄露账户状态。"""
    deleted = await _raise_for_deleted_user(auth_service)

    assert deleted.detail == _AUTH_FAILED_DETAIL
    for leak in ("不存在", "禁用", "已删"):
        assert leak not in str(deleted.detail)


@pytest.mark.asyncio
async def test_auth_failure_carries_www_authenticate(auth_service: AuthService):
    """RFC 9110：401 MUST 携带 `WWW-Authenticate`。"""
    deleted = await _raise_for_deleted_user(auth_service)
    disabled = await _raise_for_disabled_user(auth_service)

    assert deleted.headers is not None
    assert deleted.headers.get("WWW-Authenticate") == "Bearer"
    assert disabled.headers.get("WWW-Authenticate") == "Bearer"


def test_user_missing_is_401_not_404():
    """回归：DB 路径原先对「用户不存在」返回 **404**，与禁用的 401 可区分。"""
    assert _auth_failed().status_code == 401


# ── handler 是否把响应头透传给客户端 ────────────────────────

@pytest.mark.asyncio
async def test_http_exception_handler_forwards_www_authenticate_header():
    """回归：handler 原先构造 `JSONResponse` 时没传 `headers=exc.headers`，
    导致 `auth_service` 里显式设置的 `WWW-Authenticate` 全部到不了客户端。"""
    from app.main import http_exception_handler

    exc = HTTPException(
        status_code=401, detail="认证失败", headers={"WWW-Authenticate": "Bearer"}
    )
    request = SimpleNamespace(state=SimpleNamespace(request_id="test-request-id"))

    response = await http_exception_handler(request, exc)

    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"


@pytest.mark.asyncio
async def test_http_exception_handler_tolerates_missing_headers():
    """不是所有 `HTTPException` 都带 headers（如 403/404），不能因此炸掉。"""
    from app.main import http_exception_handler

    exc = HTTPException(status_code=403, detail="权限不足")
    request = SimpleNamespace(state=SimpleNamespace(request_id="test-request-id"))

    response = await http_exception_handler(request, exc)

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_app_error_handler_adds_www_authenticate_for_401():
    """`AuthenticationError` 走的是 AppError handler，此前同样没有该头。"""
    from app.exceptions import AuthenticationError
    from app.main import app_error_handler

    request = SimpleNamespace(state=SimpleNamespace(request_id="test-request-id"))
    response = await app_error_handler(request, AuthenticationError("任意文案"))

    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"


@pytest.mark.asyncio
async def test_app_error_handler_leaves_non_401_alone():
    """403 / 404 不该被加上认证挑战头。"""
    from app.exceptions import AuthorizationError, NotFoundError
    from app.main import app_error_handler

    request = SimpleNamespace(state=SimpleNamespace(request_id="test-request-id"))

    forbidden = await app_error_handler(request, AuthorizationError("权限不足"))
    missing = await app_error_handler(request, NotFoundError("资源不存在"))

    assert forbidden.status_code == 403
    assert forbidden.headers.get("www-authenticate") is None
    assert missing.status_code == 404
    assert missing.headers.get("www-authenticate") is None


# ── refresh 端点：同一个 oracle 的另一处 ────────────────────

def _refresh_response(user_obj):
    """以给定的「查到的用户」调用 /auth/refresh，返回响应。"""
    from fastapi.testclient import TestClient

    from app.core.database import get_db
    from app.main import app
    from app.services.auth_service import auth_service as real_auth_service

    token = real_auth_service.create_refresh_token({"user_id": 1, "sub": "someone"})

    async def _override_db():
        yield AsyncMock()

    app.dependency_overrides[get_db] = _override_db
    try:
        with (
            patch.object(real_auth_service, "is_token_blacklisted", AsyncMock(return_value=False)),
            patch.object(real_auth_service, "get_user_by_id", AsyncMock(return_value=user_obj)),
        ):
            return TestClient(app).post("/api/v1/auth/refresh", json={"refresh_token": token})
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_refresh_endpoint_deleted_and_disabled_are_indistinguishable():
    """回归：refresh 端点曾用「用户不存在」与「账号已被禁用」两个不同文案 ——
    同一个用户枚举 oracle 的另一处（PR A 初版漏掉了它）。"""
    disabled = MagicMock()
    disabled.username = "someone"
    disabled.is_active = False

    deleted = _refresh_response(None)
    inactive = _refresh_response(disabled)

    assert deleted.status_code == inactive.status_code == 401
    # request_id 每次请求都不同，比较时剔除
    dropped = {k: v for k, v in deleted.json().items() if k != "request_id"}
    inact = {k: v for k, v in inactive.json().items() if k != "request_id"}
    assert dropped == inact

    for response in (deleted, inactive):
        assert response.headers.get("www-authenticate") == "Bearer"
        for leak in ("不存在", "禁用"):
            assert leak not in response.text
