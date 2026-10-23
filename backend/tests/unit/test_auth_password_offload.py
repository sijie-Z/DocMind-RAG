"""bcrypt offload 判别测试（issue #500）。

覆盖 `AuthService` 中全部 bcrypt 调用点：登录校验、用户不存在时的 dummy 时序
校验、注册哈希、改密哈希。断言这些 CPU 密集调用都经 `asyncio.to_thread` 在
工作线程执行，登录 burst 期间事件循环仍能调度其它协程。
"""

import asyncio
import contextlib
import threading
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.auth_service import _DUMMY_HASH, AuthService


@pytest.fixture
def svc():
    """创建一个使用测试密钥的 AuthService 实例（与既有单元测试同款）。"""
    with patch("app.services.auth_service.settings") as mock_settings:
        mock_settings.JWT_SECRET_KEY = "test-secret-key-for-offload-tests-only"
        mock_settings.JWT_ALGORITHM = "HS256"
        mock_settings.ACCESS_TOKEN_EXPIRE_MINUTES = 30
        mock_settings.REFRESH_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7
        yield AuthService()


@pytest.fixture(autouse=True)
def _no_redis(monkeypatch):
    """锁定/失败计数在无 Redis 时走 no-op 分支，保证用例完全离线。"""
    import app.core.redis as redis_module

    monkeypatch.setattr(redis_module, "redis_client", None)


def _db_returning(user):
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = user
    db.execute.return_value = result
    return db


def _user():
    user = MagicMock()
    user.username = "alice"
    user.hashed_password = "$2b$12$fake-hash-for-offload-test"
    user.is_active = True
    return user


def _thread_recorder(calls, return_value):
    def fake(self, plain_password, hashed_password):
        calls.append(
            {
                "thread": threading.current_thread(),
                "ident": threading.get_ident(),
                "plain": plain_password,
                "hashed": hashed_password,
            }
        )
        return return_value

    return fake


class TestLoginVerifyOffload:
    @pytest.mark.asyncio
    async def test_verify_runs_in_worker_thread(self, svc, monkeypatch):
        """登录校验必须在线程（非事件循环线程）中执行。"""
        calls = []
        monkeypatch.setattr(AuthService, "verify_password", _thread_recorder(calls, True))
        user = _user()

        result = await svc.authenticate_user(_db_returning(user), "alice", "secret")

        assert result is user
        assert len(calls) == 1
        assert calls[0]["thread"] is not threading.main_thread()
        assert calls[0]["ident"] != threading.main_thread().ident
        assert calls[0]["plain"] == "secret"
        assert calls[0]["hashed"] == user.hashed_password

    @pytest.mark.asyncio
    async def test_dummy_verify_for_unknown_user_runs_in_worker_thread(self, svc, monkeypatch):
        """用户不存在时仍执行一次 dummy bcrypt（时序防护），且同样 offload。"""
        calls = []
        monkeypatch.setattr(AuthService, "verify_password", _thread_recorder(calls, False))
        record_failure = AsyncMock()
        monkeypatch.setattr(AuthService, "_record_login_failure", record_failure)

        result = await svc.authenticate_user(_db_returning(None), "ghost", "secret")

        assert result is None
        assert len(calls) == 1
        assert calls[0]["hashed"] == _DUMMY_HASH
        assert calls[0]["ident"] != threading.main_thread().ident
        # 失败计数语义不变：dummy 校验后仍记录一次失败
        record_failure.assert_awaited_once_with("ghost")

    @pytest.mark.asyncio
    async def test_to_thread_is_the_offload_primitive(self, svc, monkeypatch):
        """显式断言登录路径经由 `asyncio.to_thread` 调度（而非裸同步调用）。"""
        calls = []
        real_to_thread = asyncio.to_thread

        async def spy(func, /, *args, **kwargs):
            calls.append((func, args, kwargs))
            return await real_to_thread(func, *args, **kwargs)

        monkeypatch.setattr(AuthService, "verify_password", lambda self, plain, hashed: True)
        monkeypatch.setattr(asyncio, "to_thread", spy)
        user = _user()

        result = await svc.authenticate_user(_db_returning(user), "alice", "secret")

        assert result is user
        assert len(calls) == 1

    @pytest.mark.asyncio
    async def test_event_loop_ticks_while_logins_run(self, svc, monkeypatch):
        """5 个并发登录（每个 0.2s）期间 tick 协程持续被调度，证明未串行阻塞循环。"""
        n = 5
        sleep_seconds = 0.2

        def slow_verify(self, plain_password, hashed_password):
            time.sleep(sleep_seconds)
            return True

        monkeypatch.setattr(AuthService, "verify_password", slow_verify)
        user = _user()
        db = _db_returning(user)

        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        ticker_task = asyncio.create_task(ticker())
        try:
            started = time.perf_counter()
            results = await asyncio.gather(
                *(svc.authenticate_user(db, f"user{i}", "secret") for i in range(n))
            )
            elapsed = time.perf_counter() - started
        finally:
            ticker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await ticker_task

        assert all(result is user for result in results)
        assert ticks >= 2 * n, f"事件循环被阻塞：{elapsed:.2f}s 内仅 tick {ticks} 次"
        assert elapsed < sleep_seconds * n * 0.6, f"登录串行执行：{elapsed:.2f}s"


class TestHashOffload:
    @pytest.mark.asyncio
    async def test_create_user_hashes_in_worker_thread(self, svc, monkeypatch):
        """注册路径的 hash 必须在线程中执行（auth_service.py:478）。"""
        calls = []

        def fake_hash(self, password):
            calls.append((threading.get_ident(), password))
            return "$2b$12$hashed-in-worker"

        monkeypatch.setattr(AuthService, "hash_password", fake_hash)
        db = AsyncMock()
        db.add = MagicMock()
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        user = await svc.create_user(db, "alice", "alice@example.com", "secret")

        assert user.hashed_password == "$2b$12$hashed-in-worker"
        assert len(calls) == 1
        assert calls[0][0] != threading.main_thread().ident
        assert calls[0][1] == "secret"
        db.flush.assert_awaited_once()
        db.refresh.assert_awaited_once_with(user)

    @pytest.mark.asyncio
    async def test_update_user_password_hashes_in_worker_thread(self, svc, monkeypatch):
        """改密路径的 hash 必须在线程中执行（auth_service.py:538）。"""
        calls = []

        def fake_hash(self, password):
            calls.append((threading.get_ident(), password))
            return "$2b$12$hashed-in-worker"

        monkeypatch.setattr(AuthService, "hash_password", fake_hash)
        user = _user()
        db = _db_returning(user)
        db.flush = AsyncMock()
        db.refresh = AsyncMock()

        updated = await svc.update_user_password(db, 1, "new-secret")

        assert updated is user
        assert user.hashed_password == "$2b$12$hashed-in-worker"
        assert len(calls) == 1
        assert calls[0][0] != threading.main_thread().ident
        assert calls[0][1] == "new-secret"
