"""Agent tool-call ledger — 工具调用配对持久化模型（C3a）。

背景：工具调用配对（tool/call 与 tool/result）此前只存在于内存环形缓冲
（``app/agent/tracing.py`` 的 TraceStore）与 SSE 事件流里；进程崩溃 / 取消 /
resume 之后，已经派发却永远没有返回的 tool_call 没有持久账目可核。本模型把
每次调用的账目落到与 ``agent_tasks`` 同库（崩溃场景天然可查）：

- ``agent_tool_calls``   —— 登记（REGISTERED）→ 派发（DISPATCHED）→ 终态；
- ``agent_tool_results`` —— 工具返回的落账（append-only；一次调用至多一条）。

唯一约束（防重复落账）：
- calls(task_id, call_id) 唯一 —— 同一任务内 call_id 不重复登记；
- calls(task_id, source_event_seq) 唯一 —— call 事件序号防重放；
- results(task_id, call_id) 唯一 —— 一次调用至多一条结果；
- results(task_id, source_event_seq) 唯一 —— 结果事件序号防重复入账。

``source_event_seq`` 由 ``app/agent/tool_ledger.py`` 按 task 维度发放（跨进程
重启从库里 MAX 续号）；call 与 result 各占一个序号，"result 序号 > call 序号"
即配对顺序的可核证据。

状态列用定长 String 而非 SQLAlchemy Enum：状态集合会随运维口径演进，走
Python 常量（``ToolCallStatus``）+ 前端/查询按字符串过滤，避免 MySQL ENUM
每次扩展都要 MODIFY 列（007/009 的迁移负担）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.database import Base


class ToolCallStatus:
    """工具调用账目状态（大写字符串入库）。"""

    # 正常路径
    REGISTERED = "REGISTERED"      # 已登记、尚未派发
    DISPATCHED = "DISPATCHED"      # 已派发、等待 result
    COMPLETED = "COMPLETED"        # result 成功落账
    FAILED = "FAILED"              # result 失败/异常落账

    # 合成终态（崩溃/取消/resume 时对未完成 call 的补写）
    TOOL_OUTCOME_UNKNOWN = "TOOL_OUTCOME_UNKNOWN"          # 执行中失联
    TOOL_NOT_STARTED = "TOOL_NOT_STARTED"                  # 已登记未派发
    ABORTED_BEFORE_DISPATCH = "ABORTED_BEFORE_DISPATCH"    # 取消前


class AgentToolCall(Base):
    """一次工具调用的登记与终态（一行 = 一个 call_id）。"""

    __tablename__ = "agent_tool_calls"
    __table_args__ = (
        UniqueConstraint("task_id", "call_id", name="uq_agent_tool_calls_task_call"),
        UniqueConstraint("task_id", "source_event_seq", name="uq_agent_tool_calls_task_seq"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_tasks.id", ondelete="CASCADE"),
        index=True, nullable=False,
    )
    call_id: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="LLM 提供的 tool_call id",
    )
    tool_name: Mapped[str] = mapped_column(String(128), nullable=False)
    arguments: Mapped[str] = mapped_column(
        Text, default="", nullable=False, comment="参数 JSON（截断存储）",
    )
    source_event_seq: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="本任务内单调事件序号（call 事件）",
    )
    status: Mapped[str] = mapped_column(
        String(32), default=ToolCallStatus.REGISTERED, index=True, nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(),
    )


class AgentToolResult(Base):
    """工具返回的落账（一行 = 一次调用的唯一结果；append-only）。"""

    __tablename__ = "agent_tool_results"
    __table_args__ = (
        UniqueConstraint("task_id", "call_id", name="uq_agent_tool_results_task_call"),
        UniqueConstraint("task_id", "source_event_seq", name="uq_agent_tool_results_task_seq"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_tasks.id", ondelete="CASCADE"),
        index=True, nullable=False,
    )
    call_id: Mapped[str] = mapped_column(String(64), nullable=False)
    tool_call_pk: Mapped[int] = mapped_column(
        Integer, ForeignKey("agent_tool_calls.id", ondelete="CASCADE"),
        index=True, nullable=False, comment="关联 agent_tool_calls.id",
    )
    source_event_seq: Mapped[int] = mapped_column(
        Integer, nullable=False, comment="本任务内单调事件序号（result 事件）",
    )
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, comment="success | error",
    )
    result_summary: Mapped[str] = mapped_column(
        Text, default="", nullable=False, comment="结果摘要（截断存储）",
    )
    error_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
