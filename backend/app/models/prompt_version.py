"""
提示词模板版本管理模型
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.database import Base


class PromptTemplateVersion(Base):
    """提示词模板版本历史"""

    __tablename__ = "prompt_template_versions"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    prompt_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("prompt_templates.id", ondelete="CASCADE"), index=True, comment="模板ID"
    )
    version: Mapped[int] = mapped_column(Integer, comment="版本号（每个模板自增）")
    name: Mapped[str] = mapped_column(String(100), comment="版本名称（历史的模板名）")
    content: Mapped[str] = mapped_column(Text, comment="版本内容")
    description: Mapped[str | None] = mapped_column(String(255), comment="版本描述")
    change_note: Mapped[str | None] = mapped_column(String(500), comment="变更说明")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="创建时间"
    )
    creator_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), comment="创建者ID"
    )
