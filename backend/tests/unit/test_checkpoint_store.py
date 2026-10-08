"""issue #201 最小版：checkpoint 持久化层（sqlite 全链，无外部服务）。

覆盖：create_task / save_checkpoint / load_for_resume 往返、进度列派生、
partial 截断、归属校验、损坏快照安全降级、状态机（begin_resume / mark_terminal
的合法与非法路径）、Redis 租约（假 client：冲突拒绝 / 释放 / 续租 / 缺失降级）。

AgentTask 表经 create_all 建（与迁移路径的口径由 test_schema_path_parity 门禁；
006 已把 status 枚举对齐成大写成员名——本模块全程走 ORM，不受裸 SQL 大小写坑影响）。
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — 注册全部表，供 create_all（AgentTask 在内）
from app.agent import checkpoint
from app.agent.checkpoint import (
    PARTIAL_OUTPUT_CAP,
    TaskBusyError,
    TaskNotFoundError,
    TaskNotResumableError,
)
from app.agent.config import AgentConfig
from app.agent.exec_context import ExecutionContext
from app.agent.planner import Plan, PlanStep
from app.core.database import Base
from app.models.agent_task import AgentTask, TaskStatus


@pytest.fixture
async def db_factory(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'checkpoint.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(checkpoint, "AsyncSessionLocal", factory)
    yield factory
    await engine.dispose()


async def _get_task(factory, task_id: str) -> AgentTask | None:
    async with factory() as db:
        return await db.get(AgentTask, task_id)


class _FakeRedis:
    """checkpoint 用到的三个命令的最小替身：set(nx/ex) / delete / expire。"""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.expires: dict[str, int | None] = {}

    def __bool__(self):
        return True

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        self.expires[key] = ex
        return True

    async def delete(self, key):
        self.store.pop(key, None)
        return 1

    async def expire(self, key, ttl):
        if key in self.store:
            self.expires[key] = ttl
            return True
        return False


class _RedisDown:
    def __bool__(self):
        return False


async def test_create_task_starts_running_with_expiry(db_factory):
    task_id = await checkpoint.create_task(user_id=7, organization_id=1, query="问题")

    assert task_id and len(task_id) == 12, "task.id = uuid4 hex[:12]（对齐 agent:replay 命名空间）"
    task = await _get_task(db_factory, task_id)
    assert task.status == TaskStatus.RUNNING, "本版无排队语义：起点直接 RUNNING"
    assert task.user_id == 7 and task.organization_id == 1
    assert task.retry_count == 0 and task.context_snapshot is None
    assert task.expires_at is not None


async def test_save_then_load_roundtrip_carries_plan_progress_and_artifacts(db_factory):
    task_id = await checkpoint.create_task(user_id=7, organization_id=1, query="原始问题")
    ctx = ExecutionContext(query="原始问题", task_id=task_id)
    ctx.artifacts["iterations_used"] = 1
    ctx.total_tokens = 250
    ctx.current_step_id = "s1"
    plan = Plan(id="p1", goal="g", steps=[
        PlanStep(id="s1", description="一", status="completed", result="R1"),
        PlanStep(id="s2", description="二", dependencies=["s1"]),
    ], completed_steps=1)

    ok = await checkpoint.save_checkpoint(
        task_id=task_id, ctx=ctx, plan=plan, trigger="after_each_step",
        partial_output="半截答案", history=[{"role": "user", "content": "原始问题"}],
        config=AgentConfig(model="m"), run_state={"replan_attempts": 0},
        session_id="sess-9",
    )
    assert ok

    task, payload = await checkpoint.load_for_resume(task_id=task_id, user_id=7)
    assert payload is not None
    assert payload["ctx"]["artifacts"]["iterations_used"] == 1, "迭代预算必须随快照落盘"
    assert payload["ctx"]["total_tokens"] == 250
    assert payload["partial_output"] == "半截答案"
    assert payload["plan"]["steps"][0]["result"] == "R1"
    assert payload["session_id"] == "sess-9"
    # 进度列：任务视图（Part 2 的 GET /tasks/{id}）不解快照也能展示
    assert task.completed_steps == 1 and task.total_steps == 2 and task.progress == 0.5
    assert task.current_step_id == "s1" and task.plan_id == "p1"


async def test_save_checkpoint_caps_partial_output(db_factory):
    task_id = await checkpoint.create_task(user_id=1, organization_id=None, query="q")
    ok = await checkpoint.save_checkpoint(
        task_id=task_id, ctx=ExecutionContext(query="q"), plan=None,
        trigger="on_disconnect", partial_output="x" * (PARTIAL_OUTPUT_CAP + 100),
    )
    assert ok
    _, payload = await checkpoint.load_for_resume(task_id=task_id, user_id=1)
    assert len(payload["partial_output"]) == PARTIAL_OUTPUT_CAP
    assert payload["plan"] is None, "崩溃于规划阶段：快照有 ctx、无 plan（可重规划）"


async def test_load_for_resume_scopes_to_owner(db_factory):
    task_id = await checkpoint.create_task(user_id=7, organization_id=1, query="q")
    with pytest.raises(TaskNotFoundError):  # 非本人 → 404 语义
        await checkpoint.load_for_resume(task_id=task_id, user_id=8)
    with pytest.raises(TaskNotFoundError):
        await checkpoint.load_for_resume(task_id="does-not-exist", user_id=7)


async def test_load_task_org_none_task_not_reachable_with_org_context(db_factory):
    """收紧语义（评审 B1 附带项）：给定组织上下文时要求**严格相等**——任务
    org=None 的旧守卫（`task.organization_id is not None`）会把它放行给任意组织。
    变异点：恢复该守卫 → 本条红（返回值不再抛错）。"""
    async with db_factory() as db:
        db.add(AgentTask(id="noorg0000001", user_id=1, organization_id=None,
                         query="q", status=TaskStatus.RUNNING))
        await db.commit()

    with pytest.raises(TaskNotFoundError):
        await checkpoint.load_task(task_id="noorg0000001", user_id=1, organization_id=1)

    # 不带组织上下文（归一化的 service 内部读路径）仍可读：None = "不校验"
    task = await checkpoint.load_task(task_id="noorg0000001", user_id=1)
    assert task.id == "noorg0000001"


async def test_corrupt_snapshot_degrades_to_no_payload_without_raising(db_factory):
    """损坏 checkpoint 的安全降级：任何结构性坏数据 → payload None，绝不崩。
    变异点：parse_snapshot 放行未知版本/非 dict → 对应断言变红。"""
    for bad in ({"v": 999, "ctx": {}}, {"v": 1, "ctx": "bad"}, ["garbage"], "junk"):
        task_id = await checkpoint.create_task(user_id=1, organization_id=None, query="q")
        async with db_factory() as db:
            task = await db.get(AgentTask, task_id)
            task.context_snapshot = bad
            await db.commit()
        task, payload = await checkpoint.load_for_resume(task_id=task_id, user_id=1)
        assert task is not None and payload is None


async def test_truncated_json_snapshot_row_read_write_heals(db_factory):
    """R1（issue #278）：快照列是截断 JSON（无服务端校验的载体可达；生产 MySQL
    JSON 列拦得住，sqlite/带外写库拦不住）时，任务行照常可读可写——读路径按
    "无快照"降级不抛，认领/收尾/落盘不被毒丸卡死，首个新快照覆盖坏数据（自愈）。

    变异点：还原 _load_task_row 的 defer（整行 db.get）/ 单独读快照的 try/except
    → 对应步骤抛 JSONDecodeError → 本用例红。
    """
    task_id = await checkpoint.create_task(user_id=7, organization_id=1, query="q")
    async with db_factory() as db:
        await db.execute(
            text("UPDATE agent_tasks SET context_snapshot = :s WHERE id = :tid"),
            {"s": '{"v": 1, "ctx": {"query": "q"', "tid": task_id},  # 截断的合法开头
        )
        await db.commit()

    task, payload = await checkpoint.load_for_resume(task_id=task_id, user_id=7)
    assert task is not None and payload is None, "截断快照按无快照降级（不得抛 JSONDecodeError）"

    claimed = await checkpoint.begin_resume(task_id)  # 毒丸行不得卡住认领
    assert claimed.status == TaskStatus.RUNNING and claimed.retry_count == 1

    assert await checkpoint.mark_terminal(task_id, TaskStatus.FAILED, error="x") is True

    ok = await checkpoint.save_checkpoint(  # 新快照落盘 = 覆盖坏数据（自愈）
        task_id=task_id, ctx=ExecutionContext(query="q"), plan=None, trigger="after_planning",
    )
    assert ok is True

    _, healed = await checkpoint.load_for_resume(task_id=task_id, user_id=7)
    assert healed is not None and healed["v"] == 1, "坏数据已被新快照覆盖：读路径恢复"


async def test_begin_resume_claims_stale_running_and_failed(db_factory):
    """陈旧 RUNNING（崩溃形态）经 RUNNING→FAILED→RUNNING 接管——若实现走了
    RUNNING→RUNNING 会抛 ValueError（该边不在状态机里）→ 本测试红。"""
    task_id = await checkpoint.create_task(user_id=1, organization_id=None, query="q")

    task = await checkpoint.begin_resume(task_id)
    assert task.status == TaskStatus.RUNNING
    assert (await _get_task(db_factory, task_id)).retry_count == 1, "每次认领计一次重试"

    # run 内异常 → FAILED；FAILED→RUNNING 是既有重试边，可再次恢复
    assert await checkpoint.mark_terminal(task_id, TaskStatus.FAILED, error="boom", error_type="X")
    task2 = await checkpoint.begin_resume(task_id)
    assert task2.status == TaskStatus.RUNNING
    assert (await _get_task(db_factory, task_id)).retry_count == 2
    row = await _get_task(db_factory, task_id)
    assert row.last_error == "boom" and row.last_error_type == "X"


async def test_terminal_task_refuses_resume_and_mark_terminal_is_safe(db_factory):
    task_id = await checkpoint.create_task(user_id=1, organization_id=None, query="q")
    assert await checkpoint.mark_terminal(task_id, TaskStatus.COMPLETED)
    row = await _get_task(db_factory, task_id)
    assert row.status == TaskStatus.COMPLETED and row.completed_at is not None

    with pytest.raises(TaskNotResumableError):
        await checkpoint.begin_resume(task_id)

    # 幂等：重复收尾不报错；终态→终态的非法转换安全拒绝（False、不抛）
    assert await checkpoint.mark_terminal(task_id, TaskStatus.COMPLETED) is True
    assert await checkpoint.mark_terminal(task_id, TaskStatus.FAILED) is False
    assert (await _get_task(db_factory, task_id)).status == TaskStatus.COMPLETED
    # 缺失/空 id 一律安全返回 False
    assert await checkpoint.mark_terminal("missing", TaskStatus.FAILED) is False
    assert await checkpoint.mark_terminal(None, TaskStatus.FAILED) is False


async def test_mark_terminal_refuses_illegal_transition_from_pending(db_factory):
    async with db_factory() as db:
        db.add(AgentTask(id="pend00000001", user_id=1, organization_id=None,
                         query="q", status=TaskStatus.PENDING))
        await db.commit()

    assert await checkpoint.mark_terminal("pend00000001", TaskStatus.COMPLETED) is False
    assert (await _get_task(db_factory, "pend00000001")).status == TaskStatus.PENDING


async def test_claim_conflict_then_release_frees_the_lease(db_factory, monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(checkpoint, "redis_client", fake)
    task_id = await checkpoint.create_task(user_id=1, organization_id=None, query="q")
    key = checkpoint._lock_key(task_id)

    assert await checkpoint.claim_task(task_id) is True
    assert await checkpoint.claim_task(task_id) is False, "并发 resume：拿不到锁"
    with pytest.raises(TaskBusyError):  # 重复 resume → 409 语义
        await checkpoint.begin_resume(task_id)

    # save_checkpoint 顺带续租（resume 期间每步快照都刷新租约）
    fake.expires[key] = 1
    await checkpoint.save_checkpoint(
        task_id=task_id, ctx=ExecutionContext(query="q"), plan=None,
        trigger="after_each_step",
    )
    assert fake.expires[key] == checkpoint.LEASE_SECONDS

    await checkpoint.release_task(task_id)
    assert key not in fake.store
    assert await checkpoint.claim_task(task_id) is True


async def test_claim_degrades_when_redis_unavailable(db_factory, monkeypatch):
    """Redis 不可用 → 降级为"仅状态检查"（放行），不得把 resume 卡死。"""
    monkeypatch.setattr(checkpoint, "redis_client", _RedisDown())
    task_id = await checkpoint.create_task(user_id=1, organization_id=None, query="q")
    assert await checkpoint.claim_task(task_id) is True
    task = await checkpoint.begin_resume(task_id)
    assert task.status == TaskStatus.RUNNING


async def test_mark_terminal_releases_lease(db_factory, monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr(checkpoint, "redis_client", fake)
    task_id = await checkpoint.create_task(user_id=1, organization_id=None, query="q")
    await checkpoint.claim_task(task_id)

    assert await checkpoint.mark_terminal(task_id, TaskStatus.COMPLETED)
    assert checkpoint._lock_key(task_id) not in fake.store, "终态落定必须释放租约"
