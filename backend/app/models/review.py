"""文档核验身份（合同/制度/模板的角色、版本族与内容哈希）。

与 ``Document`` 核心表分开：只服务「证据核验」场景，不把版本平台语义塞进
上传/解析主表。每篇文档最多一行；删除文档时由知识库统一删除链清理。
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.database import Base

# 允许的文档角色；写接口在服务层校验，DB 保存字符串（不建 MySQL ENUM，
# 避免后续角色扩展需要 ALTER TABLE）。
DOC_ROLES = ("policy", "template", "contract")


class ReviewDocumentMeta(Base):
    """文档核验身份：谁在哪个版本族/版本序上、以哪个内容哈希注册。"""

    __tablename__ = "review_document_meta"
    __table_args__ = (
        # 同组织同角色同族同版本/同族版本序各只能注册一次；family/version 为
        # NULL 的制度/模板不参与唯一索引（服务层仅在同时给出时做重复检查）。
        UniqueConstraint(
            "organization_id",
            "doc_role",
            "family_key",
            "version_label",
            name="uq_review_document_meta_identity",
        ),
        UniqueConstraint(
            "organization_id",
            "doc_role",
            "family_key",
            "version_seq",
            name="uq_review_document_meta_version_seq",
        ),
        Index(
            "ix_review_document_meta_family",
            "organization_id",
            "doc_role",
            "family_key",
            "version_seq",
        ),
    )

    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id"), primary_key=True
    )
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=False, index=True
    )
    doc_role: Mapped[str] = mapped_column(String(16), nullable=False)
    family_key: Mapped[str | None] = mapped_column(String(128))
    version_label: Mapped[str | None] = mapped_column(String(64))
    version_seq: Mapped[int | None] = mapped_column(Integer)
    supersedes_document_id: Mapped[str | None] = mapped_column(String(36))
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    registered_by: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="注册时间"
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), comment="更新时间"
    )
