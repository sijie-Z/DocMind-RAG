"""issue #203 ①② 死配置接线后的行为契约（循环治理），外加一处顺手修的边界。

- ① `max_iterations`：单次 run 内可「执行」的计划步骤数，跨 replan 轮次累计；
  重试不另计（重试归 max_retries_per_step 管）。达上限 → 剩余 pending 置 skipped +
  decision(iteration_limit) + thinking，run 正常收尾。
- ② `max_total_tokens`：token 预算熔断（0 = 不限制）。检查点全部落在
  「还能阻止下一次 LLM 调用」的边界：步骤派发前、每次 attempt（含重试）前、
  反思/对抗审查/重规划门前。
- 顺手修：serial 模式「全部剩余步骤因前置失败被跳过」原先会 pending[0] 越界崩溃。

每个行为断言都标注了变异点（把接线删回原状，对应用例必须变红）。
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent.config import AgentConfig
from app.agent.events import AgentEvent
from app.agent.exec_context import ExecutionContext
from app.agent.executor import Executor
from app.agent.loop import PERAgentLoop
from app.agent.planner import Plan, PlanStep


@pytest.fixture(autouse=True)
def _no_ctx_persistence(monkeypatch):
    """ExecutionContext.save() 在测试里不应写 Redis / benchmark 目录。"""
    async def _save(self, ttl=86400 * 7):
        return False

    monkeypatch.setattr(ExecutionContext, "save", _save)


def _memory() -> MagicMock:
    m = MagicMock()
    m.record_experience = AsyncMock()
    return m


def _chain(*descriptions: str) -> Plan:
    """带依赖链的计划（s1 → s2 → …），避免 DAG 一次把多步全派发绕过守卫。"""
    steps: list[PlanStep] = []
    prev: str | None = None
    for i, desc in enumerate(descriptions, start=1):
        sid = f"s{i}"
        steps.append(PlanStep(id=sid, description=desc,
                              dependencies=[prev] if prev else []))
        prev = sid
    return Plan(id="p1", goal="g", steps=steps)


def _completed_spy(calls: list[str], on_run=None):
    """替身 _execute_step_with_retry：记录被执行的 step、标记完成、yield 一个 chunk。"""
    async def _spy(*, step, plan, history, organization_id, user_id,
                   enable_thinking, ctx=None):
        calls.append(step.id)
        step.status = "completed"
        if on_run is not None:
            on_run(ctx)
        yield AgentEvent(type="chunk", content=f"结果-{step.id}")

    return _spy


# ── ① max_iterations ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_iteration_cap_truncates_remaining_steps():
    """变异点：删去 execute() 的 iteration 守卫 → spy 被调用两次 → 本测试红。"""
    cfg = AgentConfig(max_iterations=1, enable_planning=False, enable_reflection=False)
    ex = Executor(AsyncMock(), cfg, _memory())
    plan = _chain("第一步", "第二步")
    ctx = ExecutionContext(query="q")
    calls: list[str] = []
    ex._execute_step_with_retry = _completed_spy(calls)

    events = [e async for e in ex.execute(plan, ctx=ctx)]

    assert calls == ["s1"], "max_iterations=1 只允许执行 1 步"
    assert plan.steps[1].status == "skipped"
    assert any(d.action == "iteration_limit" and d.phase == "execute" for d in ctx.decisions)
    assert any(e.type == "thinking" and "迭代上限" in e.content for e in events)


@pytest.mark.asyncio
async def test_iteration_cap_exact_off_by_one():
    """max_iterations=2 且 3 步链：恰好执行 2 步，第 3 步被跳过（防差一不设限）。"""
    cfg = AgentConfig(max_iterations=2, enable_planning=False, enable_reflection=False)
    ex = Executor(AsyncMock(), cfg, _memory())
    plan = _chain("A", "B", "C")
    ctx = ExecutionContext(query="q")
    calls: list[str] = []
    ex._execute_step_with_retry = _completed_spy(calls)

    [e async for e in ex.execute(plan, ctx=ctx)]

    assert calls == ["s1", "s2"]
    assert plan.steps[2].status == "skipped"


@pytest.mark.asyncio
async def test_iteration_cap_zero_means_unlimited():
    """0 = 不限制：max_iterations=0 时 3 步链全部执行。"""
    cfg = AgentConfig(max_iterations=0, enable_planning=False, enable_reflection=False)
    ex = Executor(AsyncMock(), cfg, _memory())
    plan = _chain("A", "B", "C")
    ctx = ExecutionContext(query="q")
    calls: list[str] = []
    ex._execute_step_with_retry = _completed_spy(calls)

    [e async for e in ex.execute(plan, ctx=ctx)]

    assert calls == ["s1", "s2", "s3"]
    assert all(d.action != "iteration_limit" for d in ctx.decisions)


# ── 顺手修：serial 模式越界崩溃 ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_serial_all_pending_have_failed_deps_does_not_crash():
    """前置失败把 pending 清空时，serial 派发不得 IndexError（#203 顺手修）。"""
    cfg = AgentConfig(enable_planning=False, enable_reflection=False)
    ex = Executor(AsyncMock(), cfg, _memory(), execution_mode="serial")
    plan = _chain("第一步", "第二步")
    ctx = ExecutionContext(query="q")

    async def _fail(*, step, plan, history, organization_id, user_id,
                    enable_thinking, ctx=None):
        step.status = "failed"
        step.error_context = "boom"
        yield AgentEvent(type="thinking", content="失败")

    ex._execute_step_with_retry = _fail
    [e async for e in ex.execute(plan, ctx=ctx)]

    assert plan.steps[0].status == "failed"
    assert plan.steps[1].status == "skipped"


# ── ② max_total_tokens（生产者：预算视角的 budget_exceeded） ────────────────


@pytest.mark.asyncio
async def test_budget_exceeded_blocks_new_llm_calls():
    """变异点：删去 _execute_step_with_retry 的预算守卫 →
    client.create 被调用、error_context 不含 budget_exceeded → 本测试红。"""
    client = AsyncMock()
    cfg = AgentConfig(max_total_tokens=100, enable_planning=False, enable_reflection=False)
    ex = Executor(client, cfg, _memory())
    step = PlanStep(id="s1", description="第一步")
    plan = Plan(id="p1", goal="g", steps=[step])
    ctx = ExecutionContext(query="q")
    ctx.total_tokens = 100  # 已达上限

    events = [e async for e in ex._execute_step_with_retry(
        step=step, plan=plan, history=None, organization_id=1, user_id=0,
        enable_thinking=False, ctx=ctx,
    )]

    assert client.chat.completions.create.call_count == 0, "预算已尽不得发起任何 LLM 调用"
    assert step.status == "failed"
    assert "budget_exceeded" in (step.error_context or "")
    errs = [e for e in events if e.type == "tool_error"]
    assert errs and errs[0].error_type == "budget_exceeded"
    assert ctx.failures, "预算熔断必须点亮 ctx.failures / run_report 的 has-failures"
    assert any(d.action == "budget_exceeded" and d.phase == "execute" for d in ctx.decisions)


@pytest.mark.asyncio
async def test_budget_exceeded_skips_remaining_dispatch():
    """变异点：删去 execute() 的预算派发守卫 → s2 也被执行 → 本测试红。"""
    cfg = AgentConfig(max_total_tokens=100, enable_planning=False, enable_reflection=False)
    ex = Executor(AsyncMock(), cfg, _memory())
    plan = _chain("第一步", "第二步")
    ctx = ExecutionContext(query="q")
    calls: list[str] = []

    def _burn(ctx_):
        ctx_.total_tokens = 100  # 本步执行期间烧光预算

    ex._execute_step_with_retry = _completed_spy(calls, on_run=_burn)

    events = [e async for e in ex.execute(plan, ctx=ctx)]

    assert calls == ["s1"]
    assert plan.steps[1].status == "skipped"
    assert any(d.action == "budget_exceeded" and d.phase == "execute" for d in ctx.decisions)
    assert any(e.type == "thinking" and "token 预算" in e.content for e in events)


# ── ② loop 门：预算已尽不再烧反思/审查/重规划 ────────────────────────────────


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


class _OneStepPlanner:
    async def plan(self, query, history=None, memory_context="", ctx=None,
                   replan_feedback=""):
        yield AgentEvent(type="plan_start", plan_id="plan-1", content="制定计划...")
        yield AgentEvent(type="plan_step", plan_id="plan-1", plan_step_id="s1",
                         content="唯一一步", tool_hint="search_knowledge_base")
        yield AgentEvent(type="plan_complete", plan_id="plan-1", plan_goal="目标",
                         content="计划完成")


class _BudgetBurningExecutor:
    """执行阶段把 ctx.total_tokens 烧到超预算（预算只在执行中耗尽）。"""

    def __init__(self, memory: _FakeMemory):
        self.memory = memory

    def reset_for_request(self):
        pass

    async def execute(self, plan, history=None, organization_id=1, user_id=0,
                      enable_thinking=True, ctx=None):
        for s in plan.steps:
            s.status = "completed"
            plan.completed_steps += 1
            self.memory.store_step_result(
                s.id, {"description": s.description, "status": "completed", "result": "结果"},
            )
        if ctx is not None:
            ctx.total_tokens = 999
        yield AgentEvent(type="chunk", content="答案")


class _CountingReflector:
    def __init__(self):
        self.calls = 0

    async def reflect(self, plan, results, ctx=None):
        self.calls += 1
        yield AgentEvent(type="reflection", reflection_result="pass",
                         content="", plan_id=plan.id)


class _CountingReviewer:
    def __init__(self):
        self.calls = 0

    async def review(self, plan, results):
        self.calls += 1
        return {"issues_found": [], "recommendation": "pass"}


@pytest.mark.asyncio
async def test_budget_gate_skips_reflection_and_review():
    """变异点：删去 loop 反思门/审查门的 over_budget 条件 → 两器被调用 → 本测试红。"""
    loop = PERAgentLoop(
        MagicMock(),
        AgentConfig(enable_memory=False, enable_thinking=False, enable_experience=False,
                    max_total_tokens=100),
        organization_id=1, user_id=0,
    )
    memory = _FakeMemory()
    loop.memory = memory
    loop._executor = _BudgetBurningExecutor(memory)
    loop._planner = _OneStepPlanner()
    reflector, reviewer = _CountingReflector(), _CountingReviewer()
    loop._reflector = reflector
    loop._reviewer = reviewer
    loop._write_experiences = AsyncMock()

    events = await asyncio.wait_for(
        _collect(loop), timeout=15.0,
    )

    assert reflector.calls == 0, "预算已尽不得再发起反思调用"
    assert reviewer.calls == 0, "预算已尽不得再发起对抗审查调用"
    assert any(e.type == "done" for e in events), "run 应正常收尾"


async def _collect(loop) -> list:
    return [e async for e in loop.run("问题")]


# ── 配置面 ──────────────────────────────────────────────────────────────────


def test_default_max_iterations_is_two_plan_rounds():
    """默认 20 = max_plan_steps(10) × (1 + MAX_REPLANS_PER_RUN)：不饿死合法 replan 轮。"""
    assert AgentConfig().max_iterations == 20


def test_max_total_tokens_roundtrip():
    cfg = AgentConfig(max_total_tokens=12345, max_iterations=7)
    d = cfg.to_dict()
    assert d["max_total_tokens"] == 12345
    restored = AgentConfig.from_dict(d)
    assert restored.max_total_tokens == 12345
    assert restored.max_iterations == 7


def test_over_budget_helper_semantics():
    unlimited = AgentConfig()  # max_total_tokens=0
    ctx = ExecutionContext(query="q")
    ctx.total_tokens = 10 ** 9
    assert unlimited.over_budget(ctx) is False
    assert unlimited.over_budget(None) is False

    capped = AgentConfig(max_total_tokens=100)
    ctx.total_tokens = 99
    assert capped.over_budget(ctx) is False
    ctx.total_tokens = 100
    assert capped.over_budget(ctx) is True


def test_thinking_max_tokens_removed_stale_key_ignored():
    """扫尾：thinking_max_tokens 零消费且无合法消费点（同组的 plan/reflection 都有真实
    限幅调用，而它对应的"思考 LLM 调用"并不存在——同类调用是最终答案生成，不能按
    500 截断），按"接或删"原则删除；旧持久化配置的残留键被 from_dict 安全忽略。"""
    assert not hasattr(AgentConfig(), "thinking_max_tokens")
    cfg = AgentConfig.from_dict({"thinking_max_tokens": 500, "model": "m"})
    assert cfg.model == "m"
