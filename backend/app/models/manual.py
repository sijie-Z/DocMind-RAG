"""
系统操作手册模型
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.database import Base


class SystemManual(Base):
    """系统操作手册模型"""
    __tablename__ = "system_manuals"

    id: Mapped[int] = mapped_column(primary_key=True, index=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(200), comment="手册标题")
    content: Mapped[str] = mapped_column(Text, comment="手册内容(Markdown)")
    category: Mapped[str] = mapped_column(String(50), default="general", comment="分类")
    sort_order: Mapped[int] = mapped_column(Integer, default=0, comment="排序权重")
    is_published: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否发布")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "content": self.content,
            "category": self.category,
            "sort_order": self.sort_order,
            "is_published": self.is_published,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None
        }
