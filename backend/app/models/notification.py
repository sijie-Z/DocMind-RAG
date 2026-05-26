"""
通知模型
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.database import Base


class Notification(Base):
    """系统通知模型"""
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True, index=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text)
    type: Mapped[str] = mapped_column(String(50), default="system")  # system, document, chat
    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    target_route: Mapped[str | None] = mapped_column(String(100))
    target_id: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    user: Mapped[Optional["User"]] = relationship("User", backref="notifications")

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
