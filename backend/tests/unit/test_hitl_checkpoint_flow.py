"""HITL：checkpoint 层的审批暂停 / 审批记账 / 取消旗收敛（sqlite 全链）。

被测契约（app/agent/checkpoint.py 的 mark_waiting_approval /
apply_approval_decision / begin_resume 取消旗分支 / get_pending_approval）：

- 暂停写路径：RUNNING→WAITING_APPROVAL 经状态机、幂等、终态拒绝、释放租约；
- 审批记账：单写者顺序（租约→校验→记账→转 RUNNING）、steer 指令入
  run_state.steering、pending 清除、retry_count 递增；
- 409 语义：非 waiting_approval / 无 pending / 终态 / 超时（FAILED +
  approval_timeout，绝不伪装"被拒绝"）；
- 取消旗：begin_resume 观察到旗 → 如实收敛 CANCELLED 并拒绝恢复。
"""

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — 注册全部表，供 create_all
from app.agent import approval, checkpoint
from app.agent.config import AgentConfig
from app.agent.exec_context import ExecutionContext
from app.agent.planner import Plan, PlanStep
from app.core.database import Base
from app.models.agent_task import AgentTask, TaskStatus, can_transition


@pytest.fixture
async def db_factory(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'hitl.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(checkpoint, "AsyncSessionLocal", factory)
    yield factory
    await engine.dispose()


class _FakeRedis:
    """checkpoint 用到的命令替身：set(nx/ex) / setex / get / delete / expire。"""

    def __init__(self):
        self.store: dict[str, str] = {}

    def __bool__(self):
        return True

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


def _pending(tool: str = "execute_python", args: dict | None = None, **extra) -> dict:
    args = args if args is not None else {"code": "print(1)"}
    request = approval.ApprovalRequest(
        tool_name=tool,
        tool_args=args,
        tool_call_id="tc-1",
        risk_level="high",
        reason="高风险工具执行前需人工审批",
        step_id="s1",
        plan_id="p1",
        **extra,
    )
    return request.to_dict()


async def _seed_waiting_task(
    db_factory, *, pending: dict | None
) -> tuple[str, ExecutionContext, Plan]:
    """种一个"审批门挂起"的现场：RUNNING 任务 + 快照（含 pending_approval）。"""
    task_id = await checkpoint.create_task(user_id=7, organization_id=1, query="跑个脚本")
    ctx = ExecutionContext(query="跑个脚本", task_id=task_id)
    plan = Plan(id="p1", goal="g", steps=[PlanStep(id="s1", description="一")])
    run_state = {"pending_approval": pending} if pending is not None else {}
    assert await checkpoint.save_checkpoint(
        task_id=task_id,
        ctx=ctx,
        plan=plan,
        trigger="before_approval",
        config=AgentConfig(),
        run_state=run_state,
        session_id="sess-1",
    )
    assert await checkpoint.mark_waiting_approval(task_id)
    return task_id, ctx, plan


async def _get_task(factory, task_id: str) -> AgentTask | None:
    async with factory() as db:
        return await db.get(AgentTask, task_id)


# ── 状态机边 ─────────────────────────────────────────────────────────────────


class TestTransitions:
    def test_waiting_approval_edges(self):
        assert can_transition(TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL)
        assert can_transition(TaskStatus.WAITING_APPROVAL, TaskStatus.RUNNING)
        assert can_transition(TaskStatus.WAITING_APPROVAL, TaskStatus.CANCELLED)
        assert can_transition(TaskStatus.WAITING_APPROVAL, TaskStatus.FAILED)

    def test_cancelled_edges_and_terminal(self):
        for src in (
            TaskStatus.PENDING,
            TaskStatus.RUNNING,
            TaskStatus.WAITING_TOOL,
            TaskStatus.WAITING_HUMAN,
            TaskStatus.FAILED,
        ):
            assert can_transition(src, TaskStatus.CANCELLED), src
        assert not can_transition(TaskStatus.CANCELLED, TaskStatus.RUNNING)
        assert not can_transition(TaskStatus.COMPLETED, TaskStatus.CANCELLED)

    def test_status_helpers(self):
        from app.models.agent_task import AgentTask as T

        def _mk(status):
            return T(id="x", user_id=1, query="q", status=status)

        assert _mk(TaskStatus.CANCELLED).is_terminal
        assert not _mk(TaskStatus.WAITING_APPROVAL).is_terminal
        assert _mk(TaskStatus.WAITING_APPROVAL).is_recoverable
        assert not _mk(TaskStatus.CANCELLED).is_recoverable


# ── 暂停写路径 ───────────────────────────────────────────────────────────────


class TestMarkWaitingApproval:
    @pytest.mark.asyncio
    async def test_transitions_and_releases_lease(self, db_factory, fake_redis):
        task_id = await checkpoint.create_task(user_id=7, organization_id=1, query="q")
        assert await checkpoint.claim_task(task_id) is True
        assert checkpoint._lock_key(task_id) in fake_redis.store

        assert await checkpoint.mark_waiting_approval(task_id) is True
        task = await _get_task(db_factory, task_id)
        assert task.status == TaskStatus.WAITING_APPROVAL
        assert checkpoint._lock_key(task_id) not in fake_redis.store, (
            "暂停必须归还租约——审批端点的 resume 认领不能被卡住"
        )

    @pytest.mark.asyncio
    async def test_idempotent_on_reentry(self, db_factory, fake_redis):
        task_id = await checkpoint.create_task(user_id=7, organization_id=1, query="q")
        assert await checkpoint.mark_waiting_approval(task_id) is True
        assert await checkpoint.mark_waiting_approval(task_id) is True

    @pytest.mark.asyncio
    async def test_refuses_terminal(self, db_factory, fake_redis):
        task_id = await checkpoint.create_task(user_id=7, organization_id=1, query="q")
        assert await checkpoint.mark_terminal(task_id, TaskStatus.COMPLETED) is True
        assert await checkpoint.mark_waiting_approval(task_id) is False
        task = await _get_task(db_factory, task_id)
        assert task.status == TaskStatus.COMPLETED


# ── 审批记账 ─────────────────────────────────────────────────────────────────


class TestApplyApprovalDecision:
    @pytest.mark.asyncio
    async def test_approve_records_decision_steering_and_resumes(self, db_factory, fake_redis):
        task_id, _, _ = await _seed_waiting_task(db_factory, pending=_pending())

        task, envelope, record = await checkpoint.apply_approval_decision(
            task_id=task_id,
            user_id=7,
            organization_id=1,
            decision="approve",
            modified_instruction="只允许查 2024 年数据",
            note="看过了",
        )

        assert task.status == TaskStatus.RUNNING
        assert record["decision"] == "approve"
        assert record["tool_name"] == "execute_python"
        assert record["modified_instruction"] == "只允许查 2024 年数据"
        run_state = envelope["run"]
        assert "pending_approval" not in run_state
        assert run_state["approval_decisions"][-1]["decision"] == "approve"
        assert run_state["steering"] == ["只允许查 2024 年数据"]
        fresh = await _get_task(db_factory, task_id)
        assert fresh.status == TaskStatus.RUNNING
        assert fresh.retry_count == 1
        # 认领成功：租约由后续 resume 流持有（本函数不释放）
        assert checkpoint._lock_key(task_id) in fake_redis.store

    @pytest.mark.asyncio
    async def test_deny_is_sticky_but_no_steering(self, db_factory, fake_redis):
        task_id, _, _ = await _seed_waiting_task(db_factory, pending=_pending())
        _, envelope, record = await checkpoint.apply_approval_decision(
            task_id=task_id,
            user_id=7,
            organization_id=1,
            decision="deny",
            modified_instruction="不该被采纳",
            note="生产环境禁用",
        )
        assert record["decision"] == "deny"
        assert record["modified_instruction"] is None, "deny 不携带改指令（语义只属 approve）"
        run_state = envelope["run"]
        assert "steering" not in run_state
        assert run_state["approval_decisions"][-1]["args_hash"] == record["args_hash"]

    @pytest.mark.asyncio
    async def test_409_when_not_waiting_approval(self, db_factory, fake_redis):
        task_id = await checkpoint.create_task(user_id=7, organization_id=1, query="q")
        with pytest.raises(checkpoint.TaskNotWaitingApprovalError):
            await checkpoint.apply_approval_decision(
                task_id=task_id,
                user_id=7,
                organization_id=1,
                decision="approve",
            )
        # 失败路径释放租约（可立即重试）
        assert checkpoint._lock_key(task_id) not in fake_redis.store

    @pytest.mark.asyncio
    async def test_409_when_no_pending_request(self, db_factory, fake_redis):
        task_id, _, _ = await _seed_waiting_task(db_factory, pending=None)
        with pytest.raises(checkpoint.TaskNotWaitingApprovalError):
            await checkpoint.apply_approval_decision(
                task_id=task_id,
                user_id=7,
                organization_id=1,
                decision="approve",
            )

    @pytest.mark.asyncio
    async def test_409_when_terminal(self, db_factory, fake_redis):
        task_id, _, _ = await _seed_waiting_task(db_factory, pending=_pending())
        assert await checkpoint.mark_terminal(task_id, TaskStatus.CANCELLED) is True
        with pytest.raises(checkpoint.TaskNotResumableError):
            await checkpoint.apply_approval_decision(
                task_id=task_id,
                user_id=7,
                organization_id=1,
                decision="approve",
            )

    @pytest.mark.asyncio
    async def test_404_for_foreign_user(self, db_factory, fake_redis):
        task_id, _, _ = await _seed_waiting_task(db_factory, pending=_pending())
        with pytest.raises(checkpoint.TaskNotFoundError):
            await checkpoint.apply_approval_decision(
                task_id=task_id,
                user_id=999,
                organization_id=1,
                decision="approve",
            )

    @pytest.mark.asyncio
    async def test_expired_pending_marks_failed_not_denied(self, db_factory, fake_redis):
        """超时的诚实语义：FAILED + approval_timeout——绝不留"用户拒绝了"的假记录。"""
        pending = _pending()
        pending["expires_at"] = 1.0  # 早已过期
        task_id, _, _ = await _seed_waiting_task(db_factory, pending=pending)

        with pytest.raises(checkpoint.TaskNotWaitingApprovalError) as ei:
            await checkpoint.apply_approval_decision(
                task_id=task_id,
                user_id=7,
                organization_id=1,
                decision="approve",
            )
        assert ei.value.detail.get("error_type") == "approval_timeout"
        task = await _get_task(db_factory, task_id)
        assert task.status == TaskStatus.FAILED
        assert task.last_error_type == "approval_timeout"
        # 快照里没有伪造的 deny 记录
        _, payload = await checkpoint.load_for_resume(task_id=task_id, user_id=7)
        run_state = payload["run"]
        assert not run_state.get("approval_decisions")

    @pytest.mark.asyncio
    async def test_busy_lease_rejects_concurrent_decisions(self, db_factory, fake_redis):
        task_id, _, _ = await _seed_waiting_task(db_factory, pending=_pending())
        assert await checkpoint.claim_task(task_id) is True  # 模拟并发中的另一个应答
        with pytest.raises(checkpoint.TaskBusyError):
            await checkpoint.apply_approval_decision(
                task_id=task_id,
                user_id=7,
                organization_id=1,
                decision="approve",
            )


# ── 取消旗（协作式取消的收敛边）───────────────────────────────────────────────


class TestCancelFlag:
    @pytest.mark.asyncio
    async def test_request_and_query_flag(self, fake_redis):
        assert await checkpoint.request_cancel("t-1") is True
        assert await checkpoint.is_cancel_requested("t-1") is True
        assert await checkpoint.is_cancel_requested("t-2") is False
        await checkpoint.clear_cancel_flag("t-1")
        assert await checkpoint.is_cancel_requested("t-1") is False

    @pytest.mark.asyncio
    async def test_flag_channel_down_is_false(self, monkeypatch):
        class _Down:
            def __bool__(self):
                return False

        monkeypatch.setattr(checkpoint, "redis_client", _Down())
        assert await checkpoint.request_cancel("t-1") is False
        assert await checkpoint.is_cancel_requested("t-1") is False

    @pytest.mark.asyncio
    async def test_begin_resume_converges_stale_flag_to_cancelled(self, db_factory, fake_redis):
        """取消请求在 run 死前未被消费 → 下一次 resume 尝试负责如实收敛，绝不复活。"""
        task_id, _, _ = await _seed_waiting_task(db_factory, pending=_pending())
        await checkpoint.request_cancel(task_id)

        with pytest.raises(checkpoint.TaskNotResumableError) as ei:
            await checkpoint.begin_resume(task_id)
        assert ei.value.detail.get("state") == "cancelled"
        task = await _get_task(db_factory, task_id)
        assert task.status == TaskStatus.CANCELLED
        assert task.last_error_type == "user_cancelled"
        # 归还租约（不悬挂）
        assert checkpoint._lock_key(task_id) not in fake_redis.store


# ── 待审批读取 ───────────────────────────────────────────────────────────────


class TestGetPendingApproval:
    @pytest.mark.asyncio
    async def test_roundtrip(self, db_factory, fake_redis):
        pending = _pending()
        task_id, _, _ = await _seed_waiting_task(db_factory, pending=pending)
        got = await checkpoint.get_pending_approval(task_id)
        assert got is not None
        assert got["tool_name"] == "execute_python"
        assert got["args_hash"] == pending["args_hash"]
        assert got["risk_level"] == "high"

    @pytest.mark.asyncio
    async def test_none_on_missing_or_corrupt(self, db_factory, fake_redis):
        assert await checkpoint.get_pending_approval("nope") is None
        task_id = await checkpoint.create_task(user_id=7, organization_id=1, query="q")
        assert await checkpoint.get_pending_approval(task_id) is None
