"""技能系统接线验收（批次十起：reviewed-only 组织技能库）。

- **索引进提示词**：`build_skills_index` 含仓库真实技能；executor 提示词按开关注入
  （受限工具上下文除外——评审 B1）；
- **真工具**：`list_skills` / `load_skill_reference`（指南与参考两种形态、错误显式）；
- **运行时读侧**：`_apply_skill_hints` 只读 `org_skill_service.match_skill`
  （approved + enabled + 哈希通过 + 工具可见），只补空缺不覆盖；
- **自动学习已停用**：loop 不再持有 Redis SkillManager，不 load/prune/learn；
  `SkillManager` 仅作为基准/测试工具保留（下方仍锁住其自身行为）。
"""

import asyncio
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
    assert "load_skill_reference" in names_all
    # issue #509：list_skills 已合并为 load_skill_reference 的缺省行为（hidden），
    # schema 只发规范名；旧名仍经 TOOL_ALIASES 可用（执行路径见下方用例）。
    entry = tool_registry.get("list_skills")
    assert entry is not None and entry.hidden
    assert entry.superseded_by == "load_skill_reference"


@pytest.mark.asyncio
async def test_load_skill_reference_guide_and_reference():
    listed = await tool_registry.execute_detailed("list_skills", {}, organization_id=1, user_id=0)
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
        AgentConfig(
            enable_planning=False, enable_reflection=False, tool_tags=["search", "analysis"]
        )
    )
    sys_child = [m for m in msgs_child if m.get("role") == "system"][0]["content"]
    assert "## 可用技能" not in sys_child, "受限上下文不得注入技能索引（B1）"
    assert "load_skill_reference" not in sys_child, "受限上下文不得引导调用技能工具（B1）"


@pytest.mark.asyncio
async def test_apply_skill_hints_only_fills_missing(monkeypatch):
    """批次十：读侧改为 org_skill_service.match_skill（reviewed-only）。

    变异点：`_apply_skill_hints` 改回 `SkillManager.match` / 不复核可见工具集
    → 本用例与 test_org_skill_wiring 的 Redis 预置用例红。
    """
    from app.agent.loop import PERAgentLoop
    from app.services import org_skill_service

    loop = PERAgentLoop(MagicMock(), AgentConfig(), organization_id=1)
    matched = org_skill_service.MatchedSkill(
        skill_id="sk-1",
        version_no=3,
        tool_sequence=(
            {"tool_name": "search_knowledge_base"},
            {"tool_name": "web_search"},
        ),
    )

    async def _match(_session, **_kwargs):
        return matched

    monkeypatch.setattr(org_skill_service, "match_skill", _match)
    steps = [
        PlanStep(id="s1", description="a"),
        PlanStep(id="s2", description="b", tool_hint="execute_python"),
    ]
    plan = Plan(id="p1", goal="g", steps=steps)

    matched_id = await loop._apply_skill_hints(plan, "查一下")

    assert steps[0].tool_hint == "search_knowledge_base", "空缺应被技能提示补上"
    assert steps[1].tool_hint == "execute_python", "已有提示不得被覆盖"
    assert matched_id == "sk-1", "应返回实际被应用的 skill_id"


@pytest.mark.asyncio
async def test_apply_skill_hints_skips_tools_not_visible_in_context(monkeypatch):
    """#245 第 5/6 条：跨会话工具配置漂移——技能记的工具名不在当前可见集（tool_tags
    受限）时跳过应用，不得写进步骤诱导模型调用被执行层拒绝的工具。

    变异点：_apply_skill_hints 里 `hint and hint in visible` 还原为 `hint` → 红。
    """
    from app.agent.loop import PERAgentLoop
    from app.services import org_skill_service

    loop = PERAgentLoop(
        MagicMock(), AgentConfig(tool_tags=["search", "analysis"]), organization_id=1
    )
    matched = org_skill_service.MatchedSkill(
        skill_id="sk-9",
        version_no=1,
        tool_sequence=(
            {"tool_name": "delegate_subtask"},
            {"tool_name": "search_knowledge_base"},
        ),
    )

    async def _match(_session, **_kwargs):
        return matched

    monkeypatch.setattr(org_skill_service, "match_skill", _match)
    steps = [PlanStep(id="s1", description="a"), PlanStep(id="s2", description="b")]
    plan = Plan(id="p1", goal="g", steps=steps)

    matched_id = await loop._apply_skill_hints(plan, "查一下")

    assert steps[0].tool_hint is None, "不在当前可见集的 hint 必须跳过（不为不可见工具背书）"
    assert steps[1].tool_hint == "search_knowledge_base", "可见 hint 仍按位置配对应用"
    assert matched_id == "sk-9"

    # 全部不可见 → 一条都没应用上 → None。
    matched_too = org_skill_service.MatchedSkill(
        skill_id="sk-10", version_no=1, tool_sequence=({"tool_name": "delegate_subtask"},)
    )

    async def _match_too(_session, **_kwargs):
        return matched_too

    monkeypatch.setattr(org_skill_service, "match_skill", _match_too)
    plan2 = Plan(id="p2", goal="g", steps=[PlanStep(id="s1", description="a")])
    assert await loop._apply_skill_hints(plan2, "查一下") is None
    assert plan2.steps[0].tool_hint is None


@pytest.mark.asyncio
async def test_apply_skill_hints_respects_disabled_tools(monkeypatch):
    """#245 第 5/6 条（disabled_tools 侧）：被 disabled_tools 过滤掉的工具同样
    不可见——hint 跳过应用。

    变异点：同 test_apply_skill_hints_skips_tools_not_visible_in_context。
    """
    from app.agent.loop import PERAgentLoop
    from app.services import org_skill_service

    loop = PERAgentLoop(MagicMock(), AgentConfig(disabled_tools=["web_search"]), organization_id=1)
    matched = org_skill_service.MatchedSkill(
        skill_id="sk-11",
        version_no=1,
        tool_sequence=({"tool_name": "web_search"},),
    )

    async def _match(_session, **_kwargs):
        return matched

    monkeypatch.setattr(org_skill_service, "match_skill", _match)
    plan = Plan(id="p1", goal="g", steps=[PlanStep(id="s1", description="a")])

    assert await loop._apply_skill_hints(plan, "查一下") is None
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
async def test_auto_learning_is_disabled():
    """批次十：loop 不得再持有/调用 Redis SkillManager 的自动学习闭环。"""
    from app.agent.loop import PERAgentLoop

    loop = PERAgentLoop(MagicMock(), AgentConfig(), organization_id=1)
    assert not hasattr(loop, "skill_manager"), "生产 loop 不得再持有 Redis SkillManager"
    plan = Plan(
        id="p1",
        goal="g",
        steps=[
            PlanStep(id="s1", description="a", tool_hint="t1"),
            PlanStep(id="s2", description="b", tool_hint="t2"),
        ],
    )
    ctx = ExecutionContext(query="q")
    # 空操作：不抛异常、不创建任何技能记录（完整断言见 test_org_skill_wiring）。
    assert await loop._learn_skills(ctx, plan, {"grades": {"s1": ("A", "")}}) is None


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
    async for _ in subagent.delegate_task(
        MagicMock(), "做一个播客脚本", organization_id=1, user_id=9
    ):
        pass

    cfg = captured["config"]
    assert cfg.enable_skills is False, "子代理必须关闭技能（提示词/提示/学习全隔离，B1）"
    assert cfg.tool_tags == ["search", "analysis"]


# ── 评审 B2：反馈边 / 过期清理 / 去重 ───────────────────────────────────────


@pytest.mark.asyncio
async def test_skill_outcome_feedback_is_noop():
    """批次十：使用结果回报已停用（v1 无成功率字段），且 loop 不持有 SkillManager。"""
    from app.agent.loop import PERAgentLoop

    loop = PERAgentLoop(MagicMock(), AgentConfig(), organization_id=1)
    assert not hasattr(loop, "skill_manager")
    assert (
        await loop._record_skill_outcome("sk-1", {"grades": {"s1": ("A", ""), "s2": ("A", "")}})
        is None
    )
    assert await loop._record_skill_outcome("sk-1", {"grades": {"s1": ("C", "")}}) is None
    assert await loop._record_skill_outcome(None, {"grades": {"s1": ("A", "")}}) is None
    assert await loop._record_skill_outcome("sk-1", {}) is None


@pytest.mark.asyncio
async def test_skill_feedback_strengthens_matching_and_refreshes():
    """保留模块（基准/测试用）自洽性：SkillManager 仍可独立工作。

    批次十起生产 loop 不再调用它；这里只锁住被保留模块自身不回归。
    """
    import time as _time

    from app.agent.skills import SkillManager

    mgr = SkillManager()
    skill = await mgr.create_skill(
        name="Auto: t",
        description="d",
        trigger_patterns=["帮我生成播客脚本", "并配上开场音乐"],
        tool_sequence=[{"tool_name": "search_knowledge_base", "description": "x"}],
    )
    fresh = skill.last_used_at
    skill.last_used_at = _time.time() - 3600  # 拨回一小时，便于断言"被刷新"

    # 冷启动（success_rate=0 → 乘数 0.5）：单模式近似改写命不中
    assert mgr.match("帮我生成播客脚本就行") is None

    await mgr.record_success(skill.id)

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
        name="Auto: t",
        description="d",
        trigger_patterns=["abc"],
        tool_sequence=[{"tool_name": "x"}],
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


# ── 学习侧触发词"重复记账"去重 ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_learn_dedups_repeated_chinese_trigger_cold_start_no_match():
    """纯中文查询曾被同一串重复记两次触发词（`re.findall` 提整段
    汉字串 + `split()` 循环 `isalpha()` 再记一遍）→ 冷启动单模式 2.0×0.5=1.0
    恰好踩线命中。提取器保序去重后：单串 1.0×0.5=0.5 < 1.0 不命中。

    变异点：删去 `learn_from_plan_success` 的 `list(dict.fromkeys(keywords))`
    → 两条断言同时红（pattern 变回重复；match 由 None 变命中）。
    """
    from app.agent.skills import SkillManager

    mgr = SkillManager()
    steps = [{"tool_hint": "t1", "description": "a"}, {"tool_hint": "t2", "description": "b"}]
    query = "帮我分析销售数据"  # 纯中文串：findall 与 split 循环各记一次

    skill = await mgr.learn_from_plan_success(query, steps)

    assert skill is not None
    assert skill.trigger_patterns == [query], (
        "同一串重复记账必须保序去重：重复数不得变成额外加分（凑数踩线 1.0）"
    )
    assert mgr.match(query) is None, "冷启动 1 模式 × 0.5 = 0.5 < 1.0：不命中"


@pytest.mark.asyncio
async def test_dedup_keeps_existing_patterns_and_strengthened_match_intact():
    """对照（存量不受影响 + 设计路径不回归）：修复只作用于学习侧
    提取器——① 存量技能（含修复前重复记账的 pattern）原样保留、强化后照常命中；
    ② 去重后的新学技能经一次使用强化，从"冷启动不命中"变为"命中"。
    """
    from app.agent.skills import SkillManager

    mgr = SkillManager()
    legacy = await mgr.create_skill(
        name="Auto: 存量",
        description="d",
        trigger_patterns=["帮我生成播客脚本", "帮我生成播客脚本"],  # 修复前学习侧产物
        tool_sequence=[{"tool_name": "search_knowledge_base", "description": "x"}],
    )
    legacy.success_count = 1  # 存量已强化
    assert legacy.trigger_patterns == ["帮我生成播客脚本", "帮我生成播客脚本"], (
        "存量 pattern 保留，不做迁移改写（修复只作用于提取器）"
    )
    assert mgr.match("帮我生成播客脚本") is legacy, "存量已强化技能照常命中"

    steps = [{"tool_hint": "t1", "description": "a"}, {"tool_hint": "t2", "description": "b"}]
    fresh = await mgr.learn_from_plan_success("帮我分析销售数据", steps)
    assert fresh is not None, "与存量 pattern 不同，应正常学习"
    assert mgr.match("帮我分析销售数据") is None, "冷启动不命中（0.5 < 1.0）"
    await mgr.record_success(fresh.id)
    assert mgr.match("帮我分析销售数据") is fresh, "强化后 1×1.0 ≥ 1.0 命中（设计路径不回归）"


@pytest.mark.asyncio
async def test_dedup_keeps_first_occurrence_order_before_cap5():
    """次序锁定（评审补强）：从 >5 候选（含重复）中**先保序去重、
    后截 5**——重复不得占帽位，首现序不得漂移。

    变异点：① 去重挪到截 5 之后（`keywords[:5]` 再 `dict.fromkeys`）→ 重复占掉
    帽位、只剩 4 个唯一词 → 红；② `dict.fromkeys` 换 `set` → 顺序漂移 → 红。
    """
    from app.agent.skills import SkillManager

    mgr = SkillManager()
    steps = [{"tool_hint": "t1", "description": "a"}, {"tool_hint": "t2", "description": "b"}]
    # 提取器原始序列：[汉字串, 汉字串, alpha, bravo, charlie, delta, echo, foxtrot]
    # （findall 汉字串 1 条 + split 循环含汉字串再记 1 次与 6 个英文词）
    query = "汇总季度财务报告 alpha bravo charlie delta echo foxtrot"

    skill = await mgr.learn_from_plan_success(query, steps)

    assert skill is not None
    assert skill.trigger_patterns == [
        "汇总季度财务报告",
        "alpha",
        "bravo",
        "charlie",
        "delta",
    ], "先去重后截 5 且保首现序：重复不得占帽位（否则只剩 4 唯一词）、顺序不得漂移"


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

    async def plan(self, query, history=None, memory_context="", ctx=None, replan_feedback=""):
        yield AgentEvent(type="plan_start", plan_id="plan-1", content="制定计划...")
        yield AgentEvent(
            type="plan_step",
            plan_id="plan-1",
            plan_step_id="s1",
            content="唯一步骤",
            tool_hint=None,
        )
        yield AgentEvent(
            type="plan_complete", plan_id="plan-1", plan_goal="目标", content="计划完成"
        )


class _FakeExecutor:
    def __init__(self, memory: _FakeMemory):
        self.memory = memory
        self.seen_tool_hints: list = []

    def reset_for_request(self):
        pass

    async def execute(
        self,
        plan,
        history=None,
        organization_id=1,
        user_id=0,
        enable_thinking=True,
        ctx=None,
        on_step_done=None,
        human_guidance="",
        document_context="",
    ):
        for s in plan.steps:
            self.seen_tool_hints.append(s.tool_hint)
            s.status = "completed"
            plan.completed_steps += 1
            self.memory.store_step_result(
                s.id,
                {
                    "description": s.description,
                    "status": "completed",
                    "result": "这是一段足够完整的执行结果内容，包含具体事实与结论，用于质检评级。"
                    * 5,
                    "tool_hint": s.tool_hint,
                },
            )
        yield AgentEvent(type="chunk", content="答案")


class _FakeReflector:
    async def reflect(self, plan, results, ctx=None):
        yield AgentEvent(type="reflection", reflection_result="pass", content="", plan_id=plan.id)


class _FakeReviewer:
    async def review(self, plan, results, ctx=None):
        return {"issues_found": [], "recommendation": "pass"}


@pytest.mark.asyncio
async def test_run_uses_reviewed_skill_and_never_redis(monkeypatch):
    """run 级见证（批次十）：命中 reviewed 技能时补 tool_hint；loop 不再
    load/prune/learn 或记录 Redis 技能使用结果。

    变异点：把 `_apply_skill_hints` 改回 `SkillManager.match`、或在 run 起点
    恢复 `skill_manager.load()/prune_stale()` → 本用例红。
    """
    from app.agent.loop import PERAgentLoop
    from app.services import org_skill_service

    async def _no_save(self, ttl=86400 * 7):
        return False

    monkeypatch.setattr(ExecutionContext, "save", _no_save)

    loop = PERAgentLoop(
        MagicMock(),
        AgentConfig(enable_memory=False, enable_thinking=False, enable_experience=False),
        organization_id=1,
        user_id=0,
    )
    memory = _FakeMemory()
    loop.memory = memory
    loop._planner = _FakePlanner()
    loop._executor = _FakeExecutor(memory)
    loop._reflector = _FakeReflector()
    loop._reviewer = _FakeReviewer()
    loop._write_experiences = AsyncMock()

    async def _match(_session, **_kwargs):
        return org_skill_service.MatchedSkill(
            skill_id="sk-reviewed",
            version_no=2,
            tool_sequence=({"tool_name": "search_knowledge_base"},),
        )

    monkeypatch.setattr(org_skill_service, "match_skill", _match)

    events = await asyncio.wait_for(
        _collect(loop),
        timeout=15.0,
    )

    assert not hasattr(loop, "skill_manager"), "run 不得再持有 Redis SkillManager"
    assert loop._executor.seen_tool_hints == ["search_knowledge_base"], (
        "读侧应把 reviewed 技能的工具补上空缺的 tool_hint"
    )
    assert any(e.type == "done" for e in events), "run 应正常收尾"


async def _collect(loop) -> list:
    return [e async for e in loop.run("帮我生成播客脚本")]
