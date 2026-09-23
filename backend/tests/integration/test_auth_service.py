"""AuthService 单元测试 — 不依赖外部服务。"""
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

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
        exp = datetime.fromtimestamp(payload["exp"], tz=UTC)
        now = datetime.now(UTC)
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
            jwt.decode(token, "wrong-secret", algorithms=[auth_service.algorithm])
            # If it doesn't raise, the token was somehow valid with wrong key (shouldn't happen)
            raise AssertionError("Should have raised an error")
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


# ── 身份权威：DB（issue #82 / PR B）────────────────────────

class TestDbIsTheOnlyIdentityAuthority:
    """`get_current_user` 不再读 `user:{id}` 缓存，身份每次请求回源 DB。

    这里替换掉了 PR A 的 `TestCachePathAuthDecision`（回归 #85）—— 那个类测的是
    「缓存命中」这条路径的异常语义，而该路径已被整体删除（issue #82 / PR B）。
    `except Exception` 吞掉认证决策的坑也随之消失：缓存解析不再存在，也就没有
    需要吞的解析失败。
    """

    @staticmethod
    def _credentials(auth_service: AuthService) -> HTTPAuthorizationCredentials:
        token = auth_service.create_access_token({"user_id": 1})
        return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    @staticmethod
    def _db_returning(user) -> AsyncMock:
        db = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = user
        db.execute.return_value = result
        return db

    @pytest.mark.asyncio
    async def test_redis_outage_does_not_affect_identity(self, auth_service: AuthService):
        """Redis 全挂时身份判定照常。

        改造前这是「缓存拿不到 → 回退 DB」；现在 DB 不是回退路径，而是唯一路径，
        所以 Redis 的可用性对身份判定不再有任何影响（token 黑名单仍用它，且
        `is_token_blacklisted` 是 fail-open 的）。
        """
        user = MagicMock()
        user.id = 1
        user.username = "u"
        user.role = "user"
        user.is_active = True
        db = self._db_returning(user)

        with patch("app.services.auth_service.RedisTools") as mock_redis:
            mock_redis.exists = AsyncMock(side_effect=ConnectionError("Redis down"))
            mock_redis.get_cache = AsyncMock(side_effect=ConnectionError("Redis down"))
            result = await auth_service.get_current_user(
                credentials=self._credentials(auth_service), db=db
            )

        assert result is user
        db.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_decision_comes_from_db_not_cache(self, auth_service: AuthService):
        """缓存里是「有效账号」的快照，DB 里该账号已被禁用 → 401。"""
        stale = json.dumps({"id": 1, "username": "u", "role": "user", "is_active": True})
        disabled = MagicMock()
        disabled.id = 1
        disabled.is_active = False

        with patch("app.services.auth_service.RedisTools") as mock_redis:
            mock_redis.exists = AsyncMock(return_value=False)
            mock_redis.get_cache = AsyncMock(return_value=stale)
            with pytest.raises(HTTPException) as exc:
                await auth_service.get_current_user(
                    credentials=self._credentials(auth_service),
                    db=self._db_returning(disabled),
                )

        assert exc.value.status_code == 401
