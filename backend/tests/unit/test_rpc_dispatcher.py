"""JSON-RPC dispatcher 与 RPCService 的单元测试（issue #480 §5.3 / §5.4）。"""

import asyncio

import pytest

import app.rpc.dispatcher as dispatcher_module
from app.agent.events import AgentEvent
from app.agent.service import agent_service
from app.rpc.dispatcher import Dispatcher, RPCError, RPCService


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
