"""运行中转向（steering）—— ASGI 环级：端点语义 + 审批暂停期间的并入全环。

判别点（app/agent/steering.py 模块契约 + 端点）：
- running 任务 → 200 入队（pending 计数递增）；
- waiting_approval 任务 → 200，且响应文案如实说明"并入审批恢复后的执行"；
- 其它状态（completed/failed 等）→ 409 TASK_NOT_STEERABLE（诚实文案）；
- 缓冲满 → 429 STEER_BUFFER_FULL（等消费后再发）；
- 协调存储不可达 → 503（绝不假装已受理）；
- 404：未知任务 / 非本人任务；
- 全环：审批暂停 → steer 入队 → approve 恢复 → 流内 steer_applied +
  恢复步骤提示词携带指导 → 终态 completed 后缓冲清空。
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.api.v1.endpoints.agent as agent_api
import app.models  # noqa: F401 — 注册全部表，供 create_all
from app.agent import checkpoint, steering
from app.agent.exec_context import ExecutionContext
from app.agent.loop import PERAgentLoop
from app.agent.registry import ToolResult, tool_registry
from app.agent.service import agent_service
from app.core.database import Base
from app.core.security import get_current_user
from app.models.agent_task import TaskStatus
from app.rag.retriever import HybridRetriever

USER_ID = 7
_TASK_KW = "fake_danger"
STEER = "请只查 2024 年之后的文档"


class _FakeRedis:
    """字符串面 + 列表面（与 test_steering_queue / test_steer_loop_wiring 同款）。"""

    def __bool__(self):
        return True

    def __init__(self):
        self.store: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}

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
        self.lists.pop(key, None)
        return 1

    async def expire(self, key, ttl):
        return key in self.store or key in self.lists

    async def llen(self, key):
        return len(self.lists.get(key, []))

    async def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])

    async def lpop(self, key, count=None):
        items = self.lists.get(key)
        if not items:
            return None
        if count is None:
            return items.pop(0)
        n = min(int(count), len(items))
        out = items[:n]
        del items[:n]
        return out or None

    async def lrem(self, key, count, value):
        items = self.lists.get(key, [])
        for i in range(len(items) - 1, -1, -1):
            if items[i] == value:
                del items[i]
                return 1
        return 0


class _RedisDown:
    def __bool__(self):
        return False


def _mock_user(user_id: int = USER_ID, org_id: int | None = 1):
    class _User:
        pass

    user = _User()
    user.id = user_id
    user.organization_id = org_id
    user.is_active = True
    return user


def _sse_payloads(text: str) -> list[dict]:
    import json as _json

    lines = [ln[len("data: ") :] for ln in text.splitlines() if ln.startswith("data: ")]
    return [_json.loads(ln) for ln in lines]


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    async def _save(self, ttl=86400 * 7):
        return False

    monkeypatch.setattr(ExecutionContext, "save", _save)
    monkeypatch.setattr(PERAgentLoop, "_write_experiences", AsyncMock())
    monkeypatch.setattr(HybridRetriever, "get_embedding", AsyncMock(return_value=[]))


@pytest.fixture
async def db_factory(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'steer_api.db'}")
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
    monkeypatch.setattr(steering, "redis_client", r)
    return r


@pytest.fixture
def fake_danger_tool():
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


def _fake_client(captured: list | None = None):
    async def _create(*, messages, tools=None, **kwargs):
        if captured is not None:
            captured.append(messages)
        if tools:
            tc = SimpleNamespace(
                id="tc-1", function=SimpleNamespace(name=_TASK_KW, arguments='{"target": "prod"}')
            )
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
    "query": "帮我执行一段脚本",
    "enable_planning": False,
    "enable_reflection": False,
    "enable_memory": False,
    "enable_thinking": True,
}


async def _client_fixture(monkeypatch, captured=None):
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: _mock_user()
    monkeypatch.setattr(agent_service, "_client", _fake_client(captured), raising=False)
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://steer")


@pytest.fixture
async def client(monkeypatch):
    async with await _client_fixture(monkeypatch) as c:
        yield c
    from app.main import app

    app.dependency_overrides.pop(get_current_user, None)


# ── 端点语义 ─────────────────────────────────────────────────────────────────


async def test_steer_running_task_queued_in_order(db_factory, fake_redis, client):
    tid = await checkpoint.create_task(user_id=USER_ID, organization_id=1, query="q")
    r1 = await client.post(f"/api/v1/agent/tasks/{tid}/steer", json={"message": "第一条"})
    assert r1.status_code == 200
    body = r1.json()["data"]
    assert body["state"] == "queued" and body["pending"] == 1
    assert "步边界" in body["message"]

    r2 = await client.post(f"/api/v1/agent/tasks/{tid}/steer", json={"message": "第二条"})
    assert r2.status_code == 200 and r2.json()["data"]["pending"] == 2
    # 队列保序（fake 后端直查）
    assert fake_redis.lists[f"agent:task:steer:{tid}"] == ["第一条", "第二条"]


async def test_steer_waiting_approval_queued_with_honest_message(db_factory, fake_redis, client):
    tid = await checkpoint.create_task(user_id=USER_ID, organization_id=1, query="q")
    assert await checkpoint.mark_waiting_approval(tid) is True

    r = await client.post(f"/api/v1/agent/tasks/{tid}/steer", json={"message": STEER})
    assert r.status_code == 200
    body = r.json()["data"]
    assert body["state"] == "queued" and body["pending"] == 1
    assert "审批" in body["message"], "等待审批期间的语义必须如实说明并入恢复执行"


async def test_steer_409_on_non_steerable_states(db_factory, fake_redis, client):
    tid = await checkpoint.create_task(user_id=USER_ID, organization_id=1, query="q")
    assert await checkpoint.mark_terminal(tid, TaskStatus.COMPLETED) is True
    r = await client.post(f"/api/v1/agent/tasks/{tid}/steer", json={"message": "m"})
    assert r.status_code == 409
    assert r.json()["error_code"] == "TASK_NOT_STEERABLE"

    tid2 = await checkpoint.create_task(user_id=USER_ID, organization_id=1, query="q")
    assert await checkpoint.mark_terminal(tid2, TaskStatus.FAILED) is True  # 非终态但不可转向
    r2 = await client.post(f"/api/v1/agent/tasks/{tid2}/steer", json={"message": "m"})
    assert r2.status_code == 409
    assert r2.json()["error_code"] == "TASK_NOT_STEERABLE"


async def test_steer_429_when_buffer_full(db_factory, fake_redis, client):
    tid = await checkpoint.create_task(user_id=USER_ID, organization_id=1, query="q")
    for i in range(steering.MAX_STEER_QUEUE):
        assert await steering.push_steer(tid, f"m{i}") > 0
    r = await client.post(f"/api/v1/agent/tasks/{tid}/steer", json={"message": "溢出"})
    assert r.status_code == 429
    assert r.json()["error_code"] == "STEER_BUFFER_FULL"
    assert "满" in r.json()["message"]


async def test_steer_503_when_channel_down(db_factory, fake_redis, client, monkeypatch):
    tid = await checkpoint.create_task(user_id=USER_ID, organization_id=1, query="q")
    monkeypatch.setattr(steering, "redis_client", _RedisDown())
    r = await client.post(f"/api/v1/agent/tasks/{tid}/steer", json={"message": "m"})
    assert r.status_code == 503, "通道不可用绝不假装受理"


async def test_steer_404_unknown_or_foreign_task(db_factory, fake_redis, client):
    r = await client.post("/api/v1/agent/tasks/zzz-unknown/steer", json={"message": "m"})
    assert r.status_code == 404

    foreign = await checkpoint.create_task(user_id=999, organization_id=1, query="q")
    r2 = await client.post(f"/api/v1/agent/tasks/{foreign}/steer", json={"message": "m"})
    assert r2.status_code == 404, "非本人任务不可见（不泄露归属）"


async def test_steer_422_on_empty_message(db_factory, fake_redis, client):
    tid = await checkpoint.create_task(user_id=USER_ID, organization_id=1, query="q")
    r = await client.post(f"/api/v1/agent/tasks/{tid}/steer", json={"message": ""})
    assert r.status_code == 422


async def test_steer_422_on_blank_message_and_queue_untouched(db_factory, fake_redis, client):
    """评审 MINOR-A：纯空白消息 strip 后为空——422 明确拒绝（文案含指引），
    且绝不入队（曾以空串入队 → 步边界幻影 steer_applied；变异点：移除
    TaskSteerRequest 的空白校验器 → 本用例红）。"""
    tid = await checkpoint.create_task(user_id=USER_ID, organization_id=1, query="q")
    r = await client.post(f"/api/v1/agent/tasks/{tid}/steer", json={"message": "   \n\t "})
    assert r.status_code == 422
    detail = json.dumps(r.json(), ensure_ascii=False)
    assert "空白" in detail, "422 必须附明确文案"
    assert f"agent:task:steer:{tid}" not in fake_redis.lists, "空白消息绝不入队"


# ── 全环：暂停 → steer → approve 恢复 → 指导并入执行 ─────────────────────────


async def test_full_ring_steer_during_waiting_approval_merges_into_resume(
    db_factory, fake_redis, fake_danger_tool, monkeypatch
):
    from app.main import app

    captured: list = []
    app.dependency_overrides[get_current_user] = lambda: _mock_user()
    monkeypatch.setattr(agent_service, "_client", _fake_client(captured), raising=False)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://steer") as c:
            spy = AsyncMock(return_value=ToolResult.ok(data="假工具结果"))
            with patch.object(tool_registry, "execute_detailed", new=spy):
                r = await c.post("/api/v1/agent/chat", json=_CHAT_BODY)
                assert r.status_code == 200
                payloads = _sse_payloads(r.text)
                apr = [p for p in payloads if p.get("type") == "approval_required"]
                assert apr, "必须暂停在审批点"
                tid = apr[0]["task_id"]
                assert payloads[-1].get("task_state") == "waiting_approval"

                # 等待审批期间发送指导
                rs = await c.post(f"/api/v1/agent/tasks/{tid}/steer", json={"message": STEER})
                assert rs.status_code == 200
                assert "审批" in rs.json()["data"]["message"]

                n_calls_before = len(captured)
                # 审批恢复：指导并入 resume 的引导
                r2 = await c.post(
                    f"/api/v1/agent/tasks/{tid}/approval", json={"decision": "approve"}
                )
                assert r2.status_code == 200
                p2 = _sse_payloads(r2.text)
                applied = [p for p in p2 if p.get("type") == "steer_applied"]
                assert applied and applied[0]["content"] == STEER
                assert any(p.get("type") == "tool_result" for p in p2)
                assert p2[-1].get("type") == "done" and "task_state" not in p2[-1]

                # 恢复执行里新发的 LLM 调用（审批重跑步骤）携带指导
                new_prompts = [m[0]["content"] for m in captured[n_calls_before:]]
                assert any(STEER in p and "人工指导" in p for p in new_prompts), (
                    "恢复步骤的提示词必须携带并入的指导"
                )
                assert spy.await_count == 1

            # 终态后缓冲清空
            g = await c.get(f"/api/v1/agent/tasks/{tid}")
            assert g.json()["data"]["state"] == "completed"
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert await steering.drain_steers(tid) == [], "终态清缓冲"
