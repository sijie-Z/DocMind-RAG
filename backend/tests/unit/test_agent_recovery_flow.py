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

import asyncio
import json
import time
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


async def _consume_until_n_completed(agen, n: int = 1, max_events: int = 200):
    """消费到第 n 个"步骤完成"事件即停，**不关闭**生成器（= 活跃执行期快照）。"""
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
    return events


async def _crash_after_n_completed_steps(agen, n: int = 2, max_events: int = 200):
    """消费事件流，在第 n 个"步骤完成"事件后停止并 aclose（= 客户端中断/进程死亡）。"""
    events = await _consume_until_n_completed(agen, n, max_events)
    await agen.aclose()
    return events


async def _wait_until(pred, timeout: float = 3.0) -> bool:
    """轮询等待（本文件跨任务时序断言用；窗口小、代价低）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        await asyncio.sleep(0.01)
    return pred()


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


@pytest.mark.parametrize(
    "edit, healed_ok",
    [
        (
            lambda snap: snap["ctx"]["artifacts"].__setitem__("iterations_used", "很多"),
            lambda healed: isinstance(healed["ctx"]["artifacts"].get("iterations_used"), int),
        ),
        (
            lambda snap: snap.__setitem__("history", "boom"),
            lambda healed: healed["history"] is None or all(
                isinstance(m, dict) for m in healed["history"]
            ),
        ),
    ],
    ids=["artifacts-iterations-not-int", "history-not-list"],
)
@pytest.mark.asyncio
async def test_value_drift_snapshot_resume_degrades_and_heals(edit, healed_ok, db_factory):
    """#287/R1 值级漂移全链：带外把快照改成"合法 JSON、值类型坏"——resume
    不得炸（修复前：iterations_used="很多" → executor `"很多" >= max_iterations`
    TypeError；history="boom" → 真实 executor 的消息构建 `msg.get` AttributeError
    ——且错误兜底把坏值原样回写快照、此后每次 resume 复炸 = 毒丸），按降级语义
    续跑剩余步骤，新快照里坏值被修复（规整后的形态）——读侧闸门 + 写侧"以
    校验后值为准"两半同时闭合。

    变异点：去掉读侧规整 → resume 抛对应异常 → 红（"resume 不炸"面）；
    写侧规整由 test_checkpoint_roundtrip 的写侧断言单独钉住（"不以坏值
    回写"面）。
    """
    client, calls = _fake_openai_client()
    svc = AgentService(openai_client=client)
    agen = svc.chat(QUERY, organization_id=1, user_id=USER_ID, config=_agent_config())
    events = await _crash_after_n_completed_steps(agen, n=2)
    task_id = _task_id_of(events)
    assert calls["n"] == 2

    # 带外写库（绕过回写路径）：合法 JSON 快照 + 值类型漂移
    async with db_factory() as db:
        row = await db.get(AgentTask, task_id)
        snap = row.context_snapshot
        edit(snap)
        await db.execute(
            text("UPDATE agent_tasks SET context_snapshot = :s WHERE id = :tid"),
            {"s": json.dumps(snap, ensure_ascii=False), "tid": task_id},
        )
        await db.commit()

    # resume：不得炸（修复前 TypeError/AttributeError）、只续跑剩余 1 步、COMPLETED
    events2 = [e async for e in svc.resume_chat(task_id, user_id=USER_ID)]

    assert any(e.type == "done" for e in events2)
    assert calls["n"] == 3, "坏值降级为默认：只续跑剩余 1 步（绝不重跑/崩溃）"
    task = await _get_task(db_factory, task_id)
    assert task.status == TaskStatus.COMPLETED, "收尾不得被值级坏数据卡死"

    # 快照被修复：坏值不再存在（写回的是规整后的形态）
    _, healed = await checkpoint.load_for_resume(task_id=task_id, user_id=USER_ID)
    assert healed_ok(healed), f"新快照必须已修复坏值（实际 {healed.get('ctx', {}).get('artifacts')} / history={healed.get('history')!r}）"


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
async def test_disconnect_finalize_order_snapshot_before_lease_release(db_factory, monkeypatch):
    """#287 项2（顺序不变量，评审 S-1 反转变异 30 全绿 → 本用例钉死）：
    resume 断流收尾时，兜底快照必须先落盘**完成**，租约归还只能发生在其后。
    顺序不可反：反向会让新认领者先拿到锁开跑，迟到的旧快照随后覆盖它的更新
    状态（resume 语义破坏窗口）；且 save 内部还会续租，与"刚释放"打架。

    变异点：把收尾单元内两步顺序反转（release→save）→ 实测序列
    ["release", "save-done"] ≠ 期望 → 红。
    """
    fake = _FakeRedisLock()
    monkeypatch.setattr(checkpoint, "redis_client", fake)
    client, _ = _fake_openai_client()
    svc = AgentService(openai_client=client)
    agen = svc.chat(QUERY, organization_id=1, user_id=USER_ID, config=_agent_config())
    events = await _crash_after_n_completed_steps(agen, n=2)
    task_id = _task_id_of(events)

    # 探针在初始 chat 断流之后安装：只观察这一次 resume 断流的收尾序列
    order: list[str] = []
    orig_save = checkpoint.save_from_live
    orig_release = checkpoint.release_task

    async def spy_save(**kwargs):
        result = await orig_save(**kwargs)
        order.append("save-done")
        return result

    async def spy_release(tid):
        order.append("release")
        return await orig_release(tid)

    monkeypatch.setattr(checkpoint, "save_from_live", spy_save)
    monkeypatch.setattr(checkpoint, "release_task", spy_release)

    # resume 消费到 1 个步骤完成后断流（= 客户端中断）
    agen2 = svc.resume_chat(task_id, user_id=USER_ID)
    await _crash_after_n_completed_steps(agen2, n=1)

    assert order == ["save-done", "release"], (
        "断流收尾顺序不变量：快照先落（完成）→ 租约后还；实测 " + str(order)
    )
    assert checkpoint._lock_key(task_id) not in fake.store, "顺序对 + 租约确已归还"


@pytest.mark.asyncio
async def test_chat_disconnect_never_releases_and_active_run_keeps_lease(db_factory, monkeypatch):
    """#287 项3（release_lease 守卫，评审 S-3 无条件还租变异 30 全绿 → 本用例钉死）：
    - 活跃执行期（生成器仍在产事件、未断流）不误还租——锁在位、release 零调用；
    - chat 断流绝不还租（从未认领；无条件归还只会误删并发 resume 持有的锁）；
    - 只有 resume 断流才恰好归还一次。

    变异点：守卫 `if release_lease:` 无条件化（chat 也还租）→ chat 段 release
    调用数 ≠ 0 → 红。
    """
    fake = _FakeRedisLock()
    monkeypatch.setattr(checkpoint, "redis_client", fake)
    client, _ = _fake_openai_client()
    svc = AgentService(openai_client=client)

    released: list[str | None] = []
    orig_release = checkpoint.release_task

    async def spy_release(tid):
        released.append(tid)
        return await orig_release(tid)

    monkeypatch.setattr(checkpoint, "release_task", spy_release)

    # ── chat 路径：活跃期 + 断流后，零归还 ──
    agen = svc.chat(QUERY, organization_id=1, user_id=USER_ID, config=_agent_config())
    events = await _consume_until_n_completed(agen, n=1)
    task_id = _task_id_of(events)
    assert released == [], "活跃执行期不得误还租（chat 从未持锁）"
    await agen.aclose()
    await asyncio.sleep(0.1)
    assert released == [], "chat 断流不得归还（无认领；无条件归还会误删并发锁）"

    # ── resume 路径：活跃期零归还（锁在位）；断流恰好归还一次 ──
    agen2 = svc.resume_chat(task_id, user_id=USER_ID)
    await _consume_until_n_completed(agen2, n=1)
    assert checkpoint._lock_key(task_id) in fake.store, "resume 持有期锁必须在位"
    assert released == [], "resume 活跃执行期不得提前还租"
    await agen2.aclose()
    assert await _wait_until(lambda: len(released) == 1), "断流必须恰好归还一次"
    assert released == [task_id], "只归还本任务的租约（不多不少）"
    assert checkpoint._lock_key(task_id) not in fake.store


@pytest.mark.asyncio
async def test_disconnect_finalize_unit_survives_cancel_on_outer_await(db_factory, monkeypatch):
    """#287 项4（shield 语义边界，按 #300/F1 修复后的实际语义钉）+
    #300 F1 引擎级判别：取消落在收尾单元 shield 的**外层 await** 上
    （ASGI 取消路径的实测形态：anyio 任务组层级触发式重复投递恰好打断
    "尚未完成的外层 await"）——受保护单元（save→release 同一任务）不受影响，
    在后台完整执行：租约仍归还、顺序仍"快照先落、还租在后"。

    变异点：拆回两个独立 shield（修复前形态）→ 取消打断首个外层 await →
    release 分支不可达 → 锁仍在、order 缺 "release" → 红。
    """
    fake = _FakeRedisLock()
    monkeypatch.setattr(checkpoint, "redis_client", fake)
    task_id = await checkpoint.create_task(user_id=USER_ID, organization_id=1, query=QUERY)
    assert await checkpoint.claim_task(task_id) is True, "认领租约（模拟 resume 持有）"

    gate = asyncio.Event()
    order: list[str] = []
    orig_save = checkpoint.save_from_live
    orig_release = checkpoint.release_task

    async def gated_save(**kwargs):
        await gate.wait()  # 挡住 save：确保取消时任务停进 shield 外层 await
        result = await orig_save(**kwargs)
        order.append("save")
        return result

    async def spy_release(tid):
        order.append("release")
        return await orig_release(tid)

    monkeypatch.setattr(checkpoint, "save_from_live", gated_save)
    monkeypatch.setattr(checkpoint, "release_task", spy_release)

    class _Agent:
        checkpoint_live = {
            "ctx": ExecutionContext(query=QUERY, task_id=task_id),
            "plan": None,
            "partial_output": "",
            "history": None,
            "config": _agent_config(),
        }

    svc = AgentService(openai_client=MagicMock())
    t = asyncio.create_task(
        svc._checkpoint_disconnect(task_id, _Agent(), release_lease=True)
    )
    await asyncio.sleep(0.05)  # 让任务停到 shield 外层 await（save 挂在 gate 上）
    assert order == [], "收尾单元应被 gate 挡住（前置条件）"

    t.cancel()  # 取消落点 = shield 外层 await（ASGI 路径同点）
    with pytest.raises(asyncio.CancelledError):
        await t
    assert order == [], "外层 await 被取消，但受保护单元未被打断（还在 gate 上）"

    gate.set()  # 放行受保护单元（模拟后台完成）
    assert await _wait_until(lambda: len(order) == 2), (
        f"受保护单元必须后台完整执行（save→release），实测 {order}"
    )
    assert order == ["save", "release"], "顺序不变量在取消路径上同样成立"
    assert checkpoint._lock_key(task_id) not in fake.store, "租约必须归还（#300 核心断言）"


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
