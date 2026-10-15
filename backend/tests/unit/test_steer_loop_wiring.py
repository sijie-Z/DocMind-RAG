"""运行中转向（steering）—— loop 接线判别：步边界消费 → 下一步提示词生效。

被测契约（loop._consume_steering + run 的两处消费点）：
- 任务 running 中收到 steer：**不打断当前步骤**，在下一个步边界消费——
  steer_applied 事件紧跟步骤完成事件、先于下一步开始（变异点：把消费挪到
  run 末尾/不去消费 → 事件序与提示词断言红）；
- 注入机制与 HITL"改指令续跑"完全同源：executor.human_guidance → 步骤
  system prompt 的"## 人工指导"段；指导对**后续所有步骤**持续有效；
- 连续多条保序（steer_applied 顺序 = 提示词内出现顺序）；
- waiting_approval 期间入队的 steer：resume 的 run 起点 drain 并入引导；
- 消费即持久化：drain 后随 after_steer 落一次快照（run_state.steering）。

驱动方式：真实 Executor/loop.run + 假 LLM 客户端（捕获每步 messages）+
假 planner（链式步骤）+ sqlite 任务行 + 手写 _FakeRedis。逐事件手动拉取
生成器，在 s1 完成事件与下一事件之间完成"运行中发指导"的时序复刻。
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — 注册全部表，供 create_all
from app.agent import checkpoint, steering
from app.agent.config import AgentConfig
from app.agent.events import AgentEvent
from app.agent.exec_context import ExecutionContext
from app.agent.loop import PERAgentLoop
from app.agent.planner import Plan, PlanStep
from app.core.database import Base
from app.models.agent_task import AgentTask, TaskStatus
from app.rag.retriever import HybridRetriever

STEER = "请只查 2024 年之后的文档"


# ── 假件 ─────────────────────────────────────────────────────────────────────


class _FakeRedis:
    """字符串面 + 列表面（见 test_steering_queue 同款；本文件自持一份，
    与既有 HITL 测试"每文件一套替身"的仓库惯例一致）。"""

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

    async def get_context_for_query(self, query):
        return ""

    async def record_experience(self, **kwargs):
        pass

    async def record_interaction(self, user_input, assistant_response):
        pass


class _ChainPlanner:
    """s1→s2→…→sn 链式计划（与 test_checkpoint_wiring 同款）。"""

    def __init__(self, n=3):
        self.calls = 0
        self.n = n

    async def plan(self, query, history=None, memory_context="", ctx=None, replan_feedback=""):
        self.calls += 1
        yield AgentEvent(type="plan_start", plan_id="plan-1", content="制定计划...")
        prev = None
        for i in range(1, self.n + 1):
            yield AgentEvent(
                type="plan_step", plan_id="plan-1", plan_step_id=f"s{i}",
                content=f"第{i}步", dependencies=[prev] if prev else [],
            )
            prev = f"s{i}"
        yield AgentEvent(
            type="plan_complete", plan_id="plan-1", plan_goal="目标", content="计划完成",
        )


def _capturing_client(captured: list):
    """假 LLM：记录每次调用的 messages，返回纯文本结论（无工具调用）。"""

    async def _create(*, messages, tools=None, **kwargs):
        captured.append(messages)
        msg = SimpleNamespace(content=f"结论-{len(captured)}", tool_calls=None, role="assistant")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=msg, finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
        )

    client = MagicMock()
    client.chat.completions.create = _create
    return client


def _cfg() -> AgentConfig:
    return AgentConfig(
        enable_memory=False, enable_reflection=False, enable_skills=False,
        enable_experience=False, enable_thinking=False, enable_planning=True,
    )


def _make_loop(client, *, n=3) -> PERAgentLoop:
    loop = PERAgentLoop(client, _cfg(), organization_id=1, user_id=0)
    loop.memory = _FakeMemory()
    loop._planner = _ChainPlanner(n)
    return loop


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    async def _save(self, ttl=86400 * 7):
        return False

    monkeypatch.setattr(ExecutionContext, "save", _save)
    from unittest.mock import AsyncMock

    monkeypatch.setattr(HybridRetriever, "get_embedding", AsyncMock(return_value=[]))


@pytest.fixture
async def db_factory(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'steer_loop.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(checkpoint, "AsyncSessionLocal", factory)
    yield factory
    await engine.dispose()


@pytest.fixture
def fake_redis(monkeypatch):
    r = _FakeRedis()
    monkeypatch.setattr(steering, "redis_client", r)
    monkeypatch.setattr(checkpoint, "redis_client", r)
    return r


async def _pull_until(agen, events: list, pred, limit: int = 300) -> bool:
    for _ in range(limit):
        try:
            ev = await agen.__anext__()
        except StopAsyncIteration:
            return False
        events.append(ev)
        if pred(ev):
            return True
    raise AssertionError("事件流未在预期步数内到达目标事件")


def _is_step_done(ev, step_id: str) -> bool:
    return (
        ev.type == "thinking"
        and ev.plan_step_status in ("completed", "failed")
        and ev.plan_step_id == step_id
    )


def _is_step_start(ev, step_id: str) -> bool:
    return ev.type == "thinking" and ev.plan_step_id == step_id and ev.content.startswith("执行步骤:")


async def _snapshot(engine_factory, task_id: str):
    async with engine_factory() as db:
        row = await db.get(AgentTask, task_id)
        return row.context_snapshot


# ── 用例 ─────────────────────────────────────────────────────────────────────


async def test_mid_run_steer_applies_at_next_boundary_not_current_step(fake_redis, db_factory):
    """running 中 steer：s1 的提示词不含指导（不打断当前步骤）；s1 完成事件
    之后紧接 steer_applied（先于 s2 开始）；s2/s3 提示词含指导且持续有效。

    变异点：删掉步边界消费分支 → 无 steer_applied、s2 提示词无指导 → 红。
    """
    tid = await checkpoint.create_task(user_id=1, organization_id=1, query="q")
    captured: list = []
    loop = _make_loop(_capturing_client(captured), n=3)

    events: list = []
    agen = loop.run("任务", task_id=tid)
    assert await _pull_until(agen, events, lambda e: _is_step_done(e, "s1"))

    # 时序复刻：位于 s1 完成、s2 未开始的"步边界"间隙（生成器挂在 yield 上）
    assert await steering.push_steer(tid, STEER) == 1
    rest: list = []
    async for ev in agen:
        rest.append(ev)
    all_events = events + rest

    types_steps = [(i, e) for i, e in enumerate(all_events)]
    idx_done1 = next(i for i, e in types_steps if _is_step_done(e, "s1"))
    idx_steer = next(
        i for i, e in types_steps if e.type == "steer_applied"
    )
    idx_next = next(i for i, e in types_steps if _is_step_start(e, "s2"))
    assert idx_done1 < idx_steer < idx_next, (
        "steer_applied 必须紧跟步边界、先于下一步开始"
    )
    applied = all_events[idx_steer]
    assert applied.content == STEER, "事件含消息摘要（原文）"
    assert applied.task_id == tid

    # 恰好一条 steer_applied（消费一次，不重复注入）
    assert sum(1 for e in all_events if e.type == "steer_applied") == 1

    # 提示词断言：s1 无（当前步骤不被打断）；s2、s3 有（对后续步骤持续有效）
    assert len(captured) == 3
    assert STEER not in captured[0][0]["content"]
    for later in (captured[1], captured[2]):
        system_content = later[0]["content"]
        assert "人工指导" in system_content
        assert STEER in system_content

    # 消费即持久化：after_steer 快照的 run_state.steering 已含指导
    snap = await _snapshot(db_factory, tid)
    assert snap["run"]["steering"] == [STEER]


async def test_multiple_steers_order_preserved(fake_redis, db_factory):
    """连续多条保序：steer_applied 顺序 = 发送顺序；提示词内出现顺序一致
    （变异点：把 drain 改成 LIFO / 反转注入 → 红）。"""
    tid = await checkpoint.create_task(user_id=1, organization_id=1, query="q")
    captured: list = []
    loop = _make_loop(_capturing_client(captured), n=2)

    msgs = ["第一条指导", "第二条指导", "第三条指导"]
    events: list = []
    agen = loop.run("任务", task_id=tid)
    assert await _pull_until(agen, events, lambda e: _is_step_done(e, "s1"))
    for m in msgs:
        assert await steering.push_steer(tid, m) > 0
    rest = [ev async for ev in agen]
    all_events = events + rest

    applied = [e.content for e in all_events if e.type == "steer_applied"]
    assert applied == msgs, "保序：发送顺序 = 事件顺序"

    s2_prompt = captured[1][0]["content"]
    positions = [s2_prompt.index(m) for m in msgs]
    assert positions == sorted(positions), "提示词内指导顺序与发送顺序一致"


async def test_steer_while_waiting_approval_merges_into_resume(fake_redis, db_factory):
    """waiting_approval 期间入队的 steer：resume 的 run 起点 drain 并入引导
    （与 modified_instruction 通道合流），续跑步骤的提示词携带指导。

    变异点：把 run 起点消费删掉 → resume 流无 steer_applied、提示词无指导 → 红。
    """
    tid = await checkpoint.create_task(user_id=1, organization_id=1, query="q")
    # 暂停前的快照：s1 已完成（不再重跑），s2 待执行——审批暂停恢复的等价形态
    plan = Plan(id="p1", goal="g", steps=[
        PlanStep(id="s1", description="一", status="completed", result="旧结果"),
        PlanStep(id="s2", description="二", dependencies=["s1"]),
    ])
    plan.completed_steps = 1
    payload = checkpoint.build_envelope(
        ctx=ExecutionContext(query="q"), plan=plan, trigger="before_approval",
        run_state={"pending_approval": {"tool_name": "fake_danger", "args_hash": "h"}},
    )
    assert await checkpoint.mark_waiting_approval(tid) is True

    # 等待审批期间发送指导（无活跃 run 消费 → 落队列）
    assert await steering.push_steer(tid, "重点关注风险章节") == 1

    captured: list = []
    loop = _make_loop(_capturing_client(captured), n=2)
    events = [ev async for ev in loop.run("q", task_id=tid, resume=payload)]

    applied = [e for e in events if e.type == "steer_applied"]
    assert applied and applied[0].content == "重点关注风险章节"
    assert applied[0].task_id == tid
    # resume 只跑 s2（s1 完成态不重跑）
    assert len(captured) == 1
    assert "重点关注风险章节" in captured[0][0]["content"]

    # 并入快照 run_state.steering（多暂停周期不丢）
    snap = await _snapshot(db_factory, tid)
    assert "重点关注风险章节" in snap["run"]["steering"]


async def test_resume_keeps_existing_snapshot_plan_when_steering_consumed(fake_redis, db_factory):
    """run 起点消费 steer 会落一次快照——快照里的计划必须保留（变异点：
    checkpoint_live["plan"] 未提前挂 resume 计划 → 本用例红：计划被抹成 null）。"""
    tid = await checkpoint.create_task(user_id=1, organization_id=1, query="q")
    plan = Plan(id="p1", goal="恢复目标", steps=[
        PlanStep(id="s1", description="一", status="completed", result="旧结果"),
        PlanStep(id="s2", description="二", dependencies=["s1"]),
    ])
    plan.completed_steps = 1
    payload = checkpoint.build_envelope(
        ctx=ExecutionContext(query="q"), plan=plan, trigger="on_disconnect",
        run_state={},
    )
    assert await steering.push_steer(tid, "并入的指导") == 1

    captured: list = []
    loop = _make_loop(_capturing_client(captured), n=2)
    events = [ev async for ev in loop.run("q", task_id=tid, resume=payload)]
    assert any(e.type == "steer_applied" for e in events)

    snap = await _snapshot(db_factory, tid)
    assert snap["plan"] is not None, "消费 steer 的落盘不得抹掉既有计划"
    assert snap["plan"]["goal"] == "恢复目标"
    assert snap["run"]["steering"] == ["并入的指导"]


async def test_terminal_run_leaves_queue_consistent(fake_redis, db_factory):
    """终止一致性：run 正常收尾（_save_checkpoint on_finish）+ 服务侧 mark_terminal
    后，缓冲清空、指导不再残留（终态清缓冲的环内验证）。"""
    tid = await checkpoint.create_task(user_id=1, organization_id=1, query="q")
    captured: list = []
    loop = _make_loop(_capturing_client(captured), n=1)
    events = [ev async for ev in loop.run("任务", task_id=tid)]
    assert any(e.type == "done" for e in events)
    # run 之后队列本就为空（无 steer）；补 mark_terminal 断言清理契约
    assert await steering.push_steer(tid, "迟到的指导") == 1
    assert await checkpoint.mark_terminal(tid, TaskStatus.COMPLETED) is True
    assert await steering.drain_steers(tid) == []
