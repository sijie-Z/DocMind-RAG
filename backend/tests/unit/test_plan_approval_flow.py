"""B（计划审批）—— checkpoint 写路径 + loop 暂停/重规划语义判别。

被测契约（checkpoint.apply_plan_approval_decision 与 loop 的
_pause_for_plan_approval / resume 分支）：

- always 模式：planner 每产出一版计划 → 暂停（任务态 waiting_plan_approval +
  plan_approval_required 事件含结构化步骤）+ **执行器零执行**（变异点：暂停点
  挪到执行之后 / 不暂停 → 零执行断言红）；
- never 模式（默认）：完全旧行为；
- 检查点不可用：不制造假暂停——如实 thinking + 继续执行；
- approve：弹 pending → resume 直接执行获批计划（不再打扰）；
- edit：白名单校验后整体替换步骤（非法输入 ValueError——端点层映射 422）；
- reject#1：带理由重规划一次（run_state.plan_reject_replan；loop 放弃被拒计划，
  理由进 planner 反馈）→ 新计划在 always 模式再次暂停；
- reject#2：终态 REJECTED + last_error 记理由（绝不无限重规划）；
- bare resume（pending 未决）绝不执行：原地重挂起（深度防御）。

驱动方式：真实 loop.run + 假 planner（脚本化计划）+ 假 LLM + sqlite + 手写
_FakeRedis（与 test_steer_loop_wiring 同款）。
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
from app.core.database import Base
from app.models.agent_task import AgentTask, TaskStatus
from app.rag.retriever import HybridRetriever


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


class _ScriptedPlanner:
    """脚本化 planner：每次 plan() 依次吐出下一组步骤；记录 replan_feedback。"""

    def __init__(self, script: list[list[str]]):
        self.script = script
        self.calls: list[dict] = []

    async def plan(self, query, history=None, memory_context="", ctx=None, replan_feedback=""):
        self.calls.append({"query": query, "replan_feedback": replan_feedback})
        steps = self.script[min(len(self.calls) - 1, len(self.script) - 1)]
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


def _client(captured: list):
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


def _cfg(**overrides) -> AgentConfig:
    base = dict(
        enable_memory=False,
        enable_reflection=False,
        enable_skills=False,
        enable_experience=False,
        enable_thinking=False,
        enable_planning=True,
        plan_approval_mode="always",
    )
    base.update(overrides)
    return AgentConfig(**base)


def _make_loop(client, planner, config=None) -> PERAgentLoop:
    loop = PERAgentLoop(client, config or _cfg(), organization_id=1, user_id=0)
    loop.memory = _FakeMemory()
    loop._planner = planner
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
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'plan_approval.db'}")
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


async def _row(factory, task_id: str) -> AgentTask:
    async with factory() as db:
        return await db.get(AgentTask, task_id)


async def _events(agen) -> list:
    return [ev async for ev in agen]


# ── loop 暂停语义 ────────────────────────────────────────────────────────────


async def test_always_mode_pauses_after_planning_with_zero_execution(db_factory, fake_redis):
    """always：planner 出计划 → 暂停（事件 + 任务态 + 快照步骤），执行器零执行。

    变异点：删掉 planning 分支的暂停调用 → 执行器被调用、任务态 completed 系
    事件出现 → 红。
    """
    tid = await checkpoint.create_task(user_id=1, organization_id=1, query="q")
    captured: list = []
    planner = _ScriptedPlanner([["第一步", "第二步"]])
    loop = _make_loop(_client(captured), planner)

    events = await _events(loop.run("目标", task_id=tid))

    par = [e for e in events if e.type == "plan_approval_required"]
    assert par, "always 模式必须暂停并发计划审批事件"
    assert par[0].task_id == tid
    assert [s["description"] for s in par[0].plan_steps] == ["第一步", "第二步"]
    assert par[0].plan_goal == "目标"
    assert captured == [], "暂停在任何执行之前（执行器零 LLM 调用）"
    assert not any(e.type == "done" for e in events), "暂停不是完成"
    assert loop.paused_for_plan_approval is True

    row = await _row(db_factory, tid)
    assert row.status == TaskStatus.WAITING_PLAN_APPROVAL
    snap = row.context_snapshot
    assert snap["run"]["pending_plan_approval"]["steps"][0]["description"] == "第一步"


async def test_never_mode_runs_without_pause(db_factory, fake_redis):
    """never（默认）：完全旧行为——计划产出即执行，无审批事件。"""
    tid = await checkpoint.create_task(user_id=1, organization_id=1, query="q")
    captured: list = []
    planner = _ScriptedPlanner([["唯一一步"]])
    loop = _make_loop(_client(captured), planner, config=_cfg(plan_approval_mode="never"))

    events = await _events(loop.run("目标", task_id=tid))

    assert not any(e.type == "plan_approval_required" for e in events)
    assert any(e.type == "done" for e in events)
    assert len(captured) == 1, "计划直接执行"
    row = await _row(db_factory, tid)
    assert row.status == TaskStatus.RUNNING, "loop 不碰终态机（收尾由 service 负责）"


async def test_checkpoint_unwired_degrades_honestly(db_factory, fake_redis):
    """plan_approval_mode=always 但无 checkpoint：不制造假暂停——如实说明并继续。"""
    captured: list = []
    planner = _ScriptedPlanner([["唯一一步"]])
    loop = _make_loop(_client(captured), planner)  # task_id=None → 未接 checkpoint

    events = await _events(loop.run("目标"))

    assert not any(e.type == "plan_approval_required" for e in events)
    hints = [e for e in events if e.type == "thinking" and "检查点不可用" in e.content]
    assert hints, "降级必须如实说明"
    assert len(captured) == 1, "降级 = 继续执行（不是假暂停）"
    assert loop.paused_for_plan_approval is False


# ── checkpoint 决策写路径 ────────────────────────────────────────────────────


async def _seed_waiting_plan_task(db_factory, fake_redis, tid: str) -> None:
    """把任务推到 waiting_plan_approval（走真实 loop 暂停，保证现场真实）。"""
    planner = _ScriptedPlanner([["第一步", "第二步"]])
    loop = _make_loop(_client([]), planner)
    events = await _events(loop.run("目标", task_id=tid))
    assert any(e.type == "plan_approval_required" for e in events)


async def test_approve_pops_pending_and_resumes_execution(db_factory, fake_redis):
    tid = await checkpoint.create_task(user_id=1, organization_id=1, query="q")
    await _seed_waiting_plan_task(db_factory, fake_redis, tid)

    task, envelope, record = await checkpoint.apply_plan_approval_decision(
        task_id=tid,
        user_id=1,
        organization_id=1,
        decision="approve",
    )
    assert task.status == TaskStatus.RUNNING
    assert record["decision"] == "approve"
    assert "pending_plan_approval" not in (envelope["run"] or {})

    # resume 直接执行获批计划：不再发 plan_approval_required
    captured: list = []
    loop2 = _make_loop(_client(captured), _ScriptedPlanner([["不该被用"]]))
    events = await _events(loop2.run("目标", task_id=tid, resume=envelope))
    assert not any(e.type == "plan_approval_required" for e in events)
    assert any(e.type == "done" for e in events)
    assert len(captured) == 2, "获批计划的两步被执行"


async def test_edit_replaces_steps_and_executes_edited(db_factory, fake_redis):
    tid = await checkpoint.create_task(user_id=1, organization_id=1, query="q")
    await _seed_waiting_plan_task(db_factory, fake_redis, tid)

    _task, envelope, record = await checkpoint.apply_plan_approval_decision(
        task_id=tid,
        user_id=1,
        organization_id=1,
        decision="edit",
        edited_steps=[
            {"id": "s1", "description": "人类改过的第一步", "dependencies": [], "tool_hint": None},
        ],
    )
    assert record["edited_step_count"] == 1
    steps = envelope["plan"]["steps"]
    assert [s["description"] for s in steps] == ["人类改过的第一步"]
    assert steps[0]["status"] == "pending"

    captured: list = []
    loop2 = _make_loop(_client(captured), _ScriptedPlanner([["不该被用"]]))
    events = await _events(loop2.run("目标", task_id=tid, resume=envelope))
    assert any(e.type == "done" for e in events)
    assert len(captured) == 1, "只执行编辑后的 1 步"
    assert "人类改过的第一步" in captured[0][-1]["content"], "执行的是编辑后的步骤"


async def test_edit_validation_rejects_invalid_steps(db_factory, fake_redis):
    """服务层第二道闸门：非法编辑绝不写进快照（端点层同组校验 → 422）。"""
    tid = await checkpoint.create_task(user_id=1, organization_id=1, query="q")
    await _seed_waiting_plan_task(db_factory, fake_redis, tid)

    for bad in (
        [],  # 空列表
        [{"id": "s1", "description": "x", "dependencies": ["s9"]}],  # 依赖不存在
        [{"id": "s1", "description": "x"}, {"id": "s1", "description": "y"}],  # id 重复
        [{"id": "s1", "description": "  "}],  # description 空
        [{"id": "s1", "description": "x", "dependencies": ["s1"]}],  # 自依赖
    ):
        with pytest.raises(ValueError):
            await checkpoint.apply_plan_approval_decision(
                task_id=tid,
                user_id=1,
                organization_id=1,
                decision="edit",
                edited_steps=bad,
            )
    # 任务仍停在等待态（失败不破坏现场）
    row = await _row(db_factory, tid)
    assert row.status == TaskStatus.WAITING_PLAN_APPROVAL


async def test_reject_once_replans_with_reason_then_repause(db_factory, fake_redis):
    """reject#1：带理由重规划一次——loop 放弃被拒计划、理由进 planner 反馈；
    always 模式下新计划再次暂停（pending 重建）。"""
    tid = await checkpoint.create_task(user_id=1, organization_id=1, query="q")
    await _seed_waiting_plan_task(db_factory, fake_redis, tid)

    _task, envelope, record = await checkpoint.apply_plan_approval_decision(
        task_id=tid,
        user_id=1,
        organization_id=1,
        decision="reject",
        note="不要逐个查，请合并为一次检索",
    )
    assert record.get("replan") is True and record["rejections"] == 1
    run = envelope["run"]
    assert "pending_plan_approval" not in run
    assert run["plan_reject_replan"]["feedback"] == "不要逐个查，请合并为一次检索"

    captured: list = []
    planner = _ScriptedPlanner([["合并检索的新计划"]])
    loop2 = _make_loop(_client(captured), planner)
    events = await _events(loop2.run("目标", task_id=tid, resume=envelope))

    # 被拒计划不执行：planner 被调用且收到拒绝理由
    assert len(planner.calls) == 1
    assert planner.calls[0]["replan_feedback"] == "不要逐个查，请合并为一次检索"
    assert captured == [], "被拒计划零执行（新计划又暂停）"
    assert not any(e.content.startswith("执行步骤:") for e in events)
    par = [e for e in events if e.type == "plan_approval_required"]
    assert par and par[-1].plan_steps[0]["description"] == "合并检索的新计划", "新计划再次等审批"

    row = await _row(db_factory, tid)
    assert row.status == TaskStatus.WAITING_PLAN_APPROVAL
    assert (row.context_snapshot["run"] or {}).get("plan_approval_rejections") == 1

    # 第二次拒绝 → 终态 REJECTED + 理由入 last_error；无流可续
    task, _env2, record2 = await checkpoint.apply_plan_approval_decision(
        task_id=tid,
        user_id=1,
        organization_id=1,
        decision="reject",
        note="还是不行",
    )
    assert record2.get("terminal") is True
    assert task.status == TaskStatus.REJECTED
    row = await _row(db_factory, tid)
    assert row.status == TaskStatus.REJECTED
    assert row.last_error_type == "plan_rejected"
    assert "还是不行" in (row.last_error or "")
    assert row.is_terminal is True


async def test_bare_resume_of_pending_plan_repauses_without_execution(db_factory, fake_redis):
    """深度防御：pending 未决时的 bare resume 绝不执行——原地重挂起（重呈现）。"""
    tid = await checkpoint.create_task(user_id=1, organization_id=1, query="q")
    await _seed_waiting_plan_task(db_factory, fake_redis, tid)
    # 直接取快照做 resume payload（模拟绕过端点的裸 resume；begin_resume 已把
    # 状态翻回 RUNNING——loop 必须靠 pending 标记自我刹车）
    async with db_factory() as db:
        row = await db.get(AgentTask, tid)
        payload = row.context_snapshot
        row.transition(TaskStatus.RUNNING)
        await db.commit()

    captured: list = []
    loop2 = _make_loop(_client(captured), _ScriptedPlanner([["不该被用"]]))
    events = await _events(loop2.run("目标", task_id=tid, resume=payload))

    assert captured == [], "未获批准的计划绝不被 resume 执行"
    par = [e for e in events if e.type == "plan_approval_required"]
    assert par, "原地重挂起（重呈现审批卡）"
    row = await _row(db_factory, tid)
    assert row.status == TaskStatus.WAITING_PLAN_APPROVAL


async def test_409_semantics_of_decision_endpoint(db_factory, fake_redis):
    tid = await checkpoint.create_task(user_id=1, organization_id=1, query="q")
    # 非等待计划审批状态 → TASK_NOT_WAITING_PLAN_APPROVAL
    with pytest.raises(checkpoint.TaskNotWaitingPlanApprovalError):
        await checkpoint.apply_plan_approval_decision(
            task_id=tid,
            user_id=1,
            organization_id=1,
            decision="approve",
        )
    # 终态 → TASK_NOT_RESUMABLE
    await checkpoint.mark_terminal(tid, TaskStatus.COMPLETED)
    with pytest.raises(checkpoint.TaskNotResumableError):
        await checkpoint.apply_plan_approval_decision(
            task_id=tid,
            user_id=1,
            organization_id=1,
            decision="approve",
        )
    # 非本人 → 404 语义
    tid2 = await checkpoint.create_task(user_id=9, organization_id=1, query="q")
    with pytest.raises(checkpoint.TaskNotFoundError):
        await checkpoint.apply_plan_approval_decision(
            task_id=tid2,
            user_id=1,
            organization_id=1,
            decision="approve",
        )


async def test_plan_approval_mode_frozen_in_config_roundtrip(db_factory, fake_redis):
    """config 闸门（#319 同族）：plan_approval_mode 进 to_dict/from_dict 往返；
    坏值降级为 never。"""
    cfg = AgentConfig(plan_approval_mode="always")
    assert cfg.to_dict()["plan_approval_mode"] == "always"
    assert AgentConfig.from_dict(cfg.to_dict()).plan_approval_mode == "always"
    drifted = AgentConfig.from_dict({"plan_approval_mode": 7})
    assert drifted.plan_approval_mode == "never", "坏值降级为默认"
