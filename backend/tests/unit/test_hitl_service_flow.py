"""HITL：service 级全链 —— 暂停不置终态 / 批准续跑完成 / 协作式取消收敛（sqlite + 假 LLM）。

真实链路：AgentService.chat → 真 PERAgentLoop/Executor（enable_planning=False
= 单步计划，不耗规划 LLM）→ 假 OpenAI client（执行步发 tool_call、合成步直答）。
registry.execute_detailed 用替身（审批/取消测试不碰真实工具）。

判别点：
- 暂停（approval_required）后 service **不得**置终态（任务保持 waiting_approval），
  工具零执行——变异点：finalize 不看 paused_for_approval → 任务变 COMPLETED → 红；
- 批准后 resume 续跑：工具恰好执行一次、任务 COMPLETED（续跑复用 checkpoint 链）；
- 活跃期取消（工具前检查点）：run_cancelled 收敛、工具零执行、终态 CANCELLED
  ——变异点：忽略取消旗 → 工具被执行、任务 COMPLETED → 红；
- 低风险工具（risky 模式）零审批零回归。
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — 注册全部表，供 create_all
from app.agent import checkpoint
from app.agent.config import AgentConfig
from app.agent.exec_context import ExecutionContext
from app.agent.loop import PERAgentLoop
from app.agent.registry import ToolResult, tool_registry
from app.agent.service import AgentService
from app.core.database import Base
from app.models.agent_task import AgentTask, TaskStatus
from app.rag.retriever import HybridRetriever

QUERY = "帮我执行一段脚本"
USER_ID = 7


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    async def _save(self, ttl=86400 * 7):
        return False

    monkeypatch.setattr(ExecutionContext, "save", _save)
    monkeypatch.setattr(PERAgentLoop, "_write_experiences", AsyncMock())
    monkeypatch.setattr(HybridRetriever, "get_embedding", AsyncMock(return_value=[]))


@pytest.fixture
async def db_factory(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'hitl_svc.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(checkpoint, "AsyncSessionLocal", factory)
    yield factory
    await engine.dispose()


class _FakeRedis:
    def __bool__(self):
        return True

    def __init__(self):
        self.store: dict[str, str] = {}

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
        return 1

    async def expire(self, key, ttl):
        return key in self.store


@pytest.fixture
def fake_redis(monkeypatch):
    r = _FakeRedis()
    monkeypatch.setattr(checkpoint, "redis_client", r)
    return r


def _cfg(**overrides) -> AgentConfig:
    base = dict(
        enable_planning=False,  # 单步计划，不耗规划 LLM（结构化模板也不走）
        enable_reflection=False, enable_memory=False, enable_skills=False,
        enable_experience=False, enable_thinking=False,
    )
    base.update(overrides)
    return AgentConfig(**base)


def _client_tool_then_plain(
    tool: str = "execute_python",
    args: str = '{"code": "print(1)"}',
    *,
    tool_calls: list | None = None,
    started: asyncio.Event | None = None,
    block: asyncio.Event | None = None,
):
    """执行步（带 tools 参数）发一个 tool_call；合成/其它调用直答。"""
    counters = {"tools": 0, "plain": 0}

    async def _create(*, messages, tools=None, **kwargs):
        if tools:
            counters["tools"] += 1
            if started is not None:
                started.set()
            if block is not None:
                await block.wait()
            tc = SimpleNamespace(id="tc-1", function=SimpleNamespace(name=tool, arguments=args))
            msg = SimpleNamespace(content="", tool_calls=[tc], role="assistant")
        else:
            counters["plain"] += 1
            msg = SimpleNamespace(content="已完成。", tool_calls=None, role="assistant")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=msg, finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
        )

    client = MagicMock()
    client.chat.completions.create = _create
    return client, counters


async def _get_task(factory, task_id: str) -> AgentTask | None:
    async with factory() as db:
        return await db.get(AgentTask, task_id)


async def _only_task_id(factory) -> str:
    async with factory() as db:
        row = (await db.execute(select(AgentTask))).scalars().first()
    assert row is not None
    return row.id


@pytest.mark.asyncio
async def test_pause_keeps_waiting_approval_and_resume_approve_completes(db_factory, fake_redis):
    client, counters = _client_tool_then_plain()
    svc = AgentService(openai_client=client)
    spy = AsyncMock(return_value=ToolResult.ok(data="假执行结果"))

    with patch.object(tool_registry, "execute_detailed", new=spy):
        # ── 第一段：跑到审批点暂停 ──
        events = [e async for e in svc.chat(QUERY, organization_id=1, user_id=USER_ID, config=_cfg())]

        approval_events = [e for e in events if e.type == "approval_required"]
        assert approval_events, "必须发出 approval_required"
        assert approval_events[0].tool_name == "execute_python"
        assert approval_events[0].risk_level == "high"
        assert "审批" in approval_events[0].content
        task_id = approval_events[0].task_id
        assert task_id, "事件必须盖章 task_id（发现性入口）"

        assert spy.await_count == 0, "暂停在任何调用执行之前"
        task = await _get_task(db_factory, task_id)
        assert task.status == TaskStatus.WAITING_APPROVAL, (
            "暂停是诚实状态：既不 COMPLETED 也不 FAILED（变异点：service 不看 "
            "paused_for_approval → 这里变 COMPLETED → 红）"
        )
        pending = await checkpoint.get_pending_approval(task_id)
        assert pending and pending["tool_name"] == "execute_python"
        assert pending["expires_at"] is None, "默认 TTL=0：无限等待（不伪造超时）"

        # ── 第二段：人类批准 → 续跑（与端点同序：记账+认领 → resume_chat(claim=False)）──
        task_row, payload, record = await checkpoint.apply_approval_decision(
            task_id=task_id, user_id=USER_ID, organization_id=1, decision="approve",
        )
        assert record["decision"] == "approve"
        events2 = [
            e async for e in svc.resume_chat(task_id, user_id=USER_ID, claim=False)
        ]

    assert spy.await_count == 1, "批准后恰好执行一次（粘滞决定：不再打扰）"
    called_name, called_args = spy.await_args.args[0], spy.await_args.args[1]
    assert called_name == "execute_python" and called_args == {"code": "print(1)"}
    assert any(e.type == "tool_result" for e in events2)
    assert any(e.type == "done" for e in events2)
    assert not any(e.type == "approval_required" for e in events2), "已批准决定不得再暂停"
    task = await _get_task(db_factory, task_id)
    assert task.status == TaskStatus.COMPLETED
    assert counters["tools"] == 2, "1 次暂停前尝试 + 1 次续跑尝试（步骤重跑一次，at-least-once）"


@pytest.mark.asyncio
async def test_live_cancel_before_tool_execution_converges_cancelled(db_factory, fake_redis):
    """活跃期取消（工具前检查点）：旗在 LLM 调用进行中置位 → LLM 返回后、
    工具执行前被观察到 → run_cancelled + CANCELLED，工具零执行。

    变异点：忽略取消旗 → 工具被执行、任务 COMPLETED → 本用例红。
    """
    started = asyncio.Event()
    block = asyncio.Event()
    client, counters = _client_tool_then_plain(started=started, block=block)
    svc = AgentService(openai_client=client)
    spy = AsyncMock(return_value=ToolResult.ok(data="不该被执行"))

    events: list = []

    async def _consume():
        async for e in svc.chat(QUERY, organization_id=1, user_id=USER_ID, config=_cfg()):
            events.append(e)

    with patch.object(tool_registry, "execute_detailed", new=spy):
        consumer = asyncio.create_task(_consume())
        assert await asyncio.wait_for(started.wait(), timeout=10), "假 LLM 未被调用"
        task_id = await _only_task_id(db_factory)

        # 取消请求（与 POST /cancel 的活跃路径同款：置旗）——发生在 LLM 挂起期间。
        assert await checkpoint.request_cancel(task_id) is True
        block.set()
        await asyncio.wait_for(consumer, timeout=10)

    assert spy.await_count == 0, "工具前检查点必须拦住已取消 run 的工具执行"
    assert any(e.type == "run_cancelled" for e in events)
    assert not any(e.type == "approval_required" for e in events), "正在取消的 run 不该进暂停"
    task = await _get_task(db_factory, task_id)
    assert task.status == TaskStatus.CANCELLED, "诚实终态 cancelled（变异点：按 COMPLETED 收尾 → 红）"
    assert task.last_error_type == "user_cancelled"


@pytest.mark.asyncio
async def test_cancel_before_run_starts_converges_at_phase_checkpoint(db_factory, fake_redis):
    """阶段间检查点：run 启动前已置旗 → 规划阶段前的第一个检查点直接收敛
    （任何 LLM 调用都不发起）。"""
    client, counters = _client_tool_then_plain()
    svc = AgentService(openai_client=client)

    # 手工种一个任务 + 旗（模拟"取消请求先于 SSE 开流到达"的竞态）。
    task_id = await checkpoint.create_task(user_id=USER_ID, organization_id=1, query=QUERY)
    await checkpoint.request_cancel(task_id)

    spy = AsyncMock(return_value=ToolResult.ok(data="x"))
    with patch.object(tool_registry, "execute_detailed", new=spy):
        events = [
            e async for e in svc.resume_chat(task_id, user_id=USER_ID, claim=False)
        ]

    assert any(e.type == "run_cancelled" for e in events)
    assert counters["tools"] == 0 and counters["plain"] == 0, (
        "阶段检查点在任何 LLM 调用之前收敛"
    )
    task = await _get_task(db_factory, task_id)
    assert task.status == TaskStatus.CANCELLED
    assert spy.await_count == 0


@pytest.mark.asyncio
async def test_low_risk_tool_no_pause_no_regression(db_factory, fake_redis):
    client, counters = _client_tool_then_plain(tool="search_knowledge_base", args='{"query": "q"}')
    svc = AgentService(openai_client=client)
    spy = AsyncMock(return_value=ToolResult.ok(data="检索结果"))

    with patch.object(tool_registry, "execute_detailed", new=spy):
        events = [e async for e in svc.chat(QUERY, organization_id=1, user_id=USER_ID, config=_cfg())]

    assert not any(e.type == "approval_required" for e in events), (
        "risky 模式默认只拦 high：低风险工具不得产生审批暂停"
    )
    assert spy.await_count == 1
    assert any(e.type == "done" for e in events)
    task_id = next(e.task_id for e in events if e.task_id)
    task = await _get_task(db_factory, task_id)
    assert task.status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_pause_uses_ttl_from_config(db_factory, fake_redis):
    """approval_timeout_seconds>0 时 pending 记录带 expires_at（TTL 策略可配置）。"""
    client, _ = _client_tool_then_plain()
    svc = AgentService(openai_client=client)
    spy = AsyncMock(return_value=ToolResult.ok(data="x"))

    with patch.object(tool_registry, "execute_detailed", new=spy):
        events = [
            e async for e in svc.chat(
                QUERY, organization_id=1, user_id=USER_ID,
                config=_cfg(approval_timeout_seconds=600),
            )
        ]

    task_id = next(e.task_id for e in events if e.task_id)
    pending = await checkpoint.get_pending_approval(task_id)
    assert pending is not None and isinstance(pending.get("expires_at"), float)
