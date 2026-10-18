"""issue #242「配置两面通」的行为契约。

- **PUT /agent/config 收下并持久化** `max_tokens` / `max_plan_steps` / `max_retries_per_step`
  （此前这三个字段被 pydantic 静默丢弃：前端"保存成功"，服务端永远用默认值）；
- **聊天以用户已保存配置为基底**（此前基底被整体忽略，设置页旋钮改了也永不生效），
  请求字段按既定语义覆盖；
- 顺带闭合 #239 minor-3：编排器的评审调用透传 ctx。

每条断言附变异点（把对应接线删回原状，用例必须变红）。
"""
from unittest.mock import AsyncMock, MagicMock  # noqa: F401

import pytest
from pydantic import ValidationError

from app.agent.config import AgentConfig
from app.api.v1.endpoints import agent as agent_ep


def _fake_user(uid: int = 7):
    return type("U", (), {"id": uid})()


@pytest.fixture
def redis_store(monkeypatch):
    """以内存字典替换 AgentConfig 的持久化（不依赖真实 Redis）。"""
    store: dict = {}

    async def _load(cls, session_id):
        return store.get(session_id)

    async def _save(self, session_id, ttl=86400 * 7):
        store[session_id] = self

    monkeypatch.setattr(AgentConfig, "load_from_redis", classmethod(_load))
    monkeypatch.setattr(AgentConfig, "save_to_redis", _save)
    return store


# ── PUT /agent/config ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_config_update_persists_agent_knobs(redis_store):
    """变异点：删去 PUT 处理里的 max_plan_steps 等赋值 → 本用例红。"""
    body = agent_ep.AgentConfigUpdate(
        max_tokens=2048, max_plan_steps=7, max_retries_per_step=1,
    )
    resp = await agent_ep.update_config(body, current_user=_fake_user())

    data = resp["data"]
    assert data["max_tokens"] == 2048
    assert data["max_plan_steps"] == 7
    assert data["max_retries_per_step"] == 1
    saved = redis_store["user:7"]
    assert (saved.max_tokens, saved.max_plan_steps, saved.max_retries_per_step) == (2048, 7, 1), (
        "必须真正持久化，而不是只回显"
    )


def test_config_update_rejects_out_of_range():
    """变异点：去掉 Field 的 ge/le 约束 → 本用例红。"""
    with pytest.raises(ValidationError):
        agent_ep.AgentConfigUpdate(max_plan_steps=0)
    with pytest.raises(ValidationError):
        agent_ep.AgentConfigUpdate(max_retries_per_step=99)
    with pytest.raises(ValidationError):
        agent_ep.AgentConfigUpdate(max_tokens=64)


# ── 聊天组配置：持久化为基底 + 请求覆盖 ─────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_config_seeds_from_stored_and_request_overrides(redis_store):
    """变异点：helper 忽略 stored（`config = AgentConfig()`）→ 本用例红。

    注意：本用例钉住的是 helper 自身的语义；**端点的接线**由
    `test_chat_endpoint_wires_stored_config_into_run` 单独钉住（评审指出：
    仅测 helper 无法防"端点忘记调用 helper"式的回归）。
    """
    redis_store["user:7"] = AgentConfig(
        max_plan_steps=7,
        max_retries_per_step=1,
        enable_reflection=False,
        system_prompt_override="SAVED-PROMPT",
    )
    body = agent_ep.AgentChatRequest(query="q", enable_reflection=True)

    cfg = await agent_ep._build_agent_config(body, 7)

    assert cfg.enable_reflection is True, "请求恒有值的字段 → 以请求为准"
    assert cfg.max_plan_steps == 7, "请求不携带的旋钮 → 继承用户已保存的值（#242 的核心）"
    assert cfg.max_retries_per_step == 1
    assert cfg.system_prompt_override == "SAVED-PROMPT", "请求未显式给值 → 保留已保存设置"


@pytest.mark.asyncio
async def test_chat_config_without_stored_keeps_old_behavior(redis_store):
    """无已保存配置时行为与旧版一致（默认值 + 安全加固清单）。"""
    body = agent_ep.AgentChatRequest(query="q")
    cfg = await agent_ep._build_agent_config(body, 7)
    assert cfg.max_plan_steps == AgentConfig().max_plan_steps
    assert cfg.max_retries_per_step == AgentConfig().max_retries_per_step
    assert "execute_python" in cfg.disabled_tools, "服务端强制禁用清单必须仍在"


@pytest.mark.asyncio
async def test_chat_config_merges_server_side_disabled_tools(redis_store):
    """请求非空 disabled_tools 时照旧走服务端合并（安全加固语义不变）。"""
    body = agent_ep.AgentChatRequest(query="q", disabled_tools=["web_search"])
    cfg = await agent_ep._build_agent_config(body, 7)
    assert "web_search" in cfg.disabled_tools and "execute_python" in cfg.disabled_tools


# ── 评审 B-1：继承存储值时也必须重新合并服务端禁用清单 ─────────────────────


@pytest.mark.asyncio
async def test_empty_request_never_unlocks_server_forced_tools(redis_store):
    """评审 B-1：请求不带 disabled_tools 时，继承的存储值也必须重新合并服务端清单。

    存储可能来自旧版本或带外写入（如 `[]`，不含后来新增的默认禁用项）。
    变异点：把 helper 改回"仅请求非空才 merge" → 本用例红。
    """
    redis_store["user:7"] = AgentConfig(disabled_tools=[])  # 带外写入/旧版本存储形态
    cfg = await agent_ep._build_agent_config(agent_ep.AgentChatRequest(query="q"), 7)
    for tool in ("execute_python", "execute_sql", "mcp_call"):
        assert tool in cfg.disabled_tools, f"{tool} 不得因存储值形态而被放开"


@pytest.mark.asyncio
async def test_stored_disabled_list_is_extended_not_replaced(redis_store):
    """继承的存储清单同样要与服务端默认清单取并集（不是二选一）。"""
    redis_store["user:7"] = AgentConfig(disabled_tools=["web_search"])
    cfg = await agent_ep._build_agent_config(agent_ep.AgentChatRequest(query="q"), 7)
    assert "web_search" in cfg.disabled_tools, "用户存储的禁用项必须保留"
    assert "execute_python" in cfg.disabled_tools, "服务端默认清单必须合并进来"


# ── 端点接线（评审：仅测 helper 防不住"端点忘记调用 helper"）────────────────


@pytest.mark.asyncio
async def test_chat_endpoint_wires_stored_config_into_run(redis_store, monkeypatch):
    """驱动真实端点：存储的配置必须到达 agent_service.chat。

    变异点：把 event_stream 里 `await _build_agent_config(body, current_user.id)`
    还原为纯请求构造 → 本用例红（捕获到的 max_plan_steps 变回默认 10）。
    """
    from app.agent.events import AgentEvent

    redis_store["user:7"] = AgentConfig(max_plan_steps=7)
    captured: dict = {}

    async def _fake_chat(**kwargs):
        captured.update(kwargs)
        yield AgentEvent(type="done")

    monkeypatch.setattr(agent_ep.agent_service, "chat", _fake_chat)
    monkeypatch.setattr(agent_ep, "require_organization", AsyncMock(return_value=3))

    resp = await agent_ep.agent_chat(
        agent_ep.AgentChatRequest(query="q"), current_user=_fake_user(), db=MagicMock(),
    )
    async for _ in resp.body_iterator:
        break

    assert captured, "agent_service.chat 应被调用"
    assert captured["config"].max_plan_steps == 7, "存储配置必须真正进入这次 run"
    assert captured["organization_id"] == 3


# ── #239 minor-3：编排器评审透传 ctx ───────────────────────────────────────


@pytest.mark.asyncio
async def test_orchestrator_review_receives_ctx(monkeypatch):
    """变异点：删去 _run_review 里的 ctx=ctx → 本用例红。"""
    import app.agent.reviewer as reviewer_mod
    from app.agent import orchestrator as orch_mod
    from app.agent.exec_context import ExecutionContext
    from app.agent.planner import Plan

    captured: dict = {}

    class _ReviewerStub:
        def __init__(self, openai_client=None, config=None):
            pass

        async def review(self, plan, results, ctx=None):
            captured["ctx"] = ctx
            return {"issues_found": []}

    monkeypatch.setattr(reviewer_mod, "Reviewer", _ReviewerStub)

    planner_stub = type("P", (), {"client": object()})()
    orch = orch_mod.Orchestrator(planner=planner_stub, organization_id=1)
    ctx = ExecutionContext(query="q")

    issues = await orch._run_review(Plan(id="p1", goal="g", steps=[]), [], ctx)

    assert issues == []
    assert captured["ctx"] is ctx, "编排器评审必须把 ctx 交给 Reviewer（用量记账依赖它）"
