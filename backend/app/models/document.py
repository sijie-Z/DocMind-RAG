"""
文档模型 - 管理文档上传、解析和存储
"""

import enum
from datetime import datetime
from typing import Any, Optional

import sqlalchemy as sa
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.database import Base


class DocumentStatus(enum.Enum):
    """文档处理状态"""

    PENDING = "pending"  # 等待处理
    UPLOADED = "uploaded"  # 已上传
    PARSING = "parsing"  # 解析中
    PARSED = "parsed"  # 解析完成
    INDEXED = "indexed"  # 已索引
    FAILED = "failed"  # 处理失败


class DocumentVisibility(enum.Enum):
    """文档可见性：组织级（默认，行为与旧版一致）或私有（仅上传者+授权人）。"""

    ORGANIZATION = "organization"
    PRIVATE = "private"


class DocumentType(enum.Enum):
    """文档类型"""

    PDF = "pdf"
    WORD = "word"
    EXCEL = "excel"
    PPT = "ppt"
    TXT = "txt"
    OTHER = "other"


class Document(Base):
    """文档表 - 存储文档基本信息"""

    __tablename__ = "documents"

    # 主键不再附加 index=True：ix_documents_id 与 PRIMARY 完全冗余，且在
    # 「建案 FOR UPDATE IN(...) vs 并发 DELETE」下会形成主键/二级索引锁序颠倒
    # （实测 MySQL 1213 死锁）。迁移 025 删除该索引。
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    filename: Mapped[str] = mapped_column(String(255), comment="原始文件名")
    file_path: Mapped[str] = mapped_column(String(500), comment="文件存储路径")
    file_size: Mapped[int] = mapped_column(Integer, comment="文件大小(字节)")
    file_type: Mapped[DocumentType] = mapped_column(Enum(DocumentType), comment="文件类型")
    mime_type: Mapped[str | None] = mapped_column(String(100), comment="MIME类型")
    md5_hash: Mapped[str | None] = mapped_column(String(32), index=True, comment="文件MD5哈希")

    # 文档元数据
    title: Mapped[str | None] = mapped_column(String(500), comment="文档标题")
    author: Mapped[str | None] = mapped_column(String(255), comment="作者")
    description: Mapped[str | None] = mapped_column(Text, comment="文档描述")
    keywords: Mapped[Any | None] = mapped_column(JSON, comment="关键词列表")

    # 处理状态
    status: Mapped[DocumentStatus] = mapped_column(
        Enum(DocumentStatus), default=DocumentStatus.PENDING, index=True, comment="处理状态"
    )
    parse_error: Mapped[str | None] = mapped_column(Text, comment="解析错误信息")

    # 文档级可见性（对标 Onyx 的文档 ACL 最小版）：
    #   organization = 组织内全员可见（默认，行为与旧版一致）
    #   private      = 仅上传者 + document_acl 中被授权的用户可见
    visibility: Mapped[DocumentVisibility] = mapped_column(
        # 与迁移 028 一致：持久化小写 value，而非枚举成员名（大写）
        Enum(
            DocumentVisibility,
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        default=DocumentVisibility.ORGANIZATION,
        server_default=DocumentVisibility.ORGANIZATION.value,
        index=True,
        comment="可见性：organization=组织可见，private=上传者+授权人",
    )

    # 文档级启停：停用后不参与检索召回（保留内容与索引，随时可恢复）。
    # 对标 RAGFlow/Dify/FastGPT 的「禁用/归档文档」能力——合同旧版本、
    # 过期制度等场景需要「留档但不污染召回」。
    is_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default=sa.text("1"),
        index=True,
        comment="是否启用（停用后不参与检索）",
    )
    # 内容统计
    content_length: Mapped[int | None] = mapped_column(Integer, comment="文本内容长度")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, comment="分块数量")

    # 权限和归属
    organization_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id"), index=True, comment="所属组织"
    )
    uploaded_by: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), index=True, comment="上传用户"
    )

    # 时间戳
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), comment="更新时间"
    )
    parsed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="解析完成时间"
    )
    indexed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="索引完成时间"
    )

    # 关联关系
    organization: Mapped[Optional["Organization"]] = relationship(
        "Organization", back_populates="documents"
    )
    uploader: Mapped[Optional["User"]] = relationship("User", back_populates="documents")
    chunks: Mapped[list["DocumentChunk"]] = relationship(
        "DocumentChunk", back_populates="document", cascade="all, delete-orphan"
    )

    # 不在此声明 collation：全库统一继承数据库默认值（canonical =
    # utf8mb4_unicode_ci，由部署配置与 CI 保证），避免迁移路径与 create_all
    # 路径因逐表覆盖而产生分歧。


class DocumentAcl(Base):
    """文档级授权表：private 文档的显式授权名单。

    只对 ``visibility=private`` 的文档生效；``organization`` 文档的检索与
    访问不查本表（避免无谓 JOIN）。subject 目前只支持 user（部门/组授权
    留待后续，避免引入未使用的能力）。
    """

    __tablename__ = "document_acl"
    __table_args__ = (
        # 同一文档对同一用户只应有一条授权（幂等 upsert 依赖它）
        UniqueConstraint("document_id", "subject_id", name="uq_document_acl_doc_subject"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), index=True, comment="文档ID"
    )
    subject_type: Mapped[str] = mapped_column(
        String(20), default="user", comment="授权主体类型（当前仅 user）"
    )
    subject_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True, comment="被授权用户ID"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="授权时间"
    )


class DocumentChunk(Base):
    """文档分块表 - 存储文档解析后的文本块"""

    __tablename__ = "document_chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id"), index=True, comment="所属文档"
    )

    # 分块信息
    chunk_index: Mapped[int] = mapped_column(Integer, comment="分块序号")
    chunk_text: Mapped[str] = mapped_column(Text, comment="分块文本内容")
    original_text: Mapped[str | None] = mapped_column(
        Text, comment="未加检索背景摘要的原文；legacy 分块为 NULL"
    )
    chunk_length: Mapped[int] = mapped_column(Integer, comment="分块长度")

    # 上下文信息
    start_pos: Mapped[int | None] = mapped_column(Integer, comment="在原文中的起始位置")
    end_pos: Mapped[int | None] = mapped_column(Integer, comment="在原文中的结束位置")
    page_number: Mapped[int | None] = mapped_column(Integer, comment="页码（PDF等格式）")
    section_title: Mapped[str | None] = mapped_column(String(500), comment="章节标题")

    # 向量嵌入（用于相似度搜索）
    embedding_id: Mapped[str | None] = mapped_column(
        String(36), comment="向量嵌入ID，关联到Elasticsearch"
    )

    # 元数据
    meta_data: Mapped[Any | None] = mapped_column(JSON, comment="额外元数据")

    # 时间戳
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="创建时间"
    )

    # 关联关系
    document: Mapped[Optional["Document"]] = relationship("Document", back_populates="chunks")

    # 不在此声明 collation：全库统一继承数据库默认值（canonical =
    # utf8mb4_unicode_ci，由部署配置与 CI 保证），避免迁移路径与 create_all
    # 路径因逐表覆盖而产生分歧。


class DocumentTag(Base):
    """文档标签关联表"""

    __tablename__ = "document_tags"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id"), index=True, comment="文档ID"
    )
    tag_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tags.id"), index=True, comment="标签ID"
    )

    # 时间戳
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="创建时间"
    )

    # 不在此声明 collation：全库统一继承数据库默认值（canonical =
    # utf8mb4_unicode_ci，由部署配置与 CI 保证），避免迁移路径与 create_all
    # 路径因逐表覆盖而产生分歧。


class Tag(Base):
    """标签表"""

    __tablename__ = "tags"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, comment="标签名称")
    color: Mapped[str] = mapped_column(String(7), default="#007bff", comment="标签颜色")
    description: Mapped[str | None] = mapped_column(String(500), comment="标签描述")

    # 统计信息
    document_count: Mapped[int] = mapped_column(Integer, default=0, comment="关联文档数量")

    # 时间戳
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), onupdate=func.now(), comment="更新时间"
    )

    # 不在此声明 collation：全库统一继承数据库默认值（canonical =
    # utf8mb4_unicode_ci，由部署配置与 CI 保证），避免迁移路径与 create_all
    # 路径因逐表覆盖而产生分歧。
