# -*- coding: utf-8 -*-
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ForeignKey
from sqlalchemy.sql import func
from app.core.database import Base

class PromptTemplate(Base):
    """
    提示词模板模型
    用于管理系统预设或用户自定义的 AI 提示词
    """
    __tablename__ = "prompt_templates"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, index=True, nullable=False, comment="模板名称")
    content = Column(Text, nullable=False, comment="提示词内容")
    description = Column(String(255), comment="模板描述")
    is_active = Column(Boolean, default=True, comment="是否激活")
    is_system = Column(Boolean, default=False, comment="是否为系统内置")
    category = Column(String(50), default="general", comment="分类：如通用、翻译、摘要等")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), comment="创建时间")
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), comment="更新时间")
    creator_id = Column(Integer, ForeignKey("users.id"), comment="创建者ID")
