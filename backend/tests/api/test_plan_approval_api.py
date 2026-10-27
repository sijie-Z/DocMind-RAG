"""B（计划审批）—— ASGI 环级：暂停 → 三决策全路径 → 续跑/终态（真路由 +
sqlite + 受控 planner + 假 LLM/假工具）。

判别点：
- always 模式 /chat：plan_approval_required（含结构化步骤，自足）+ done
  task_state=waiting_plan_approval + **执行器零执行**；GET 状态视图带计划现场；
- approve → SSE 续跑 → 工具执行 → completed；
- edit → 校验后替换步骤再执行（执行的是编辑后描述）；非法编辑 422 且现场不破坏；
- reject#1（附理由）→ 带理由重规划（planner 收到反馈）→ 新计划再次暂停；
- reject#2 → 200 JSON state=rejected + last_error 记理由；终态不可 resume（409）；
- waiting_plan_approval 期间：直连 resume 409 TASK_WAITING_PLAN_APPROVAL、
  cancel → 200 cancelled、steer → 409。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.api.v1.endpoints.agent as agent_api
import app.models  # noqa: F401 — 注册全部表，供 create_all
from app.agent import checkpoint, steering
from app.agent.events import AgentEvent
from app.agent.exec_context import ExecutionContext
from app.agent.loop import PERAgentLoop
from app.agent.planner import Planner
from app.agent.registry import ToolResult, tool_registry
from app.agent.service import agent_service
from app.core.database import Base
from app.core.security import get_current_user
from app.rag.retriever import HybridRetriever

USER_ID = 7
_TASK_KW = "search_knowledge_base"  # 低危只读工具：不触发工具审批门，聚焦计划审批


class _FakeRedis:
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
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'plan_api.db'}")
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


def _scripted_planner(monkeypatch, script: list[list[str]]) -> list[dict]:
    """类级 patch Planner.plan：调用序号 → 脚本步骤组；记录 replan_feedback。"""
    calls: list[dict] = []

    async def _plan(self, query, history=None, memory_context="", ctx=None, replan_feedback=""):
        calls.append({"query": query, "replan_feedback": replan_feedback})
        steps = script[min(len(calls) - 1, len(script) - 1)]
        yield AgentEvent(type="plan_start", plan_id="plan-1", content="制定计划...")
        prev = None
        for i, desc in enumerate(steps, start=1):
            yield AgentEvent(
                type="plan_step",
                plan_id="plan-1",
                plan_step_id=f"s{i}",
                content=desc,
                dependencies=[prev] if prev else [],
            )
            prev = f"s{i}"
        yield AgentEvent(
            type="plan_complete",
            plan_id="plan-1",
            plan_goal="目标",
            content="计划完成",
        )

    monkeypatch.setattr(Planner, "plan", _plan)
    return calls


def _fake_client(captured: list | None = None, tool_name: str = _TASK_KW):
    async def _create(*, messages, tools=None, **kwargs):
        if captured is not None:
            captured.append(messages)
        if tools:
            tc = SimpleNamespace(
                id="tc-1", function=SimpleNamespace(name=tool_name, arguments='{"query": "q"}')
            )
            msg = SimpleNamespace(content="", tool_calls=[tc], role="assistant")
        else:
            msg = SimpleNamespace(
                content="根据检索结果：这是答案。", tool_calls=None, role="assistant"
            )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=msg, finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
        )

    client = MagicMock()
    client.chat.completions.create = _create
    return client


_PLAN_BODY = {
    "query": "帮我查资料",
    "enable_planning": True,
    "enable_reflection": False,
    "enable_memory": False,
    "enable_thinking": True,
    "plan_approval_mode": "always",
}


@pytest.fixture
async def client(monkeypatch):
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: _mock_user()
    monkeypatch.setattr(agent_service, "_client", _fake_client(), raising=False)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://plan") as c:
        yield c
    app.dependency_overrides.pop(get_current_user, None)


async def _chat_and_plan_pause(c, spy) -> tuple[str, list[dict]]:
    r = await c.post("/api/v1/agent/chat", json=_PLAN_BODY)
    assert r.status_code == 200
    payloads = _sse_payloads(r.text)
    par = [p for p in payloads if p.get("type") == "plan_approval_required"]
    assert par, f"必须暂停在计划审批点：{payloads[-3:]}"
    assert par[0]["plan_steps"], "事件必须自足携带结构化步骤"
    assert payloads[-1].get("task_state") == "waiting_plan_approval"
    assert spy.await_count == 0, "暂停在任何执行之前"
    return par[0]["task_id"], payloads


# ── 用例 ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pause_then_approve_executes(db_factory, fake_redis, monkeypatch, client):
    _scripted_planner(monkeypatch, [["第一步：检索", "第二步：综合"]])
    spy = AsyncMock(return_value=ToolResult.ok(data="检索结果"))
    with patch.object(tool_registry, "execute_detailed", new=spy):
        tid, _ = await _chat_and_plan_pause(client, spy)

        # 状态视图：诚实状态 + 计划现场（刷新回到卡片的入口）
        g = await client.get(f"/api/v1/agent/tasks/{tid}")
        data = g.json()["data"]
        assert data["state"] == "waiting_plan_approval"
        assert data["is_recoverable"] is True
        assert [s["description"] for s in data["plan_approval"]["steps"]] == [
            "第一步：检索",
            "第二步：综合",
        ]

        # 批准 → SSE 续跑 → 执行
        r2 = await client.post(
            f"/api/v1/agent/tasks/{tid}/plan-approval", json={"decision": "approve"}
        )
        assert r2.status_code == 200
        assert r2.headers["content-type"].startswith("text/event-stream")
        p2 = _sse_payloads(r2.text)
        assert not any(p.get("type") == "plan_approval_required" for p in p2), "获批不再打扰"
        assert any(p.get("type") == "tool_result" for p in p2)
        done2 = p2[-1]
        assert done2["type"] == "done" and done2.get("decision") == "approve"
        assert done2.get("task_state") == "completed"
        assert spy.await_count == 2, "两步各执行一次工具"

    g2 = await client.get(f"/api/v1/agent/tasks/{tid}")
    assert g2.json()["data"]["state"] == "completed"


@pytest.mark.asyncio
async def test_edit_replaces_steps_then_executes_edited(db_factory, fake_redis, monkeypatch):
    from app.main import app

    captured: list = []
    app.dependency_overrides[get_current_user] = lambda: _mock_user()
    monkeypatch.setattr(agent_service, "_client", _fake_client(captured), raising=False)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://plan") as c:
            _scripted_planner(monkeypatch, [["原始第一步"]])
            spy = AsyncMock(return_value=ToolResult.ok(data="x"))
            with patch.object(tool_registry, "execute_detailed", new=spy):
                tid, _ = await _chat_and_plan_pause(c, spy)

                r2 = await c.post(
                    f"/api/v1/agent/tasks/{tid}/plan-approval",
                    json={
                        "decision": "edit",
                        "edited_steps": [
                            {
                                "id": "s9",
                                "description": "人类编辑后的唯一步骤",
                                "dependencies": [],
                                "tool_hint": None,
                            },
                        ],
                    },
                )
                assert r2.status_code == 200
                p2 = _sse_payloads(r2.text)
                assert p2[-1]["type"] == "done" and p2[-1].get("decision") == "edit"
                prompts = [m[-1]["content"] for m in captured if m]
                assert any("人类编辑后的唯一步骤" in p for p in prompts), (
                    "执行的是编辑后的步骤（不是原始步骤）"
                )
                assert not any("原始第一步" in p for p in prompts)
                assert spy.await_count == 1
            g = await c.get(f"/api/v1/agent/tasks/{tid}")
            assert g.json()["data"]["state"] == "completed"
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.asyncio
async def test_edit_invalid_inputs_are_422_and_site_intact(
    db_factory, fake_redis, monkeypatch, client
):
    _scripted_planner(monkeypatch, [["第一步", "第二步"]])
    spy = AsyncMock(return_value=ToolResult.ok(data="x"))
    with patch.object(tool_registry, "execute_detailed", new=spy):
        tid, _ = await _chat_and_plan_pause(client, spy)

        bad_payloads = [
            {"decision": "edit"},  # 缺 edited_steps
            {"decision": "edit", "edited_steps": []},  # 空列表
            {
                "decision": "edit",
                "edited_steps": [{"id": "s1", "description": "x", "dependencies": ["s9"]}],
            },  # 依赖不存在
            {
                "decision": "edit",
                "edited_steps": [
                    {"id": "s1", "description": "x"},
                    {"id": "s1", "description": "y"},
                ],
            },  # id 重复
            {
                "decision": "edit",
                "edited_steps": [{"id": "s1", "description": "x", "dependencies": ["s1"]}],
            },  # 自依赖
            {
                "decision": "approve",
                "edited_steps": [{"id": "s1", "description": "x"}],
            },  # 非 edit 携带 edited_steps
        ]
        for bad in bad_payloads:
            r = await client.post(f"/api/v1/agent/tasks/{tid}/plan-approval", json=bad)
            assert r.status_code == 422, f"{bad} 应为 422"

        # 现场不破坏：仍在等待审批，可正常批准
        g = await client.get(f"/api/v1/agent/tasks/{tid}")
        assert g.json()["data"]["state"] == "waiting_plan_approval"
        r2 = await client.post(
            f"/api/v1/agent/tasks/{tid}/plan-approval", json={"decision": "approve"}
        )
        assert r2.status_code == 200
        assert _sse_payloads(r2.text)[-1]["type"] == "done"


@pytest.mark.asyncio
async def test_reject_once_replans_then_second_reject_terminal(
    db_factory, fake_redis, monkeypatch, client
):
    planner_calls = _scripted_planner(monkeypatch, [["原始计划第一步"], ["重规划后的第一步"]])
    spy = AsyncMock(return_value=ToolResult.ok(data="x"))
    with patch.object(tool_registry, "execute_detailed", new=spy):
        tid, _ = await _chat_and_plan_pause(client, spy)

        # reject#1（附理由）→ 带理由重规划 → 新计划再次暂停
        r2 = await client.post(
            f"/api/v1/agent/tasks/{tid}/plan-approval",
            json={"decision": "reject", "note": "请合并为一次检索"},
        )
        assert r2.status_code == 200
        p2 = _sse_payloads(r2.text)
        par2 = [p for p in p2 if p.get("type") == "plan_approval_required"]
        assert par2, "重规划后的新计划必须再次请求审批"
        assert par2[0]["plan_steps"][0]["description"] == "重规划后的第一步"
        assert p2[-1].get("task_state") == "waiting_plan_approval"
        assert len(planner_calls) == 2
        assert planner_calls[1]["replan_feedback"] == "请合并为一次检索", (
            "拒绝理由必须成为重规划反馈"
        )
        assert spy.await_count == 0, "被拒计划零执行"

        # reject#2 → 终态 rejected（JSON 回执，无流可续）
        r3 = await client.post(
            f"/api/v1/agent/tasks/{tid}/plan-approval",
            json={"decision": "reject", "note": "还是不行"},
        )
        assert r3.status_code == 200
        assert r3.headers["content-type"].startswith("application/json")
        body = r3.json()["data"]
        assert body["state"] == "rejected" and body["rejections"] == 2

        g = await client.get(f"/api/v1/agent/tasks/{tid}")
        data = g.json()["data"]
        assert data["state"] == "rejected"
        assert data["is_recoverable"] is False
        assert data["last_error_type"] == "plan_rejected"
        assert "还是不行" in (data["last_error"] or "")

        # 终态不可复活：resume / 再应答 全部 409
        r4 = await client.post(f"/api/v1/agent/tasks/{tid}/resume")
        assert r4.status_code == 409
        assert r4.json()["error_code"] == "TASK_NOT_RESUMABLE"
        r5 = await client.post(
            f"/api/v1/agent/tasks/{tid}/plan-approval", json={"decision": "approve"}
        )
        assert r5.status_code == 409


@pytest.mark.asyncio
async def test_waiting_plan_approval_guard_rails(db_factory, fake_redis, monkeypatch, client):
    _scripted_planner(monkeypatch, [["第一步"]])
    spy = AsyncMock(return_value=ToolResult.ok(data="x"))
    with patch.object(tool_registry, "execute_detailed", new=spy):
        tid, _ = await _chat_and_plan_pause(client, spy)

        # 直连 resume：审批门不许绕过（409 TASK_WAITING_PLAN_APPROVAL）
        r = await client.post(f"/api/v1/agent/tasks/{tid}/resume")
        assert r.status_code == 409
        assert r.json()["error_code"] == "TASK_WAITING_PLAN_APPROVAL"

        # 转向端点：等待计划审批不在可转向状态（409 诚实文案）
        r_steer = await client.post(f"/api/v1/agent/tasks/{tid}/steer", json={"message": "m"})
        assert r_steer.status_code == 409
        assert r_steer.json()["error_code"] == "TASK_NOT_STEERABLE"
        assert "waiting_plan_approval" in r_steer.json()["message"]

        # 取消：等待计划审批中同样可直接终态收敛
        r_cancel = await client.post(f"/api/v1/agent/tasks/{tid}/cancel")
        assert r_cancel.status_code == 200
        assert r_cancel.json()["data"]["state"] == "cancelled"
        g = await client.get(f"/api/v1/agent/tasks/{tid}")
        assert g.json()["data"]["state"] == "cancelled"

        # 已取消后计划审批应答 → 409（不复活）
        r6 = await client.post(
            f"/api/v1/agent/tasks/{tid}/plan-approval", json={"decision": "approve"}
        )
        assert r6.status_code == 409
