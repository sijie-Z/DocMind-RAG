"""
聊天模型 - 管理聊天会话和消息
"""
import enum
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.database import Base


class ChatSessionStatus(enum.Enum):
    """聊天会话状态"""
    ACTIVE = "active"      # 活跃
    PAUSED = "paused"      # 暂停
    ENDED = "ended"       # 结束


class MessageType(enum.Enum):
    """消息类型"""
    USER = "user"          # 用户消息
    ASSISTANT = "assistant"  # AI助手消息
    SYSTEM = "system"      # 系统消息


class ChatSession(Base):
    """聊天会话表"""
    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True, comment="用户ID")
    organization_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("organizations.id"), index=True, comment="组织ID")

    # 会话信息
    title: Mapped[str | None] = mapped_column(String(200), comment="会话标题")
    status: Mapped[ChatSessionStatus] = mapped_column(Enum(ChatSessionStatus), default=ChatSessionStatus.ACTIVE, comment="会话状态")

    # 统计信息
    message_count: Mapped[int] = mapped_column(Integer, default=0, comment="消息数量")
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="最后消息时间")

    # 配置信息
    settings: Mapped[Any | None] = mapped_column(JSON, comment="会话设置")

    # 时间戳
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), comment="创建时间")
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), onupdate=func.now(), comment="更新时间")

    # 关联关系
    user: Mapped[Optional["User"]] = relationship("User", back_populates="chat_sessions")
    organization: Mapped[Optional["Organization"]] = relationship("Organization", back_populates="chat_sessions")
    messages: Mapped[list["ChatMessage"]] = relationship("ChatMessage", back_populates="session", cascade="all, delete-orphan")


class ChatMessage(Base):
    """聊天消息表"""
    __tablename__ = "chat_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, index=True)
    session_id: Mapped[str] = mapped_column(String(36), ForeignKey("chat_sessions.id"), index=True, comment="会话ID")

    # 消息内容
    content: Mapped[str] = mapped_column(Text, comment="消息内容")
    message_type: Mapped[MessageType] = mapped_column(Enum(MessageType), comment="消息类型")

    # 元数据
    meta_data: Mapped[Any | None] = mapped_column(JSON, comment="额外元数据")

    # 用户反馈 (用于 RAG 质量评估)
    feedback: Mapped[int] = mapped_column(Integer, default=0, comment="用户反馈")
    feedback_note: Mapped[str | None] = mapped_column(Text, comment="反馈备注")

    # 时间戳
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), comment="创建时间")

    # 关联关系
    session: Mapped[Optional["ChatSession"]] = relationship("ChatSession", back_populates="messages")
