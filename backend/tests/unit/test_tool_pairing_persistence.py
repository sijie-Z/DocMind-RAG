"""C3a：工具调用配对持久化 + 合成终态（先红后绿验收）。

覆盖四条验收线：
1. 正常路径 call→result 配对完整（防回退）；
2. 中断/取消/resume 后未完成 call 补写合成终态
   （TOOL_OUTCOME_UNKNOWN / TOOL_NOT_STARTED / ABORTED_BEFORE_DISPATCH）；
3. 重复 result 被唯一约束/幂等拦截；
4. 孤儿 result（无对应 call）显式报错，不静默丢账。

以及配对平衡断言：压缩/截断切点不得拆散 call-result 对。

DB 用 sqlite 内存/临时文件（照 test_checkpoint_store 的 fixture 口径），
Redis 用假 client；不需要真实 MySQL/Kafka。
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.agent.service  # noqa: F401 — 触发全部工具注册（executor 用例）
import app.models  # noqa: F401 — 注册全部表
from app.agent import checkpoint, tool_ledger, tool_pairs
from app.agent.config import AgentConfig
from app.agent.executor import Executor
from app.agent.memory_bridge import AgentMemoryBridge
from app.agent.planner import Plan, PlanStep
from app.agent.registry import tool_registry
from app.agent.tool_ledger import OrphanToolResultError
from app.core.database import Base
from app.models.agent_task import TaskStatus
from app.models.agent_tool_call import AgentToolCall, AgentToolResult


class _FakeRedis:
    """checkpoint 租约/取消旗用到的最小替身。"""

    def __init__(self):
        self.store: dict[str, str] = {}

    def __bool__(self):
        return True

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def delete(self, key):
        self.store.pop(key, None)
        return 1

    async def expire(self, key, ttl):
        return key in self.store

    async def get(self, key):
        return self.store.get(key)


@pytest.fixture
async def db_factory(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'tool_ledger.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(tool_ledger, "AsyncSessionLocal", factory)
    monkeypatch.setattr(checkpoint, "AsyncSessionLocal", factory)
    monkeypatch.setattr(checkpoint, "redis_client", _FakeRedis())
    yield factory
    await engine.dispose()


async def _new_task(factory, query: str = "问题") -> str:
    return await checkpoint.create_task(user_id=7, organization_id=1, query=query)


async def _calls(factory, task_id: str) -> list[AgentToolCall]:
    async with factory() as db:
        rows = (await db.execute(
            select(AgentToolCall)
            .where(AgentToolCall.task_id == task_id)
            .order_by(AgentToolCall.source_event_seq)
        )).scalars().all()
        return list(rows)


async def _results(factory, task_id: str) -> list[AgentToolResult]:
    async with factory() as db:
        rows = (await db.execute(
            select(AgentToolResult)
            .where(AgentToolResult.task_id == task_id)
            .order_by(AgentToolResult.source_event_seq)
        )).scalars().all()
        return list(rows)


# ── 1. 正常路径：call→result 配对完整 ─────────────────────────────────────────


async def test_normal_path_registers_dispatches_and_records_pair(db_factory):
    task_id = await _new_task(db_factory)

    assert await tool_ledger.register_calls(
        task_id, [("call-1", "search_knowledge_base", {"query": "x"})],
    ) == 1
    assert await tool_ledger.mark_dispatched(task_id, "call-1") is True
    assert await tool_ledger.record_result(
        task_id, "call-1", success=True, result="命中文档 3 条",
    ) is True

    calls = await _calls(db_factory, task_id)
    results = await _results(db_factory, task_id)
    assert len(calls) == 1
    call = calls[0]
    assert call.status == tool_ledger.STATUS_COMPLETED
    assert call.tool_name == "search_knowledge_base"
    assert call.call_id == "call-1"
    assert len(results) == 1
    assert results[0].call_id == "call-1"
    assert results[0].tool_call_pk == call.id, "结果必须关联 call 记录"
    assert results[0].status == "success"
    assert results[0].source_event_seq > call.source_event_seq, (
        "result 的事件序号必须晚于 call，顺序可核"
    )


# ── 2. 重复 result：唯一约束 / 幂等拦截 ──────────────────────────────────────


async def test_duplicate_result_blocked_idempotently(db_factory):
    task_id = await _new_task(db_factory)
    await tool_ledger.register_calls(task_id, [("call-1", "t", {})])
    await tool_ledger.mark_dispatched(task_id, "call-1")
    assert await tool_ledger.record_result(
        task_id, "call-1", success=True, result="第一次结果",
    ) is True

    dup = await tool_ledger.record_result(
        task_id, "call-1", success=True, result="第二次结果",
    )
    assert dup is False, "重复 result 必须被幂等拦截（不得二次落账）"

    results = await _results(db_factory, task_id)
    assert len(results) == 1, "唯一约束语义：一次调用至多一条 result"
    assert results[0].result_summary == "第一次结果", "首条账目不得被重复事件覆盖"


async def test_db_unique_constraints_guard_duplicate_rows(db_factory):
    task_id = await _new_task(db_factory)
    await tool_ledger.register_calls(task_id, [("call-1", "t", {})])
    await tool_ledger.mark_dispatched(task_id, "call-1")
    await tool_ledger.record_result(task_id, "call-1", success=True, result="ok")

    async with db_factory() as db:
        call = (await db.execute(
            select(AgentToolCall).where(AgentToolCall.task_id == task_id)
        )).scalars().first()
        db.add(AgentToolResult(
            task_id=task_id, call_id=call.call_id, tool_call_pk=call.id,
            source_event_seq=999, status="success", result_summary="repeat",
        ))
        with pytest.raises(IntegrityError):
            await db.commit()


# ── 3. 孤儿 result：报错不静默 ───────────────────────────────────────────────


async def test_orphan_result_raises(db_factory):
    task_id = await _new_task(db_factory)
    with pytest.raises(OrphanToolResultError):
        await tool_ledger.record_result(task_id, "no-such-call", success=True, result="x")
    assert await _results(db_factory, task_id) == [], "孤儿 result 不得静默落账"


# ── 4. 合成终态：中断两类 + 取消两类 ────────────────────────────────────────


async def test_interrupt_after_dispatch_marks_outcome_unknown(db_factory):
    task_id = await _new_task(db_factory)
    await tool_ledger.register_calls(task_id, [("call-1", "slow_tool", {})])
    await tool_ledger.mark_dispatched(task_id, "call-1")

    await tool_ledger.synthesize_terminal(task_id, reason="interrupt")

    call = (await _calls(db_factory, task_id))[0]
    assert call.status == tool_ledger.STATUS_TOOL_OUTCOME_UNKNOWN, (
        "执行中失联：已派发但无 result，必须补写 TOOL_OUTCOME_UNKNOWN"
    )


async def test_interrupt_before_dispatch_marks_not_started(db_factory):
    task_id = await _new_task(db_factory)
    await tool_ledger.register_calls(task_id, [("call-1", "t", {})])

    await tool_ledger.synthesize_terminal(task_id, reason="interrupt")

    call = (await _calls(db_factory, task_id))[0]
    assert call.status == tool_ledger.STATUS_TOOL_NOT_STARTED, (
        "已登记未派发：崩溃/中断时补写 TOOL_NOT_STARTED"
    )


async def test_cancel_marks_aborted_before_dispatch_and_outcome_unknown(db_factory):
    task_id = await _new_task(db_factory)
    await tool_ledger.register_calls(task_id, [
        ("call-a", "t", {}), ("call-b", "t", {}),
    ])
    await tool_ledger.mark_dispatched(task_id, "call-a")

    await tool_ledger.synthesize_terminal(task_id, reason="cancel")

    by_id = {c.call_id: c for c in await _calls(db_factory, task_id)}
    assert by_id["call-a"].status == tool_ledger.STATUS_TOOL_OUTCOME_UNKNOWN, (
        "取消时正在执行：TOOL_OUTCOME_UNKNOWN"
    )
    assert by_id["call-b"].status == tool_ledger.STATUS_ABORTED_BEFORE_DISPATCH, (
        "取消前已登记未派发：ABORTED_BEFORE_DISPATCH"
    )


async def test_begin_resume_sweeps_previous_run_leftovers(db_factory):
    """resume 接线：恢复旧 run 前，未完成 call 必须补写合成终态。"""
    task_id = await _new_task(db_factory)
    await tool_ledger.register_calls(task_id, [
        ("lost", "slow_tool", {}), ("queued", "t", {}),
    ])
    await tool_ledger.mark_dispatched(task_id, "lost")

    await checkpoint.begin_resume(task_id)

    by_id = {c.call_id: c for c in await _calls(db_factory, task_id)}
    assert by_id["lost"].status == tool_ledger.STATUS_TOOL_OUTCOME_UNKNOWN
    assert by_id["queued"].status == tool_ledger.STATUS_TOOL_NOT_STARTED


async def test_mark_terminal_cancelled_sweeps_with_cancel_semantics(db_factory):
    """取消接线：CANCELLED 终态落定时按取消语义补写合成终态。"""
    task_id = await _new_task(db_factory)
    await tool_ledger.register_calls(task_id, [
        ("running", "slow_tool", {}), ("pending", "t", {}),
    ])
    await tool_ledger.mark_dispatched(task_id, "running")

    assert await checkpoint.mark_terminal(task_id, TaskStatus.CANCELLED) is True

    by_id = {c.call_id: c for c in await _calls(db_factory, task_id)}
    assert by_id["running"].status == tool_ledger.STATUS_TOOL_OUTCOME_UNKNOWN
    assert by_id["pending"].status == tool_ledger.STATUS_ABORTED_BEFORE_DISPATCH


# ── 5. 执行器接线：先落 call 再 dispatch；结果落账 ───────────────────────────


def _client_with_tool_call(tool_name: str, arguments: str = "{}"):
    client = MagicMock()

    async def _create(*, messages, tools=None, **kwargs):
        resp = MagicMock()
        choice = MagicMock()
        msg = MagicMock()
        tc = MagicMock()
        tc.id = "tc-exec-1"
        tc.function.name = tool_name
        tc.function.arguments = arguments
        msg.tool_calls = [tc]
        msg.content = ""
        msg.role = "assistant"
        choice.message = msg
        resp.choices = [choice]
        return resp

    client.chat.completions.create = _create
    return client


def _mk_executor(config: AgentConfig, client) -> Executor:
    memory = AgentMemoryBridge(agent_id="t", organization_id=1, user_id=0)
    return Executor(client, config, memory)


async def _run_step(executor):
    step = PlanStep(id="s1", description="执行子任务")
    plan = Plan(id="p1", goal="g", steps=[step])
    events = []
    async for ev in executor._execute_step_once(
        step=step, plan=plan, history=None, organization_id=2, user_id=0,
        enable_thinking=False,
    ):
        events.append(ev)
    return events


class _OkResult:
    success = True
    error = None

    def __str__(self):
        return "工具结果正文"


async def test_executor_persists_call_before_dispatch_and_result(db_factory):
    task_id = await _new_task(db_factory)
    cfg = AgentConfig(enable_planning=False, enable_reflection=False)
    executor = _mk_executor(cfg, _client_with_tool_call("search_knowledge_base"))
    executor.tool_ledger_task_id = task_id

    with patch.object(
        tool_registry, "execute_detailed", new=AsyncMock(return_value=_OkResult()),
    ):
        events = await _run_step(executor)

    assert [e.type for e in events].count("tool_call") == 1
    calls = await _calls(db_factory, task_id)
    assert len(calls) == 1 and calls[0].call_id == "tc-exec-1"
    assert calls[0].status == tool_ledger.STATUS_COMPLETED
    results = await _results(db_factory, task_id)
    assert len(results) == 1 and results[0].call_id == "tc-exec-1"


async def test_executor_cancel_before_dispatch_leaves_registered_until_sweep(db_factory):
    """取消点在派发前：call 已登记（先落库），终态由取消收尾补写。"""
    from app.agent import approval

    task_id = await _new_task(db_factory)
    cfg = AgentConfig(enable_planning=False, enable_reflection=False)
    executor = _mk_executor(cfg, _client_with_tool_call("search_knowledge_base"))
    executor.tool_ledger_task_id = task_id

    checks = {"n": 0}

    async def _cancel_check():
        checks["n"] += 1
        return checks["n"] >= 3  # 前两个检查点放行，第三个（逐工具前）取消

    executor.cancel_check = _cancel_check

    with patch.object(tool_registry, "execute_detailed", new=AsyncMock()) as spy:
        with pytest.raises(approval.RunCancelled):
            await _run_step(executor)
    assert spy.await_count == 0, "取消点必须在 dispatch 之前"

    calls = await _calls(db_factory, task_id)
    assert len(calls) == 1 and calls[0].status == tool_ledger.STATUS_REGISTERED, (
        "先落库：取消时 call 记录必须已存在且仍是未派发态"
    )

    await checkpoint.mark_terminal(task_id, TaskStatus.CANCELLED)
    calls = await _calls(db_factory, task_id)
    assert calls[0].status == tool_ledger.STATUS_ABORTED_BEFORE_DISPATCH


# ── 6. 配对平衡断言：压缩/截断切点不拆散 call-result 对 ────────────────────


def _pair(ids=("t1",)):
    assistant = {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"id": i, "type": "function", "function": {"name": "f", "arguments": "{}"}} for i in ids],
    }
    results = [{"role": "tool", "tool_call_id": i, "content": "结果"} for i in ids]
    return [assistant] + results


def test_validate_tool_pairs_rejects_orphan_result():
    with pytest.raises(tool_pairs.ToolPairingError):
        tool_pairs.validate_tool_pairs([{"role": "tool", "tool_call_id": "ghost", "content": "x"}])


def test_validate_tool_pairs_rejects_dangling_call():
    with pytest.raises(tool_pairs.ToolPairingError):
        tool_pairs.validate_tool_pairs(_pair()[0:1])


def test_balance_cut_does_not_split_pair():
    messages = [{"role": "user", "content": f"u{i}"} for i in range(3)]
    messages += _pair()
    messages += [{"role": "user", "content": "tail"}]
    # 构造会拆的切点：切在 call 与 result 之间（index 4）
    cut = tool_pairs.balance_cut(messages, 4)
    assert cut <= 3, "切点必须回退到 call 之前，call/result 同侧"


def test_context_engine_fit_keeps_pair_together():
    from app.agent.context import ContextEngine

    engine = ContextEngine(max_context_tokens=60, tail_window=2)
    messages = [{"role": "user", "content": "旧问题" * 30}]
    messages += _pair()
    messages += [{"role": "user", "content": "新问题" * 20}]

    fitted = engine.fit(messages)
    tool_pairs.validate_tool_pairs(fitted)  # 压缩结果不得出现孤儿 result/悬空 call
    call_present = any(m.get("tool_calls") for m in fitted)
    result_present = any(m.get("role") == "tool" for m in fitted)
    assert call_present == result_present, "call 与 result 必须同侧保留"


def test_truncate_messages_drops_whole_pair_when_budget_short():
    from app.agent.context import ContextEngine

    engine = ContextEngine(max_context_tokens=10, tail_window=1)
    pair = _pair()
    kept = engine._truncate_messages(pair, token_budget=2)
    tool_pairs.validate_tool_pairs(kept)
    assert kept == [] or len(kept) == len(pair), "预算不足时整对丢弃，绝不拆散"


def test_checkpoint_history_cap_keeps_pair_together():
    history = [{"role": "user", "content": f"u{i}"} for i in range(3)]
    history += _pair(("cap1",))  # call 在 index 3，result 在 index 4
    history += [{"role": "user", "content": f"v{i}"} for i in range(49)]

    envelope = checkpoint.build_envelope(
        ctx=None, plan=None, trigger="test", history=history,
    )
    frozen = envelope["history"]
    tool_pairs.validate_tool_pairs(frozen)
    assert len(frozen) <= checkpoint.HISTORY_CAP, "上限仍是硬帽（配对不拆靠摘孤儿）"
    assert all(m.get("role") != "tool" for m in frozen[:1]), "窗口头部不得是孤儿 result"
