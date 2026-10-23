"""终端 API / WS / 工具契约测试（issue #477 任务 3-4）。

全部使用 FakeBackend + 假 Redis，不依赖 Docker / MySQL / 真实 Redis：

* HTTP：禁用 503、Docker 不可用 503、超配额 409、cols/rows 400、404、
  401 无关（认证由既有依赖覆盖）、DELETE 后列表不再返回、票据轮换。
* WS：成功连接/输入/ping-pong/退出、无票据/非法 Origin/过期/复用/非本人
  一律 1008 且不收任何 output。
* 工具（task 4）：禁用/越权/超时/截断/审批强制（TERMINAL_REQUIRE_APPROVAL）。
"""

from __future__ import annotations

import asyncio
import json
import time
import types
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

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


def _configure_service(monkeypatch, *, ring_buffer_bytes: int = 4096):
    """把 terminal_service 单例接到 FakeBackend 管理器 + 假 Redis。"""
    backends: list[FakeBackend] = []

    def factory(**kwargs) -> FakeBackend:
        backend = FakeBackend(**kwargs)
        backends.append(backend)
        return backend

    manager = TerminalManager(
        config=TerminalConfig(
            max_per_user=2,
            max_per_org=10,
            ring_buffer_bytes=ring_buffer_bytes,
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

    # 审计写库替身：terminal_service._audit 会调用 audit_service.log_activity。
    from app.services import audit_service as audit_module

    audit_mock = AsyncMock()
    monkeypatch.setattr(audit_module.audit_service, "log_activity", audit_mock)

    settings = terminal_service._settings()
    monkeypatch.setattr(settings, "TERMINAL_ENABLED", True)
    monkeypatch.setattr(settings, "SANDBOX_MODE", "docker")
    monkeypatch.setattr(settings, "TERMINAL_TICKET_TTL_SECONDS", 60)
    monkeypatch.setattr(settings, "ALLOWED_ORIGINS", [*settings.ALLOWED_ORIGINS, TEST_ORIGIN])

    return types.SimpleNamespace(
        manager=manager,
        backends=backends,
        redis=fake_redis,
        settings=settings,
        audit=audit_mock,
    )


@pytest.fixture
def env(monkeypatch):
    """FakeBackend 管理器 + 假 Redis + 独立 FastAPI app（TestClient 单 portal）。"""
    configured = _configure_service(monkeypatch)
    holder = types.SimpleNamespace(current=FakeUser())
    app = FastAPI()
    app.include_router(terminal_api.router, prefix=TERMINAL_PREFIX)
    app.add_exception_handler(AppError, _app_error_handler)

    async def _db_override():
        yield None

    app.dependency_overrides[get_current_user] = lambda: holder.current
    app.dependency_overrides[get_db] = _db_override

    with TestClient(app) as client:
        configured.client = client
        configured.holder = holder
        configured.portal = client.portal
        yield configured
        # 在同一个 portal loop 内关闭全部会话，避免跨 loop 清理。
        client.portal.call(configured.manager.shutdown)


@pytest.fixture
async def tool_env(monkeypatch):
    """工具测试用：不建 TestClient（任务/锁都在 pytest 自己的事件循环里）。"""
    configured = _configure_service(monkeypatch)
    yield configured
    await configured.manager.shutdown()


def _create(env, **overrides) -> dict[str, Any]:
    payload = {
        "session_id": "11111111-1111-4111-8111-111111111111",
        "cols": 100,
        "rows": 30,
        **overrides,
    }
    response = env.client.post(f"{TERMINAL_PREFIX}/sessions", json=payload)
    assert response.status_code == 200, response.text
    return response.json()["data"]


def _connect(env, terminal_id: str, ticket: str, origin: str = TEST_ORIGIN):
    return env.client.websocket_connect(
        f"{TERMINAL_PREFIX}/ws/{terminal_id}",
        headers={"origin": origin},
        subprotocols=[f"auth.{ticket}"],
    )


def _await_policy_close(ws, timeout: float = 3.0) -> int:
    """在超时内等待服务端以 close 帧拒绝；期间收到任何数据帧都判失败。

    直接轮询 TestClient 内部的 anyio 流：``ws.receive_json()`` 在「服务端错误地
    保持连接」时会永久阻塞，把回归（拒绝路径被短路）变成 CI 挂起而不是红灯。
    """
    from anyio import EndOfStream, WouldBlock

    deadline = time.monotonic() + timeout
    while True:
        try:
            message = ws.portal.call(ws._send_rx.receive_nowait)
        except WouldBlock:
            message = None
        except EndOfStream:
            raise AssertionError("WS 会话已结束但没有收到 close 帧") from None
        if message is not None:
            if message["type"] == "websocket.close":
                return int(message.get("code", 0))
            raise AssertionError(f"拒绝路径不应发送数据帧: {message}")
        if time.monotonic() > deadline:
            raise AssertionError("服务端未在超时内以 1008 关闭连接（拒绝契约被破坏）")
        time.sleep(0.01)


def _assert_ws_rejected(env, terminal_id: str, ticket: str, origin: str = TEST_ORIGIN) -> None:
    with _connect(env, terminal_id, ticket, origin) as ws:
        code = _await_policy_close(ws)
    assert code == 1008


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
    assert sessions[0]["session_id"] == "11111111-1111-4111-8111-111111111111"
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
    # owner 值 = "{user_id}:{worker_id}"；本人 + 其他 worker → 409。
    env.redis.store[f"terminal:owner:{remote_id}"] = "1:other-worker"
    response = env.client.delete(f"{TERMINAL_PREFIX}/sessions/{remote_id}")
    assert response.status_code == 409
    assert response.json()["detail"] == "terminal_owned_elsewhere"
    response = env.client.post(f"{TERMINAL_PREFIX}/sessions/{remote_id}/ticket")
    assert response.status_code == 409

    # 其他用户的终端（即使存在）一律 404，防存在性枚举（issue §5.4）。
    foreign_id = str(uuid.uuid4())
    env.redis.store[f"terminal:owner:{foreign_id}"] = "2:other-worker"
    response = env.client.delete(f"{TERMINAL_PREFIX}/sessions/{foreign_id}")
    assert response.status_code == 404
    assert response.json()["detail"] == "terminal_not_found"
    response = env.client.post(f"{TERMINAL_PREFIX}/sessions/{foreign_id}/ticket")
    assert response.status_code == 404


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
    with env.client.websocket_connect(
        f"{TERMINAL_PREFIX}/ws/{data['terminal_id']}",
        headers={"origin": TEST_ORIGIN},
    ) as ws:
        assert _await_policy_close(ws) == 1008


def test_ws_rejects_bad_origin(env) -> None:
    data = _create(env)
    _assert_ws_rejected(env, data["terminal_id"], data["ticket"], origin="http://evil.example")


def test_ws_rejects_missing_origin(env) -> None:
    data = _create(env)
    with env.client.websocket_connect(
        f"{TERMINAL_PREFIX}/ws/{data['terminal_id']}",
        subprotocols=[f"auth.{data['ticket']}"],
    ) as ws:
        assert _await_policy_close(ws) == 1008


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


# ── Agent 六工具（task 4）────────────────────────────────────────────────────


async def _call_tool(
    name: str, arguments: dict[str, Any], *, user_id: int = 1, organization_id: int = 1
):
    import app.agent.tools  # noqa: F401 — 触发 @register_tool 注册
    from app.agent.registry import tool_registry

    return await tool_registry.execute_detailed(
        name, arguments, user_id=user_id, organization_id=organization_id
    )


async def _wait_until_async(predicate, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("条件在超时内未满足")
        await asyncio.sleep(0.01)


async def test_terminal_tools_registered_high_risk() -> None:
    import app.agent.tools  # noqa: F401
    from app.agent.registry import tool_registry

    for name in (
        "terminal_open",
        "terminal_send",
        "terminal_read",
        "terminal_signal",
        "terminal_close",
        "terminal_list",
    ):
        entry = tool_registry.get(name)
        assert entry is not None, name
        assert entry.risk_level == "high", name


async def test_tools_disabled_returns_terminal_disabled(tool_env, monkeypatch) -> None:
    monkeypatch.setattr(terminal_service, "enabled", lambda: False, raising=False)
    for name, args in (
        ("terminal_open", {}),
        ("terminal_list", {}),
        ("terminal_read", {"terminal_id": "x"}),
    ):
        result = await _call_tool(name, args)
        assert result.success is False
        assert result.error.code == "terminal_disabled"


async def test_tool_open_send_read_close_list_roundtrip(tool_env) -> None:
    opened = await _call_tool("terminal_open", {"session_id": "s1", "cols": 90, "rows": 20})
    assert opened.success, opened
    terminal_id = opened.data["terminal_id"]
    assert opened.data["ws_path"].endswith(terminal_id)
    backend = tool_env.backends[0]
    assert backend.spawn_args == (90, 20)

    sent = await _call_tool("terminal_send", {"terminal_id": terminal_id, "data": "echo hi\n"})
    assert sent.success and sent.data == {"bytes_written": 8}
    assert backend.sent == [b"echo hi\n"]

    backend.feed(b"hi\n")
    session = tool_env.manager.get_local(terminal_id)
    await _wait_until_async(lambda: session.has_unread())
    read = await _call_tool("terminal_read", {"terminal_id": terminal_id, "max_bytes": 100})
    assert read.success
    assert read.data["data"] == "hi\n"
    assert read.data["truncated"] is False
    assert read.data["dropped_total"] == 0

    listing = await _call_tool("terminal_list", {})
    assert [t["terminal_id"] for t in listing.data["terminals"]] == [terminal_id]

    signalled = await _call_tool("terminal_signal", {"terminal_id": terminal_id, "signal": "INT"})
    assert signalled.success and backend.signals == ["INT"]

    closed = await _call_tool("terminal_close", {"terminal_id": terminal_id})
    assert closed.success and closed.data == {"ok": True}
    listing = await _call_tool("terminal_list", {})
    assert listing.data["terminals"] == []


async def test_tool_foreign_terminal_returns_not_found(tool_env) -> None:
    opened = await _call_tool("terminal_open", {})
    terminal_id = opened.data["terminal_id"]
    for name, args in (
        ("terminal_send", {"terminal_id": terminal_id, "data": "x"}),
        ("terminal_read", {"terminal_id": terminal_id}),
        ("terminal_signal", {"terminal_id": terminal_id, "signal": "TERM"}),
        ("terminal_close", {"terminal_id": terminal_id}),
    ):
        result = await _call_tool(name, args, user_id=2)
        assert result.success is False, (name, result)
        assert result.error.code == "terminal_not_found", (name, result)
    # 越权调用没有产生副作用。
    assert tool_env.manager.has_local(terminal_id)


async def test_tool_read_timeout(tool_env, monkeypatch) -> None:
    from app.agent.tools import terminal as terminal_tools

    opened = await _call_tool("terminal_open", {})
    terminal_id = opened.data["terminal_id"]
    monkeypatch.setattr(terminal_tools, "_READ_WAIT_SECONDS", 0.05)
    result = await _call_tool("terminal_read", {"terminal_id": terminal_id})
    assert result.success is False
    assert result.error.code == "terminal_timeout"


async def test_tool_read_truncation_and_dropped_total(tool_env) -> None:
    opened = await _call_tool("terminal_open", {})
    terminal_id = opened.data["terminal_id"]
    backend = tool_env.backends[0]
    backend.feed(b"x" * 5000)  # ring buffer 4096 → 丢弃 904 字节
    session = tool_env.manager.get_local(terminal_id)
    await _wait_until_async(lambda: session.dropped_total == 904)

    reads = []
    for _ in range(4):
        result = await _call_tool("terminal_read", {"terminal_id": terminal_id, "max_bytes": 1024})
        assert result.success, result
        assert len(result.data["data"]) == 1024
        reads.append(result.data)
    assert reads[0]["truncated"] is True
    assert reads[-1]["truncated"] is False
    assert reads[-1]["dropped_total"] == 904


async def test_tool_quota_maps_to_rate_limited(tool_env) -> None:
    await _call_tool("terminal_open", {})
    await _call_tool("terminal_open", {})
    result = await _call_tool("terminal_open", {})
    assert result.success is False
    assert result.error.code == "rate_limited"


# ── 强制审批（executor）─────────────────────────────────────────────────────


def _make_executor(approval_mode: str = "never"):
    import app.agent.tools  # noqa: F401 — 确保 terminal_* 已注册（risk_level 判定）
    from app.agent.config import AgentConfig
    from app.agent.executor import Executor

    config = AgentConfig()
    config.approval_mode = approval_mode
    return Executor(MagicMock(), config, MagicMock())


def _tool_call(name: str, arguments: dict[str, Any]):
    return types.SimpleNamespace(
        id=f"call-{name}",
        function=types.SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


async def test_terminal_forced_approval_no_channel_denies(monkeypatch) -> None:
    """TERMINAL_REQUIRE_APPROVAL=true + approval_mode=never + 无审批通道 →
    强制 deny（fail-closed），绝不静默执行。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "TERMINAL_REQUIRE_APPROVAL", True)
    executor = _make_executor("never")
    arguments = {"cols": 80}
    calls = [(_tool_call("terminal_open", arguments), arguments)]

    events = [event async for event in executor._approval_precheck(calls, None, None)]
    assert events == []
    assert executor._gate_materialization("terminal_open", arguments) == "deny"
    # 非终端工具不受影响（never 模式照旧放行）。
    assert executor._gate_materialization("web_search", {"query": "x"}) is None


async def test_terminal_forced_approval_uses_channel(monkeypatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "TERMINAL_REQUIRE_APPROVAL", True)
    executor = _make_executor("never")
    executor.approval_check = AsyncMock(return_value="unavailable")
    arguments = {"terminal_id": "t-1", "data": "rm -rf /"}
    calls = [(_tool_call("terminal_send", arguments), arguments)]

    events = [event async for event in executor._approval_precheck(calls, None, None)]
    assert events == []
    executor.approval_check.assert_awaited_once()
    request = executor.approval_check.await_args.args[0]
    assert request.tool_name == "terminal_send"
    assert request.risk_level == "high"
    assert executor.approval_degraded is True
    assert executor._gate_materialization("terminal_send", arguments) == "unavailable"


async def test_terminal_forced_approval_can_be_disabled(monkeypatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "TERMINAL_REQUIRE_APPROVAL", False)
    executor = _make_executor("never")
    executor.approval_check = AsyncMock(return_value="unavailable")
    arguments = {"cols": 80}
    calls = [(_tool_call("terminal_open", arguments), arguments)]

    _events = [event async for event in executor._approval_precheck(calls, None, None)]
    executor.approval_check.assert_not_awaited()
    assert executor._gate_materialization("terminal_open", arguments) is None


# ── 审计（issue §3：创建/关闭/命令摘要）────────────────────────────────────


def test_audit_records_create_command_and_close(env) -> None:
    data = _create(env)
    terminal_id = data["terminal_id"]
    with _connect(env, terminal_id, data["ticket"]) as ws:
        ws.send_json({"type": "input", "data": "echo audit-ok\n"})
        _wait_until(lambda: env.backends[0].sent == [b"echo audit-ok\n"])
        _wait_until(
            lambda: any(
                call.kwargs.get("action") == "terminal_command"
                for call in env.audit.await_args_list
            )
        )
    env.client.delete(f"{TERMINAL_PREFIX}/sessions/{terminal_id}")

    actions = [call.kwargs.get("action") for call in env.audit.await_args_list]
    assert "terminal_create" in actions
    assert "terminal_command" in actions
    assert "terminal_close" in actions
    command_call = next(
        call
        for call in env.audit.await_args_list
        if call.kwargs.get("action") == "terminal_command"
    )
    assert command_call.kwargs.get("detail") == "echo audit-ok"
    assert command_call.kwargs.get("target_id") == terminal_id


def test_summarize_line_handles_backspace_and_control_chars() -> None:
    from app.services.terminal_service import _summarize_line

    assert _summarize_line("lss\x7f") == "ls"  # 退格生效
    assert _summarize_line("echo a\x01b") == "echo ab"  # 控制字符脱敏
    assert _summarize_line("ls\x1b[A") == "ls"  # 方向键 CSI 序列被剥离
    assert _summarize_line("   ") is None
    assert _summarize_line("\r\n") is None
    assert len(_summarize_line("x" * 500)) == 200


async def test_audit_lines_per_input_are_capped(tool_env) -> None:
    """一条超多行输入不得逐行写库拖垮输入循环（最多 20 行 + 1 条计数摘要）。"""
    data = "\n".join(f"cmd-{i}" for i in range(30)) + "\n"
    await terminal_service.audit_completed_lines(user_id=1, terminal_id="t", data=data)
    actions = [
        call.kwargs.get("action")
        for call in tool_env.audit.await_args_list
        if call.kwargs.get("action") == "terminal_command"
    ]
    assert len(actions) == 21
    assert "truncated" in tool_env.audit.await_args_list[-1].kwargs.get("detail", "")


def test_audit_records_keystroke_commands_across_frames(env) -> None:
    """xterm 逐键发送：回车单独一帧，服务端必须按行缓冲后再审计。"""
    data = _create(env)
    terminal_id = data["terminal_id"]
    with _connect(env, terminal_id, data["ticket"]) as ws:
        for chunk in ("echo", " ", "audit", "\r"):
            ws.send_json({"type": "input", "data": chunk})
        _wait_until(lambda: env.backends[0].sent == [b"echo", b" ", b"audit", b"\r"])
        _wait_until(
            lambda: any(
                call.kwargs.get("action") == "terminal_command"
                and call.kwargs.get("detail") == "echo audit"
                for call in env.audit.await_args_list
            )
        )


# ── 回收器 / 孤儿清理（对抗评审 M4）──────────────────────────────────────


def _created_ago(seconds: float) -> str:
    from datetime import UTC, datetime, timedelta

    return (datetime.now(UTC) - timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _container_info(terminal_id: str, age_seconds: float) -> dict[str, Any]:
    return {
        "id": f"cid-{terminal_id}",
        "name": f"docmind-term-{terminal_id}",
        "labels": {"docmind.terminal": terminal_id},
        "created": _created_ago(age_seconds),
        "status": "running",
    }


async def test_orphan_cleanup_matrix(tool_env, monkeypatch) -> None:
    """owner 归属/心跳/年龄/本地会话四维判定；Redis 活时只删确定的孤儿。"""
    import app.services.terminal_service as service_module

    # 本地会话必须保留。
    local_session = await tool_env.manager.create(user_id=1, org_id=1)
    local_id = local_session.terminal_id

    for terminal_id, value in (
        ("t-alive", "1:worker-a"),
        ("t-dead-old", "1:worker-dead"),
        ("t-dead-young", "1:worker-dead2"),
        ("t-self", "1:test-worker"),
    ):
        tool_env.redis.store[f"terminal:owner:{terminal_id}"] = value
    tool_env.redis.store["terminal:worker:worker-a"] = "1"  # 存活 worker

    containers = [
        _container_info(local_id, 60),
        _container_info("t-alive", 60),
        _container_info("t-dead-old", 200),  # > 心跳宽限 120s → 删
        _container_info("t-dead-young", 30),  # 宽限期内 → 保留（M3）
        _container_info("t-self", 60),  # 本 worker 重启残留 → 删
        _container_info("t-noowner-young", 30),  # 无 owner 且未超寿命 → 保留
        _container_info("t-noowner-old", 4000),  # 无 owner 且超寿命 → 删
    ]
    removed: list[str] = []

    def _fake_remove(container_id: str) -> bool:
        removed.append(container_id)
        # 真实 Docker 删除后容器不再出现在下一轮列表里。
        containers[:] = [c for c in containers if c["id"] != container_id]
        return True

    monkeypatch.setattr(service_module, "list_terminal_containers", lambda: containers)
    monkeypatch.setattr(service_module, "remove_terminal_container", _fake_remove)

    async def _docker_ok() -> bool:
        return True

    monkeypatch.setattr(terminal_service, "docker_available", _docker_ok, raising=False)

    now = time.time()
    count = await terminal_service.cleanup_orphan_containers(now=now)
    # 第一轮：本 worker 重启残留 + 无 owner 且超寿命 → 删；
    # owner 心跳缺失只登记候选（首见），不立即删。
    assert count == 2
    assert removed == ["cid-t-self", "cid-t-noowner-old"]
    assert f"cid-{local_id}" not in removed

    # 宽限期内（缺失 60s）仍不删。
    assert await terminal_service.cleanup_orphan_containers(now=now + 60) == 0

    # 缺失 ≥ 120s：两个心跳缺失的 owner 容器被删（含年龄较小的那个）。
    count = await terminal_service.cleanup_orphan_containers(now=now + 121)
    assert count == 2
    assert removed == [
        "cid-t-self",
        "cid-t-noowner-old",
        "cid-t-dead-old",
        "cid-t-dead-young",
    ]
    # 候选已清空，重复调用不重复删除。
    assert await terminal_service.cleanup_orphan_containers(now=now + 200) == 0


async def test_orphan_cleanup_keeps_everything_when_redis_down(tool_env, monkeypatch) -> None:
    import app.services.terminal_service as service_module

    removed: list[str] = []
    monkeypatch.setattr(
        service_module,
        "list_terminal_containers",
        lambda: [_container_info("t-any", 4000)],
    )
    monkeypatch.setattr(
        service_module,
        "remove_terminal_container",
        lambda cid: removed.append(cid) or True,
    )

    async def _docker_ok() -> bool:
        return True

    monkeypatch.setattr(terminal_service, "docker_available", _docker_ok, raising=False)

    def _no_redis():
        raise TerminalRedisUnavailable("down")

    monkeypatch.setattr(terminal_service, "_redis", _no_redis, raising=False)

    assert await terminal_service.cleanup_orphan_containers() == 0
    assert removed == []


# ── Docker 后端（无 daemon）：读队列语义 / 镜像校验 ────────────────────────


async def test_docker_backend_read_queue_semantics() -> None:
    from app.agent.terminal.docker_backend import DockerTerminalBackend

    backend = DockerTerminalBackend(terminal_id="t", user_id=1, image="python:3.11-slim")
    backend._out_queue = asyncio.Queue()
    backend._slots.acquire()  # 模拟读线程投递前的背压槽位
    backend._deliver(b"hello")
    assert await backend.read(3) == b"hel"
    assert await backend.read(100) == b"lo"
    backend._deliver(None)
    assert await backend.read(10) == b""
    assert await backend.read(10) == b""  # EOF 粘性（reader 不会重复等待）


def test_terminal_image_must_be_pinned() -> None:
    from app.agent.terminal.docker_backend import validate_terminal_image
    from app.agent.terminal.manager import TerminalError

    assert validate_terminal_image("python:3.11-slim") == "python:3.11-slim"
    assert (
        validate_terminal_image("registry.local:5000/team/term:1.2")
        == "registry.local:5000/team/term:1.2"
    )
    with pytest.raises(TerminalError):
        validate_terminal_image("python:latest")
    with pytest.raises(TerminalError):
        validate_terminal_image("python")  # 裸名等价 latest
    with pytest.raises(TerminalError):
        validate_terminal_image("")


async def test_sweep_audits_reaped_close(tool_env) -> None:
    session = await tool_env.manager.create(user_id=1, org_id=1)
    session.last_activity_mono -= 901  # 超过空闲 900s
    count = await terminal_service.sweep()
    assert count == 1
    close_calls = [
        call
        for call in tool_env.audit.await_args_list
        if call.kwargs.get("action") == "terminal_close"
    ]
    assert close_calls, "回收关闭必须落审计"
    assert close_calls[-1].kwargs.get("detail") == "reason=idle_timeout"
