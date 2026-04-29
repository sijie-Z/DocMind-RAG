# -*- coding: utf-8 -*-
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base

class Notification(Base):
    """系统通知模型"""
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)
    type = Column(String(50), default="system", nullable=False)  # system, document, chat
    is_read = Column(Boolean, default=False, nullable=False)
    target_route = Column(String(100), nullable=True)
    target_id = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=False)

    # 关联
    user = relationship("User", backref="notifications")

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "title": self.title,
            "content": self.content,
            "type": self.type,
            "is_read": self.is_read,
            "target_route": self.target_route,
            "target_id": self.target_id,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }
