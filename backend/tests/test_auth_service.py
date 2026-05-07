# -*- coding: utf-8 -*-
"""AuthService 单元测试 — 不依赖外部服务。"""
import pytest
import jwt
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, AsyncMock

from app.services.auth_service import AuthService


@pytest.fixture
def auth_service():
    """创建一个使用测试密钥的 AuthService 实例。"""
    with patch("app.services.auth_service.settings") as mock_settings:
        mock_settings.JWT_SECRET_KEY = "test-secret-key-for-unit-tests-only"
        mock_settings.JWT_ALGORITHM = "HS256"
        mock_settings.ACCESS_TOKEN_EXPIRE_MINUTES = 30
        mock_settings.REFRESH_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7
        svc = AuthService()
        yield svc


# ── 密码哈希 ──────────────────────────────────────────────

class TestPasswordHashing:
    def test_hash_password_returns_string(self, auth_service: AuthService):
        hashed = auth_service.hash_password("MyP@ssw0rd!")
        assert isinstance(hashed, str)
        assert hashed != "MyP@ssw0rd!"

    def test_hash_password_different_each_time(self, auth_service: AuthService):
        h1 = auth_service.hash_password("same_password")
        h2 = auth_service.hash_password("same_password")
        assert h1 != h2  # bcrypt salt differs

    def test_verify_password_correct(self, auth_service: AuthService):
        hashed = auth_service.hash_password("correct_password")
        assert auth_service.verify_password("correct_password", hashed) is True

    def test_verify_password_incorrect(self, auth_service: AuthService):
        hashed = auth_service.hash_password("correct_password")
        assert auth_service.verify_password("wrong_password", hashed) is False

    def test_verify_password_empty_inputs(self, auth_service: AuthService):
        assert auth_service.verify_password("", "some_hash") is False
        assert auth_service.verify_password("some_pass", "") is False
        assert auth_service.verify_password("", "") is False

    def test_verify_password_bytes_hash(self, auth_service: AuthService):
        hashed = auth_service.hash_password("test123")
        assert auth_service.verify_password("test123", hashed.encode("utf-8")) is True

    def test_verify_password_invalid_hash(self, auth_service: AuthService):
        assert auth_service.verify_password("test", "not-a-valid-bcrypt-hash") is False


# ── JWT Token 创建与验证 ──────────────────────────────────

class TestTokenCreation:
    def test_create_access_token_contains_claims(self, auth_service: AuthService):
        token = auth_service.create_access_token({"user_id": 42, "role": "admin"})
        payload = jwt.decode(token, auth_service.secret_key, algorithms=[auth_service.algorithm])
        assert payload["user_id"] == 42
        assert payload["role"] == "admin"
        assert payload["type"] == "access"
        assert "exp" in payload
        assert "iat" in payload

    def test_create_refresh_token_type(self, auth_service: AuthService):
        token = auth_service.create_refresh_token({"user_id": 1})
        payload = jwt.decode(token, auth_service.secret_key, algorithms=[auth_service.algorithm])
        assert payload["type"] == "refresh"

    def test_create_token_custom_expiry(self, auth_service: AuthService):
        delta = timedelta(minutes=5)
        token = auth_service.create_access_token({"user_id": 1}, expires_delta=delta)
        payload = jwt.decode(token, auth_service.secret_key, algorithms=[auth_service.algorithm])
        exp = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)
        now = datetime.now(timezone.utc)
        # Should expire ~5 minutes from now (allow 10s tolerance)
        assert abs((exp - now).total_seconds() - 300) < 10

    def test_create_token_does_not_mutate_input(self, auth_service: AuthService):
        data = {"user_id": 1}
        auth_service.create_access_token(data)
        assert "exp" not in data
        assert "type" not in data


class TestTokenVerification:
    def test_verify_valid_token(self, auth_service: AuthService):
        token = auth_service.create_access_token({"user_id": 7})
        payload = auth_service.verify_token(token)
        assert payload is not None
        assert payload["user_id"] == 7

    def test_verify_expired_token(self, auth_service: AuthService):
        token = auth_service.create_access_token(
            {"user_id": 1}, expires_delta=timedelta(seconds=-1)
        )
        payload = auth_service.verify_token(token)
        assert payload is None

    def test_verify_tampered_token(self, auth_service: AuthService):
        token = auth_service.create_access_token({"user_id": 1})
        tampered = token[:-5] + "XXXXX"
        payload = auth_service.verify_token(tampered)
        assert payload is None

    def test_verify_token_wrong_secret(self, auth_service: AuthService):
        token = auth_service.create_access_token({"user_id": 1})
        # Try to verify with a different secret
        try:
            payload = jwt.decode(token, "wrong-secret", algorithms=[auth_service.algorithm])
            # If it doesn't raise, the token was somehow valid with wrong key (shouldn't happen)
            assert False, "Should have raised an error"
        except jwt.PyJWTError:
            pass  # expected

    def test_verify_token_returns_none_for_garbage(self, auth_service: AuthService):
        assert auth_service.verify_token("not.a.jwt") is None
        assert auth_service.verify_token("") is None


# ── Token 黑名单 ──────────────────────────────────────────

class TestTokenBlacklist:
    @pytest.mark.asyncio
    async def test_blacklist_and_check(self, auth_service: AuthService):
        token = auth_service.create_access_token({"user_id": 1})

        with patch("app.services.auth_service.RedisTools") as mock_redis:
            mock_redis.set_cache = AsyncMock()
            mock_redis.exists = AsyncMock(return_value=True)

            await auth_service.blacklist_token(token)
            is_blacklisted = await auth_service.is_token_blacklisted(token)

        assert is_blacklisted is True
        mock_redis.set_cache.assert_called_once()

    @pytest.mark.asyncio
    async def test_is_token_blacklisted_false(self, auth_service: AuthService):
        with patch("app.services.auth_service.RedisTools") as mock_redis:
            mock_redis.exists = AsyncMock(return_value=False)
            assert await auth_service.is_token_blacklisted("some_token") is False

    @pytest.mark.asyncio
    async def test_blacklist_expired_token_noop(self, auth_service: AuthService):
        """Expired tokens should not be added to blacklist (no TTL left)."""
        token = auth_service.create_access_token(
            {"user_id": 1}, expires_delta=timedelta(seconds=-10)
        )
        with patch("app.services.auth_service.RedisTools") as mock_redis:
            mock_redis.set_cache = AsyncMock()
            await auth_service.blacklist_token(token)
            mock_redis.set_cache.assert_not_called()

    @pytest.mark.asyncio
    async def test_blacklist_redis_error_graceful(self, auth_service: AuthService):
        """Redis errors should be caught, not raised."""
        token = auth_service.create_access_token({"user_id": 1})
        with patch("app.services.auth_service.RedisTools") as mock_redis:
            mock_redis.exists = AsyncMock(side_effect=ConnectionError("Redis down"))
            result = await auth_service.is_token_blacklisted(token)
        assert result is False  # fails open


# ── RBAC ──────────────────────────────────────────────────

class TestRBAC:
    def test_require_role_returns_dependency(self, auth_service: AuthService):
        dep = auth_service.require_role("admin")
        assert callable(dep)

    def test_require_admin_returns_dependency(self, auth_service: AuthService):
        dep = auth_service.require_admin()
        assert callable(dep)
