"""issue #201 最小版：崩溃 → resume → 续跑 的全链行为用例（sqlite + 假 LLM）。

真实链路走法（非替身）：AgentService.chat → 真 PERAgentLoop/Planner（结构化模板，
不耗 LLM 规划）→ 真 Executor + 假 OpenAI client（无 tool_calls 的直答步）。
在第 2/3 步完成后停止消费并 aclose 生成器 —— 等价于客户端中断/进程死亡时刻
（loop 停在 yield 上，checkpoint.save_from_live 兜底）；随后用新 service 调用
resume_chat，断言：
  - 只执行剩余步骤（不重跑已完成步）、无重复产出、最终产物正确、任务 COMPLETED；
  - 红基线（对应报告测试 1）：checkpoint 写路径被拔掉（no-op）→ 快照缺失 →
    resume 只能从头重跑全部步骤（"不接 checkpoint 会丢"的判别性证明）。
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — 注册全部表，供 create_all
from app.agent import checkpoint
from app.agent.config import AgentConfig
from app.agent.exec_context import ExecutionContext
from app.agent.loop import PERAgentLoop
from app.agent.service import AgentService
from app.core.database import Base
from app.models.agent_task import AgentTask, TaskStatus

QUERY = "总结一下这份项目资料"
USER_ID = 7


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """隔离与 checkpoint 无关的副作用（replay 落盘 / 经验回写）。"""
    async def _save(self, ttl=86400 * 7):
        return False

    monkeypatch.setattr(ExecutionContext, "save", _save)
    monkeypatch.setattr(PERAgentLoop, "_write_experiences", AsyncMock())


@pytest.fixture
async def db_factory(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'recovery.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(checkpoint, "AsyncSessionLocal", factory)
    yield factory
    await engine.dispose()


class _FakeRedisLock:
    def __bool__(self):
        return True

    def __init__(self):
        self.store: dict[str, str] = {}

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


def _fake_openai_client():
    """直答型假 client：第 n 次调用返回 "步骤结果{n}"（无 tool_calls）。"""
    calls = {"n": 0}

    async def _create(**kwargs):
        calls["n"] += 1
        msg = SimpleNamespace(content=f"步骤结果{calls['n']}", tool_calls=None, role="assistant")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=msg, finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
        )

    client = MagicMock()
    client.chat.completions.create = _create
    return client, calls


def _agent_config(**overrides) -> AgentConfig:
    base = dict(
        enable_reflection=False, enable_memory=False, enable_skills=False,
        enable_experience=False, enable_thinking=False,
    )
    base.update(overrides)
    return AgentConfig(**base)


async def _crash_after_n_completed_steps(agen, n: int = 2, max_events: int = 200):
    """消费事件流，在第 n 个"步骤完成"事件后停止并 aclose（= 客户端中断/进程死亡）。"""
    events = []
    async for ev in agen:
        events.append(ev)
        done = sum(
            1 for e in events
            if e.type == "thinking" and e.plan_step_status == "completed"
        )
        if done >= n:
            break
        assert len(events) < max_events, "未等到足够的步骤完成事件"
    await agen.aclose()
    return events


async def _get_task(factory, task_id: str) -> AgentTask | None:
    async with factory() as db:
        return await db.get(AgentTask, task_id)


def _task_id_of(events) -> str:
    ids = {e.task_id for e in events if e.task_id}
    assert ids, "事件必须盖上 task_id（发现性：客户端拿到恢复入口的钥匙）"
    assert len(ids) == 1
    return ids.pop()


@pytest.mark.asyncio
async def test_crash_then_resume_continues_from_checkpoint(db_factory):
    """崩溃→resume→续跑：从断点继续、不重复执行已完成步骤、最终产物正确、
    任务 COMPLETED、快照终态完整。"""
    client, calls = _fake_openai_client()
    svc = AgentService(openai_client=client)

    # ── 第一次 run：跑到第 2 步完成后"崩溃" ──
    agen = svc.chat(QUERY, organization_id=1, user_id=USER_ID, config=_agent_config())
    events = await _crash_after_n_completed_steps(agen, n=2)
    task_id = _task_id_of(events)

    assert calls["n"] == 2, "假 LLM 只应被前两步各调用一次"
    task = await _get_task(db_factory, task_id)
    assert task.status == TaskStatus.RUNNING, "断流不改状态：陈旧 RUNNING = 可恢复"

    _, payload = await checkpoint.load_for_resume(task_id=task_id, user_id=USER_ID)
    assert payload is not None, "断流兜底快照必须落盘"
    assert [s["status"] for s in payload["plan"]["steps"]] == [
        "completed", "completed", "pending",
    ], "断点保真：前两步 completed、第三步 pending"
    assert payload["partial_output"] == "步骤结果1步骤结果2", "已产出答案前缀被冻结"
    assert payload["ctx"]["artifacts"]["iterations_used"] == 2, "迭代预算随快照延续"

    # ── resume：只续跑剩余 1 步 ──
    events2 = [e async for e in svc.resume_chat(task_id, user_id=USER_ID)]
    chunk_text = "".join(e.content for e in events2 if e.type == "chunk")

    assert any(e.type == "done" for e in events2)
    assert calls["n"] == 3, "resume 只应新发起 1 次 LLM 调用（s3），绝不重跑已完成步"
    assert "步骤结果3" in chunk_text
    assert "步骤结果1" not in chunk_text, "已完成步骤的产出不得在 resume 流里重复回放"

    task = await _get_task(db_factory, task_id)
    assert task.status == TaskStatus.COMPLETED
    assert task.retry_count == 1, "一次 resume = 一次重试计数"
    _, final_payload = await checkpoint.load_for_resume(task_id=task_id, user_id=USER_ID)
    assert [s["status"] for s in final_payload["plan"]["steps"]] == [
        "completed", "completed", "completed",
    ], "终态快照：计划全步骤完成（on_finish 触发点）"
    assert task.progress == 1.0


@pytest.mark.asyncio
async def test_resume_after_completion_is_rejected_and_not_rerun(db_factory):
    client, calls = _fake_openai_client()
    svc = AgentService(openai_client=client)
    agen = svc.chat(QUERY, organization_id=1, user_id=USER_ID, config=_agent_config())
    events = await _crash_after_n_completed_steps(agen, n=2)
    task_id = _task_id_of(events)
    [e async for e in svc.resume_chat(task_id, user_id=USER_ID)]
    n_after_resume = calls["n"]

    dup = svc.resume_chat(task_id, user_id=USER_ID)
    with pytest.raises(checkpoint.TaskNotResumableError):  # 完成后重复 resume → 409 语义
        await anext(dup)
    assert calls["n"] == n_after_resume, "被拒绝的 resume 不得产生任何新调用"


@pytest.mark.asyncio
async def test_concurrent_resume_is_rejected_by_lease(db_factory, monkeypatch):
    monkeypatch.setattr(checkpoint, "redis_client", _FakeRedisLock())
    client, _ = _fake_openai_client()
    svc = AgentService(openai_client=client)
    agen = svc.chat(QUERY, organization_id=1, user_id=USER_ID, config=_agent_config())
    events = await _crash_after_n_completed_steps(agen, n=2)
    task_id = _task_id_of(events)

    assert await checkpoint.claim_task(task_id) is True, "模拟另一个 resume 已持有租约"
    busy = svc.resume_chat(task_id, user_id=USER_ID)
    with pytest.raises(checkpoint.TaskBusyError):  # 并发 resume → 409 语义
        await anext(busy)


@pytest.mark.asyncio
async def test_resume_chat_with_claim_false_does_not_reclaim(db_factory):
    """端点先 prepare_resume（HTTP 409 语义在开流前），再 resume_chat(claim=False)。
    变异点：claim=False 仍走 begin_resume → 陈旧 RUNNING→FAILED→RUNNING 再来一遍
    → retry_count 变 2（断言 1 → 红）。"""
    client, calls = _fake_openai_client()
    svc = AgentService(openai_client=client)
    agen = svc.chat(QUERY, organization_id=1, user_id=USER_ID, config=_agent_config())
    events = await _crash_after_n_completed_steps(agen, n=2)
    task_id = _task_id_of(events)

    task, payload = await svc.prepare_resume(task_id, user_id=USER_ID)
    assert task.status == TaskStatus.RUNNING and payload is not None
    assert (await _get_task(db_factory, task_id)).retry_count == 1

    events2 = [e async for e in svc.resume_chat(task_id, user_id=USER_ID, claim=False)]

    assert any(e.type == "done" for e in events2)
    assert calls["n"] == 3, "claim=False 路径同样只续跑剩余 1 步"
    assert (await _get_task(db_factory, task_id)).retry_count == 1, "不得重复认领"


@pytest.mark.asyncio
async def test_without_checkpoint_write_progress_is_lost_baseline(db_factory, monkeypatch):
    """红基线（报告测试 1）：把 checkpoint 写路径拔成 no-op → 崩溃后进度全丢 →
    resume 只能从规划阶段重跑全部步骤。"不接 checkpoint 会丢"的判别性证明；
    与之成对的上一个用例（写路径开启）只重跑 1 步。"""
    real_save = checkpoint.save_checkpoint

    async def _noop_save(**kwargs):
        return False

    monkeypatch.setattr(checkpoint, "save_checkpoint", _noop_save)
    client, calls = _fake_openai_client()
    svc = AgentService(openai_client=client)

    agen = svc.chat(QUERY, organization_id=1, user_id=USER_ID, config=_agent_config())
    events = await _crash_after_n_completed_steps(agen, n=2)
    task_id = _task_id_of(events)
    assert calls["n"] == 2

    task = await _get_task(db_factory, task_id)
    assert task.context_snapshot is None, "写路径被拔掉：零快照（进度无从恢复）"
    _, payload = await checkpoint.load_for_resume(task_id=task_id, user_id=USER_ID)
    assert payload is None, "无快照 → resume 无断点可续（这正是要修的缺口）"

    # 恢复写路径，走安全降级路径 resume：只能从头重跑全部 3 步
    monkeypatch.setattr(checkpoint, "save_checkpoint", real_save)
    events2 = [e async for e in svc.resume_chat(task_id, user_id=USER_ID, config=_agent_config())]
    assert any(e.type == "done" for e in events2)
    assert calls["n"] == 5, "降级 resume 重跑了全部 3 步（2 + 3）——对比开启路径只 +1"
    assert (await _get_task(db_factory, task_id)).status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_missing_snapshot_resume_degrades_to_fresh_planning(db_factory):
    """无 checkpoint 的 resume（快照缺失，非损坏）：从规划阶段干净重跑，不崩。"""
    client, calls = _fake_openai_client()
    svc = AgentService(openai_client=client)
    # 直接建任务行（模拟"建行成功但一次快照都没来得及写"）
    task_id = await checkpoint.create_task(user_id=USER_ID, organization_id=1, query=QUERY)

    events = [e async for e in svc.resume_chat(task_id, user_id=USER_ID, config=_agent_config())]

    assert any(e.type == "done" for e in events)
    assert calls["n"] == 3, "干净重跑：3 步各一次调用"
    task = await _get_task(db_factory, task_id)
    assert task.status == TaskStatus.COMPLETED
    async with db_factory() as db:
        rows = (await db.execute(select(AgentTask).where(AgentTask.id == task_id))).scalars().all()
    assert len(rows) == 1, "同一 task 行内重跑，不新建任务"
