"""终端 API / WS / 工具契约测试（issue #477 任务 3-4）。

全部使用 FakeBackend + 假 Redis，不依赖 Docker / MySQL / 真实 Redis：

* HTTP：禁用 503、Docker 不可用 503、超配额 409、cols/rows 400、404、
  401 无关（认证由既有依赖覆盖）、DELETE 后列表不再返回、票据轮换。
* WS：成功连接/输入/ping-pong/退出、无票据/非法 Origin/过期/复用/非本人
  一律 1008 且不收任何 output。
* 工具（task 4）：禁用/越权/超时/截断/审批强制（TERMINAL_REQUIRE_APPROVAL）。
"""

from __future__ import annotations

import time
import types
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.agent.terminal.manager import (
    FakeBackend,
    TerminalConfig,
    TerminalManager,
    TerminalRedisUnavailable,
)
from app.api.v1.endpoints import terminal as terminal_api
from app.core.database import get_db
from app.core.security import get_current_user
from app.exceptions import AppError
from app.services.terminal_service import terminal_service

TERMINAL_PREFIX = "/api/v1/terminal"
TEST_ORIGIN = "http://testserver"


# ── 测试替身 ─────────────────────────────────────────────────────────────────


class FakeRedis:
    """最小异步 Redis 替身：只实现终端服务用到的 get/set/delete/getdel/exists。

    TTL 不真正计时；「过期」由测试直接删键模拟（GETDEL 语义与真实 Redis 一致）。
    """

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        self.store[key] = value
        return True

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def delete(self, key: str) -> int:
        return 1 if self.store.pop(key, None) is not None else 0

    async def getdel(self, key: str) -> str | None:
        return self.store.pop(key, None)

    async def exists(self, key: str) -> int:
        return 1 if key in self.store else 0


class FakeUser:
    def __init__(self, user_id: int = 1, organization_id: int | None = 1) -> None:
        self.id = user_id
        self.organization_id = organization_id
        self.is_active = True
        self.is_superuser = False
        self.role = "user"


async def _fake_require_organization(current_user, db) -> int:
    return int(current_user.organization_id or 1)


async def _app_error_handler(request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "code": exc.status_code,
            "error_code": exc.error_code,
            "message": exc.message,
            "detail": exc.detail,
            "data": None,
        },
    )


def _wait_until(predicate, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("条件在超时内未满足")
        time.sleep(0.01)


# ── fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def env(monkeypatch):
    """FakeBackend 管理器 + 假 Redis + 独立 FastAPI app（TestClient 单 portal）。"""
    backends: list[FakeBackend] = []

    def factory(**kwargs) -> FakeBackend:
        backend = FakeBackend(**kwargs)
        backends.append(backend)
        return backend

    manager = TerminalManager(
        config=TerminalConfig(
            max_per_user=2,
            max_per_org=10,
            ring_buffer_bytes=4096,
            max_read_bytes=1024,
            max_input_bytes=1024,
        ),
        backend_factory=factory,
        worker_id="test-worker",
    )
    fake_redis = FakeRedis()

    monkeypatch.setattr(terminal_service, "_manager", manager, raising=False)
    monkeypatch.setattr(terminal_service, "worker_id", "test-worker", raising=False)
    monkeypatch.setattr(terminal_service, "enabled", lambda: True, raising=False)
    monkeypatch.setattr(terminal_service, "_redis", lambda: fake_redis, raising=False)
    monkeypatch.setattr(terminal_service, "_docker_probe", (time.monotonic(), True), raising=False)
    monkeypatch.setattr(terminal_api, "require_organization", _fake_require_organization)

    settings = terminal_service._settings()
    monkeypatch.setattr(settings, "TERMINAL_ENABLED", True)
    monkeypatch.setattr(settings, "SANDBOX_MODE", "docker")
    monkeypatch.setattr(settings, "TERMINAL_TICKET_TTL_SECONDS", 60)
    monkeypatch.setattr(settings, "ALLOWED_ORIGINS", [*settings.ALLOWED_ORIGINS, TEST_ORIGIN])

    holder = types.SimpleNamespace(current=FakeUser())
    app = FastAPI()
    app.include_router(terminal_api.router, prefix=TERMINAL_PREFIX)
    app.add_exception_handler(AppError, _app_error_handler)

    async def _db_override():
        yield None

    app.dependency_overrides[get_current_user] = lambda: holder.current
    app.dependency_overrides[get_db] = _db_override

    with TestClient(app) as client:
        yield types.SimpleNamespace(
            client=client,
            manager=manager,
            backends=backends,
            redis=fake_redis,
            holder=holder,
            settings=settings,
            portal=client.portal,
        )
        # 在同一个 portal loop 内关闭全部会话，避免跨 loop 清理。
        client.portal.call(manager.shutdown)


def _create(env, **overrides) -> dict[str, Any]:
    payload = {"session_id": "chat-1", "cols": 100, "rows": 30, **overrides}
    response = env.client.post(f"{TERMINAL_PREFIX}/sessions", json=payload)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _connect(env, terminal_id: str, ticket: str, origin: str = TEST_ORIGIN):
    return env.client.websocket_connect(
        f"{TERMINAL_PREFIX}/ws/{terminal_id}",
        headers={"origin": origin},
        subprotocols=[f"auth.{ticket}"],
    )


def _assert_ws_rejected(env, terminal_id: str, ticket: str, origin: str = TEST_ORIGIN) -> None:
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with _connect(env, terminal_id, ticket, origin) as ws:
            ws.receive_json()
    assert excinfo.value.code == 1008


# ── HTTP：开关与依赖 ─────────────────────────────────────────────────────────


def test_disabled_returns_503(env, monkeypatch) -> None:
    monkeypatch.setattr(terminal_service, "enabled", lambda: False, raising=False)
    response = env.client.post(f"{TERMINAL_PREFIX}/sessions", json={})
    assert response.status_code == 503
    body = response.json()
    assert body["error_code"] == "SERVICE_UNAVAILABLE"
    assert body["detail"] == "terminal_disabled"
    assert env.manager.list_for_user(1) == []
    assert env.backends == []

    # 其它端点同样 503。
    assert env.client.get(f"{TERMINAL_PREFIX}/sessions").status_code == 503
    assert env.client.delete(f"{TERMINAL_PREFIX}/sessions/x").status_code == 503


async def _no_docker() -> bool:
    return False


def test_docker_unavailable_returns_503_without_side_effect(env, monkeypatch) -> None:
    monkeypatch.setattr(terminal_service, "docker_available", _no_docker, raising=False)
    response = env.client.post(f"{TERMINAL_PREFIX}/sessions", json={})
    assert response.status_code == 503
    assert response.json()["detail"] == "docker_unavailable"
    assert env.manager.list_for_user(1) == []
    assert env.backends == []  # 无半成品资源


def test_ast_sandbox_mode_rejects_terminal(env) -> None:
    original = env.settings.SANDBOX_MODE
    try:
        env.settings.SANDBOX_MODE = "ast"
        # docker_available 探针被 patch 为 True，但 ast 模式必须先被拒绝。
        response = env.client.post(f"{TERMINAL_PREFIX}/sessions", json={})
        assert response.status_code == 503
        assert response.json()["detail"] == "docker_unavailable"
    finally:
        env.settings.SANDBOX_MODE = original


def test_quota_returns_409(env) -> None:
    _create(env)
    _create(env)
    response = env.client.post(f"{TERMINAL_PREFIX}/sessions", json={})
    assert response.status_code == 409
    body = response.json()
    assert body["error_code"] == "CONFLICT"
    assert body["detail"] == "terminal_quota_exceeded"


def test_cols_rows_out_of_range_returns_400(env) -> None:
    for payload in ({"cols": 0}, {"cols": 1001}, {"rows": 0}, {"rows": 501}):
        response = env.client.post(f"{TERMINAL_PREFIX}/sessions", json=payload)
        assert response.status_code == 400, payload
        assert response.json()["error_code"] == "VALIDATION_ERROR"


def test_redis_unavailable_is_fail_closed(env, monkeypatch) -> None:
    def _no_redis():
        raise TerminalRedisUnavailable("redis down")

    monkeypatch.setattr(terminal_service, "_redis", _no_redis, raising=False)
    response = env.client.post(f"{TERMINAL_PREFIX}/sessions", json={})
    assert response.status_code == 503
    assert response.json()["detail"] == "redis_unavailable"
    assert env.manager.list_for_user(1) == []  # 已回收半成品


# ── HTTP：CRUD 与票据 ────────────────────────────────────────────────────────


def test_create_list_delete_contract(env) -> None:
    data = _create(env)
    assert data["terminal_id"]
    assert data["ws_path"] == f"{TERMINAL_PREFIX}/ws/{data['terminal_id']}"
    assert data["ticket"]
    assert data["expires_in"] == 60

    listing = env.client.get(f"{TERMINAL_PREFIX}/sessions")
    assert listing.status_code == 200
    sessions = listing.json()["data"]["sessions"]
    assert [s["terminal_id"] for s in sessions] == [data["terminal_id"]]
    assert sessions[0]["session_id"] == "chat-1"
    assert sessions[0]["status"] == "running"
    assert sessions[0]["created_at"] and sessions[0]["last_activity_at"]

    deleted = env.client.delete(f"{TERMINAL_PREFIX}/sessions/{data['terminal_id']}")
    assert deleted.status_code == 200
    assert deleted.json()["data"] == {"killed": True}

    listing = env.client.get(f"{TERMINAL_PREFIX}/sessions")
    assert listing.json()["data"]["sessions"] == []


def test_delete_unknown_and_foreign_returns_404(env) -> None:
    assert env.client.delete(f"{TERMINAL_PREFIX}/sessions/{uuid.uuid4()}").status_code == 404

    data = _create(env)
    env.holder.current = FakeUser(user_id=2)
    assert env.client.delete(f"{TERMINAL_PREFIX}/sessions/{data['terminal_id']}").status_code == 404
    assert (
        env.client.post(f"{TERMINAL_PREFIX}/sessions/{data['terminal_id']}/ticket").status_code
        == 404
    )
    assert env.client.get(f"{TERMINAL_PREFIX}/sessions").json()["data"]["sessions"] == []


def test_remote_owner_returns_409(env) -> None:
    remote_id = str(uuid.uuid4())
    env.redis.store[f"terminal:owner:{remote_id}"] = "other-worker"
    response = env.client.delete(f"{TERMINAL_PREFIX}/sessions/{remote_id}")
    assert response.status_code == 409
    assert response.json()["detail"] == "terminal_owned_elsewhere"
    response = env.client.post(f"{TERMINAL_PREFIX}/sessions/{remote_id}/ticket")
    assert response.status_code == 409


def test_ticket_rotation_invalidates_old_ticket(env) -> None:
    data = _create(env)
    terminal_id, first_ticket = data["terminal_id"], data["ticket"]

    response = env.client.post(f"{TERMINAL_PREFIX}/sessions/{terminal_id}/ticket")
    assert response.status_code == 200
    second_ticket = response.json()["data"]["ticket"]
    assert second_ticket != first_ticket
    assert f"terminal:ticket:{first_ticket}" not in env.redis.store
    assert f"terminal:ticket:{second_ticket}" in env.redis.store

    # 旧票据握手失败，新票据可用。
    _assert_ws_rejected(env, terminal_id, first_ticket)
    with _connect(env, terminal_id, second_ticket) as ws:
        _ = ws


# ── WS：握手与消息协议 ───────────────────────────────────────────────────────


def test_ws_requires_auth_subprotocol(env) -> None:
    data = _create(env)
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with env.client.websocket_connect(
            f"{TERMINAL_PREFIX}/ws/{data['terminal_id']}",
            headers={"origin": TEST_ORIGIN},
        ) as ws:
            ws.receive_json()
    assert excinfo.value.code == 1008


def test_ws_rejects_bad_origin(env) -> None:
    data = _create(env)
    _assert_ws_rejected(env, data["terminal_id"], data["ticket"], origin="http://evil.example")


def test_ws_rejects_missing_origin(env) -> None:
    data = _create(env)
    with pytest.raises(WebSocketDisconnect) as excinfo:
        with env.client.websocket_connect(
            f"{TERMINAL_PREFIX}/ws/{data['terminal_id']}",
            subprotocols=[f"auth.{data['ticket']}"],
        ) as ws:
            ws.receive_json()
    assert excinfo.value.code == 1008


def test_ws_expired_ticket_rejected(env) -> None:
    data = _create(env)
    env.redis.store.pop(f"terminal:ticket:{data['ticket']}")  # 模拟 TTL 过期
    _assert_ws_rejected(env, data["terminal_id"], data["ticket"])


def test_ws_ticket_is_one_time(env) -> None:
    data = _create(env)
    with _connect(env, data["terminal_id"], data["ticket"]) as ws:
        _ = ws
    _assert_ws_rejected(env, data["terminal_id"], data["ticket"])


def test_ws_foreign_terminal_rejected(env) -> None:
    data = _create(env)  # terminal 属于 user 1
    forged = "forged-ticket-value"
    env.redis.store[f"terminal:ticket:{forged}"] = f"2:{data['terminal_id']}"
    _assert_ws_rejected(env, data["terminal_id"], forged)


def test_ws_ticket_terminal_mismatch_rejected(env) -> None:
    first = _create(env)
    second = _create(env)
    # 用 first 的票据去连 second 的终端。
    _assert_ws_rejected(env, second["terminal_id"], first["ticket"])


def test_ws_output_replay_input_ping_and_exit(env) -> None:
    data = _create(env)
    terminal_id = data["terminal_id"]
    session = env.manager.get_local(terminal_id)
    backend = env.backends[0]

    # 先产生输出（在 portal loop 内 feed），等 reader 写入 ring buffer。
    env.portal.call(backend.feed, b"hello-from-backend\n")
    _wait_until(session.has_unread)

    with _connect(env, terminal_id, data["ticket"]) as ws:
        replay = ws.receive_json()
        assert replay == {"type": "output", "data": "hello-from-backend\n"}

        ws.send_json({"type": "input", "data": "ls\n"})
        _wait_until(lambda: backend.sent == [b"ls\n"])

        ws.send_json({"type": "resize", "cols": 132, "rows": 43})
        _wait_until(lambda: backend.resizes == [(132, 43)])

        ws.send_json({"type": "ping"})
        assert ws.receive_json() == {"type": "pong"}

        # 非法 JSON 返回 error 且连接不中断。
        ws.send_text("not-json")
        error = ws.receive_json()
        assert error["type"] == "error"
        assert error["code"] == "invalid_json"

        # 进程退出 → exit 消息（code=0）。
        env.portal.call(backend.emit_eof, 0)
        exit_message = ws.receive_json()
        assert exit_message["type"] in ("output", "exit")
        if exit_message["type"] == "output":
            exit_message = ws.receive_json()
        assert exit_message == {"type": "exit", "code": 0}


def test_ws_single_writer_busy_error(env) -> None:
    data = _create(env)
    session = env.manager.get_local(data["terminal_id"])

    with _connect(env, data["terminal_id"], data["ticket"]) as ws:

        async def _hold_send_lock() -> None:
            await session.send_lock.acquire()

        async def _release_send_lock() -> None:
            session.send_lock.release()

        # 模拟 Agent 工具正在写入（从 portal loop 内持锁）。
        env.portal.call(_hold_send_lock)
        try:
            ws.send_json({"type": "input", "data": "b" * 8})
            message = ws.receive_json()
            assert message["type"] == "error"
            assert message["code"] == "terminal_busy"
            assert env.backends[0].sent == []
        finally:
            env.portal.call(_release_send_lock)


def test_ws_input_size_limit(env) -> None:
    data = _create(env)
    with _connect(env, data["terminal_id"], data["ticket"]) as ws:
        ws.send_json({"type": "input", "data": "x" * 2048})
        message = ws.receive_json()
        assert message["type"] == "error"
        assert message["code"] == "terminal_input_too_large"
        assert env.backends[0].sent == []
