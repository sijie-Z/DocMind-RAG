"""
聊天模型 - 管理聊天会话和消息
"""

import enum
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.database import Base


class ChatSessionStatus(enum.Enum):
    """聊天会话状态"""

    ACTIVE = "active"  # 活跃
    PAUSED = "paused"  # 暂停
    ENDED = "ended"  # 结束


class MessageType(enum.Enum):
    """消息类型"""

    USER = "user"  # 用户消息
    ASSISTANT = "assistant"  # AI助手消息
    SYSTEM = "system"  # 系统消息


class ChatSession(Base):
    """聊天会话表"""

    __tablename__ = "chat_sessions"
    __table_args__ = (
        # issue #478：session_mode=reuse 的会话查找键（rule_id + payload.session_key）。
        # 也服务"按规则查会话"（最左前缀 rule_id）。
        Index(
            "ix_chat_sessions_webhook_rule_session_key",
            "webhook_rule_id",
            "webhook_session_key",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), index=True, comment="用户ID"
    )
    organization_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("organizations.id"), index=True, comment="组织ID"
    )

    # 会话信息
    title: Mapped[str | None] = mapped_column(String(200), comment="会话标题")
    status: Mapped[ChatSessionStatus] = mapped_column(
        Enum(ChatSessionStatus), default=ChatSessionStatus.ACTIVE, comment="会话状态"
    )

    # 统计信息
    message_count: Mapped[int] = mapped_column(Integer, default=0, comment="消息数量")
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="最后消息时间"
    )

    # 配置信息
    settings: Mapped[Any | None] = mapped_column(JSON, comment="会话设置")

    # 分叉谱系（issue #476）
    # - parent_session_id：直接母会话；根会话为 None。
    # - root_session_id：谱系根会话 id（根会话 = 自身），多级 fork 仍指向最初会话。
    # - fork_point_message_id：切点消息 id（**源会话**的消息 id）。刻意不加外键：
    #   父会话删除/归档后，子会话谱系仍需可查（外键 SET NULL 会抹掉谱系事实）。
    # - fork_reason：分叉原因短标签（默认 user_fork）。
    parent_session_id: Mapped[str | None] = mapped_column(
        String(36), index=True, comment="父会话ID（fork 来源）"
    )
    root_session_id: Mapped[str | None] = mapped_column(
        String(36), index=True, comment="谱系根会话ID（根会话=自身）"
    )
    fork_point_message_id: Mapped[str | None] = mapped_column(
        String(36), comment="分叉切点消息ID（源会话消息，无外键）"
    )
    fork_reason: Mapped[str | None] = mapped_column(String(100), comment="分叉原因标签")

    # Webhook 来源（issue #478）：source 默认 user；webhook 投递建的会话记录
    # 规则与投递 id，reuse 模式另有 session_key（payload 顶层字段）。
    source: Mapped[str] = mapped_column(
        String(32), default="user", server_default="user", comment="会话来源（user/webhook）"
    )
    webhook_rule_id: Mapped[str | None] = mapped_column(String(36), comment="Webhook 规则ID")
    webhook_delivery_id: Mapped[str | None] = mapped_column(
        String(36), comment="首次创建该会话的投递ID"
    )
    webhook_session_key: Mapped[str | None] = mapped_column(
        String(128), comment="reuse 模式的会话键（payload.session_key）"
    )

    # 时间戳
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), comment="更新时间"
    )

    # 关联关系
    user: Mapped[Optional["User"]] = relationship("User", back_populates="chat_sessions")
    organization: Mapped[Optional["Organization"]] = relationship(
        "Organization", back_populates="chat_sessions"
    )
    messages: Mapped[list["ChatMessage"]] = relationship(
        "ChatMessage", back_populates="session", cascade="all, delete-orphan"
    )


class ChatMessage(Base):
    """聊天消息表"""

    __tablename__ = "chat_messages"
    __table_args__ = (
        # issue #476：会话内单调序号 + 唯一约束——fork 的边界语义（seq <= 边界）
        # 与复制时的重排都依赖它；并发的同会话写入由 (session_id, seq) 兜底。
        UniqueConstraint("session_id", "seq", name="uq_chat_messages_session_seq"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, index=True)
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("chat_sessions.id"), index=True, comment="会话ID"
    )
    # 会话内单调序号（从 1 开始）。存量数据由迁移 012 按 (created_at, id) 回填；
    # 新消息一律由 app.services.chat_seq.next_message_seq 分配（含锁/唯一约束兜底）。
    seq: Mapped[int] = mapped_column(Integer, nullable=False, comment="会话内单调序号（从1开始）")

    # 消息内容
    content: Mapped[str] = mapped_column(Text, comment="消息内容")
    message_type: Mapped[MessageType] = mapped_column(Enum(MessageType), comment="消息类型")

    # 元数据
    meta_data: Mapped[Any | None] = mapped_column(JSON, comment="额外元数据")

    # 用户反馈 (用于 RAG 质量评估)
    feedback: Mapped[int] = mapped_column(Integer, default=0, comment="用户反馈")
    feedback_note: Mapped[str | None] = mapped_column(Text, comment="反馈备注")

    # 时间戳
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="创建时间"
    )

    # 关联关系
    session: Mapped[Optional["ChatSession"]] = relationship(
        "ChatSession", back_populates="messages"
    )
