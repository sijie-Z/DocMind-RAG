"""HITL：ASGI 级全环 —— 暂停 → 审批/拒绝/取消 → 续跑（真路由 + sqlite + 假 LLM/假工具）。

危险工具用**替身**（fake_danger：注册表声明 risk_level="high"——同时验证
"注册表声明进入审批判定"这条接线），execute_detailed 用 AsyncMock（不碰真实
工具/服务）。链路：真 require_organization + 真 checkpoint + 真 loop/executor。

判别点：
- POST /chat 流：approval_required（含工具/参数/风险）+ done.task_state=
  waiting_approval；工具零执行；GET 状态视图 = waiting_approval + approval 详情；
- POST /approval approve → SSE 续跑 → 工具恰好执行一次 → COMPLETED；
- POST /approval deny → 结构化失败 approval_denied、工具零执行、run 收敛完成；
- waiting_approval 中 POST /cancel → 直接终态 cancelled；后续 approval/resume 409；
- running/completed 任务上的 cancel/approval 冲突语义（409）；未知任务 404；
- 活跃任务 cancel → 202 cancel_requested（旗已置，run 在检查点收敛——收敛行为
  的判别用例在 tests/unit/test_hitl_service_flow.py）。
"""


from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — 注册全部表，供 create_all
import app.api.v1.endpoints.agent as agent_api
from app.agent import checkpoint
from app.agent.exec_context import ExecutionContext
from app.agent.loop import PERAgentLoop
from app.agent.registry import ToolResult, tool_registry
from app.agent.service import agent_service
from app.core.database import Base
from app.core.security import get_current_user
from app.models.agent_task import AgentTask, TaskStatus
from app.rag.retriever import HybridRetriever

QUERY = "帮我执行一段脚本"
USER_ID = 7
_TASK_KW = "fake_danger"


def _mock_user(user_id: int = USER_ID, org_id: int | None = 1):
    class _User:
        pass

    user = _User()
    user.id = user_id
    user.organization_id = org_id
    user.is_active = True
    return user


class _FakeRedis:
    def __bool__(self):
        return True

    def __init__(self):
        self.store: dict[str, str] = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def setex(self, key, ttl, value):
        self.store[key] = value
        return True

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, key):
        self.store.pop(key, None)
        return 1

    async def expire(self, key, ttl):
        return key in self.store


def _sse_payloads(text: str) -> list[dict]:
    lines = [ln for ln in text.split("\n\n") if ln.startswith("data: ")]
    import json as _json

    return [_json.loads(ln[len("data: "):]) for ln in lines]


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    async def _save(self, ttl=86400 * 7):
        return False

    monkeypatch.setattr(ExecutionContext, "save", _save)
    monkeypatch.setattr(PERAgentLoop, "_write_experiences", AsyncMock())
    monkeypatch.setattr(HybridRetriever, "get_embedding", AsyncMock(return_value=[]))


@pytest.fixture
async def db_factory(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'hitl_api.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(checkpoint, "AsyncSessionLocal", factory)
    monkeypatch.setattr(agent_api, "AsyncSessionLocal", factory)
    yield factory
    await engine.dispose()


@pytest.fixture
def fake_redis(monkeypatch):
    r = _FakeRedis()
    monkeypatch.setattr(checkpoint, "redis_client", r)
    return r


@pytest.fixture
def fake_danger_tool():
    """危险工具替身：注册表**声明** risk_level="high"（审批判定输入的真实接线）。"""

    async def _handler(**_):
        return "should-not-run"

    tool_registry.register(
        name=_TASK_KW,
        description="测试替身：高危工具",
        parameters={"type": "object", "properties": {"target": {"type": "string"}}},
        handler=_handler,
        risk_level="high",
    )
    yield _TASK_KW
    tool_registry._tools.pop(_TASK_KW, None)


def _fake_client(tool_name: str = _TASK_KW, args: str = '{"target": "prod"}'):
    async def _create(*, messages, tools=None, **kwargs):
        if tools:
            tc = SimpleNamespace(id="tc-1", function=SimpleNamespace(name=tool_name, arguments=args))
            msg = SimpleNamespace(content="", tool_calls=[tc], role="assistant")
        else:
            msg = SimpleNamespace(content="已完成安全验证。", tool_calls=None, role="assistant")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=msg, finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
        )

    client = MagicMock()
    client.chat.completions.create = _create
    return client


_CHAT_BODY = {
    "query": QUERY,
    "enable_planning": False,
    "enable_reflection": False,
    "enable_memory": False,
    "enable_thinking": True,
}


@pytest.fixture
async def client(monkeypatch):
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: _mock_user()
    monkeypatch.setattr(agent_service, "_client", _fake_client(), raising=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://hitl") as c:
        yield c
    app.dependency_overrides.pop(get_current_user, None)


async def _chat_and_pause(c, execute_spy) -> tuple[str, list[dict]]:
    r = await c.post("/api/v1/agent/chat", json=_CHAT_BODY)
    assert r.status_code == 200
    payloads = _sse_payloads(r.text)
    apr = [p for p in payloads if p.get("type") == "approval_required"]
    assert apr, f"必须暂停在审批点：{payloads[-3:]}"
    p = apr[0]
    assert p["tool_name"] == _TASK_KW
    assert p["tool_args"] == {"target": "prod"}
    assert p["risk_level"] == "high"
    assert "审批" in p["content"]
    done = payloads[-1]
    assert done["type"] == "done" and done.get("task_state") == "waiting_approval"
    assert execute_spy.await_count == 0, "暂停在任何执行之前"
    return p["task_id"], payloads


@pytest.mark.asyncio
async def test_full_ring_pause_then_approve_recovers_and_completes(
    db_factory, fake_redis, fake_danger_tool, client
):
    spy = AsyncMock(return_value=ToolResult.ok(data="假工具结果"))
    with patch.object(tool_registry, "execute_detailed", new=spy):
        task_id, payloads = await _chat_and_pause(client, spy)

        # 状态视图：诚实状态 + 审批现场（刷新/重连回到现场的入口）
        g = await client.get(f"/api/v1/agent/tasks/{task_id}")
        data = g.json()["data"]
        assert data["state"] == "waiting_approval"
        assert data["is_recoverable"] is True
        assert data["approval"]["tool_name"] == _TASK_KW
        assert data["approval"]["args_hash"]
        assert data["approval_expired"] is False

        # 人类批准 → SSE 续跑
        r2 = await client.post(
            f"/api/v1/agent/tasks/{task_id}/approval", json={"decision": "approve"}
        )
        assert r2.status_code == 200
        assert r2.headers["content-type"].startswith("text/event-stream")
        p2 = _sse_payloads(r2.text)
        assert any(p["type"] == "tool_result" for p in p2), "批准后工具必须执行"
        assert not any(p["type"] == "approval_required" for p in p2)
        done2 = p2[-1]
        assert done2["type"] == "done" and done2.get("decision") == "approve"
        assert "task_state" not in done2, "正常完成不盖暂停/取消章"

        assert spy.await_count == 1
        assert spy.await_args.args[0] == _TASK_KW
        assert spy.await_args.args[1] == {"target": "prod"}

    g2 = await client.get(f"/api/v1/agent/tasks/{task_id}")
    assert g2.json()["data"]["state"] == "completed"


@pytest.mark.asyncio
async def test_full_ring_deny_feeds_structured_failure_without_execution(
    db_factory, fake_redis, fake_danger_tool, client
):
    spy = AsyncMock(return_value=ToolResult.ok(data="不该被执行"))
    with patch.object(tool_registry, "execute_detailed", new=spy):
        task_id, _ = await _chat_and_pause(client, spy)

        r2 = await client.post(
            f"/api/v1/agent/tasks/{task_id}/approval",
            json={"decision": "deny", "note": "生产环境禁止操作"},
        )
        assert r2.status_code == 200
        p2 = _sse_payloads(r2.text)
        denials = [p for p in p2 if p.get("type") == "tool_error"
                   and p.get("error_type") == "approval_denied"]
        assert denials, "拒绝必须以结构化失败 approval_denied 喂给反思链路"
        assert "生产环境禁止操作" in denials[0]["content"]
        assert spy.await_count == 0, "被拒绝的调用绝不执行"
        assert p2[-1]["decision"] == "deny"

    g = await client.get(f"/api/v1/agent/tasks/{task_id}")
    assert g.json()["data"]["state"] == "completed", "拒绝后 run 继续/收敛（不装死）"


@pytest.mark.asyncio
async def test_cancel_while_waiting_approval_closes_loop(
    db_factory, fake_redis, fake_danger_tool, client
):
    spy = AsyncMock(return_value=ToolResult.ok(data="x"))
    with patch.object(tool_registry, "execute_detailed", new=spy):
        task_id, _ = await _chat_and_pause(client, spy)

        r = await client.post(f"/api/v1/agent/tasks/{task_id}/cancel")
        assert r.status_code == 200
        assert r.json()["data"]["state"] == "cancelled"

        g = await client.get(f"/api/v1/agent/tasks/{task_id}")
        data = g.json()["data"]
        assert data["state"] == "cancelled"
        assert data["is_recoverable"] is False

        # 已取消的任务绝不复活：approval / resume 全部 409
        r2 = await client.post(
            f"/api/v1/agent/tasks/{task_id}/approval", json={"decision": "approve"}
        )
        assert r2.status_code == 409
        assert r2.json()["error_code"] == "TASK_NOT_RESUMABLE"
        r3 = await client.post(f"/api/v1/agent/tasks/{task_id}/resume")
        assert r3.status_code == 409

        # 幂等：再次 cancel 仍 200（already=True）
        r4 = await client.post(f"/api/v1/agent/tasks/{task_id}/cancel")
        assert r4.status_code == 200
        assert r4.json()["data"].get("already") is True


@pytest.mark.asyncio
async def test_cancel_active_task_returns_202_and_sets_flag(db_factory, fake_redis):
    task_id = await checkpoint.create_task(user_id=USER_ID, organization_id=1, query="q")
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: _mock_user()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://hitl"
        ) as c:
            r = await c.post(f"/api/v1/agent/tasks/{task_id}/cancel")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert r.status_code == 202
    assert r.json()["data"]["state"] == "cancel_requested"
    assert await checkpoint.is_cancel_requested(task_id) is True, (
        "活跃取消 = 置旗（协作式），run 在下一个检查点收敛"
    )
    # 收敛由 run 侧完成（本用例只断言受理语义；收敛判别见 service_flow 测试）


@pytest.mark.asyncio
async def test_approval_409_when_not_waiting_approval(db_factory, fake_redis):
    task_id = await checkpoint.create_task(user_id=USER_ID, organization_id=1, query="q")
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: _mock_user()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://hitl"
        ) as c:
            r = await c.post(
                f"/api/v1/agent/tasks/{task_id}/approval", json={"decision": "approve"}
            )
            assert r.status_code == 409
            assert r.json()["error_code"] == "TASK_NOT_WAITING_APPROVAL"

            # 已终态任务 cancel → 409 TASK_NOT_CANCELLABLE
            assert await checkpoint.mark_terminal(task_id, TaskStatus.COMPLETED) is True
            r2 = await c.post(f"/api/v1/agent/tasks/{task_id}/cancel")
            assert r2.status_code == 409
            assert r2.json()["error_code"] == "TASK_NOT_CANCELLABLE"

            # 未知任务 → 404
            r3 = await c.post(
                "/api/v1/agent/tasks/zzz-unknown/approval", json={"decision": "approve"}
            )
            assert r3.status_code == 404
            r4 = await c.post("/api/v1/agent/tasks/zzz-unknown/cancel")
            assert r4.status_code == 404
    finally:
        app.dependency_overrides.pop(get_current_user, None)
