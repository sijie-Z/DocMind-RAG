"""Scheduled Task — 用户创建的定时/触发式 agent 任务（harness 维度 7：后台/主动任务）。

一个 ScheduledTask 行 = 用户的一条自动化："到点让 agent 执行 prompt"。
进程内调度器（app/services/scheduler/engine.py）按 next_run_at 到点触发，
经既有 agent task 服务（agent_service.chat）创建运行，完成后向创建者发通知。

单实例语义：调度器是进程内 asyncio 循环，无分布式锁——多 worker 部署
需外部调度（当前不支持，属已知边界）。

时间口径：库内全部为 UTC（MySQL DATETIME 不存时区）。`timezone` 字段
只影响 cron 表达式的解释（按该时区的墙钟计算，再换算 UTC 落库）；
interval / once_at 是绝对时间，与时区字段无关。
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.database import Base


class ScheduleType(enum.Enum):
    """调度类型（成员名入库，与 taskstatus 口径一致，见迁移 006）。"""

    CRON = "cron"  # 5 字段 cron 表达式，按 timezone 墙钟解释
    INTERVAL = "interval"  # 固定间隔（秒），固定延迟语义（从上次触发时刻起算）
    ONCE = "once"  # 绝对时间单次触发，触发后自动停用（enabled=False, next_run_at=None）


class ScheduleRunStatus(str, enum.Enum):
    """最后一次运行的状态（String 列 + Python 常量；跳过不计入本字段）。

    与 workflow.py 的 status 同型：值集合会随演进扩大，不锁进 MySQL ENUM。
    重叠跳过单独记在 skipped_overlap_count 计数器，不覆盖本字段——
    保证 last_status 永远如实反映"最近一次真实运行"的结果。
    """

    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class ScheduledTask(Base):
    """用户的一条定时 agent 任务。"""

    __tablename__ = "scheduled_tasks"

    # ── Identity ──
    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        index=True,
        default=lambda: uuid.uuid4().hex[:12],
    )
    created_by: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id"),
        index=True,
        comment="任务创建者；运行与完成通知都归属此人",
    )
    organization_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("organizations.id"),
        index=True,
        comment="创建时解析的组织上下文，供运行继承",
    )

    # ── Content ──
    name: Mapped[str] = mapped_column(String(200), comment="任务名（通知与列表展示）")
    prompt: Mapped[str] = mapped_column(Text, comment="到点交给 agent 的指令")

    # ── Schedule spec ──
    schedule_type: Mapped[ScheduleType] = mapped_column(Enum(ScheduleType))
    cron_expr: Mapped[str | None] = mapped_column(String(100), comment="schedule_type=cron 时必填")
    interval_seconds: Mapped[int | None] = mapped_column(
        Integer,
        comment="schedule_type=interval 时必填；固定延迟（触发时刻起算）",
    )
    once_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        comment="schedule_type=once 时必填；UTC 绝对时间",
    )
    timezone: Mapped[str] = mapped_column(
        String(64),
        default="UTC",
        comment="IANA 时区名；只影响 cron 表达式解释，存库时间一律 UTC",
    )

    # ── State ──
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        comment="False=暂停或已完成（单次任务触发后置 False）",
    )
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        index=True,
        comment="UTC；NULL=不再有下次（单次任务已触发，或暂停且未恢复）",
    )

    # ── Last run bookkeeping ──
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        comment="最近一次运行的触发时刻（UTC）",
    )
    last_status: Mapped[str | None] = mapped_column(
        String(20),
        comment="ScheduleRunStatus 取值；None=从未运行",
    )
    last_task_id: Mapped[str | None] = mapped_column(
        String(36),
        comment="最近一次运行对应的 agent_tasks.id（结果链接）",
    )
    last_error: Mapped[str | None] = mapped_column(Text, comment="最近一次失败的错误摘要")
    run_count: Mapped[int] = mapped_column(Integer, default=0, comment="实际运行次数（不含跳过）")
    skipped_overlap_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        comment="因上一轮未结束而跳过的次数",
    )

    # ── Timestamps ──
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        onupdate=func.now(),
    )

    @property
    def is_completed_once(self) -> bool:
        """单次任务已触发完毕（用于 resume 守卫：不可复活）。"""
        return (
            self.schedule_type == ScheduleType.ONCE
            and self.last_run_at is not None
            and self.next_run_at is None
        )

    def __repr__(self) -> str:
        return (
            f"<ScheduledTask id={self.id!r} name={self.name!r} "
            f"type={self.schedule_type.name} enabled={self.enabled} "
            f"next_run_at={self.next_run_at!r}>"
        )
