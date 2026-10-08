"""issue #200 接线验收：技能系统三件套。

- **索引进提示词**：`build_skills_index` 含仓库真实技能；executor 提示词按开关注入
  （受限工具上下文除外——评审 B1）；
- **真工具**：`list_skills` / `load_skill_reference`（指南与参考两种形态、错误显式）；
- **学习闭环（读/写/反馈/清理侧）**：`_apply_skill_hints` 只补空缺不覆盖；
  `_learn_skills` 质检全 A 才学；使用结果反馈（B2）；过期清理（B2）；同 query 去重（m3）。
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.agent.service  # noqa: F401 — 触发全部工具注册
import app.agent.tools  # noqa: F401
from app.agent.config import AgentConfig
from app.agent.events import AgentEvent
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
async def test_load_skill_reference_missing_reference_not_found():
    """#245 第 4 条：技能存在、但 reference 名不存在 → not_found（此前只测了
    "技能不存在"分支，这条 reference 分支无自动化测试）。

    变异点：删掉 curated_skill_tools.load_skill_reference 里 `if content is None:`
    的 not_found 分支（或置 False）→ 本用例红。
    """
    miss = await tool_registry.execute_detailed(
        "load_skill_reference",
        {"skill_name": "ai-podcast", "reference": "no-such-reference"},
        organization_id=1,
        user_id=0,
    )
    assert not miss.success, "reference 不存在必须显式失败，不得静默返回 None"
    assert miss.error.code == "not_found"
    assert "没有名为" in miss.error.message and "ai-podcast" in miss.error.message


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

    # 评审 B1：受限工具上下文（tool_tags 非空，如子代理）看不到技能工具（schema 过滤 +
    # 执行层白名单），提示词同样不得注入索引——不能承诺一个必被拒绝的工具。
    msgs_child = await _run_with(
        AgentConfig(enable_planning=False, enable_reflection=False,
                    tool_tags=["search", "analysis"])
    )
    sys_child = [m for m in msgs_child if m.get("role") == "system"][0]["content"]
    assert "## 可用技能" not in sys_child, "受限上下文不得注入技能索引（B1）"
    assert "load_skill_reference" not in sys_child, "受限上下文不得引导调用技能工具（B1）"


@pytest.mark.asyncio
async def test_apply_skill_hints_only_fills_missing():
    from app.agent.loop import PERAgentLoop

    loop = PERAgentLoop(MagicMock(), AgentConfig(), organization_id=1)
    loop.skill_manager = MagicMock()
    loop.skill_manager.match.return_value = SimpleNamespace(
        id="sk-1",
        tool_sequence=[
            {"tool_name": "search_knowledge_base", "description": "x"},
            {"tool_name": "web_search", "description": "y"},
        ],
    )
    steps = [
        PlanStep(id="s1", description="a"),
        PlanStep(id="s2", description="b", tool_hint="execute_python"),
    ]
    plan = Plan(id="p1", goal="g", steps=steps)

    matched_id = loop._apply_skill_hints(plan, "查一下")

    assert steps[0].tool_hint == "search_knowledge_base", "空缺应被技能提示补上"
    assert steps[1].tool_hint == "execute_python", "已有提示不得被覆盖"
    assert matched_id == "sk-1", "应返回实际被应用的 skill_id（B2 反馈边的输入）"


@pytest.mark.asyncio
async def test_apply_skill_hints_skips_tools_not_visible_in_context():
    """#245 第 5/6 条：跨会话工具配置漂移——技能记的工具名不在当前可见集（tool_tags
    受限）时跳过应用，不得写进步骤诱导模型调用被执行层拒绝的工具。

    变异点：_apply_skill_hints 里 `hint and hint in visible` 还原为 `hint` → 红。
    """
    from app.agent.loop import PERAgentLoop

    loop = PERAgentLoop(
        MagicMock(), AgentConfig(tool_tags=["search", "analysis"]), organization_id=1
    )
    loop.skill_manager = MagicMock()
    loop.skill_manager.match.return_value = SimpleNamespace(
        id="sk-9",
        tool_sequence=[
            {"tool_name": "delegate_subtask", "description": "漂移前学到的（不在白名单）"},
            {"tool_name": "search_knowledge_base", "description": "当前可见"},
        ],
    )
    steps = [PlanStep(id="s1", description="a"), PlanStep(id="s2", description="b")]
    plan = Plan(id="p1", goal="g", steps=steps)

    matched_id = loop._apply_skill_hints(plan, "查一下")

    assert steps[0].tool_hint is None, "不在当前可见集的 hint 必须跳过（不为不可见工具背书）"
    assert steps[1].tool_hint == "search_knowledge_base", "可见 hint 仍按位置配对应用"
    assert matched_id == "sk-9"

    # 全部不可见 → 一条都没应用上 → None：反馈边不得把"没真正用上"算作使用。
    loop.skill_manager.match.return_value = SimpleNamespace(
        id="sk-10",
        tool_sequence=[{"tool_name": "delegate_subtask", "description": "不可见"}],
    )
    plan2 = Plan(id="p2", goal="g", steps=[PlanStep(id="s1", description="a")])
    assert loop._apply_skill_hints(plan2, "查一下") is None
    assert plan2.steps[0].tool_hint is None


@pytest.mark.asyncio
async def test_apply_skill_hints_respects_disabled_tools():
    """#245 第 5/6 条（disabled_tools 侧）：被 disabled_tools 过滤掉的工具同样
    不可见——hint 跳过应用。

    变异点：同 test_apply_skill_hints_skips_tools_not_visible_in_context。
    """
    from app.agent.loop import PERAgentLoop

    loop = PERAgentLoop(
        MagicMock(), AgentConfig(disabled_tools=["web_search"]), organization_id=1
    )
    loop.skill_manager = MagicMock()
    loop.skill_manager.match.return_value = SimpleNamespace(
        id="sk-11",
        tool_sequence=[{"tool_name": "web_search", "description": "已被禁用"}],
    )
    plan = Plan(id="p1", goal="g", steps=[PlanStep(id="s1", description="a")])

    assert loop._apply_skill_hints(plan, "查一下") is None
    assert plan.steps[0].tool_hint is None, "disabled_tools 里的工具不得被 hint 复活"


def test_build_messages_skill_index_gates():
    """#245 第 1/3 条：loop._build_messages（tool-less 入口，经 POST /agent/chat
    enable_tools=false 真实可达——不是死代码）的技能索引门三态：
    默认注入 / 受限上下文不注入（B1）/ toolless 不注入（悬挂引用：#245 第 3 条）。

    变异点：门中删去 `self.config.enable_tools` 条件 → toolless 断言红。
    """
    from app.agent.loop import PERAgentLoop

    def _system_of(config: AgentConfig) -> str:
        loop = PERAgentLoop(MagicMock(), config, organization_id=1)
        msgs = loop._build_messages("q", None, None, "")
        return [m for m in msgs if m.get("role") == "system"][0]["content"]

    sys_default = _system_of(AgentConfig())
    assert "## 可用技能" in sys_default, "默认（有工具）路径应注入技能索引"

    sys_child = _system_of(AgentConfig(tool_tags=["search", "analysis"]))
    assert "## 可用技能" not in sys_child, "受限上下文不得注入（B1 同立场）"

    sys_toolless = _system_of(AgentConfig(enable_tools=False))
    assert "## 可用技能" not in sys_toolless, "toolless 不得注入索引（悬挂引用，#245 第 3 条）"
    assert "load_skill_reference" not in sys_toolless, "调用不带 tools，不得引导调用技能工具"


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


# ── 评审 B1：子代理上下文全隔离 ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delegate_child_config_disables_skills(monkeypatch):
    """B1 变异点：删去 delegate_task 里的 enable_skills=False → 本测试红。"""
    import app.agent.subagent as subagent

    captured: dict = {}

    class _StubLoop:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def run(self, task):
            yield AgentEvent(type="done", content="")

    monkeypatch.setattr(subagent, "PERAgentLoop", _StubLoop)
    async for _ in subagent.delegate_task(MagicMock(), "做一个播客脚本"):
        pass

    cfg = captured["config"]
    assert cfg.enable_skills is False, "子代理必须关闭技能（提示词/提示/学习全隔离，B1）"
    assert cfg.tool_tags == ["search", "analysis"]


# ── 评审 B2：反馈边 / 过期清理 / 去重 ───────────────────────────────────────


@pytest.mark.asyncio
async def test_skill_outcome_feedback_success_failure_and_no_evidence():
    from app.agent.loop import PERAgentLoop

    loop = PERAgentLoop(MagicMock(), AgentConfig(), organization_id=1)
    loop.skill_manager = MagicMock()
    loop.skill_manager.record_success = AsyncMock()
    loop.skill_manager.record_failure = AsyncMock()

    await loop._record_skill_outcome("sk-1", {"grades": {"s1": ("A", ""), "s2": ("A", "")}})
    assert loop.skill_manager.record_success.await_count == 1
    assert loop.skill_manager.record_failure.await_count == 0

    await loop._record_skill_outcome("sk-1", {"grades": {"s1": ("C", "")}})
    assert loop.skill_manager.record_failure.await_count == 1

    loop.skill_manager.record_success.reset_mock()
    await loop._record_skill_outcome(None, {"grades": {"s1": ("A", "")}})  # 未命中
    await loop._record_skill_outcome("sk-1", {})                           # 无证据
    assert loop.skill_manager.record_success.await_count == 0


@pytest.mark.asyncio
async def test_skill_feedback_strengthens_matching_and_refreshes():
    """B2 复现的反面：用一次且全 A → success_count=1、last_used_at 刷新、
    近似改写（只命中一个触发模式）从命不中变为命得中。"""
    import time as _time

    from app.agent.loop import PERAgentLoop
    from app.agent.skills import SkillManager

    mgr = SkillManager()
    skill = await mgr.create_skill(
        name="Auto: t", description="d",
        trigger_patterns=["帮我生成播客脚本", "并配上开场音乐"],
        tool_sequence=[{"tool_name": "search_knowledge_base", "description": "x"}],
    )
    fresh = skill.last_used_at
    skill.last_used_at = _time.time() - 3600  # 拨回一小时，便于断言"被刷新"

    # 冷启动（success_rate=0 → 乘数 0.5）：单模式近似改写命不中
    assert mgr.match("帮我生成播客脚本就行") is None

    loop = PERAgentLoop(MagicMock(), AgentConfig(), organization_id=1)
    loop.skill_manager = mgr
    await loop._record_skill_outcome(skill.id, {"grades": {"s1": ("A", "")}})

    assert skill.success_count == 1
    assert skill.last_used_at > fresh - 3600 + 3599, "使用后 last_used_at 必须刷新（不再 7 天过期）"
    # 强化后：1 × (0.5 + 0.5×1.0) = 1.0 ≥ 阈值 → 同一近似改写命得中
    assert mgr.match("帮我生成播客脚本就行") is skill


@pytest.mark.asyncio
async def test_prune_stale_removes_aged_skills():
    import time as _time

    from app.agent.skills import SkillManager

    mgr = SkillManager()
    skill = await mgr.create_skill(
        name="Auto: t", description="d",
        trigger_patterns=["abc"], tool_sequence=[{"tool_name": "x"}],
    )
    skill.last_used_at = _time.time() - 8 * 24 * 3600
    assert await mgr.prune_stale() == 1
    assert skill.id not in mgr._skills


@pytest.mark.asyncio
async def test_learn_dedups_same_query_and_rejects_no_trigger():
    from app.agent.skills import SkillManager

    mgr = SkillManager()
    steps = [{"tool_hint": "t1", "description": "a"}, {"tool_hint": "t2", "description": "b"}]

    first = await mgr.learn_from_plan_success("帮我总结这两份文档", steps)
    assert first is not None
    second = await mgr.learn_from_plan_success("帮我总结这两份文档", steps)
    assert second is None, "同一 query 不重复沉淀（m3）"
    assert len(mgr._skills) == 1
    assert await mgr.learn_from_plan_success("ok go", steps) is None, "无触发词不造注定无用的技能"


class _FakeMemory:
    def __init__(self):
        self.results: dict = {}

    def reset_working(self):
        self.results = {}

    def get_all_results(self):
        return dict(self.results)

    def store_step_result(self, step_id, result):
        self.results[step_id] = result

    def get_step_result(self, step_id, run_id=None):
        return self.results.get(step_id)

    async def record_interaction(self, user_input, assistant_response):
        pass


class _FakePlanner:
    """产出一个没有 tool_hint 的步骤：技能读侧应把命中技能的工具补上。"""

    async def plan(self, query, history=None, memory_context="", ctx=None,
                   replan_feedback=""):
        yield AgentEvent(type="plan_start", plan_id="plan-1", content="制定计划...")
        yield AgentEvent(type="plan_step", plan_id="plan-1", plan_step_id="s1",
                         content="唯一步骤", tool_hint=None)
        yield AgentEvent(type="plan_complete", plan_id="plan-1", plan_goal="目标",
                         content="计划完成")


class _FakeExecutor:
    def __init__(self, memory: _FakeMemory):
        self.memory = memory
        self.seen_tool_hints: list = []

    def reset_for_request(self):
        pass

    async def execute(self, plan, history=None, organization_id=1, user_id=0,
                      enable_thinking=True, ctx=None, on_step_done=None):
        for s in plan.steps:
            self.seen_tool_hints.append(s.tool_hint)
            s.status = "completed"
            plan.completed_steps += 1
            self.memory.store_step_result(s.id, {
                "description": s.description,
                "status": "completed",
                "result": "这是一段足够完整的执行结果内容，包含具体事实与结论，用于质检评级。" * 5,
                "tool_hint": s.tool_hint,
            })
        yield AgentEvent(type="chunk", content="答案")


class _FakeReflector:
    async def reflect(self, plan, results, ctx=None):
        yield AgentEvent(type="reflection", reflection_result="pass",
                         content="", plan_id=plan.id)


class _FakeReviewer:
    async def review(self, plan, results, ctx=None):
        return {"issues_found": [], "recommendation": "pass"}


@pytest.mark.asyncio
async def test_run_records_skill_feedback_and_prunes(monkeypatch):
    """run 级见证（评审 B2 的变异点全在这条链上）：删掉 load / prune / 反馈任一
    处接线（loop.run 开头与 Phase 2c），本测试精确红。"""
    from app.agent.loop import PERAgentLoop

    async def _no_save(self, ttl=86400 * 7):
        return False

    monkeypatch.setattr(ExecutionContext, "save", _no_save)

    loop = PERAgentLoop(
        MagicMock(),
        AgentConfig(enable_memory=False, enable_thinking=False, enable_experience=False),
        organization_id=1, user_id=0,
    )
    memory = _FakeMemory()
    loop.memory = memory
    loop._planner = _FakePlanner()
    loop._executor = _FakeExecutor(memory)
    loop._reflector = _FakeReflector()
    loop._reviewer = _FakeReviewer()
    loop._write_experiences = AsyncMock()

    mgr = MagicMock()
    mgr.load = AsyncMock()
    mgr.prune_stale = AsyncMock()
    mgr.learn_from_plan_success = AsyncMock(return_value=None)
    mgr.record_success = AsyncMock()
    mgr.record_failure = AsyncMock()
    mgr.match.return_value = SimpleNamespace(
        id="sk-1",
        tool_sequence=[{"tool_name": "search_knowledge_base", "description": "x"}],
    )
    loop.skill_manager = mgr

    events = await asyncio.wait_for(
        _collect(loop), timeout=15.0,
    )

    assert mgr.load.await_count == 1, "run 开头应懒加载技能"
    assert mgr.prune_stale.await_count == 1, "run 开头应清理过期技能（B2）"
    assert loop._executor.seen_tool_hints == ["search_knowledge_base"], (
        "读侧应把命中技能的工具补上空缺的 tool_hint"
    )
    total = mgr.record_success.await_count + mgr.record_failure.await_count
    assert total == 1, "命中且应用的技能必须在回合结束时回报使用结果（B2 反馈边）"
    recorded = mgr.record_success.await_args or mgr.record_failure.await_args
    assert recorded.args[0] == "sk-1"
    assert any(e.type == "done" for e in events), "run 应正常收尾"


async def _collect(loop) -> list:
    return [e async for e in loop.run("帮我生成播客脚本")]
