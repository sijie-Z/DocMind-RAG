"""Agent Task — persistent task lifecycle checkpoint model.

Design contract (§3 in agent-reliability-design.md).
Replaces ephemeral ExecutionContext with a DB-checkpoint so a run
can be paused / recovered / resumed across process restarts.

This is Phase 1 (data model only).  The executor does NOT use it yet.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.database import Base

# Webhook 投递的渲染 prompt 上限 1 MiB（与 chat_messages.content 同一原因，评审
# blocker B2 的 checkpoint 侧）：MySQL TEXT 64 KiB 会让 create_task fail-open
# 丢 checkpoint；MEDIUMTEXT = 16 MiB。SQLite 保持 TEXT。
_QUERY_TEXT = Text().with_variant(mysql.MEDIUMTEXT(), "mysql")


class TaskStatus(enum.Enum):
    """Lifecycle states (§3.1 + §13.5-13.6 + HITL + 计划审批).

    pending               — queued, not yet started
    running               — active coroutine
    waiting_tool          — a tool is executing (potentially long)
    waiting_approval      — 审批门挂起：执行器停在"待执行工具"之前，等人类
                            批准/拒绝/改指令（POST /tasks/{id}/approval）
    waiting_plan_approval — 计划审批挂起：planner 已产出计划，等人类批准/
                            编辑步骤后批准/拒绝（plan_approval_mode=always）
    waiting_human         — blocked on human response（legacy 态：无写入者）
    failed                — terminal: unrecoverable (can retry)
    completed             — terminal: succeeded
    abandoned             — terminal: user explicitly gave up（legacy 态：无写入者）
    cancelled             — terminal: 经 cancel 协议协作式取消（诚实终态：不伪装完成）
    rejected              — terminal: 计划被人类拒绝且重规划机会已用尽（诚实终态：
                            不是失败、不是取消——是"我看过，不要这个计划"）
    paused                — 会话 fork 克隆出的快照任务（issue #476）：不是运行中的
                            run，只是"从母任务的已结算 checkpoint 继续"的起点；
                            可经 resume 进入 RUNNING。刻意与 RUNNING 分离：fork 出的
                            子会话不得冒充"源会话有运行中任务"。
    """

    PENDING = "pending"
    RUNNING = "running"
    WAITING_TOOL = "waiting_tool"
    WAITING_APPROVAL = "waiting_approval"
    WAITING_PLAN_APPROVAL = "waiting_plan_approval"
    WAITING_HUMAN = "waiting_human"
    FAILED = "failed"
    COMPLETED = "completed"
    ABANDONED = "abandoned"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    PAUSED = "paused"


# ── Valid transitions ──────────────────────────────────────────────────
_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.PENDING: {TaskStatus.RUNNING, TaskStatus.ABANDONED, TaskStatus.CANCELLED},
    TaskStatus.RUNNING: {
        TaskStatus.WAITING_TOOL,
        TaskStatus.WAITING_HUMAN,
        TaskStatus.WAITING_APPROVAL,
        TaskStatus.WAITING_PLAN_APPROVAL,
        TaskStatus.FAILED,
        TaskStatus.COMPLETED,
        TaskStatus.ABANDONED,
        TaskStatus.CANCELLED,
    },
    TaskStatus.WAITING_TOOL: {
        TaskStatus.RUNNING,
        TaskStatus.FAILED,
        TaskStatus.ABANDONED,
        TaskStatus.CANCELLED,
    },
    # HITL 审批等待（approval.py）：人类应答 → 回 RUNNING 续跑；取消 → 终态。
    TaskStatus.WAITING_APPROVAL: {TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.CANCELLED},
    # 计划审批等待（plan approval）：approve/edit → 回 RUNNING 执行；
    # reject 且重规划额度用尽 → REJECTED 终态；取消 → CANCELLED；错误 → FAILED。
    TaskStatus.WAITING_PLAN_APPROVAL: {
        TaskStatus.RUNNING,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
        TaskStatus.REJECTED,
    },
    TaskStatus.WAITING_HUMAN: {
        TaskStatus.RUNNING,
        TaskStatus.ABANDONED,
        TaskStatus.FAILED,
        TaskStatus.CANCELLED,
    },
    # terminal states
    # FAILED 还有 CANCELLED 边：失败任务上滞留的取消旗在下一次 resume 前被
    # 观察时如实收敛（用户已放弃，不复活）——见 checkpoint.begin_resume。
    TaskStatus.FAILED: {TaskStatus.RUNNING, TaskStatus.CANCELLED},
    TaskStatus.COMPLETED: set(),
    TaskStatus.ABANDONED: set(),
    TaskStatus.CANCELLED: set(),
    TaskStatus.REJECTED: set(),
    # fork 克隆任务（issue #476）：resume 边 → RUNNING；用户放弃/取消 → 终态。
    # 刻意不提供 RUNNING→PAUSED：PAUSED 只作为克隆起点存在，不是运行中暂停。
    TaskStatus.PAUSED: {
        TaskStatus.RUNNING,
        TaskStatus.ABANDONED,
        TaskStatus.CANCELLED,
    },
}


def can_transition(from_: TaskStatus, to: TaskStatus) -> bool:
    """Validate state transition."""
    return to in _TRANSITIONS.get(from_, set())


class AgentTask(Base):
    """Per-run agent task with checkpoint snapshot.

    One AgentTask row = one user agent request.  The context_snapshot
    column carries the full ExecutionContext.to_dict() at checkpoint
    boundaries, enabling resume from the last saved point.
    """

    __tablename__ = "agent_tasks"

    # ── Identity ──
    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        index=True,
        default=lambda: uuid.uuid4().hex[:12],
    )
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id"),
        index=True,
    )
    organization_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("organizations.id"),
        index=True,
    )
    # issue #476：run 所属的聊天会话（fork 的“运行中任务”判定与 checkpoint 克隆
    # 都按它关联）。可空——历史任务行早于该列，无法可靠回填（仅信封里有 session_id）。
    session_id: Mapped[str | None] = mapped_column(
        String(36),
        index=True,
        comment="聊天会话ID（fork/运行中判定用；历史行可为空）",
    )

    # ── Lifecycle ──
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus),
        default=TaskStatus.PENDING,
        index=True,
    )

    # ── Task content ──
    query: Mapped[str] = mapped_column(_QUERY_TEXT, comment="Original user query")
    plan_id: Mapped[str | None] = mapped_column(
        String(36),
        index=True,
        comment="Planner's Plan ID (Redis key ref)",
    )
    current_step_id: Mapped[str | None] = mapped_column(
        String(36),
        comment="Which PlanStep is being / was last executed",
    )

    # ── Checkpoint snapshot ──
    context_snapshot: Mapped[Any | None] = mapped_column(
        JSON,
        comment="ExecutionContext.to_dict() at last checkpoint",
    )

    # ── Progress ──
    completed_steps: Mapped[int] = mapped_column(Integer, default=0)
    total_steps: Mapped[int | None] = mapped_column(Integer)
    progress: Mapped[float] = mapped_column(Float, default=0.0)

    # ── Error context ──
    last_error: Mapped[str | None] = mapped_column(Text)
    last_error_type: Mapped[str | None] = mapped_column(String(50))

    # ── Retry ──
    retry_count: Mapped[int] = mapped_column(Integer, default=0)

    # ── Timestamps ──
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        onupdate=func.now(),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        index=True,
        comment="When this task expires and can be cleaned up",
    )

    # ── State helpers ──

    @property
    def is_terminal(self) -> bool:
        return self.status in (
            TaskStatus.COMPLETED,
            TaskStatus.ABANDONED,
            TaskStatus.CANCELLED,
            TaskStatus.REJECTED,
        )

    @property
    def is_recoverable(self) -> bool:
        """Can this task be resumed?"""
        return self.status in (
            TaskStatus.RUNNING,
            TaskStatus.WAITING_TOOL,
            TaskStatus.WAITING_APPROVAL,
            TaskStatus.WAITING_PLAN_APPROVAL,
            TaskStatus.WAITING_HUMAN,
            TaskStatus.FAILED,
            TaskStatus.PAUSED,
        )

    def transition(self, to: TaskStatus) -> None:
        """Move to a new state, validating legality first."""
        if not can_transition(self.status, to):
            raise ValueError(f"Invalid transition: {self.status.value} → {to.value}")
        self.status = to
        if to in (
            TaskStatus.COMPLETED,
            TaskStatus.ABANDONED,
            TaskStatus.CANCELLED,
            TaskStatus.REJECTED,
        ):
            self.completed_at = datetime.utcnow()

    def __repr__(self) -> str:
        return (
            f"<AgentTask id={self.id!r} status={self.status.value!r} "
            f"steps={self.completed_steps}/{self.total_steps}>"
        )
