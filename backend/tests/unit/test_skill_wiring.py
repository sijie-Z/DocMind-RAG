"""issue #200 接线验收：技能系统三件套。

- **索引进提示词**：`build_skills_index` 含仓库真实技能；executor 提示词按开关注入；
- **真工具**：`list_skills` / `load_skill_reference`（指南与参考两种形态、错误显式）；
- **学习闭环（读/写侧）**：`_apply_skill_hints` 只补空缺不覆盖；`_learn_skills` 质检全 A 才学。
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.agent.service  # noqa: F401 — 触发全部工具注册
import app.agent.tools  # noqa: F401
from app.agent.config import AgentConfig
from app.agent.exec_context import ExecutionContext
from app.agent.executor import Executor
from app.agent.memory_bridge import AgentMemoryBridge
from app.agent.planner import Plan, PlanStep
from app.agent.registry import tool_registry
from app.agent.skill_prompt import build_skills_index


def test_skills_index_contains_repo_skill():
    idx = build_skills_index()
    assert "ai-podcast" in idx
    assert "load_skill_reference" in idx, "索引必须告诉模型怎么加载细节"


def test_skills_index_empty_when_no_skills(monkeypatch):
    import app.agent.curated_skills as cs

    monkeypatch.setattr(cs.curated_skill_registry, "_skills", {})
    monkeypatch.setattr(cs.curated_skill_registry, "_loaded", True)
    assert build_skills_index() == ""


def test_skill_tools_absent_from_child_toolset():
    names_child = {
        t["function"]["name"] for t in tool_registry.to_openai_tools(["search", "analysis"])
    }
    assert "load_skill_reference" not in names_child, "子代理不应看到技能工具"
    names_all = {t["function"]["name"] for t in tool_registry.to_openai_tools()}
    assert {"list_skills", "load_skill_reference"} <= names_all


@pytest.mark.asyncio
async def test_load_skill_reference_guide_and_reference():
    listed = await tool_registry.execute_detailed(
        "list_skills", {}, organization_id=1, user_id=0
    )
    assert listed.success and "ai-podcast" in listed.data

    guide = await tool_registry.execute_detailed(
        "load_skill_reference", {"skill_name": "ai-podcast"}, organization_id=1, user_id=0
    )
    assert guide.success and "技能: ai-podcast" in str(guide.data)

    ref = await tool_registry.execute_detailed(
        "load_skill_reference",
        {"skill_name": "ai-podcast", "reference": "script-template"},
        organization_id=1,
        user_id=0,
    )
    assert ref.success and len(str(ref.data)) > 50

    miss = await tool_registry.execute_detailed(
        "load_skill_reference", {"skill_name": "不存在的技能"}, organization_id=1, user_id=0
    )
    assert not miss.success and miss.error.code == "not_found"


@pytest.mark.asyncio
async def test_executor_prompt_injects_skills_index_per_switch():
    captured: dict = {}
    client = MagicMock()

    async def _create(*, messages, tools=None, **kwargs):
        captured.setdefault("messages", []).append(messages)
        resp = MagicMock()
        choice = MagicMock()
        msg = MagicMock()
        msg.tool_calls = None
        msg.content = "ok"
        msg.role = "assistant"
        choice.message = msg
        resp.choices = [choice]
        return resp

    client.chat.completions.create = _create
    memory = AgentMemoryBridge(agent_id="t", organization_id=1, user_id=0)

    async def _run_with(config: AgentConfig):
        ex = Executor(client, config, memory)
        step = PlanStep(id="s1", description="d")
        plan = Plan(id="p1", goal="g", steps=[step])
        async for _ in ex._execute_step_once(
            step=step,
            plan=plan,
            history=None,
            organization_id=1,
            user_id=0,
            enable_thinking=False,
        ):
            pass
        return captured["messages"][-1]

    msgs_on = await _run_with(AgentConfig(enable_planning=False, enable_reflection=False))
    sys_on = [m for m in msgs_on if m.get("role") == "system"][0]["content"]
    assert "## 可用技能" in sys_on and "ai-podcast" in sys_on

    msgs_off = await _run_with(
        AgentConfig(enable_planning=False, enable_reflection=False, enable_skills=False)
    )
    sys_off = [m for m in msgs_off if m.get("role") == "system"][0]["content"]
    assert "## 可用技能" not in sys_off, "关闭技能开关时不得注入"


@pytest.mark.asyncio
async def test_apply_skill_hints_only_fills_missing():
    from app.agent.loop import PERAgentLoop

    loop = PERAgentLoop(MagicMock(), AgentConfig(), organization_id=1)
    loop.skill_manager = MagicMock()
    loop.skill_manager.get_tool_hints_for_query.return_value = [
        {"tool_name": "search_knowledge_base", "description": "x"},
        {"tool_name": "web_search", "description": "y"},
    ]
    steps = [
        PlanStep(id="s1", description="a"),
        PlanStep(id="s2", description="b", tool_hint="execute_python"),
    ]
    plan = Plan(id="p1", goal="g", steps=steps)

    loop._apply_skill_hints(plan, "查一下")

    assert steps[0].tool_hint == "search_knowledge_base", "空缺应被技能提示补上"
    assert steps[1].tool_hint == "execute_python", "已有提示不得被覆盖"


@pytest.mark.asyncio
async def test_learn_skills_gated_by_all_a_grades():
    from app.agent.loop import PERAgentLoop

    loop = PERAgentLoop(MagicMock(), AgentConfig(), organization_id=1)
    loop.skill_manager = MagicMock()
    loop.skill_manager.learn_from_plan_success = AsyncMock(
        return_value=SimpleNamespace(name="Auto: x")
    )
    plan = Plan(
        id="p1",
        goal="g",
        steps=[
            PlanStep(id="s1", description="a", tool_hint="t1"),
            PlanStep(id="s2", description="b", tool_hint="t2"),
        ],
    )
    ctx = ExecutionContext(query="q")

    await loop._learn_skills(ctx, plan, {"grades": {"s1": ("A", ""), "s2": ("A", "")}})
    assert loop.skill_manager.learn_from_plan_success.await_count == 1

    loop.skill_manager.learn_from_plan_success.reset_mock()
    await loop._learn_skills(ctx, plan, {"grades": {"s1": ("A", ""), "s2": ("C", "")}})
    assert loop.skill_manager.learn_from_plan_success.await_count == 0, "非全 A 不学习"
