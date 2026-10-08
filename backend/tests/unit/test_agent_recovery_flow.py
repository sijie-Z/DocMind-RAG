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
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — 注册全部表，供 create_all
from app.agent import checkpoint
from app.agent.config import AgentConfig
from app.agent.exec_context import ExecutionContext
from app.agent.loop import PERAgentLoop
from app.agent.service import AgentService
from app.core.database import Base
from app.models.agent_task import AgentTask, TaskStatus
from app.rag.retriever import HybridRetriever

QUERY = "总结一下这份项目资料"
USER_ID = 7


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """隔离与 checkpoint 无关的副作用（replay 落盘 / 经验回写 / 语义记忆 embedding）。

    m6/R7（#278）：本文件全链用例此前会真实发起 embedding RPC（memory_bridge 的
    语义记忆召回/经验回写经 RAGPipeline.get_embedding → HybridRetriever.get_embedding
    打真 Ollama：本机可达时实测 404、CI 不可达时连接失败——环境应答不同）。桩成
    "无网络、空向量" = 两环境既有的降级结果，用例对环境封闭、结果稳定。
    """
    async def _save(self, ttl=86400 * 7):
        return False

    monkeypatch.setattr(ExecutionContext, "save", _save)
    monkeypatch.setattr(PERAgentLoop, "_write_experiences", AsyncMock())
    monkeypatch.setattr(HybridRetriever, "get_embedding", AsyncMock(return_value=[]))


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


@pytest.mark.asyncio
async def test_truncated_json_snapshot_resume_degrades_and_heals(db_factory):
    """R1（#278）第二类坏快照：快照列是截断 JSON（无服务端校验的载体可达，如
    sqlite/带外写库）——resume 不得被毒丸炸崩，按"无快照"降级干净重跑，且首轮
    新快照落盘即覆盖坏数据（读路径自愈；修复前每次 resume 都在读行时
    JSONDecodeError）。

    变异点：还原 checkpoint 读路径容错（_load_task_row 的 defer 或单独读快照
    的 try/except）→ load_for_resume 抛 JSONDecodeError → 本用例红。
    """
    client, calls = _fake_openai_client()
    svc = AgentService(openai_client=client)
    agen = svc.chat(QUERY, organization_id=1, user_id=USER_ID, config=_agent_config())
    events = await _crash_after_n_completed_steps(agen, n=2)
    task_id = _task_id_of(events)
    assert calls["n"] == 2

    # 带外写库把快照列打成截断 JSON（绕过 ORM 序列化）
    async with db_factory() as db:
        await db.execute(
            text("UPDATE agent_tasks SET context_snapshot = :s WHERE id = :tid"),
            {"s": '{"v": 1, "ctx": {', "tid": task_id},
        )
        await db.commit()

    # resume：不得崩（修复前 JSONDecodeError），降级为从规划干净重跑
    # （快照不可读 → 规则取调用方传入的配置，与"无快照"路径同语义）
    events2 = [e async for e in svc.resume_chat(task_id, user_id=USER_ID, config=_agent_config())]

    assert any(e.type == "done" for e in events2)
    assert calls["n"] == 5, "截断快照 = 无快照：3 步全部干净重跑（2 + 3）"
    task = await _get_task(db_factory, task_id)
    assert task.status == TaskStatus.COMPLETED, "收尾（mark_terminal）不得被毒丸行卡死"
    _, healed = await checkpoint.load_for_resume(task_id=task_id, user_id=USER_ID)
    assert healed is not None and healed["plan"], "首轮新快照已覆盖坏数据：读路径自愈"


@pytest.mark.asyncio
async def test_resume_stream_events_all_carry_task_id_stamp(db_factory):
    """m5/R6（#278）：resume 流的**每一个**事件都必须盖章 task_id（chat 路径已
    有 _task_id_of 守卫；resume 路径此前零覆盖——评审 MUT-D 删盖章 37 全绿）。

    变异点：删 service.resume_chat 里的 `event.task_id = task_id` → resume 流
    事件全部退化为空 task_id → 本用例红。
    """
    client, _ = _fake_openai_client()
    svc = AgentService(openai_client=client)
    agen = svc.chat(QUERY, organization_id=1, user_id=USER_ID, config=_agent_config())
    events = await _crash_after_n_completed_steps(agen, n=1)
    task_id = _task_id_of(events)

    events2 = [e async for e in svc.resume_chat(task_id, user_id=USER_ID)]

    assert any(e.type == "done" for e in events2)
    unstamped = [e.type for e in events2 if e.task_id != task_id]
    assert not unstamped, f"resume 流事件必须全部携带 task_id（缺失事件类型: {unstamped}）"


@pytest.mark.asyncio
async def test_resume_disconnect_releases_lease_for_immediate_retry(db_factory, monkeypatch):
    """m2/R3（#278）：resume 断流（客户端中断）归还租约——立即重试不被 TaskBusy
    拒（修复前断流兜底快照会把租约续到 900s，重试被拒至多 15 分钟）。

    变异点：去掉 _checkpoint_disconnect 的 release_lease 归还（还原旧形态）→
    断流后锁仍在、重试抛 TaskBusy → 本用例红。
    """
    fake = _FakeRedisLock()
    monkeypatch.setattr(checkpoint, "redis_client", fake)
    client, _ = _fake_openai_client()
    svc = AgentService(openai_client=client)
    agen = svc.chat(QUERY, organization_id=1, user_id=USER_ID, config=_agent_config())
    events = await _crash_after_n_completed_steps(agen, n=2)
    task_id = _task_id_of(events)

    # 第一次 resume：消费到 1 个步骤完成后断流（= 客户端中断/server 取消）
    agen2 = svc.resume_chat(task_id, user_id=USER_ID)
    await _crash_after_n_completed_steps(agen2, n=1)

    assert checkpoint._lock_key(task_id) not in fake.store, (
        "resume 断流必须归还租约（否则被兜底快照续到 900s）"
    )
    # 立即重试：不得被 TaskBusy 拒绝；这一趟必须跑到完成
    events3 = [e async for e in svc.resume_chat(task_id, user_id=USER_ID)]
    assert any(e.type == "done" for e in events3)
    assert (await _get_task(db_factory, task_id)).status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_resume_passes_task_org_truthfully_without_or1(db_factory, monkeypatch):
    """m3（#278，#168/#268 禁模式）：resume 构造 PERAgentLoop 时如实传任务归属
    组织——org=None 如实为 None（绝不 `or 1` 伪造落入 1 号组织）；org=42 如实
    为 42（防过度修复/防归属错位）。

    变异点：恢复 `task.organization_id or 1` → None 段捕获到 1 → 本用例红。
    """
    captured: list[int | None] = []

    class _CapturingLoop:
        def __init__(self, openai_client=None, config=None, organization_id=None, user_id=None):
            captured.append(organization_id)
            self.checkpoint_live = None

        async def run(self, query, **kwargs):
            if False:  # 空事件 async generator：本用例只钉构造参数
                yield None

    monkeypatch.setattr("app.agent.service.PERAgentLoop", _CapturingLoop)
    client, _ = _fake_openai_client()
    svc = AgentService(openai_client=client)

    for org in (None, 42):
        task_id = await checkpoint.create_task(user_id=USER_ID, organization_id=org, query=QUERY)
        events = [e async for e in svc.resume_chat(task_id, user_id=USER_ID, config=_agent_config())]
        assert events == [], "假 loop 不产事件（本用例只钉构造参数）"
        assert (await _get_task(db_factory, task_id)).status == TaskStatus.COMPLETED

    assert captured == [None, 42], (
        "resume 必须如实传 task.organization_id（bug 形态：None 被 `or 1` 伪造为 1）"
    )
