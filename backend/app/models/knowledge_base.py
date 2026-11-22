"""知识库（多库）模型。

一个组织可以拥有多个知识库；文档的 ``knowledge_base_id`` 为 NULL 时保持旧的
「组织内全部文档」语义，不受多库功能影响。知识库本身严格按 organization_id
归属，所有读写都必须先解析调用者的有效组织，再以该组织为过滤条件。
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.database import Base


class KnowledgeBase(Base):
    """组织内的一个具名知识库（P2：多库隔离的最小实体）。"""

    __tablename__ = "knowledge_bases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, comment="知识库ID")
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id"), index=True, comment="所属组织（租户根）"
    )
    name: Mapped[str] = mapped_column(String(200), comment="知识库名称")
    description: Mapped[str | None] = mapped_column(Text, comment="知识库描述")
    created_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True, comment="创建者用户ID"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), comment="更新时间"
    )
