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

import logging
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
            yield AgentEvent(
                type="plan_step",
                plan_id="p-new",
                plan_step_id=f"s{i}",
                content=f"第{i}步",
                dependencies=[prev] if prev else [],
            )
            prev = f"s{i}"
        yield AgentEvent(type="plan_complete", plan_id="p-new", plan_goal="目标", content="ok")


class _ReplanReflector:
    def __init__(self):
        self.calls = 0

    async def reflect(self, plan, results, ctx=None):
        self.calls += 1
        yield AgentEvent(
            type="reflection",
            reflection_result="replan",
            content="缺口：需要更多数据",
            plan_id=plan.id,
        )


class _PassReviewer:
    def __init__(self):
        self.calls = 0

    async def review(self, plan, results, ctx=None):
        self.calls += 1
        return {"issues_found": [], "recommendation": "pass"}


class _SimulatedCrash(BaseException):
    """模拟进程死亡：BaseException（不会被 executor/loop 的 except Exception 吞掉）。"""


def _completed_spy(executed: list[str], yield_chunk: bool = True):
    async def _spy(*, step, plan, history, organization_id, user_id, enable_thinking, ctx=None):
        executed.append(step.id)
        if yield_chunk:
            yield AgentEvent(type="chunk", content=f"结果-{step.id}")

    return _spy


def _crashed_payload(
    *, steps, tokens=0, iterations=0, partial="", replan=0, include_plan=True, query="原始问题"
):
    ctx = ExecutionContext(query=query, task_id="t-1")
    ctx.total_tokens = tokens
    if iterations:
        ctx.artifacts["iterations_used"] = iterations
    plan = Plan(
        id="p1",
        goal="目标",
        steps=steps,
        completed_steps=sum(1 for s in steps if s.status == "completed"),
    )
    return {
        "v": 1,
        "session_id": "sess-9",
        "ctx": ctx.to_checkpoint_dict(),
        "plan": plan.to_checkpoint_dict() if include_plan else None,
        "history": [{"role": "user", "content": query}],
        "config": AgentConfig(
            enable_memory=False, enable_thinking=False, enable_skills=False, enable_reflection=False
        ).to_dict(),
        "run": {"execution_mode": "dag", "replan_attempts": replan, "replan_feedback": ""},
        "partial_output": partial,
    }


def _make_loop(*, config=None, executed=None, planner=None):
    cfg = config or AgentConfig(
        enable_memory=False,
        enable_thinking=False,
        enable_skills=False,
        enable_reflection=False,
        enable_experience=False,
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
        PlanStep(
            id="s2", description="二", dependencies=["s1"], status="completed", result="结果-s2"
        ),
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
    cfg = AgentConfig(
        enable_memory=False,
        enable_thinking=False,
        enable_skills=False,
        enable_reflection=False,
        enable_experience=False,
        max_total_tokens=100,
    )
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
    cfg = AgentConfig(
        enable_memory=False,
        enable_thinking=False,
        enable_skills=False,
        enable_reflection=False,
        enable_experience=False,
        max_iterations=2,
    )
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
    cfg = AgentConfig(
        enable_memory=False,
        enable_thinking=False,
        enable_skills=False,
        enable_reflection=True,
        enable_experience=False,
    )
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
    steps = [
        PlanStep(id="s1", description="一", status="completed", result="R1"),
        PlanStep(id="s2", description="二", dependencies=["s1"]),
    ]
    payload = _crashed_payload(steps=steps, partial="R1")
    loop, memory, executed = _make_loop()

    [
        e
        async for e in loop.run(
            "原始问题",
            history=[{"role": "user", "content": "新的消息"}],
            task_id="t-1",
            resume=payload,
        )
    ]

    assert executed == ["s2"]
    assert memory.interactions[0][1] == "R1结果-s2"


@pytest.mark.parametrize(
    "corrupt",
    [
        lambda p: p["plan"].__setitem__("steps", "boom"),
        lambda p: p["ctx"].__setitem__("artifacts", [1, 2]),
    ],
    ids=["plan-steps-not-list", "ctx-artifacts-not-mapping"],
)
@pytest.mark.asyncio
async def test_deep_corrupt_snapshot_degrades_to_fresh_planning(corrupt):
    """R1（issue #278）：合法 JSON、深结构坏形状的快照不得成为毒丸——重建段
    任何异常一律按"损坏即无快照"安全降级：从规划阶段干净重跑、全步执行、不崩，
    损坏快照的残留字段（partial 前缀等）一律不进新 run。

    实测形态：plan.steps 非列表 → Plan.from_checkpoint_dict 迭代字符串抛
    AttributeError；ctx.artifacts 非映射 → ExecutionContext.from_checkpoint_dict
    抛 TypeError。变异点：去掉 loop.run 重建段的 try/except（还原抛异常形态）→
    本例以 AttributeError/TypeError 失败 → 红。
    """
    steps = [
        PlanStep(id="s1", description="一", status="completed", result="R1"),
        PlanStep(id="s2", description="二", dependencies=["s1"]),
    ]
    payload = _crashed_payload(steps=steps, partial="损坏前缀")
    corrupt(payload)
    planner = _CountingPlanner(n=2)
    loop, memory, executed = _make_loop(planner=planner)

    events = [e async for e in loop.run("原始问题", task_id="t-1", resume=payload)]

    assert planner.calls == 1, "损坏快照 = 无快照：必须重新规划（绝不半信半疑地续跑）"
    assert executed == ["s1", "s2"], "干净重跑全部步骤"
    assert any(e.type == "done" for e in events)
    assert memory.interactions == [("原始问题", "结果-s1结果-s2")], (
        "损坏快照的残留字段（partial 前缀）不得混入新 run 的最终答案"
    )


@pytest.mark.parametrize(
    "corrupt",
    [
        lambda p: p["ctx"]["artifacts"].__setitem__("iterations_used", "很多"),
        lambda p: p.__setitem__("partial_output", 123),
        lambda p: p.__setitem__("history", "boom"),
        lambda p: p.__setitem__("history", [42, {"role": "user", "content": "真实消息"}]),
    ],
    ids=[
        "artifacts-iterations-not-int",
        "partial-output-not-str",
        "history-not-list",
        "history-element-not-mapping",
    ],
)
@pytest.mark.asyncio
async def test_value_level_drift_degrades_per_value_not_poison(corrupt, caplog):
    """#287/R1 值级漂移（与"深结构损坏"同族，但坏的是**值形状**而非结构）：
    重建段消费的每个值位过类型闸门——坏值降级为默认并留 warning，绝不炸、
    绝不流入运行态。

    修复前实测形态（每种都"每次 resume 必炸"）：
    - artifacts.iterations_used="很多" → executor `"很多" >= max_iterations`
      TypeError（派发前，任何 LLM 调用之前）；
    - partial_output=123 → 收尾拼接 `123 + pass_output` TypeError；
    - history="boom" / 含非映射元素 → 元素访问 `msg.get` AttributeError。

    变异点：去掉重建段对应值位闸门（还原坏值直通）→ 本参数组以对应异常红。
    """
    steps = [
        PlanStep(id="s1", description="一", status="completed", result="R1"),
        PlanStep(id="s2", description="二", dependencies=["s1"]),
    ]
    payload = _crashed_payload(steps=steps)
    corrupt(payload)
    planner = _CountingPlanner()
    loop, memory, executed = _make_loop(planner=planner)

    with caplog.at_level(logging.WARNING):
        events = [e async for e in loop.run("原始问题", task_id="t-1", resume=payload)]

    assert planner.calls == 0, "值级坏数据不得改变'复用快照计划'这一语义"
    assert executed == ["s2"], "剩余步骤照常续跑（坏值已降级，不得毒丸）"
    assert any(e.type == "done" for e in events)
    assert memory.interactions == [("原始问题", "结果-s2")], (
        "坏值不得混入最终答案（坏 partial 前缀降级为空串）"
    )
    ctx = loop.checkpoint_live["ctx"]
    assert isinstance(ctx.artifacts.get("iterations_used", 0), int), (
        "运行态里的迭代计数必须是规整后的整型（写回快照的值同源）"
    )
    live_history = loop.checkpoint_live["history"]
    assert live_history is None or all(isinstance(m, dict) for m in live_history), (
        "冻结历史必须是映射列表（坏值降级，绝不流入运行态/快照）"
    )
    assert any("值级漂移" in r.message for r in caplog.records), "降级必须留痕（warning：值级漂移）"


@pytest.mark.parametrize(
    "corrupt, shape_ok",
    [
        (
            lambda p: p["ctx"]["artifacts"].__setitem__("review", "boom"),
            lambda ctx: "review" not in ctx.artifacts,
        ),
    ],
    ids=["artifacts-review-not-mapping"],
)
@pytest.mark.asyncio
async def test_artifact_consumer_key_drift_degrades_not_poison(corrupt, shape_ok, caplog):
    """#319（F-CRIT-1 残族）：artifacts **消费键**形状漂移的全链——`review="boom"`
    修前：resume 收尾 run_report `review_artifact.get` AttributeError（全链
    实证 service.py:233 → loop.py:830 → run_report.py:78），错误兜底把坏值
    原样回写、每次 resume 复炸。

    修后：降级不炸、跑完（done + run_report 事件都在）、坏值不进运行态。

    变异点：去掉读侧映射契约（_ARTIFACT_MAPPING_SPECS）→ 坏值原样进运行态 →
    shape 断言红（消费侧防线保证"不炸"面独立成立；两层同时拆才是修前的
    AttributeError 全链形态，见修前红基线）。（bool 拒斥的判别钉在单元层：
    roundtrip 测试的读/写两侧断言——引擎层 bool 会被运行中的计数回写掩盖，
    不作误伤性断言。）
    """
    steps = [
        PlanStep(id="s1", description="一", status="completed", result="R1"),
        PlanStep(id="s2", description="二", dependencies=["s1"]),
    ]
    payload = _crashed_payload(steps=steps)
    corrupt(payload)
    loop, memory, executed = _make_loop()

    with caplog.at_level(logging.WARNING):
        events = [e async for e in loop.run("原始问题", task_id="t-1", resume=payload)]

    assert executed == ["s2"], "剩余步骤照常续跑（坏值已降级，不得毒丸）"
    assert any(e.type == "done" for e in events)
    assert any(e.type == "run_report" for e in events), (
        "收尾 run_report 必须照常产出（修前在这条路径上 AttributeError）"
    )
    ctx = loop.checkpoint_live["ctx"]
    assert shape_ok(ctx), f"坏值不得进运行态（artifacts={ctx.artifacts!r}）"
    assert any("值级漂移" in r.message for r in caplog.records), "降级必须留痕"


@pytest.mark.parametrize(
    "corrupt, shape_ok",
    [
        (
            lambda p: p["ctx"]["artifacts"].__setitem__("quality_gate", "boom"),
            lambda ctx: "quality_gate" not in ctx.artifacts,
        ),
        (
            lambda p: p["ctx"]["artifacts"].__setitem__(
                "quality_gate", {"passed": True, "fatal_issues": 123}
            ),
            lambda ctx: ctx.artifacts.get("quality_gate") == {"passed": True},
        ),
    ],
    ids=["quality-gate-not-mapping", "quality-gate-fatal-not-list"],
)
@pytest.mark.asyncio
async def test_quality_gate_tail_consumption_drift_degrades_not_poison(corrupt, shape_ok, caplog):
    """#319：quality_gate 消费点（run_report `qg_artifact.get("fatal_issues")`
    → `list(...)`）在正常执行轮里被 Phase 2b 覆写掩护（每轮必然重写该键），
    但**空步骤 plan**（如恢复出的计划不含待执行步）跳过 Phase 2b 后直接到达
    收尾投影——坏形状在这里炸（`"boom".get` AttributeError / `list(123)`
    TypeError），且写侧"以校验后值为准"会把坏值续给下一次 resume。

    修后：读侧闸门在重建时即降级（整键删 / 删子键），run_report 照常产出。

    变异点：去掉映射契约 → not-mapping 组红；去掉 fatal_issues 子键契约 →
    fatal-not-list 组红（坏值原样进运行态 → shape 断言红；消费侧防线在，
    "不炸"面由防线独立成立——两层同时拆才是修前的 TypeError/AttributeError
    全链形态，见修前红基线）。
    """
    payload = _crashed_payload(steps=[])  # 空步骤 plan：Phase 2b/3/3b 全跳过
    corrupt(payload)
    loop, memory, executed = _make_loop()

    with caplog.at_level(logging.WARNING):
        events = [e async for e in loop.run("原始问题", task_id="t-1", resume=payload)]

    assert executed == [], "空计划：无步骤可续跑"
    assert any(e.type == "done" for e in events)
    assert any(e.type == "run_report" for e in events), (
        "收尾 run_report 必须照常产出（修前在这条路径上 AttributeError/TypeError）"
    )
    ctx = loop.checkpoint_live["ctx"]
    assert shape_ok(ctx), f"坏值不得进运行态（artifacts={ctx.artifacts!r}）"
    assert any("值级漂移" in r.message for r in caplog.records), "降级必须留痕"


@pytest.mark.asyncio
async def test_resume_history_partial_trust_keeps_good_elements(caplog):
    """#319 覆盖缺口（评审 MV5）：history 坏元素语义 = **部分信任**（过滤：
    丢弃非映射元素、按序保留好元素），不是整列表丢弃。理由：坏元素从不是
    "合法的空历史"——单元素损坏不应把其余（至多 HISTORY_CAP=50 条）好消息
    陪葬（整弃会让 resume 静默换上下文）；而消费点（loop/executor 的
    `msg.get`）只吃映射，过滤后的形态即契约形态。

    变异点：把过滤改成整弃（frozen_history = None）→ live history 断言红；
    改成"不过滤"（坏元素直通）→ 元素断言红。
    """
    steps = [
        PlanStep(id="s1", description="一", status="completed", result="R1"),
        PlanStep(id="s2", description="二", dependencies=["s1"]),
    ]
    payload = _crashed_payload(steps=steps)
    payload["history"] = [
        {"role": "user", "content": "好1"},
        "boom",
        42,
        {"role": "assistant", "content": "好2"},
    ]
    loop, memory, executed = _make_loop()

    with caplog.at_level(logging.WARNING):
        events = [e async for e in loop.run("原始问题", task_id="t-1", resume=payload)]

    assert executed == ["s2"]
    assert any(e.type == "done" for e in events)
    live_history = loop.checkpoint_live["history"]
    assert live_history == [
        {"role": "user", "content": "好1"},
        {"role": "assistant", "content": "好2"},
    ], "部分信任：坏元素丢弃、好元素按序保留（不是整列表丢弃）"
    assert any("坏元素丢弃" in r.message for r in caplog.records), "过滤必须留痕"
