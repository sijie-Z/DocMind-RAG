"""
文档模型 - 管理文档上传、解析和存储
"""
from sqlalchemy import Column, String, Text, Integer, DateTime, Boolean, ForeignKey, JSON, Enum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base
import enum


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
    
    id = Column(String(36), primary_key=True, index=True)
    filename = Column(String(255), nullable=False, comment="原始文件名")
    file_path = Column(String(500), nullable=False, comment="文件存储路径")
    file_size = Column(Integer, nullable=False, comment="文件大小(字节)")
    file_type = Column(Enum(DocumentType), nullable=False, comment="文件类型")
    mime_type = Column(String(100), comment="MIME类型")
    md5_hash = Column(String(32), index=True, comment="文件MD5哈希")
    
    # 文档元数据
    title = Column(String(500), comment="文档标题")
    author = Column(String(255), comment="作者")
    description = Column(Text, comment="文档描述")
    keywords = Column(JSON, comment="关键词列表")
    
    # 处理状态
    status = Column(Enum(DocumentStatus), default=DocumentStatus.PENDING, index=True, comment="处理状态")
    parse_error = Column(Text, comment="解析错误信息")
    
    # 内容统计
    content_length = Column(Integer, comment="文本内容长度")
    chunk_count = Column(Integer, default=0, comment="分块数量")
    
    # 权限和归属
    organization_id = Column(Integer, ForeignKey("organizations.id"), nullable=False, index=True, comment="所属组织")
    uploaded_by = Column(Integer, ForeignKey("users.id"), nullable=False, index=True, comment="上传用户")
    
    # 时间戳
    created_at = Column(DateTime(timezone=True), server_default=func.now(), comment="创建时间")
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), comment="更新时间")
    parsed_at = Column(DateTime(timezone=True), comment="解析完成时间")
    indexed_at = Column(DateTime(timezone=True), comment="索引完成时间")
    
    # 关联关系
    organization = relationship("Organization", back_populates="documents")
    uploader = relationship("User", back_populates="documents")
    chunks = relationship("DocumentChunk", back_populates="document", cascade="all, delete-orphan")
    
    # 表配置
    __table_args__ = (
        {'mysql_engine': 'InnoDB', 'mysql_charset': 'utf8mb4', 'mysql_collate': 'utf8mb4_unicode_ci'},
    )


class DocumentChunk(Base):
    """文档分块表 - 存储文档解析后的文本块"""
    __tablename__ = "document_chunks"
    
    id = Column(String(36), primary_key=True, index=True)
    document_id = Column(String(36), ForeignKey("documents.id"), nullable=False, index=True, comment="所属文档")
    
    # 分块信息
    chunk_index = Column(Integer, nullable=False, comment="分块序号")
    chunk_text = Column(Text, nullable=False, comment="分块文本内容")
    chunk_length = Column(Integer, nullable=False, comment="分块长度")
    
    # 上下文信息
    start_pos = Column(Integer, comment="在原文中的起始位置")
    end_pos = Column(Integer, comment="在原文中的结束位置")
    page_number = Column(Integer, comment="页码（PDF等格式）")
    section_title = Column(String(500), comment="章节标题")
    
    # 向量嵌入（用于相似度搜索）
    embedding_id = Column(String(36), comment="向量嵌入ID，关联到Elasticsearch")
    
    # 元数据
    meta_data = Column(JSON, comment="额外元数据")
    
    # 时间戳
    created_at = Column(DateTime(timezone=True), server_default=func.now(), comment="创建时间")
    
    # 关联关系
    document = relationship("Document", back_populates="chunks")

    # 👇👇👇 必须加上这一段！
    __table_args__ = (
        {'mysql_engine': 'InnoDB', 'mysql_charset': 'utf8mb4', 'mysql_collate': 'utf8mb4_unicode_ci'},
    )

class DocumentTag(Base):
    """文档标签关联表"""
    __tablename__ = "document_tags"
    
    id = Column(String(36), primary_key=True, index=True)
    document_id = Column(String(36), ForeignKey("documents.id"), nullable=False, index=True, comment="文档ID")
    tag_id = Column(String(36), ForeignKey("tags.id"), nullable=False, index=True, comment="标签ID")
    
    # 时间戳
    created_at = Column(DateTime(timezone=True), server_default=func.now(), comment="创建时间")
    
    # 联合唯一约束
    __table_args__ = (
        # 一个文档不能重复添加同一个标签
        {'mysql_engine': 'InnoDB', 'mysql_charset': 'utf8mb4', 'mysql_collate': 'utf8mb4_unicode_ci'},
    )


class Tag(Base):
    """标签表"""
    __tablename__ = "tags"
    
    id = Column(String(36), primary_key=True, index=True)
    name = Column(String(100), nullable=False, unique=True, comment="标签名称")
    color = Column(String(7), default="#007bff", comment="标签颜色")
    description = Column(String(500), comment="标签描述")
    
    # 统计信息
    document_count = Column(Integer, default=0, comment="关联文档数量")
    
    # 时间戳
    created_at = Column(DateTime(timezone=True), server_default=func.now(), comment="创建时间")
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), comment="更新时间")

    # 👇👇👇 关键修复：添加这段配置，必须和 DocumentTag 一模一样！
    __table_args__ = (
        {'mysql_engine': 'InnoDB', 'mysql_charset': 'utf8mb4', 'mysql_collate': 'utf8mb4_unicode_ci'},
    )
    