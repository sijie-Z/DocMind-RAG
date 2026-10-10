"""调度引擎单元测试（sqlite 全链 + 受控时钟 + 替身注入，零真实 sleep）。

覆盖：到点触发与推进 / 幂等（同一时刻不重复触发）/ 重叠 skip 记录 /
ONCE 耗尽 / 失败如实落库与通知 / 通知失败不回滚 / run-now 语义（不动时刻表、
重叠跳过、不存在的任务）/ run_loop 接线（注入 sleeper，不真 sleep）/
默认执行器 execute_agent_run 的终态映射（#301 诚实原则）。
"""
import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  # 注册全部表供 create_all
from app.core.database import Base
from app.models.agent_task import AgentTask, TaskStatus
from app.models.scheduled_task import ScheduledTask, ScheduleType
from app.services.scheduler import engine as engine_mod
from app.services.scheduler.engine import (
    RunOutcome,
    RunRequest,
    SchedulerEngine,
    execute_agent_run,
    send_run_notification,
)

NOW = datetime(2026, 10, 10, 12, 0, 0)  # naive UTC


@pytest.fixture
async def factory():
    pg = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with pg.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(pg, expire_on_commit=False)
    await pg.dispose()


class _Clock:
    def __init__(self, start=NOW):
        self.value = start

    def __call__(self):
        return self.value

    def advance(self, **kw):
        self.value = self.value + timedelta(**kw)


class _RecordingExecutor:
    def __init__(self, result=None, gate=None, exc=None):
        self.calls = []
        self.result = result or RunOutcome("success", agent_task_id="at-1")
        self.gate = gate  # asyncio.Event：挂住运行（重叠用例）
        self.exc = exc
        self.started = asyncio.Event()

    async def __call__(self, request):
        self.calls.append(request)
        self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.exc is not None:
            raise self.exc
        return self.result


class _RecordingNotifier:
    def __init__(self, exc=None):
        self.calls = []
        self.exc = exc

    async def __call__(self, task, outcome):
        self.calls.append((task.id, outcome.status, outcome.error))
        if self.exc is not None:
            raise self.exc


async def _add_task(factory, **kw) -> str:
    params = dict(
        id="task1",
        name="日报",
        prompt="生成日报",
        schedule_type=ScheduleType.CRON,
        cron_expr="*/5 * * * *",
        timezone="UTC",
        enabled=True,
        next_run_at=NOW,
        created_by=1,
        organization_id=1,
    )
    params.update(kw)
    async with factory() as db:
        db.add(ScheduledTask(**params))
        await db.commit()
    return params["id"]


def _engine(factory, clock, execute=None, notify=None, sleep=None):
    return SchedulerEngine(
        clock=clock,
        session_factory=factory,
        execute_run=execute,
        notify=notify,
        sleep=sleep,
    )


async def _row(factory, task_id="task1") -> ScheduledTask:
    async with factory() as db:
        return await db.get(ScheduledTask, task_id)


# ── 到点触发 / 推进 / 幂等 ──────────────────────────────────────────────


class TestTickDue:
    @pytest.mark.asyncio
    async def test_fires_due_cron_and_advances(self, factory):
        await _add_task(factory)
        clock = _Clock()
        ex, no = _RecordingExecutor(), _RecordingNotifier()
        eng = _engine(factory, clock, ex, no)

        fired = await eng.tick_once()
        await eng.wait_for_idle()
        assert fired == ["task1"]

        row = await _row(factory)
        assert row.run_count == 1
        assert row.last_status == "success"
        assert row.last_task_id == "at-1"
        assert row.last_run_at == NOW
        assert row.next_run_at == NOW + timedelta(minutes=5)  # */5 从当前时刻推进
        assert ex.calls[0].origin == "due"
        assert no.calls == [("task1", "success", None)]

    @pytest.mark.asyncio
    async def test_future_not_fired(self, factory):
        await _add_task(factory, next_run_at=NOW + timedelta(minutes=1))
        eng = _engine(factory, _Clock(), _RecordingExecutor(), _RecordingNotifier())
        assert await eng.tick_once() == []

    @pytest.mark.asyncio
    async def test_second_tick_same_clock_idempotent(self, factory):
        """同一时刻重复 tick 不重复触发（触发即推进时刻表）。"""
        await _add_task(factory)
        ex = _RecordingExecutor()
        eng = _engine(factory, _Clock(), ex, _RecordingNotifier())
        assert await eng.tick_once() == ["task1"]
        await eng.wait_for_idle()
        assert await eng.tick_once() == []
        assert len(ex.calls) == 1

    @pytest.mark.asyncio
    async def test_multiple_due_all_fire(self, factory):
        await _add_task(factory, id="a")
        await _add_task(factory, id="b", next_run_at=NOW - timedelta(minutes=1))
        ex = _RecordingExecutor()
        eng = _engine(factory, _Clock(), ex, _RecordingNotifier())
        fired = await eng.tick_once()
        await eng.wait_for_idle()
        assert sorted(fired) == ["a", "b"]
        assert len(ex.calls) == 2

    @pytest.mark.asyncio
    async def test_disabled_not_fired(self, factory):
        await _add_task(factory, enabled=False)
        ex = _RecordingExecutor()
        eng = _engine(factory, _Clock(), ex, _RecordingNotifier())
        assert await eng.tick_once() == []
        assert ex.calls == []

    @pytest.mark.asyncio
    async def test_once_consumed_after_fire(self, factory):
        once_at = NOW + timedelta(seconds=30)
        await _add_task(
            factory,
            schedule_type=ScheduleType.ONCE,
            cron_expr=None,
            once_at=once_at,
            next_run_at=once_at,
        )
        clock = _Clock(once_at)
        eng = _engine(factory, clock, _RecordingExecutor(), _RecordingNotifier())
        assert await eng.tick_once() == ["task1"]
        await eng.wait_for_idle()
        row = await _row(factory)
        assert row.enabled is False
        assert row.next_run_at is None
        assert row.last_status == "success"
        assert await eng.tick_once() == []  # 不再触发


# ── 重叠策略：skip + 计数 + 推进（不覆盖 last_status）──────────────────


class TestOverlap:
    @pytest.mark.asyncio
    async def test_due_while_running_skips_and_advances(self, factory):
        await _add_task(factory)
        clock = _Clock()
        gate = asyncio.Event()
        ex = _RecordingExecutor(gate=gate)
        eng = _engine(factory, clock, ex, _RecordingNotifier())

        assert await eng.tick_once() == ["task1"]
        await ex.started.wait()

        clock.advance(minutes=5)  # 又到点，上一跑未完
        assert await eng.tick_once() == []
        row = await _row(factory)
        assert row.skipped_overlap_count == 1
        assert row.next_run_at == clock.value + timedelta(minutes=5)  # 错过的点被消费
        assert row.last_status == "running"  # 不覆盖在途状态
        assert len(ex.calls) == 1

        gate.set()
        await eng.wait_for_idle()
        row = await _row(factory)
        assert row.last_status == "success"
        assert row.run_count == 1

    @pytest.mark.asyncio
    async def test_once_overlap_keeps_due_and_delays(self, factory):
        """单次任务被手动运行挡住：保持 due，空档后补跑，不丢唯一一次。"""
        once_at = NOW + timedelta(minutes=10)
        await _add_task(
            factory,
            schedule_type=ScheduleType.ONCE,
            cron_expr=None,
            once_at=once_at,
            next_run_at=once_at,
        )
        clock = _Clock(NOW)
        gate = asyncio.Event()
        ex = _RecordingExecutor(gate=gate)
        eng = _engine(factory, clock, ex, _RecordingNotifier())

        r = await eng.run_now("task1")  # 手动触发挂住
        assert r["triggered"] is True
        await ex.started.wait()

        clock.value = once_at  # 到点
        assert await eng.tick_once() == []  # 重叠 → 跳过
        row = await _row(factory)
        assert row.skipped_overlap_count == 1
        assert row.next_run_at == once_at  # ONCE 保持 due
        assert row.enabled is True

        gate.set()
        await eng.wait_for_idle()
        assert await eng.tick_once() == ["task1"]  # 空档后补跑
        await eng.wait_for_idle()
        row = await _row(factory)
        assert row.enabled is False and row.next_run_at is None
        assert row.run_count == 2


# ── 失败诚实性 / 通知 ───────────────────────────────────────────────────


class TestFailureHonesty:
    @pytest.mark.asyncio
    async def test_executor_exception_recorded_as_failed(self, factory):
        await _add_task(factory)
        ex = _RecordingExecutor(exc=RuntimeError("boom"))
        no = _RecordingNotifier()
        eng = _engine(factory, _Clock(), ex, no)
        await eng.tick_once()
        await eng.wait_for_idle()
        row = await _row(factory)
        assert row.last_status == "failed"
        assert "RuntimeError: boom" in (row.last_error or "")
        assert no.calls == [("task1", "failed", "RuntimeError: boom")]

    @pytest.mark.asyncio
    async def test_failed_outcome_recorded(self, factory):
        await _add_task(factory)
        ex = _RecordingExecutor(result=RunOutcome("failed", "at-9", error="模型超时"))
        eng = _engine(factory, _Clock(), ex, _RecordingNotifier())
        await eng.tick_once()
        await eng.wait_for_idle()
        row = await _row(factory)
        assert row.last_status == "failed"
        assert row.last_task_id == "at-9"
        assert row.last_error == "模型超时"

    @pytest.mark.asyncio
    async def test_notify_failure_does_not_rollback(self, factory):
        await _add_task(factory)
        no = _RecordingNotifier(exc=RuntimeError("通知服务不可用"))
        eng = _engine(factory, _Clock(), _RecordingExecutor(), no)
        await eng.tick_once()
        await eng.wait_for_idle()
        row = await _row(factory)
        assert row.last_status == "success"  # 状态不受通知失败影响
        assert row.last_error is None


# ── run-now 语义 ────────────────────────────────────────────────────────


class TestRunNow:
    @pytest.mark.asyncio
    async def test_does_not_change_schedule(self, factory):
        future = NOW + timedelta(hours=3)
        await _add_task(factory, next_run_at=future)
        ex = _RecordingExecutor()
        eng = _engine(factory, _Clock(), ex, _RecordingNotifier())

        r = await eng.run_now("task1")
        await eng.wait_for_idle()
        assert r == {"triggered": True, "reason": "manual"}
        row = await _row(factory)
        assert row.next_run_at == future  # 时刻表分毫不动
        assert row.enabled is True
        assert row.run_count == 1
        assert row.last_status == "success"
        assert ex.calls[0].origin == "manual"

    @pytest.mark.asyncio
    async def test_overlap_skipped_without_counting(self, factory):
        await _add_task(factory, next_run_at=NOW + timedelta(hours=1))
        gate = asyncio.Event()
        ex = _RecordingExecutor(gate=gate)
        eng = _engine(factory, _Clock(), ex, _RecordingNotifier())

        first = await eng.run_now("task1")
        await ex.started.wait()
        second = await eng.run_now("task1")
        assert first["triggered"] is True
        assert second == {"triggered": False, "reason": "overlap"}
        row = await _row(factory)
        assert row.skipped_overlap_count == 0  # 手动跳过不计数（调用方已得到响应）
        gate.set()
        await eng.wait_for_idle()

    @pytest.mark.asyncio
    async def test_paused_task_can_run_manually(self, factory):
        await _add_task(factory, enabled=False, next_run_at=None)
        ex = _RecordingExecutor()
        eng = _engine(factory, _Clock(), ex, _RecordingNotifier())
        r = await eng.run_now("task1")
        await eng.wait_for_idle()
        assert r["triggered"] is True
        assert len(ex.calls) == 1

    @pytest.mark.asyncio
    async def test_missing_task(self, factory):
        eng = _engine(factory, _Clock(), _RecordingExecutor(), _RecordingNotifier())
        assert await eng.run_now("nope") == {"triggered": False, "reason": "not_found"}


# ── DST 秋令时风暴回归（评审 MAJOR-1，引擎级）───────────────────────────


class TestDstStormRegression:
    @pytest.mark.asyncio
    async def test_fold_window_no_trigger_storm(self, factory):
        """时钟停在秋令时重复小时第二次通过（fold=1）窗口内连续 tick，
        不得重复触发（旧实现 next_fire 返回过去时刻 → 每个 tick 都真实
        触发一次 = 41 连触风暴）。"""
        first_pass = datetime(2026, 11, 1, 5, 30)  # 01:30 EDT（fold=0）
        await _add_task(
            factory,
            cron_expr="30 1 * * *",
            timezone="America/New_York",
            next_run_at=first_pass,
        )
        clock = _Clock(datetime(2026, 11, 1, 6, 15))  # 01:15 EST（fold=1 二现中）
        ex = _RecordingExecutor()
        eng = _engine(factory, clock, ex, _RecordingNotifier())

        for _ in range(40):  # 20 分钟 × 30s 轮询
            await eng.tick_once()
            await eng.wait_for_idle()  # 假运行即时收尾——确保风暴不被重叠闸门掩盖
            clock.advance(seconds=30)

        assert len(ex.calls) == 1, f"窗口内应只触发一次，实际 {len(ex.calls)} 次（触发风暴）"
        row = await _row(factory)
        assert row.run_count == 1
        assert row.next_run_at == datetime(2026, 11, 2, 6, 30)  # 次日 01:30 EST


# ── run_loop 接线（注入 sleeper，无真实 sleep）─────────────────────────


class TestRunLoop:
    @pytest.mark.asyncio
    async def test_loop_ticks_then_cancels_without_real_sleep(self, factory):
        await _add_task(factory)
        clock = _Clock()
        sleeps = []

        async def fake_sleep(seconds):
            sleeps.append(seconds)
            raise asyncio.CancelledError  # 第二次循环前取消

        eng = _engine(factory, clock, _RecordingExecutor(), _RecordingNotifier(), sleep=fake_sleep)
        await eng.run_loop()  # CancelledError 被 run_loop 吞掉并退出
        await eng.wait_for_idle()
        assert len(sleeps) == 1
        row = await _row(factory)
        assert row.run_count == 1

    @pytest.mark.asyncio
    async def test_poll_seconds_floor(self, factory):
        eng = _engine(factory, _Clock(), _RecordingExecutor(), _RecordingNotifier(), sleep=None)
        assert eng.poll_seconds >= 5  # 未注入时读设置并夹住下限


# ── 默认执行器：终态映射（agent_tasks 行为唯一事实源，#301）─────────────


def _req(origin="due") -> RunRequest:
    return RunRequest(
        id="task1", name="n", prompt="p", created_by=1, organization_id=1, origin=origin
    )


class TestDefaultExecutor:
    @pytest.mark.asyncio
    async def test_completed_maps_success(self, monkeypatch, factory):
        monkeypatch.setattr(engine_mod, "AsyncSessionLocal", factory)
        async with factory() as db:
            db.add(AgentTask(id="a1", user_id=1, query="q", status=TaskStatus.COMPLETED))
            await db.commit()

        import app.agent.service as svc_mod

        async def fake_chat(**kw):
            yield SimpleNamespace(type="done", task_id="a1", content="")

        monkeypatch.setattr(svc_mod.agent_service, "chat", fake_chat)
        outcome = await execute_agent_run(_req())
        assert outcome.status == "success"
        assert outcome.agent_task_id == "a1"
        assert outcome.error is None

    @pytest.mark.asyncio
    async def test_failed_reads_last_error(self, monkeypatch, factory):
        monkeypatch.setattr(engine_mod, "AsyncSessionLocal", factory)
        async with factory() as db:
            db.add(
                AgentTask(
                    id="a2",
                    user_id=1,
                    query="q",
                    status=TaskStatus.FAILED,
                    last_error="模型调用失败",
                )
            )
            await db.commit()

        import app.agent.service as svc_mod

        async def fake_chat(**kw):
            yield SimpleNamespace(type="error", task_id="a2", content="ignored")

        monkeypatch.setattr(svc_mod.agent_service, "chat", fake_chat)
        outcome = await execute_agent_run(_req())
        assert outcome.status == "failed"
        assert outcome.error == "模型调用失败"

    @pytest.mark.asyncio
    async def test_non_terminal_after_clean_end_is_failed(self, monkeypatch, factory):
        """生成器干净结束但任务行仍是 RUNNING → 如实报失败，不伪装成功。"""
        monkeypatch.setattr(engine_mod, "AsyncSessionLocal", factory)
        async with factory() as db:
            db.add(AgentTask(id="a3", user_id=1, query="q", status=TaskStatus.RUNNING))
            await db.commit()

        import app.agent.service as svc_mod

        async def fake_chat(**kw):
            yield SimpleNamespace(type="done", task_id="a3", content="")

        monkeypatch.setattr(svc_mod.agent_service, "chat", fake_chat)
        outcome = await execute_agent_run(_req())
        assert outcome.status == "failed"
        assert "非终态" in (outcome.error or "")

    @pytest.mark.asyncio
    async def test_error_event_without_task_id(self, monkeypatch, factory):
        monkeypatch.setattr(engine_mod, "AsyncSessionLocal", factory)

        import app.agent.service as svc_mod

        async def fake_chat(**kw):
            yield SimpleNamespace(type="error", task_id="", content="planning 崩溃")

        monkeypatch.setattr(svc_mod.agent_service, "chat", fake_chat)
        outcome = await execute_agent_run(_req())
        assert outcome.status == "failed"
        assert outcome.error == "planning 崩溃"
        assert outcome.agent_task_id is None

    @pytest.mark.asyncio
    async def test_done_without_task_id_is_success(self, monkeypatch, factory):
        monkeypatch.setattr(engine_mod, "AsyncSessionLocal", factory)

        import app.agent.service as svc_mod

        async def fake_chat(**kw):
            yield SimpleNamespace(type="done", task_id="", content="")

        monkeypatch.setattr(svc_mod.agent_service, "chat", fake_chat)
        outcome = await execute_agent_run(_req())
        assert outcome.status == "success"
        assert outcome.agent_task_id is None


# ── 默认通知器：复用 notifications 域 ───────────────────────────────────


class TestDefaultNotifier:
    @pytest.mark.asyncio
    async def test_notification_payload(self, monkeypatch, factory):
        monkeypatch.setattr(engine_mod, "AsyncSessionLocal", factory)
        captured = {}

        async def fake_create_notification(db, user_id, title, content, type, target_route, target_id):
            captured.update(
                user_id=user_id, title=title, content=content, type=type,
                target_route=target_route, target_id=target_id,
            )

        monkeypatch.setattr(
            "app.api.v1.endpoints.notifications.create_notification", fake_create_notification
        )
        task = SimpleNamespace(
            id="task1", name="日报", run_count=3, created_by=7,
            last_run_at=NOW,
        )
        outcome = RunOutcome("success", agent_task_id="at-9")
        await send_run_notification(task, outcome)

        assert captured["user_id"] == 7
        assert "日报" in captured["title"] and "第 3 次" in captured["title"]
        assert captured["type"] == "scheduler"
        assert captured["target_route"] == "Agent"
        assert captured["target_id"] == "at-9"

    @pytest.mark.asyncio
    async def test_failed_notification_carries_error(self, monkeypatch, factory):
        monkeypatch.setattr(engine_mod, "AsyncSessionLocal", factory)
        captured = {}

        async def fake_create_notification(db, **kw):
            captured.update(kw)

        monkeypatch.setattr(
            "app.api.v1.endpoints.notifications.create_notification", fake_create_notification
        )
        task = SimpleNamespace(id="task1", name="日报", run_count=4, created_by=7, last_run_at=NOW)
        await send_run_notification(task, RunOutcome("failed", "at-9", error="模型超时"))
        assert "失败" in captured["title"]
        assert "模型超时" in captured["content"]
