"""
文档模型 - 管理文档上传、解析和存储
"""
import enum
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.database import Base


class DocumentStatus(enum.Enum):
    """文档处理状态"""
    PENDING = "pending"          # 等待处理
    UPLOADED = "uploaded"      # 已上传
    PARSING = "parsing"         # 解析中
    PARSED = "parsed"           # 解析完成
    INDEXED = "indexed"         # 已索引
    FAILED = "failed"           # 处理失败


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

    id: Mapped[str] = mapped_column(String(36), primary_key=True, index=True)
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
    status: Mapped[DocumentStatus] = mapped_column(Enum(DocumentStatus), default=DocumentStatus.PENDING, index=True, comment="处理状态")
    parse_error: Mapped[str | None] = mapped_column(Text, comment="解析错误信息")

    # 内容统计
    content_length: Mapped[int | None] = mapped_column(Integer, comment="文本内容长度")
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, comment="分块数量")

    # 权限和归属
    organization_id: Mapped[int] = mapped_column(Integer, ForeignKey("organizations.id"), index=True, comment="所属组织")
    uploaded_by: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True, comment="上传用户")

    # 时间戳
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), comment="创建时间")
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), onupdate=func.now(), comment="更新时间")
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="解析完成时间")
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), comment="索引完成时间")

    # 关联关系
    organization: Mapped[Optional["Organization"]] = relationship("Organization", back_populates="documents")
    uploader: Mapped[Optional["User"]] = relationship("User", back_populates="documents")
    chunks: Mapped[list["DocumentChunk"]] = relationship("DocumentChunk", back_populates="document", cascade="all, delete-orphan")

    __table_args__ = (
        {'mysql_engine': 'InnoDB', 'mysql_charset': 'utf8mb4', 'mysql_collate': 'utf8mb4_unicode_ci'},
    )


class DocumentChunk(Base):
    """文档分块表 - 存储文档解析后的文本块"""
    __tablename__ = "document_chunks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, index=True)
    document_id: Mapped[str] = mapped_column(String(36), ForeignKey("documents.id"), index=True, comment="所属文档")

    # 分块信息
    chunk_index: Mapped[int] = mapped_column(Integer, comment="分块序号")
    chunk_text: Mapped[str] = mapped_column(Text, comment="分块文本内容")
    chunk_length: Mapped[int] = mapped_column(Integer, comment="分块长度")

    # 上下文信息
    start_pos: Mapped[int | None] = mapped_column(Integer, comment="在原文中的起始位置")
    end_pos: Mapped[int | None] = mapped_column(Integer, comment="在原文中的结束位置")
    page_number: Mapped[int | None] = mapped_column(Integer, comment="页码（PDF等格式）")
    section_title: Mapped[str | None] = mapped_column(String(500), comment="章节标题")

    # 向量嵌入（用于相似度搜索）
    embedding_id: Mapped[str | None] = mapped_column(String(36), comment="向量嵌入ID，关联到Elasticsearch")

    # 元数据
    meta_data: Mapped[Any | None] = mapped_column(JSON, comment="额外元数据")

    # 时间戳
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), comment="创建时间")

    # 关联关系
    document: Mapped[Optional["Document"]] = relationship("Document", back_populates="chunks")

    __table_args__ = (
        {'mysql_engine': 'InnoDB', 'mysql_charset': 'utf8mb4', 'mysql_collate': 'utf8mb4_unicode_ci'},
    )


class DocumentTag(Base):
    """文档标签关联表"""
    __tablename__ = "document_tags"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, index=True)
    document_id: Mapped[str] = mapped_column(String(36), ForeignKey("documents.id"), index=True, comment="文档ID")
    tag_id: Mapped[str] = mapped_column(String(36), ForeignKey("tags.id"), index=True, comment="标签ID")

    # 时间戳
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), comment="创建时间")

    __table_args__ = (
        {'mysql_engine': 'InnoDB', 'mysql_charset': 'utf8mb4', 'mysql_collate': 'utf8mb4_unicode_ci'},
    )


class Tag(Base):
    """标签表"""
    __tablename__ = "tags"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, comment="标签名称")
    color: Mapped[str] = mapped_column(String(7), default="#007bff", comment="标签颜色")
    description: Mapped[str | None] = mapped_column(String(500), comment="标签描述")

    # 统计信息
    document_count: Mapped[int] = mapped_column(Integer, default=0, comment="关联文档数量")

    # 时间戳
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), comment="创建时间")
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), onupdate=func.now(), comment="更新时间")

    __table_args__ = (
        {'mysql_engine': 'InnoDB', 'mysql_charset': 'utf8mb4', 'mysql_collate': 'utf8mb4_unicode_ci'},
    )
