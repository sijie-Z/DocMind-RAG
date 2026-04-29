"""
聊天模型 - 管理聊天会话和消息
"""
from sqlalchemy import Column, String, Text, DateTime, ForeignKey, Enum, JSON, Boolean, Integer
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base
import enum


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
    
    id = Column(String(36), primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True, comment="用户ID")
    organization_id = Column(Integer, ForeignKey("organizations.id"), index=True, comment="组织ID")
    
    # 会话信息
    title = Column(String(200), comment="会话标题")
    status = Column(Enum(ChatSessionStatus), default=ChatSessionStatus.ACTIVE, comment="会话状态")
    
    # 统计信息
    message_count = Column(Integer, default=0, comment="消息数量")
    last_message_at = Column(DateTime(timezone=True), comment="最后消息时间")
    
    # 配置信息
    settings = Column(JSON, comment="会话设置")
    
    # 时间戳
    created_at = Column(DateTime(timezone=True), server_default=func.now(), comment="创建时间")
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), comment="更新时间")
    
    # 关联关系
    user = relationship("User", back_populates="chat_sessions")
    organization = relationship("Organization", back_populates="chat_sessions")
    messages = relationship("ChatMessage", back_populates="session", cascade="all, delete-orphan")


class ChatMessage(Base):
    """聊天消息表"""
    __tablename__ = "chat_messages"
    
    id = Column(String(36), primary_key=True, index=True)
    session_id = Column(String(36), ForeignKey("chat_sessions.id"), nullable=False, index=True, comment="会话ID")
    
    # 消息内容
    content = Column(Text, nullable=False, comment="消息内容")
    message_type = Column(Enum(MessageType), nullable=False, comment="消息类型")
    
    # 元数据
    meta_data = Column(JSON, comment="额外元数据")
    
    # 用户反馈 (用于 RAG 质量评估)
    # feedback: 1 为赞, -1 为踩, 0 为无
    feedback = Column(Integer, default=0, comment="用户反馈")
    feedback_note = Column(Text, nullable=True, comment="反馈备注")
    
    # 时间戳
    created_at = Column(DateTime(timezone=True), server_default=func.now(), comment="创建时间")
    
    # 关联关系
    session = relationship("ChatSession", back_populates="messages")
    