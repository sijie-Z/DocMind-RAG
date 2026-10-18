"""
提示词模板模型
"""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.database import Base


class PromptTemplate(Base):
    """提示词模板模型"""
    __tablename__ = "prompt_templates"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True, comment="模板名称")
    content: Mapped[str] = mapped_column(Text, comment="提示词内容")
    description: Mapped[str | None] = mapped_column(String(255), comment="模板描述")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否激活")
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否为系统内置")
    # 租户边界（A2 P0）：NULL = 系统内置模板（全组织只读可见）；非空 = 该组织私有。
    organization_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("organizations.id"),
        index=True,
        comment="所属组织；NULL=系统内置（只读）",
    )
    category: Mapped[str] = mapped_column(String(50), default="general", comment="分类")
    version: Mapped[int] = mapped_column(Integer, default=1, comment="当前版本号")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), comment="创建时间")
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), onupdate=func.now(), comment="更新时间")
    creator_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), comment="创建者ID")
