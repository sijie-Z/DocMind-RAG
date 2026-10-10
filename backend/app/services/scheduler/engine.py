"""进程内定时任务调度引擎（harness 维度 7：后台/主动任务）。

职责：轮询 scheduled_tasks 表 → 到点经既有 agent task 服务
（`agent_service.chat`，复用 service 层入口）创建一次运行 → 结束后落
last_* 字段 → 向创建者发站内通知（复用 notifications 域，含 WS 推送）。

可测性契约（仓内既有教训：禁止真实 sleep 的测试）：
- `clock` 可注入（返回 naive UTC datetime）；测试用受控时钟直接调 `tick_once()`，
  调度循环 `run_loop` 本身不参与行为测试；
- `execute_run`（默认=真实 agent 运行）、`notify`（默认=真实通知）、
  `session_factory`、`sleep` 均可注入替身。

单实例语义：本引擎是进程内 asyncio 循环、无分布式锁——多 worker 部署会各跑
一份调度（属已知边界，需外部调度器；见 models/scheduled_task.py）。

重叠策略（产品决策，记录在此）：上一跑未完又到点 → **跳过并记录**
（skipped_overlap_count +1、日志告警），不排队——队列语义需要持久化 Run 表与
背压策略，超出本阶段范围；跳过次数在 API 可见，不覆盖 last_status
（保证 last_status 永远如实反映最近一次真实运行的结果）。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select

from app.core.database import AsyncSessionLocal
from app.models.scheduled_task import (
    ScheduledTask,
    ScheduleRunStatus,
    ScheduleType,
)
from app.services.scheduler.schedule import (
    ScheduleSpecError,
    as_naive_utc,
    next_after_fire,
    next_from_now,
    utcnow_naive,
)

logger = logging.getLogger(__name__)

__all__ = [
    "RunOutcome",
    "RunRequest",
    "SchedulerEngine",
    "execute_agent_run",
    "scheduler_engine",
    "send_run_notification",
]


@dataclass(frozen=True)
class RunRequest:
    """一次运行的最小请求快照（脱离 ORM 会话后仍可用）。"""

    id: str  # scheduled_tasks.id
    name: str
    prompt: str
    created_by: int
    organization_id: int | None
    origin: str  # "due"（到点）| "manual"（run-now）


@dataclass
class RunOutcome:
    """一次运行的终态（#301 诚实原则：失败必须带错误原因，不得伪装成功）。"""

    status: str  # ScheduleRunStatus.SUCCESS / FAILED 的取值
    agent_task_id: str | None = None
    error: str | None = None

    @property
    def is_success(self) -> bool:
        return self.status == ScheduleRunStatus.SUCCESS.value


async def execute_agent_run(request: RunRequest) -> RunOutcome:
    """默认执行器：经既有 agent task 服务跑一次（与聊天同一 service 层入口）。

    终态以 agent_tasks 行的状态为唯一事实源（service 内部负责把任务行收口到
    COMPLETED/FAILED）；生成器事件里的 error 仅作补充说明。
    """
    from app.agent.config import AgentConfig
    from app.agent.service import agent_service
    from app.models.agent_task import AgentTask, TaskStatus

    agent_task_id: str | None = None
    error_hint: str | None = None

    config = AgentConfig()
    async for event in agent_service.chat(
        query=request.prompt,
        history=None,
        organization_id=request.organization_id,
        user_id=request.created_by,
        session_id=None,
        config=config,
    ):
        if getattr(event, "task_id", ""):
            agent_task_id = event.task_id
        if event.type == "error":
            error_hint = event.content or "agent 运行报错"

    if agent_task_id:
        async with AsyncSessionLocal() as db:
            row = await db.get(AgentTask, agent_task_id)
            if row is not None:
                if row.status == TaskStatus.COMPLETED:
                    return RunOutcome(ScheduleRunStatus.SUCCESS.value, agent_task_id)
                if row.status == TaskStatus.FAILED:
                    return RunOutcome(
                        ScheduleRunStatus.FAILED.value,
                        agent_task_id,
                        error=row.last_error or error_hint or "运行失败（无错误详情）",
                    )
                if row.status == TaskStatus.ABANDONED:
                    return RunOutcome(
                        ScheduleRunStatus.FAILED.value, agent_task_id, error="运行被放弃"
                    )
                detail = f"；{error_hint}" if error_hint else ""
                return RunOutcome(
                    ScheduleRunStatus.FAILED.value,
                    agent_task_id,
                    error=f"运行结束后任务状态仍为 {row.status.name}（非终态）{detail}",
                )
    if error_hint:
        return RunOutcome(ScheduleRunStatus.FAILED.value, None, error=error_hint)
    # 无 agent 任务记录且无错误事件：运行结果不可追溯（如 checkpoint.create_task
    # fail-open 返回 None 且生成器无 error 事件）。按诚实原则记失败（不确定），
    # 不得记 success——无运行记录、无法审计的"成功"是语义空洞（评审 MINOR-2）。
    return RunOutcome(
        ScheduleRunStatus.FAILED.value,
        None,
        error="无法确认运行结果：本次运行未产生可追溯的 agent 任务记录",
    )


async def send_run_notification(task: ScheduledTask, outcome: RunOutcome) -> None:
    """默认通知器：向创建者发站内通知（含 WS 推送；notifications 域既有服务）。

    标题带 run_count（唯一化），规避 notifications 域"同 user+同标题 1 分钟去重"
    在快速连续运行下静默吞掉第二条的问题。
    """
    from app.api.v1.endpoints.notifications import create_notification

    status_word = "完成" if outcome.is_success else "失败"
    title = f"定时任务《{task.name}》第 {task.run_count} 次运行{status_word}"
    parts = [f"任务《{task.name}》{status_word}。"]
    if task.last_run_at is not None:
        parts.append(f"触发时间 {as_naive_utc(task.last_run_at):%Y-%m-%d %H:%M} UTC。")
    if outcome.error:
        parts.append(f"错误：{outcome.error[:500]}")
    if outcome.agent_task_id:
        parts.append(f"运行记录：{outcome.agent_task_id}")
    async with AsyncSessionLocal() as db:
        await create_notification(
            db,
            user_id=task.created_by,
            title=title,
            content=" ".join(parts),
            type="scheduler",
            target_route="Agent",
            target_id=outcome.agent_task_id or str(task.id),
        )


class SchedulerEngine:
    """调度引擎。生产用模块级单例 `scheduler_engine`；测试自行构造带替身的实例。"""

    def __init__(
        self,
        *,
        clock=None,
        poll_seconds: int | None = None,
        execute_run=None,
        notify=None,
        session_factory=None,
        sleep=None,
    ) -> None:
        self._clock = clock or utcnow_naive
        self._poll_seconds = poll_seconds
        self._execute_run = execute_run or execute_agent_run
        self._notify = notify or send_run_notification
        self._session_factory = session_factory
        self._sleep = sleep or asyncio.sleep
        self._lock = asyncio.Lock()
        self._in_flight: dict[str, asyncio.Task] = {}

    # ── 时钟 / 会话 ────────────────────────────────────────────────

    def now(self) -> datetime:
        return self._clock()

    def _session(self):
        factory = self._session_factory or AsyncSessionLocal
        return factory()

    @property
    def poll_seconds(self) -> int:
        if self._poll_seconds is not None:
            return max(1, int(self._poll_seconds))
        from app.core.config import settings

        return max(5, int(settings.SCHEDULER_POLL_SECONDS))

    @property
    def in_flight_count(self) -> int:
        return sum(1 for t in self._in_flight.values() if not t.done())

    # ── 调度循环 ──────────────────────────────────────────────────

    async def run_loop(self) -> None:
        """常驻循环（lifespan 门控启动）。行为测试请直接调 tick_once。"""
        while True:
            try:
                await self.tick_once()
            except asyncio.CancelledError:
                break
            except Exception as e:  # 单次 tick 失败不终止调度器
                logger.error(f"定时任务调度器 tick 异常: {e}")
            try:
                await self._sleep(self.poll_seconds)
            except asyncio.CancelledError:
                break

    async def tick_once(self, now: datetime | None = None) -> list[str]:
        """单次调度扫描：触发所有到点任务，返回本次触发的任务 id 列表。"""
        now = as_naive_utc(now) if now is not None else self.now()
        due_ids = await self._load_due_ids(now)
        fired: list[str] = []
        for task_id in due_ids:
            result = await self._trigger(task_id, reason="due", now=now)
            if result.get("triggered"):
                fired.append(task_id)
        return fired

    async def run_now(self, task_id: str, now: datetime | None = None) -> dict:
        """显式手动触发（API run-now）。与到点同一执行路径；不动 next_run_at。"""
        now = as_naive_utc(now) if now is not None else self.now()
        return await self._trigger(task_id, reason="manual", now=now)

    async def wait_for_idle(self) -> None:
        """等在途运行全部收尾（测试 / 优雅收尾用）。"""
        runs = [t for t in self._in_flight.values()]
        if runs:
            await asyncio.gather(*runs, return_exceptions=True)

    async def _load_due_ids(self, now: datetime) -> list[str]:
        async with self._session() as db:
            result = await db.execute(
                select(ScheduledTask.id)
                .where(
                    ScheduledTask.enabled.is_(True),
                    ScheduledTask.next_run_at.isnot(None),
                    ScheduledTask.next_run_at <= now,
                )
                .order_by(ScheduledTask.next_run_at)
            )
            return list(result.scalars().all())

    # ── 触发（到点与 run-now 同一路径）───────────────────────────

    async def _trigger(self, task_id: str, *, reason: str, now: datetime) -> dict:
        """串行化预留 → 启动后台运行。真正执行在 `_execute_and_finalize`。"""
        async with self._lock:
            async with self._session() as db:
                task = await db.get(ScheduledTask, task_id)
                if task is None:
                    return {"triggered": False, "reason": "not_found"}

                if reason == "due":
                    next_run = as_naive_utc(task.next_run_at)
                    if not task.enabled or next_run is None or next_run > now:
                        return {"triggered": False, "reason": "not_due"}

                live = self._in_flight.get(task_id)
                if live is not None and not live.done():
                    if reason == "due":
                        await self._record_skip(db, task, now)
                    return {"triggered": False, "reason": "overlap"}

                # 预留：先推进时刻表再启动（进度落盘早于运行，重启不重复触发）
                if reason == "due":
                    if task.schedule_type == ScheduleType.ONCE:
                        task.next_run_at = None
                        task.enabled = False
                    else:
                        task.next_run_at = next_after_fire(task, now)
                task.run_count += 1
                task.last_status = ScheduleRunStatus.RUNNING.value
                await db.commit()

                run_request = RunRequest(
                    id=task.id,
                    name=task.name,
                    prompt=task.prompt,
                    created_by=task.created_by,
                    organization_id=task.organization_id,
                    origin=reason,
                )
                fired_at = now
                run_task = asyncio.create_task(
                    self._execute_and_finalize(run_request, fired_at=fired_at)
                )
                self._in_flight[task_id] = run_task
                run_task.add_done_callback(
                    lambda t, tid=task_id: (
                        self._in_flight.pop(tid, None)
                        if self._in_flight.get(tid) is t
                        else None
                    )
                )
                return {"triggered": True, "reason": reason}

    async def _record_skip(self, db, task: ScheduledTask, now: datetime) -> None:
        """到点但上一跑未完：计数 + 推进时刻表（ONCE 保持 due 顺延）。"""
        task.skipped_overlap_count += 1
        if task.schedule_type != ScheduleType.ONCE:
            try:
                task.next_run_at = next_from_now(task, now)
            except ScheduleSpecError as exc:
                # 存量数据异常（如 cron 已无解）：如实记录，不伪装推进
                task.last_error = f"跳过重叠后无法计算下次时间: {exc}"[:2000]
        await db.commit()
        logger.warning(
            f"定时任务 {task.id}（{task.name}）到点但上一轮运行未结束，本次跳过"
            f"（累计跳过 {task.skipped_overlap_count} 次）"
        )

    # ── 运行 + 收尾 + 通知 ────────────────────────────────────────

    async def _execute_and_finalize(self, request: RunRequest, *, fired_at: datetime) -> None:
        try:
            outcome = await self._execute_run(request)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # 执行器抛异常 = 失败（#301：不伪装成功）
            outcome = RunOutcome(
                ScheduleRunStatus.FAILED.value,
                error=f"{type(exc).__name__}: {exc}",
            )

        try:
            async with self._session() as db:
                task = await db.get(ScheduledTask, request.id)
                if task is None:
                    logger.warning(f"定时任务 {request.id} 运行结束时已被删除，结果不落盘")
                    return
                task.last_run_at = fired_at
                task.last_status = outcome.status
                task.last_task_id = outcome.agent_task_id
                task.last_error = outcome.error[:2000] if outcome.error else None
                await db.commit()

            # 通知失败不回滚任务状态（已提交）；只告警
            try:
                await self._notify(task, outcome)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.warning(
                    f"定时任务 {request.id} 完成通知发送失败（任务状态不受影响）: {exc}"
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(f"定时任务 {request.id} 结果落盘失败")

        logger.info(
            f"定时任务 {request.id}（{request.name}）运行结束: "
            f"status={outcome.status} task={outcome.agent_task_id} origin={request.origin}"
        )


# 生产单例（lifespan 门控启动；API run-now 共用同一实例保证重叠闸门生效）
scheduler_engine = SchedulerEngine()
