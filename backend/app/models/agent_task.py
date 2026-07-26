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
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.database import Base


class TaskStatus(enum.Enum):
    """Lifecycle states (§3.1 + §13.5-13.6).

    pending   — queued, not yet started
    running   — active coroutine
    waiting_tool  — a tool is executing (potentially long)
    waiting_human — blocked on human response
    failed    — terminal: unrecoverable (can retry)
    completed — terminal: succeeded
    abandoned — terminal: user explicitly gave up
    """

    PENDING = "pending"
    RUNNING = "running"
    WAITING_TOOL = "waiting_tool"
    WAITING_HUMAN = "waiting_human"
    FAILED = "failed"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


# ── Valid transitions ──────────────────────────────────────────────────
_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.PENDING:       {TaskStatus.RUNNING, TaskStatus.ABANDONED},
    TaskStatus.RUNNING:       {TaskStatus.WAITING_TOOL, TaskStatus.WAITING_HUMAN,
                               TaskStatus.FAILED, TaskStatus.COMPLETED,
                               TaskStatus.ABANDONED},
    TaskStatus.WAITING_TOOL:  {TaskStatus.RUNNING, TaskStatus.FAILED,
                               TaskStatus.ABANDONED},
    TaskStatus.WAITING_HUMAN: {TaskStatus.RUNNING, TaskStatus.ABANDONED,
                               TaskStatus.FAILED},
    # terminal states
    TaskStatus.FAILED:        {TaskStatus.RUNNING},  # retry
    TaskStatus.COMPLETED:     set(),
    TaskStatus.ABANDONED:     set(),
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
        String(36), primary_key=True, index=True,
        default=lambda: uuid.uuid4().hex[:12],
    )
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), index=True,
    )
    organization_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("organizations.id"), index=True,
    )

    # ── Lifecycle ──
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus), default=TaskStatus.PENDING, index=True,
    )

    # ── Task content ──
    query: Mapped[str] = mapped_column(Text, comment="Original user query")
    plan_id: Mapped[str | None] = mapped_column(
        String(36), index=True, comment="Planner's Plan ID (Redis key ref)",
    )
    current_step_id: Mapped[str | None] = mapped_column(
        String(36), comment="Which PlanStep is being / was last executed",
    )

    # ── Checkpoint snapshot ──
    context_snapshot: Mapped[Any | None] = mapped_column(
        JSON, comment="ExecutionContext.to_dict() at last checkpoint",
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
        DateTime(timezone=True), server_default=func.now(),
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(),
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True,
        comment="When this task expires and can be cleaned up",
    )

    # ── State helpers ──

    @property
    def is_terminal(self) -> bool:
        return self.status in (TaskStatus.COMPLETED, TaskStatus.ABANDONED)

    @property
    def is_recoverable(self) -> bool:
        """Can this task be resumed?"""
        return self.status in (
            TaskStatus.RUNNING,
            TaskStatus.WAITING_TOOL,
            TaskStatus.WAITING_HUMAN,
            TaskStatus.FAILED,
        )

    def transition(self, to: TaskStatus) -> None:
        """Move to a new state, validating legality first."""
        if not can_transition(self.status, to):
            raise ValueError(
                f"Invalid transition: {self.status.value} → {to.value}"
            )
        self.status = to
        if to in (TaskStatus.COMPLETED, TaskStatus.ABANDONED):
            self.completed_at = datetime.utcnow()

    def __repr__(self) -> str:
        return (
            f"<AgentTask id={self.id!r} status={self.status.value!r} "
            f"steps={self.completed_steps}/{self.total_steps}>"
        )
