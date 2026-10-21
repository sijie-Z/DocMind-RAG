"""
提示词模板模型
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.database import Base


class PromptTemplate(Base):
    """提示词模板模型"""

    __tablename__ = "prompt_templates"
    # A2：模板名唯一性按组织划分（系统模板 org=NULL，不受此约束；
    # 系统模板的重名由 seed / create 的可见性冲突检查兜底）。
    __table_args__ = (
        UniqueConstraint("organization_id", "name", name="uq_prompt_templates_org_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), index=True, comment="模板名称")
    content: Mapped[str] = mapped_column(Text, comment="提示词内容")
    description: Mapped[str | None] = mapped_column(String(255), comment="模板描述")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否激活")
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否为系统内置")
    # B4（D6）：模板层级。system=系统内置（org NULL，全组织只读）；
    # organization=组织模板（组织成员可见，激活需组织管理员）；
    # personal=个人模板（仅创建者可见/可激活，只影响自己）。
    # server_default 让存量行在迁移窗口内可读；迁移会把 is_system=1 的行回填为 system。
    scope: Mapped[str] = mapped_column(
        String(16),
        default="organization",
        server_default="organization",
        comment="模板层级：system / organization / personal",
    )
    # 租户边界（A2 P0）：NULL = 系统内置模板（全组织只读可见）；非空 = 该组织私有。
    organization_id: Mapped[int | None] = mapped_column(
        Integer,
        ForeignKey("organizations.id"),
        index=True,
        comment="所属组织；NULL=系统内置（只读）",
    )
    category: Mapped[str] = mapped_column(String(50), default="general", comment="分类")
    version: Mapped[int] = mapped_column(Integer, default=1, comment="当前版本号")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), comment="更新时间"
    )
    creator_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), comment="创建者ID"
    )
