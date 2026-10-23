"""JSON-RPC dispatcher 与 RPCService 的单元测试（issue #480 §5.3 / §5.4）。"""

import asyncio

import pytest

import app.rpc.dispatcher as dispatcher_module
from app.agent.events import AgentEvent
from app.agent.service import agent_service
from app.rpc.dispatcher import Dispatcher, RPCError, RPCService, cancel_task_and_wait


class FakeRedis:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}

    async def get(self, key: str):
        return self.kv.get(key)

    async def set(self, key: str, value: str, nx: bool = False, ex: int | None = None):
        if nx and key in self.kv:
            return None
        self.kv[key] = value
        return True

    async def setex(self, key: str, ttl: int, value: str):
        self.kv[key] = value
        return True

    async def delete(self, key: str):
        self.kv.pop(key, None)
        return 1

    def __bool__(self) -> bool:
        return True


async def _wait_for(pred, timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not pred():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_concurrent_20_requests_out_of_order():
    sent: list[dict] = []

    async def send(message: dict) -> None:
        sent.append(message)

    async def slow(params: dict):
        await asyncio.sleep(params.get("delay", 0))
        return {"n": params["n"]}

    disp = Dispatcher({"slow": slow}, send=send)
    for i in range(20):
        disp.dispatch(
            {
                "jsonrpc": "2.0",
                "id": i,
                "method": "slow",
                "params": {"n": i, "delay": (20 - i) * 0.002},
            }
        )
    await disp.wait_idle(timeout=5)
    assert len(sent) == 20
    ids = [message["id"] for message in sent]
    assert sorted(ids) == list(range(20))
    assert ids != sorted(ids), "响应必须按完成顺序乱序返回"
    assert all(message["result"] == {"n": message["id"]} for message in sent)
    await disp.close()


@pytest.mark.asyncio
async def test_duplicate_id_rejected_with_invalid_request():
    sent: list[dict] = []
    started = asyncio.Event()
    release = asyncio.Event()

    async def send(message: dict) -> None:
        sent.append(message)

    async def block(_params: dict):
        started.set()
        await release.wait()
        return "ok"

    disp = Dispatcher({"block": block}, send=send)
    disp.dispatch({"jsonrpc": "2.0", "id": 1, "method": "block", "params": {}})
    await started.wait()
    disp.dispatch({"jsonrpc": "2.0", "id": 1, "method": "block", "params": {}})
    await _wait_for(lambda: any("error" in m for m in sent))
    release.set()
    await disp.wait_idle(timeout=5)

    errors = [m for m in sent if "error" in m]
    assert len(errors) == 1 and errors[0]["error"]["code"] == -32600
    assert len([m for m in sent if "result" in m]) == 1
    await disp.close()


@pytest.mark.asyncio
async def test_unknown_method_and_invalid_request():
    disp = Dispatcher({})

    unknown = await disp.handle_message({"jsonrpc": "2.0", "id": 2, "method": "nope", "params": {}})
    assert unknown["error"]["code"] == -32601

    bad = await disp.handle_message({"id": 3, "method": "x", "params": {}})
    assert bad["error"]["code"] == -32600

    notification = await disp.handle_message({"jsonrpc": "2.0", "method": "unknown.notification"})
    assert notification is None

    await disp.close()


@pytest.mark.asyncio
async def test_server_request_cancel_and_forward_session_cancel():
    sent: list[dict] = []
    cancelled_sessions: list[str] = []

    async def send(message: dict) -> None:
        sent.append(message)

    async def session_cancel(params: dict):
        cancelled_sessions.append(params["sessionId"])
        return {"cancelled": True}

    disp = Dispatcher({"session/cancel": session_cancel}, send=send)
    task = asyncio.create_task(
        disp.request_client(
            "session/request_permission", {"toolName": "x"}, session_id="s-1", timeout=5
        )
    )
    await _wait_for(lambda: bool(sent))
    server_id = sent[0]["id"]

    await disp.handle_message(
        {"jsonrpc": "2.0", "method": "$/cancel_request", "params": {"requestId": server_id}}
    )
    result = await task
    assert result == {"cancelled": True}
    assert cancelled_sessions == ["s-1"], "取消服务端请求时必须转发 session/cancel"
    await disp.close()


@pytest.mark.asyncio
async def test_server_request_timeout_is_denial():
    sent: list[dict] = []

    async def send(message: dict) -> None:
        sent.append(message)

    disp = Dispatcher({}, send=send, permission_timeout=0.05)
    with pytest.raises(RPCError) as exc:
        await disp.request_client("session/request_permission", {}, session_id="s-1")
    assert "超时" in exc.value.message
    assert disp._pending_server == {}
    await disp.close()


@pytest.mark.asyncio
async def test_client_response_resolves_pending_and_disconnect_cleans_up():
    sent: list[dict] = []

    async def send(message: dict) -> None:
        sent.append(message)

    disp = Dispatcher({}, send=send, permission_timeout=5)
    task = asyncio.create_task(disp.request_client("session/request_permission", {}))
    await _wait_for(lambda: bool(sent))
    server_id = sent[0]["id"]
    await disp.handle_message({"jsonrpc": "2.0", "id": server_id, "result": {"decision": "allow"}})
    assert await task == {"decision": "allow"}

    # 断连：pending 请求被拒绝，任务不悬空。
    sent.clear()
    task2 = asyncio.create_task(disp.request_client("session/request_permission", {}))
    await _wait_for(lambda: bool(sent))
    await disp.close()
    result = await task2
    assert result.get("disconnected") is True
    assert disp._pending_server == {}


@pytest.mark.asyncio
async def test_rpc_service_prompt_idempotency(monkeypatch):
    """同一 clientMessageId 第二次 prompt 不产生第二次 run（幂等键命中）。"""
    fake = FakeRedis()
    monkeypatch.setattr(dispatcher_module, "redis_client", fake)
    monkeypatch.setattr(dispatcher_module, "AsyncSessionLocal", None, raising=False)

    calls = {"n": 0}

    async def fake_chat(**_kwargs):
        calls["n"] += 1
        yield AgentEvent(type="chunk", content="hi", task_id="t-1")
        yield AgentEvent(type="done", content="", task_id="t-1")

    monkeypatch.setattr(agent_service, "chat", fake_chat)

    service = RPCService(organization_id=1, user_id=7)
    service._create_session = _async_return("sess-1")  # type: ignore[assignment]
    service._save_interaction = _async_none()  # type: ignore[assignment]

    first = await service.session_prompt({"prompt": "hi", "clientMessageId": "msg-1"})
    second = await service.session_prompt({"prompt": "hi", "clientMessageId": "msg-1"})
    assert first == second, "重复 clientMessageId 必须返回同一 messageId/sessionId"
    assert first["sessionId"] == "sess-1"

    await asyncio.gather(
        *[t for t in service._prompt_tasks.values() if not t.done()], return_exceptions=True
    )
    await asyncio.sleep(0.05)
    assert calls["n"] == 1, "幂等命中后不得启动第二次 run"


def _async_return(value):
    async def _inner(*_args, **_kwargs):
        return value

    return _inner


def _async_none():
    async def _inner(*_args, **_kwargs):
        return None

    return _inner


@pytest.mark.asyncio
async def test_prompt_cancel_stops_session_events_and_reports_cancelled(tmp_path, monkeypatch):
    """session/cancel：等 DB 收敛为 cancelled 后才回 {cancelled:true}；
    之后不再发该会话 session.event；run 自然结束时也不落库、不谎报 done。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.agent.checkpoint as checkpoint
    import app.models  # noqa: F401 — create_all 需要全部表
    from app.core.database import Base
    from app.models.agent_task import AgentTask, TaskStatus
    from app.models.chat import ChatSession, ChatSessionStatus
    from app.models.user import User

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cancel.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(
            User(id=1, username="u", email="u@example.com", hashed_password="x", is_active=True)
        )
        session.add(
            ChatSession(
                id="s1",
                user_id=1,
                title="t",
                status=ChatSessionStatus.ACTIVE,
                root_session_id="s1",
            )
        )
        session.add(
            AgentTask(
                id="t1",
                user_id=1,
                organization_id=None,
                query="hi",
                status=TaskStatus.RUNNING,
                session_id="s1",
            )
        )
        await session.commit()

    fake = FakeRedis()
    monkeypatch.setattr(dispatcher_module, "redis_client", fake)
    monkeypatch.setattr(checkpoint, "redis_client", fake)
    monkeypatch.setattr(checkpoint, "AsyncSessionLocal", factory)

    started = asyncio.Event()

    async def fake_chat(**_kwargs):
        """模拟 executor：检查点观察到取消旗 → 真实 mark_terminal 收敛终态。"""
        started.set()
        for _ in range(200):
            if await checkpoint.is_cancel_requested("t1"):
                ok = await checkpoint.mark_terminal(
                    "t1",
                    TaskStatus.CANCELLED,
                    error="用户取消（测试）",
                    error_type="user_cancelled",
                )
                assert ok
                yield AgentEvent(type="run_cancelled", content="", task_id="t1")
                return
            await asyncio.sleep(0.02)
            yield AgentEvent(type="chunk", content="c", task_id="t1")
        yield AgentEvent(type="done", content="", task_id="t1")

    monkeypatch.setattr(agent_service, "chat", fake_chat)

    sent: list[dict] = []

    async def send(message: dict) -> None:
        sent.append(message)

    service = RPCService(organization_id=None, user_id=1, db_factory=factory)
    saved = {"n": 0}

    async def save_noop(*_args, **_kwargs):
        saved["n"] += 1

    service._save_interaction = save_noop  # type: ignore[assignment]
    service.build_dispatcher(send=send)

    result = await service.session_prompt(
        {"sessionId": "s1", "prompt": "hi", "clientMessageId": "m1"}
    )
    assert result["sessionId"] == "s1"
    await asyncio.wait_for(started.wait(), timeout=2)
    await asyncio.sleep(0.12)

    cancel = await service.session_cancel({"sessionId": "s1"})
    assert cancel == {"cancelled": True}
    cancel_seq = max(
        [m["params"]["seq"] for m in sent if m.get("method") == "session.event"] or [0]
    )

    await asyncio.gather(*list(service._prompt_tasks.values()), return_exceptions=True)
    late = [
        m for m in sent if m.get("method") == "session.event" and m["params"]["seq"] > cancel_seq
    ]
    statuses = [m["params"]["status"] for m in sent if m.get("method") == "session.status"]
    assert late == [], "cancel 返回后不得再发该会话的 session.event"
    assert statuses == ["cancelled"], "自然结束也要如实报 cancelled（不得谎报 done）"
    assert saved["n"] == 0, "取消后不落库"
    await engine.dispose()


class _BrokenRedis:
    """连接失败替身：命令一律抛 ConnectionError（Redis 不可用，issue #493）。"""

    def __bool__(self) -> bool:
        return True

    async def get(self, *_args, **_kwargs):
        raise ConnectionError("redis down")

    async def set(self, *_args, **_kwargs):
        raise ConnectionError("redis down")

    async def setex(self, *_args, **_kwargs):
        raise ConnectionError("redis down")

    async def delete(self, *_args, **_kwargs):
        raise ConnectionError("redis down")


async def _make_cancel_db(tmp_path, *, task_status=None):
    """建 sqlite（全部表）+ user/chat session（可选 RUNNING task）。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.models  # noqa: F401 — create_all 需要全部表
    from app.core.database import Base
    from app.models.agent_task import AgentTask
    from app.models.chat import ChatSession, ChatSessionStatus
    from app.models.user import User

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cancel-493.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(
            User(id=1, username="u", email="u@example.com", hashed_password="x", is_active=True)
        )
        session.add(
            ChatSession(
                id="s1",
                user_id=1,
                title="t",
                status=ChatSessionStatus.ACTIVE,
                root_session_id="s1",
            )
        )
        if task_status is not None:
            session.add(
                AgentTask(
                    id="t1",
                    user_id=1,
                    organization_id=None,
                    query="hi",
                    status=task_status,
                    session_id="s1",
                )
            )
        await session.commit()
    return engine, factory


@pytest.mark.asyncio
async def test_prompt_idempotency_redis_unavailable_raises_32603(monkeypatch):
    """幂等通道不可用必须 -32603，绝不 warning 后继续执行（issue #493）。"""
    monkeypatch.setattr(dispatcher_module, "redis_client", _BrokenRedis())

    service = RPCService(organization_id=1, user_id=7)
    service._create_session = _async_return("sess-1")  # type: ignore[assignment]

    with pytest.raises(RPCError) as exc:
        await service.session_prompt({"prompt": "hi", "clientMessageId": "m-1"})
    assert exc.value.code == -32603
    assert "Redis" in exc.value.message
    assert service._prompt_tasks == {}, "幂等失败不得启动 run"


@pytest.mark.asyncio
async def test_session_cancel_redis_unavailable_raises_32603(tmp_path, monkeypatch):
    """运行中任务 + Redis 不可用 → -32603，且恢复事件外发。"""
    import app.agent.checkpoint as checkpoint
    from app.models.agent_task import TaskStatus

    engine, factory = await _make_cancel_db(tmp_path, task_status=TaskStatus.RUNNING)
    try:
        monkeypatch.setattr(checkpoint, "AsyncSessionLocal", factory)
        monkeypatch.setattr(checkpoint, "redis_client", _BrokenRedis())

        service = RPCService(organization_id=None, user_id=1, db_factory=factory)
        with pytest.raises(RPCError) as exc:
            await service.session_cancel({"sessionId": "s1"})
        assert exc.value.code == -32603
        assert "Redis" in exc.value.message
        assert service._cancelled_sessions == set(), "通道不可用必须恢复事件外发"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_session_cancel_without_task_returns_32004(tmp_path, monkeypatch):
    import app.agent.checkpoint as checkpoint

    engine, factory = await _make_cancel_db(tmp_path, task_status=None)
    try:
        monkeypatch.setattr(checkpoint, "AsyncSessionLocal", factory)
        service = RPCService(organization_id=None, user_id=1, db_factory=factory)
        with pytest.raises(RPCError) as exc:
            await service.session_cancel({"sessionId": "s1"})
        assert exc.value.code == -32004
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_cancel_task_and_wait_timeout_raises_after_flag_set(tmp_path, monkeypatch):
    """旗已置但任务未收敛 → TimeoutError（取消已受理，不得谎报 cancelled）。"""
    import app.agent.checkpoint as checkpoint
    from app.models.agent_task import TaskStatus

    engine, factory = await _make_cancel_db(tmp_path, task_status=TaskStatus.RUNNING)
    try:
        fake = FakeRedis()
        monkeypatch.setattr(checkpoint, "AsyncSessionLocal", factory)
        monkeypatch.setattr(checkpoint, "redis_client", fake)

        with pytest.raises(TimeoutError):
            await cancel_task_and_wait("t1", user_id=1, timeout=0.2)
        assert fake.kv.get("agent:task:cancel:t1") == "1", "超时前必须先置取消旗"
    finally:
        await engine.dispose()
