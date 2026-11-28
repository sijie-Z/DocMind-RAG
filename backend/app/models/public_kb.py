"""公开知识库发布持久层（批次十三）。

两张表构成「管理员显式白名单」的公开只读检索能力：

- ``KbPublication``（``kb_publication``）：一次发布 = KB + 标题/描述 + token 摘要
  + 启用/过期/撤销状态 + 聊天开关/日预算。token 只存 SHA-256 摘要，明文只在
  创建/轮换响应中返回一次；失效状态由 ``enabled`` / ``expires_at`` /
  ``revoked_at`` 表达，撤销只写 ``revoked_at``（行保留供审计），不提供删除路径。
- ``DocumentPublication``（``document_publication``）：发布的白名单行。**只有**
  显式写入这里的文档才可能被匿名检索；``documents`` 表的 visibility/ACL 语义
  完全不变（公开面不读 ACL，也绝不走 ``viewer=None`` 的 fail-open 路径）。

``knowledge_base_id`` 刻意不建外键：知识库删除是既有管理动作（删除时把文档的
``knowledge_base_id`` 置空），发布记录不得让该动作在 MySQL 上以 1451 失败。
公开检索每次都会把 ``documents.knowledge_base_id`` 与发布的 ``knowledge_base_id``
重新比对，因此悬空发布不会召回任何文档。``document_id`` 则必须建外键（与
documents 删除链路保持一致：删除文档时由 ``_DOCUMENT_CHILD_MODELS`` 清理白名单行）。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, text
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.database import Base


class KbPublication(Base):
    """一个知识库的公开只读发布（token 摘要 + 启用/过期/撤销状态）。"""

    __tablename__ = "kb_publication"
    __table_args__ = (
        # token 摘要全局唯一：公开端点按摘要等值查询，命中即唯一定位。
        UniqueConstraint("token_hash", name="uq_kb_publication_token_hash"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("organizations.id"),
        nullable=False,
        index=True,
        comment="所属组织（租户隔离键 / 组织删除保护）",
    )
    knowledge_base_id: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
        index=True,
        comment="发布的知识库 id（刻意不建外键：知识库删除不受发布记录阻塞）",
    )
    token_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="公开 token 的 SHA-256 摘要（原文不落库）"
    )
    title: Mapped[str] = mapped_column(String(100), nullable=False, comment="公开页标题")
    description: Mapped[str | None] = mapped_column(
        String(500), nullable=True, comment="公开页描述（可空；仅展示）"
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, comment="启用开关（false 时公开面 404）"
    )
    chat_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        # server_default 与迁移 050 同源：存量行回填 false，迁移库/create_all 库不漂移。
        server_default=text("0"),
        comment="发布级聊天开关（false 时公开聊天 404；默认关闭）",
    )
    daily_token_budget: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="每日 token 预算（NULL = 全局默认；0 = 禁止聊天）",
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="过期时间（空 = 不过期；最长 90 天，服务层校验）",
    )
    created_by: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id"),
        nullable=False,
        comment="发布创建/最近轮换的操作人",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"),
        server_default=func.now(),
        nullable=False,
        comment="创建/最近轮换时间",
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="撤销时间（非空即失效；行保留供审计）",
    )


class DocumentPublication(Base):
    """发布白名单：`(publication_id, document_id)` 唯一。

    行只表达「这份发布允许检索这篇文档」；是否真的可检索仍由文档当前状态
    （组织/知识库/``INDEXED``/启用）在每次公开检索时重新校验。
    """

    __tablename__ = "document_publication"
    __table_args__ = (
        UniqueConstraint("publication_id", "document_id", name="uq_document_publication_pub_doc"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("organizations.id"),
        nullable=False,
        index=True,
        comment="所属组织（冗余保存，供组织删除保护与跨组织防护）",
    )
    publication_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("kb_publication.id"),
        nullable=False,
        index=True,
        comment="所属发布 id",
    )
    document_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("documents.id"),
        nullable=False,
        index=True,
        comment="白名单文档 id（文档删除时随 _DOCUMENT_CHILD_MODELS 清理）",
    )
    created_by: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=False, comment="加入白名单的操作人"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"),
        server_default=func.now(),
        nullable=False,
        comment="加入白名单时间",
    )
