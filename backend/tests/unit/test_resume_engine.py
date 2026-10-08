"""issue #201 最小版：loop.run 的 resume 执行语义（无 DB 的引擎级用例）。

钉住的语义（设计报告 2.3 / 3.3）：
- resume 有 plan：不重新规划、已完成步骤绝不重跑、"running" 在途步复位重跑一次、
  WorkingMemory 从 PlanStep.result 回灌（否则后续步骤丢前置结果）；
- resume 无 plan（崩溃于规划阶段）：从 Phase 1 重跑（仍幂等）；
- 预算延续：#203 的 token/迭代熔断读的是**恢复后的** ctx（快照不丢 artifacts /
  total_tokens，否则 resume 静默重置预算）；
- replan 计数延续：快照里的 replan_attempts 计入本轮上限（有界性不因恢复失效）；
- partial_output：resume 的最终答案 = 快照前缀 + 续跑新增（前端旧流不回放）。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent import checkpoint
from app.agent.config import AgentConfig
from app.agent.events import AgentEvent
from app.agent.exec_context import ExecutionContext
from app.agent.executor import Executor
from app.agent.loop import PERAgentLoop
from app.agent.planner import Plan, PlanStep


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    async def _save(self, ttl=86400 * 7):
        return False

    async def _noop_save(**kwargs):
        return True

    monkeypatch.setattr(ExecutionContext, "save", _save)
    monkeypatch.setattr(PERAgentLoop, "_write_experiences", AsyncMock())
    monkeypatch.setattr(checkpoint, "save_checkpoint", _noop_save)


class _FakeMemory:
    def __init__(self):
        self.results: dict = {}
        self.interactions: list = []

    def reset_working(self):
        self.results = {}

    def get_all_results(self):
        return dict(self.results)

    def store_step_result(self, step_id, result):
        self.results[step_id] = result

    def get_step_result(self, step_id, run_id=None):
        return self.results.get(step_id)

    async def record_experience(self, **kwargs):
        pass

    async def record_interaction(self, user_input, assistant_response):
        self.interactions.append((user_input, assistant_response))


class _CountingPlanner:
    def __init__(self, n=1):
        self.calls = 0
        self.n = n

    async def plan(self, query, history=None, memory_context="", ctx=None, replan_feedback=""):
        self.calls += 1
        yield AgentEvent(type="plan_start", plan_id="p-new", content="重新规划...")
        prev = None
        for i in range(1, self.n + 1):
            yield AgentEvent(type="plan_step", plan_id="p-new", plan_step_id=f"s{i}",
                             content=f"第{i}步", dependencies=[prev] if prev else [])
            prev = f"s{i}"
        yield AgentEvent(type="plan_complete", plan_id="p-new", plan_goal="目标", content="ok")


class _ReplanReflector:
    def __init__(self):
        self.calls = 0

    async def reflect(self, plan, results, ctx=None):
        self.calls += 1
        yield AgentEvent(type="reflection", reflection_result="replan",
                         content="缺口：需要更多数据", plan_id=plan.id)


class _PassReviewer:
    def __init__(self):
        self.calls = 0

    async def review(self, plan, results, ctx=None):
        self.calls += 1
        return {"issues_found": [], "recommendation": "pass"}


class _SimulatedCrash(BaseException):
    """模拟进程死亡：BaseException（不会被 executor/loop 的 except Exception 吞掉）。"""


def _completed_spy(executed: list[str], yield_chunk: bool = True):
    async def _spy(*, step, plan, history, organization_id, user_id,
                   enable_thinking, ctx=None):
        executed.append(step.id)
        if yield_chunk:
            yield AgentEvent(type="chunk", content=f"结果-{step.id}")

    return _spy


def _crashed_payload(*, steps, tokens=0, iterations=0, partial="", replan=0,
                     include_plan=True, query="原始问题"):
    ctx = ExecutionContext(query=query, task_id="t-1")
    ctx.total_tokens = tokens
    if iterations:
        ctx.artifacts["iterations_used"] = iterations
    plan = Plan(id="p1", goal="目标", steps=steps,
                completed_steps=sum(1 for s in steps if s.status == "completed"))
    return {
        "v": 1,
        "session_id": "sess-9",
        "ctx": ctx.to_checkpoint_dict(),
        "plan": plan.to_checkpoint_dict() if include_plan else None,
        "history": [{"role": "user", "content": query}],
        "config": AgentConfig(enable_memory=False, enable_thinking=False,
                              enable_skills=False, enable_reflection=False).to_dict(),
        "run": {"execution_mode": "dag", "replan_attempts": replan, "replan_feedback": ""},
        "partial_output": partial,
    }


def _make_loop(*, config=None, executed=None, planner=None):
    cfg = config or AgentConfig(
        enable_memory=False, enable_thinking=False, enable_skills=False,
        enable_reflection=False, enable_experience=False,
    )
    loop = PERAgentLoop(MagicMock(), cfg, organization_id=1, user_id=0)
    memory = _FakeMemory()
    loop.memory = memory
    executed = executed if executed is not None else []
    executor = Executor(AsyncMock(), cfg, memory)
    executor._execute_step_with_retry = _completed_spy(executed)
    loop._executor = executor
    loop._planner = planner if planner is not None else _CountingPlanner()
    return loop, memory, executed


@pytest.mark.asyncio
async def test_resume_skips_planning_and_only_runs_remaining_steps():
    """变异点：删 resume 分支（照常规划）→ planner.calls != 0 且已完成的 s1/s2
    被重跑 → 两条断言都红。"""
    steps = [
        PlanStep(id="s1", description="一", status="completed", result="结果-s1"),
        PlanStep(id="s2", description="二", dependencies=["s1"], status="completed",
                 result="结果-s2"),
        PlanStep(id="s3", description="三", dependencies=["s2"]),
    ]
    payload = _crashed_payload(steps=steps, iterations=2, partial="结果-s1结果-s2")
    planner = _CountingPlanner()
    loop, memory, executed = _make_loop(planner=planner)

    events = [e async for e in loop.run("原始问题", task_id="t-1", resume=payload)]

    assert planner.calls == 0, "有 plan 的 resume 不得重新规划"
    assert executed == ["s3"], "已完成步骤绝不重跑；从断点之后的剩余步骤续跑"
    assert any(e.type == "done" for e in events)
    # WorkingMemory 回灌：s3 的 _get_previous_results(["s2"]) 能读到结果
    assert memory.results.get("s2", {}).get("result") == "结果-s2"
    # 最终答案 = 快照前缀 + 续跑新增（不重复前缀）
    assert memory.interactions == [("原始问题", "结果-s1结果-s2结果-s3")]


@pytest.mark.asyncio
async def test_resume_without_plan_falls_back_to_planning():
    """崩溃于规划阶段（快照无 plan）：在同一 task 内从规划重跑，仍幂等。
    变异点：resume 分支无条件跳过规划 → planner.calls == 0 → 红（或断了恢复链）。"""
    payload = _crashed_payload(steps=[], include_plan=False)
    planner = _CountingPlanner(n=1)
    loop, memory, executed = _make_loop(planner=planner)

    events = [e async for e in loop.run("原始问题", task_id="t-1", resume=payload)]

    assert planner.calls == 1
    assert executed == ["s1"]
    assert any(e.type == "done" for e in events)


@pytest.mark.asyncio
async def test_resume_resets_inflight_running_step_and_finishes():
    """崩溃时"在途"（running）的步骤必须复位为 pending 重跑一次。
    变异点：删 running→pending 复位 → executor 死锁把 s3 跳过、s2 永不执行 → 红。"""
    steps = [
        PlanStep(id="s1", description="一", status="completed", result="R1"),
        PlanStep(id="s2", description="二", dependencies=["s1"], status="running"),
        PlanStep(id="s3", description="三", dependencies=["s2"]),
    ]
    payload = _crashed_payload(steps=steps, iterations=1)
    loop, _, executed = _make_loop()

    events = [e async for e in loop.run("原始问题", task_id="t-1", resume=payload)]

    assert executed == ["s2", "s3"], "在途步骤重跑一次后继续"
    assert any(e.type == "done" for e in events)


@pytest.mark.asyncio
async def test_resume_continues_token_budget_from_snapshot():
    """#203 交互：中断前预算已尽 → resume 后剩余步骤直接熔断跳过。
    变异点：from_checkpoint_dict 丢 total_tokens → 预算归零 → s2 被派发 → 红。"""
    steps = [
        PlanStep(id="s1", description="一", status="completed", result="R1"),
        PlanStep(id="s2", description="二", dependencies=["s1"]),
    ]
    payload = _crashed_payload(steps=steps, tokens=100)
    cfg = AgentConfig(enable_memory=False, enable_thinking=False, enable_skills=False,
                      enable_reflection=False, enable_experience=False, max_total_tokens=100)
    loop, _, executed = _make_loop(config=cfg)

    events = [e async for e in loop.run("原始问题", task_id="t-1", resume=payload)]

    assert executed == [], "预算已尽：不得再派发任何步骤"
    assert any(e.type == "thinking" and "token 预算" in e.content for e in events)
    assert any(e.type == "done" for e in events)


@pytest.mark.asyncio
async def test_resume_continues_iteration_budget_from_snapshot():
    """#203 ① 交互：iterations_used 随 artifacts 恢复。
    变异点：快照不存 artifacts → 迭代计数归零 → s2 被派发 → 红。"""
    steps = [
        PlanStep(id="s1", description="一", status="completed", result="R1"),
        PlanStep(id="s2", description="二", dependencies=["s1"]),
    ]
    payload = _crashed_payload(steps=steps, iterations=2)
    cfg = AgentConfig(enable_memory=False, enable_thinking=False, enable_skills=False,
                      enable_reflection=False, enable_experience=False, max_iterations=2)
    loop, _, executed = _make_loop(config=cfg)

    events = [e async for e in loop.run("原始问题", task_id="t-1", resume=payload)]

    assert executed == []
    assert any(e.type == "thinking" and "迭代上限" in e.content for e in events)


@pytest.mark.asyncio
async def test_resume_continues_replan_budget_from_snapshot():
    """replan 计数延续：快照 replan_attempts=1 已达 MAX_REPLANS_PER_RUN → 不再重规划。
    变异点：loop 不恢复 run.replan_attempts → 反思判定 replan 会真的换新计划
    （planner.calls == 1）→ 断言红。"""
    steps = [PlanStep(id="s1", description="一", status="completed", result="R1")]
    payload = _crashed_payload(steps=steps, replan=1)
    cfg = AgentConfig(enable_memory=False, enable_thinking=False, enable_skills=False,
                      enable_reflection=True, enable_experience=False)
    planner = _CountingPlanner()
    loop, _, executed = _make_loop(config=cfg, planner=planner)
    reflector, reviewer = _ReplanReflector(), _PassReviewer()
    loop._reflector = reflector
    loop._reviewer = reviewer

    events = [e async for e in loop.run("原始问题", task_id="t-1", resume=payload)]

    assert reflector.calls == 1
    assert planner.calls == 0, "replan 额度已在快照里用掉，恢复后不得再用"
    assert any(e.type == "thinking" and "重新规划次数已达上限" in e.content for e in events)
    assert any(d.action == "replan_exhausted" for d in loop.checkpoint_live["ctx"].decisions)
    assert any(e.type == "done" for e in events)


@pytest.mark.asyncio
async def test_resume_uses_frozen_history_from_snapshot():
    """history 在首快照时冻结：resume 不重读会话新消息（否则等于换上下文续跑）。"""
    steps = [PlanStep(id="s1", description="一", status="completed", result="R1"),
             PlanStep(id="s2", description="二", dependencies=["s1"])]
    payload = _crashed_payload(steps=steps, partial="R1")
    loop, memory, executed = _make_loop()

    [e async for e in loop.run("原始问题", history=[{"role": "user", "content": "新的消息"}],
                               task_id="t-1", resume=payload)]

    assert executed == ["s2"]
    assert memory.interactions[0][1] == "R1结果-s2"
