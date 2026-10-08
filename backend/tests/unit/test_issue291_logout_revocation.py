"""issue #291 判别用例：登出 / 令牌吊销链 fail-closed（Redis 故障不得谎报成功、不得放行）。

修前缺陷（三重 fail-open，探针原文见 `_orchestrate/round2/_probe_logout291_run.txt`）：
  1. `core/redis.py` `RedisTools.exists/set_cache` 把「查不了 / 写失败」吞成
     `False` / 空操作；
  2. `auth_service.blacklist_token` 把吊销写失败吞掉、`is_token_blacklisted`
     把「查不了」吞成「未吊销」——调用方无从知道吊销实际没发生；
  3. `logout` 端点因此恒 200「登出成功」，而 token 实际仍在流通
     （故障注入实测：②=200、③=200；对照组 ③=401）。

本文件用**真实 AuthService + 真实端点**（仅把 `RedisTools` 打桩成「故障」或
「内存替身」）钉住四条判别：
  - T1 吊销写失败 / 部分失败 → logout 必须 503，不得 200 谎报成功；
  - T2 吊销状态不可核验 → /auth/me 与 /auth/refresh 必须 503，不得 fail-open 放行；
  - T3 正常路径保持不变（logout 200 → 同 token /auth/me 401，吊销真实生效）；
  - T4 refresh 轮换：旧 token 的吊销写失败不得照发新令牌（防重放）。

变异自检（tools/mutate.py）：还原任一环 fail-open（`exists`/`set_cache` 吞成
False、`blacklist_token` 吞异常、`logout` 忽略失败）→ 对应断言精确变红。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.services.auth_service import auth_service as real_auth_service

_LOGOUT = "/api/v1/auth/logout"
_ME = "/api/v1/auth/me"
_REFRESH = "/api/v1/auth/refresh"


# ── Redis 替身 ──────────────────────────────────────────────


def _redis_outage() -> MagicMock:
    """Redis 全故障替身：所有数据面操作抛 ConnectionError。"""
    fake = MagicMock()

    async def _boom(*_a, **_k):
        raise ConnectionError("Redis outage (injected by issue #291 test)")

    fake.set_cache = _boom
    fake.get_cache = _boom
    fake.delete_cache = _boom
    fake.exists = _boom
    return fake


def _redis_store(store: dict[str, str]) -> MagicMock:
    """Redis 内存替身：写进 dict、读看 dict（正常路径对照）。"""
    fake = MagicMock()

    async def _set(key, value, expire=3600):
        store[key] = value

    async def _exists(key):
        return key in store

    fake.set_cache = _set
    fake.exists = _exists
    return fake


def _redis_read_ok_write_broken(store: dict[str, str]) -> MagicMock:
    """只坏「写」：exists 正常（不在黑名单）、set_cache 抛错。"""
    fake = _redis_store(store)

    async def _boom(*_a, **_k):
        raise ConnectionError("Redis write outage (injected by issue #291 test)")

    fake.set_cache = _boom
    return fake


# ── 会话 / 依赖桩 ───────────────────────────────────────────


def _mock_user() -> MagicMock:
    u = MagicMock()
    u.id = 1
    u.username = "issue291"
    u.email = "issue291@example.com"
    u.full_name = "Issue 291"
    u.role = "user"
    u.organization_id = None
    u.is_superuser = False
    u.is_active = True
    u.preferences = None
    u.created_at = None
    return u


def _override_get_db():
    async def _db():
        session = AsyncMock()
        result = MagicMock()
        result.scalar_one_or_none.return_value = _mock_user()
        session.execute = AsyncMock(return_value=result)
        yield session

    return _db


@pytest.fixture
def client():
    from app.main import app

    return TestClient(app)


@pytest.fixture(autouse=True)
def _db_override():
    from app.main import app

    app.dependency_overrides[get_db] = _override_get_db()
    yield
    app.dependency_overrides.pop(get_db, None)


def _access_token() -> str:
    return real_auth_service.create_access_token({"user_id": 1, "sub": "issue291"})


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ── T1：登出不得谎报成功 ────────────────────────────────────


class TestLogoutDoesNotLieUnderRedisOutage:
    def test_logout_not_200_when_blacklist_write_fails(self, client):
        """吊销写入失败 → 503；旧行为返回 200「登出成功」而 token 未吊销。"""
        token = _access_token()
        with patch("app.services.auth_service.RedisTools", _redis_outage()):
            r = client.post(_LOGOUT, headers=_auth_header(token))

        assert r.status_code == 503, (
            "吊销写入失败时 logout 谎报了成功（200）——token 仍可继续使用。"
            f"实际响应: {r.status_code} {r.text}"
        )
        assert "登出成功" not in r.text
        assert r.json().get("success") is False

    def test_logout_not_200_when_only_refresh_revocation_fails(self, client):
        """access 写成功、refresh 写失败 → 仍须非 200（refresh 还能续期=没登干净）。"""
        store: dict[str, str] = {}
        fake = _redis_store(store)
        calls = {"n": 0}

        async def _flaky_set(key, value, expire=3600):
            calls["n"] += 1
            if calls["n"] >= 2:  # 第一次（access）成功，第二次（refresh）失败
                raise ConnectionError("Redis write outage (injected)")
            store[key] = value

        fake.set_cache = _flaky_set
        token = _access_token()
        refresh = real_auth_service.create_refresh_token({"user_id": 1, "sub": "issue291"})

        with patch("app.services.auth_service.RedisTools", fake):
            r = client.post(
                _LOGOUT, json={"refresh_token": refresh}, headers=_auth_header(token)
            )

        assert r.status_code == 503, (
            "refresh 吊销失败被吞成了成功——refresh token 仍可换新 access token。"
            f"实际响应: {r.status_code} {r.text}"
        )
        assert "登出成功" not in r.text


# ── T2：吊销状态不可核验 → 认证面 fail-closed ───────────────


class TestRevocationCheckFailsClosed:
    def test_me_not_200_when_blacklist_check_unavailable(self, client):
        """Redis 故障 → /auth/me 必须 503；旧行为 fail-open 放行 200。

        这正是 #291 的核心危害：故障期间**已登出**的 token 会照常通过认证。
        """
        token = _access_token()
        with patch("app.services.auth_service.RedisTools", _redis_outage()):
            r = client.get(_ME, headers=_auth_header(token))

        assert r.status_code == 503, (
            "Redis 故障时无法核验吊销状态，/auth/me 却放行了——"
            "故障期间已登出的 token 同样会被放行。"
            f"实际响应: {r.status_code} {r.text}"
        )
        assert r.status_code != 200
        assert r.json().get("success") is False

    def test_refresh_not_200_when_blacklist_check_unavailable(self, client):
        """refresh 的吊销核验不可用 → 不得续期。"""
        refresh = real_auth_service.create_refresh_token({"user_id": 1, "sub": "issue291"})
        with patch("app.services.auth_service.RedisTools", _redis_outage()):
            r = client.post(_REFRESH, json={"refresh_token": refresh})

        assert r.status_code == 503, (
            f"吊销核验不可用时 refresh 仍然通行。实际响应: {r.status_code} {r.text}"
        )
        assert "access_token" not in r.text


# ── T4：refresh 轮换链 fail-closed（防旧 refresh 重放）───────


class TestRefreshRotationFailsClosed:
    def test_refresh_not_200_when_rotation_write_fails(self, client):
        """旧 refresh 的吊销写失败 → 不得签发新令牌（否则旧 refresh 可重放）。"""
        refresh = real_auth_service.create_refresh_token({"user_id": 1, "sub": "issue291"})
        with patch(
            "app.services.auth_service.RedisTools",
            _redis_read_ok_write_broken({}),
        ):
            r = client.post(_REFRESH, json={"refresh_token": refresh})

        assert r.status_code == 503, (
            "轮换写失败仍签发了新令牌——旧 refresh 可重放换新（旋转形同虚设）。"
            f"实际响应: {r.status_code} {r.text}"
        )
        assert "access_token" not in r.text


# ── T3：正常路径保持不变 ────────────────────────────────────


class TestNormalPathPreserved:
    def test_logout_then_same_token_is_rejected(self, client):
        """Redis 正常：/auth/me 200 → logout 200 → 同 token /auth/me 401。"""
        store: dict[str, str] = {}
        token = _access_token()
        with patch("app.services.auth_service.RedisTools", _redis_store(store)):
            before = client.get(_ME, headers=_auth_header(token))
            assert before.status_code == 200, before.text

            r = client.post(_LOGOUT, headers=_auth_header(token))
            assert r.status_code == 200, r.text
            assert r.json().get("message") == "登出成功"

            after = client.get(_ME, headers=_auth_header(token))

        assert after.status_code == 401, (
            "登出后同一 token 仍被接受——吊销没有生效。"
            f"实际响应: {after.status_code} {after.text}"
        )
        assert store, "吊销键根本没有写入（用例前提不成立）"

    def test_me_ok_and_not_blacklisted_under_normal_redis(self, client):
        """未登出的 token 正常核验通过（fail-closed 不得误伤正常路径）。"""
        token = _access_token()
        with patch("app.services.auth_service.RedisTools", _redis_store({})):
            r = client.get(_ME, headers=_auth_header(token))
        assert r.status_code == 200, r.text


# ── 第 1 环契约：真实 RedisTools 失败必须上抛 ────────────────


class TestRedisToolsFailureContract:
    """`RedisTools` 数据面失败必须上抛，不得与「不存在 / 写入成功」同形（#291 第 1 环）。

    上面的端点/服务用例把整个 `RedisTools` 打桩掉了（测第 2/3 环）；这一组
    打的是**真实的** `RedisTools`，注入点在它下层（`get_redis`）——防第 1 环
    被单独还原成 fail-open 时上面所有用例仍全绿（变异 MA 的精确红点）。
    """

    @staticmethod
    def _outage(monkeypatch) -> None:
        async def _down():
            raise ConnectionError("Redis down (injected)")

        monkeypatch.setattr("app.core.redis.get_redis", _down)

    @pytest.mark.asyncio
    async def test_exists_raises_instead_of_returning_false(self, monkeypatch):
        """旧行为 `except: return False` 与「不在黑名单」同形——必须改为上抛。"""
        self._outage(monkeypatch)
        from app.core.redis import RedisTools

        with pytest.raises(ConnectionError):
            await RedisTools.exists("blacklist:jti:probe")

    @pytest.mark.asyncio
    async def test_set_cache_raises_instead_of_swallowing(self, monkeypatch):
        self._outage(monkeypatch)
        from app.core.redis import RedisTools

        with pytest.raises(ConnectionError):
            await RedisTools.set_cache("blacklist:jti:probe", "1", expire=60)

    @pytest.mark.asyncio
    async def test_get_cache_raises_instead_of_returning_none(self, monkeypatch):
        self._outage(monkeypatch)
        from app.core.redis import RedisTools

        with pytest.raises(ConnectionError):
            await RedisTools.get_cache("user:1")
