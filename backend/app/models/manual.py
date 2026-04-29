# -*- coding: utf-8 -*-
from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, ForeignKey
from sqlalchemy.sql import func
from app.core.database import Base

class SystemManual(Base):
    """系统操作手册模型"""
    __tablename__ = "system_manuals"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    title = Column(String(200), nullable=False, comment="手册标题")
    content = Column(Text, nullable=False, comment="手册内容(Markdown)")
    category = Column(String(50), nullable=False, default="general", comment="分类")
    sort_order = Column(Integer, default=0, comment="排序权重")
    is_published = Column(Boolean, default=True, comment="是否发布")
    
    created_at = Column(DateTime, default=func.now(), nullable=False)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=False)
    
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
