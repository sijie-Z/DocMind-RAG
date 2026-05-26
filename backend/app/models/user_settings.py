"""
派聪明AI知识库系统 - 用户设置模型
"""
from datetime import datetime

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.sql import func

from app.core.database import Base


class UserSettings(Base):
    """用户个性化设置模型"""
    __tablename__ = "user_settings"

    id: int = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id: int = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False, index=True)
    language: str = Column(String(10), default="zh")
    theme: str = Column(String(20), default="light")
    preferences: dict = Column(JSON, default={})

    created_at: datetime = Column(DateTime, default=func.now())
    updated_at: datetime = Column(DateTime, default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<UserSettings(user_id={self.user_id}, language='{self.language}', theme='{self.theme}')>"
