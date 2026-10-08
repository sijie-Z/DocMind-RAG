"""issue #201 最小版：checkpoint 触发点接线（T1–T4）+ 静态守卫。

判别性来源：`checkpoint.save_checkpoint` 被换成一个"在调用时刻拍快照"的记录器——
断言每条触发线的顺序与**当次载荷**（plan 状态、partial_output、run_state）；
把任一触发点删掉（或 executor 删掉 on_step_done 调用），对应用例变红。

覆盖：
- T1 after_planning / T2 after_each_step（每步一次，顺序路径）/ T3
  after_execute_turn / T4 on_finish；
- 未接任务（无 task_id）与 kill-switch（enable_checkpoint=False）时零落盘；
- 落盘失败绝不阻塞 run（与 ctx.save() 同风格）；
- 静态守卫：AppTask.status 只能经 transition()；裸 SQL 不得用小写枚举值。
"""

import ast
import pathlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent import checkpoint
from app.agent.config import AgentConfig
from app.agent.events import AgentEvent
from app.agent.exec_context import ExecutionContext
from app.agent.executor import Executor
from app.agent.loop import PERAgentLoop
from app.agent.planner import Plan, PlanStep

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[2]
APP_DIR = BACKEND_DIR / "app"

_TRIGGERS = ("after_planning", "after_each_step", "after_execute_turn", "on_finish")


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """隔离与 checkpoint 无关的副作用：replay 落盘与经验回写。"""
    async def _save(self, ttl=86400 * 7):
        return False

    monkeypatch.setattr(ExecutionContext, "save", _save)
    monkeypatch.setattr(PERAgentLoop, "_write_experiences", AsyncMock())


@pytest.fixture
def saver(monkeypatch):
    """记录 checkpoint.save_checkpoint 的调用（载荷在调用时刻取样，防活对象后来被改）。"""
    calls: list[dict] = []

    async def _fake_save(**kwargs):
        plan = kwargs.get("plan")
        calls.append({
            "task_id": kwargs.get("task_id"),
            "trigger": kwargs.get("trigger"),
            "partial_output": kwargs.get("partial_output"),
            "plan_statuses": [s.status for s in plan.steps] if plan is not None else None,
            "run": dict(kwargs.get("run_state") or {}),
            "session_id": kwargs.get("session_id"),
        })
        return True

    monkeypatch.setattr(checkpoint, "save_checkpoint", _fake_save)
    return calls


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


class _ChainPlanner:
    """一步依赖下一步的计划；记录调用次数。"""

    def __init__(self, n=2):
        self.calls = 0
        self.n = n

    async def plan(self, query, history=None, memory_context="", ctx=None, replan_feedback=""):
        self.calls += 1
        yield AgentEvent(type="plan_start", plan_id="plan-1", content="制定计划...")
        prev = None
        for i in range(1, self.n + 1):
            yield AgentEvent(type="plan_step", plan_id="plan-1", plan_step_id=f"s{i}",
                             content=f"第{i}步", dependencies=[prev] if prev else [])
            prev = f"s{i}"
        yield AgentEvent(type="plan_complete", plan_id="plan-1", plan_goal="目标",
                         content="计划完成")


def _completed_spy(calls: list[str]):
    """替身 _execute_step_with_retry：记录被执行步骤并流出一个 chunk。
    （_execute_single_step 的终态回写/on_step_done 仍走真实代码。）"""
    async def _spy(*, step, plan, history, organization_id, user_id,
                   enable_thinking, ctx=None):
        calls.append(step.id)
        yield AgentEvent(type="chunk", content=f"结果-{step.id}")

    return _spy


def _make_loop(*, n=2, config=None):
    cfg = config or AgentConfig(
        enable_memory=False, enable_thinking=False, enable_skills=False,
        enable_reflection=False, enable_experience=False,
    )
    loop = PERAgentLoop(MagicMock(), cfg, organization_id=1, user_id=0)
    loop.memory = _FakeMemory()
    loop._planner = _ChainPlanner(n)
    executed: list[str] = []
    executor = Executor(AsyncMock(), cfg, loop.memory)
    executor._execute_step_with_retry = _completed_spy(executed)
    loop._executor = executor
    return loop, loop._planner, executed


@pytest.mark.asyncio
async def test_t1_t2_t3_t4_fire_in_order_with_correct_payloads(saver):
    """顺序与当次载荷逐条钉住。变异点：删任一触发点（或 executor 的
    on_step_done 调用）→ 序列错位/缺项 → 红。"""
    loop, planner, executed = _make_loop(n=2)

    events = [e async for e in loop.run("问题", task_id="t-1", session_id="sess-1")]

    assert planner.calls == 1
    assert executed == ["s1", "s2"]
    assert [c["trigger"] for c in saver] == [
        "after_planning", "after_each_step", "after_each_step", "after_execute_turn", "on_finish",
    ]
    assert all(c["task_id"] == "t-1" and c["session_id"] == "sess-1" for c in saver)

    t1 = saver[0]
    assert t1["plan_statuses"] == ["pending", "pending"], "T1 在规划完成、执行之前"
    assert saver[1]["plan_statuses"] == ["completed", "pending"], "T2 在逐步终态之后"
    assert "结果-s1" in saver[1]["partial_output"]
    assert saver[2]["plan_statuses"] == ["completed", "completed"]
    assert saver[2]["partial_output"] == "结果-s1结果-s2"
    assert saver[3]["trigger"] == "after_execute_turn"
    assert saver[3]["run"]["execution_mode"] == "dag"
    assert any(e.type == "done" for e in events)


@pytest.mark.asyncio
async def test_no_task_id_means_no_checkpoint_at_all(saver):
    loop, _, _ = _make_loop(n=1)
    events = [e async for e in loop.run("问题")]
    assert saver == []
    assert loop.checkpoint_live is None
    assert any(e.type == "done" for e in events)


@pytest.mark.asyncio
async def test_kill_switch_disables_checkpoint(saver):
    cfg = AgentConfig(
        enable_memory=False, enable_thinking=False, enable_skills=False,
        enable_reflection=False, enable_experience=False, enable_checkpoint=False,
    )
    loop, _, _ = _make_loop(n=1, config=cfg)
    [e async for e in loop.run("问题", task_id="t-1")]
    assert saver == [], "kill-switch 关闭时必须零落盘（完全退回旧行为）"
    assert loop.checkpoint_live is None


@pytest.mark.asyncio
async def test_checkpoint_failure_never_blocks_the_run(monkeypatch):
    async def _explode(**kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(checkpoint, "save_checkpoint", _explode)
    loop, _, executed = _make_loop(n=2)

    events = [e async for e in loop.run("问题", task_id="t-1")]

    assert executed == ["s1", "s2"], "落盘失败不得改变执行语义"
    assert any(e.type == "done" for e in events)


@pytest.mark.asyncio
async def test_executor_invokes_on_step_done_once_per_terminal_step():
    """变异点：删 _execute_single_step 里的 `await on_step_done(step)` → done == [] → 红。"""
    cfg = AgentConfig(enable_planning=False, enable_reflection=False)
    memory = _FakeMemory()
    executor = Executor(AsyncMock(), cfg, memory)
    plan = Plan(id="p1", goal="g", steps=[
        PlanStep(id="s1", description="一"),
        PlanStep(id="s2", description="二", dependencies=["s1"]),
    ])
    done: list[tuple[str, str]] = []

    async def _on_step_done(step):
        done.append((step.id, step.status))

    executor._execute_step_with_retry = _completed_spy([])
    [e async for e in executor.execute(plan, ctx=ExecutionContext(query="q"),
                                       on_step_done=_on_step_done)]

    assert done == [("s1", "completed"), ("s2", "completed")]


@pytest.mark.asyncio
async def test_executor_parallel_path_invokes_on_step_done():
    """变异点：删 _execute_steps_parallel 里的 on_step_done 调用 → done 缺一步 → 红。"""
    cfg = AgentConfig(enable_planning=False, enable_reflection=False)
    executor = Executor(AsyncMock(), cfg, _FakeMemory())
    plan = Plan(id="p1", goal="g", steps=[
        PlanStep(id="s1", description="a"), PlanStep(id="s2", description="b"),
    ])  # 无依赖 → 同批并行派发
    done: list[str] = []

    async def _on_step_done(step):
        done.append(step.id)

    executor._execute_step_with_retry = _completed_spy([])
    [e async for e in executor.execute(plan, on_step_done=_on_step_done)]

    assert sorted(done) == ["s1", "s2"]


def test_agent_event_task_id_is_additive_only():
    """task_id 是可选盖章字段：默认不进 SSE（旧输出一字节不变）。"""
    e = AgentEvent(type="done")
    assert "task_id" not in e.to_sse_dict()
    e.task_id = "t-1"
    assert e.to_sse_dict()["task_id"] == "t-1"


# ── 静态守卫（报告测试 7：防再踩 004/006 的枚举坑）────────────────────────────


class TestAgentTaskStatusDiscipline:
    def _app_py_files(self):
        return sorted(APP_DIR.rglob("*.py"))

    def test_no_direct_status_assignment_outside_transition(self):
        """AgentTask 的状态翻转只允许经 transition()（构造时的显式初始值除外）。
        `task.status = ...` / `agent_task.status = ...` 直接赋值零容忍。"""
        offenders: list[str] = []
        for path in self._app_py_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Assign):
                    continue
                for target in node.targets:
                    if (isinstance(target, ast.Attribute) and target.attr == "status"
                            and isinstance(target.value, ast.Name)
                            and target.value.id in {"task", "agent_task", "new_task"}):
                        offenders.append(f"{path.relative_to(BACKEND_DIR)}:{node.lineno}")
        assert not offenders, "任务状态必须经 AgentTask.transition(): " + "; ".join(offenders)

    def test_no_raw_sql_with_lowercase_agent_task_status(self):
        """裸 SQL 不得用小写枚举值：004 建的是小写、006 已对齐成大写成员名，
        残余的小写字面量必然对不上库里存的值（静默查不到/改不动）。"""
        offenders: list[str] = []
        lowercase = ("pending", "running", "waiting_tool", "waiting_human",
                     "failed", "completed", "abandoned")
        for path in self._app_py_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    raw = node.value
                    if "agent_tasks" not in raw:
                        continue
                    if any(f"'{v}'" in raw or f'"{v}"' in raw for v in lowercase):
                        offenders.append(f"{path.relative_to(BACKEND_DIR)}:{node.lineno}")
        assert not offenders, "agent_tasks 的裸 SQL 只允许大写成员名: " + "; ".join(offenders)
