"""
文件相关的Pydantic模型
"""
from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, Field
from enum import Enum


class FileStatus(str, Enum):
    """文件状态"""
    PENDING = "pending"
    UPLOADED = "uploaded"
    PARSING = "parsing"
    PARSED = "parsed"
    INDEXED = "indexed"
    FAILED = "failed"


class FileType(str, Enum):
    """文件类型"""
    PDF = "pdf"
    WORD = "word"
    EXCEL = "excel"
    PPT = "ppt"
    TXT = "txt"
    OTHER = "other"


class FileUploadResponse(BaseModel):
    """文件上传响应"""
    document_id: str = Field(..., description="文档ID")
    filename: str = Field(..., description="文件名")
    file_size: int = Field(..., description="文件大小（字节）")
    status: str = Field(..., description="处理状态")
    upload_time: str = Field(..., description="上传时间")
    message: Optional[str] = Field(None, description="提示信息")


class FileChunkUploadResponse(BaseModel):
    """文件分块上传响应"""
    status: str = Field(..., description="上传状态：chunk_uploaded 或 completed")
    chunk_index: Optional[int] = Field(None, description="分块序号")
    document_id: Optional[str] = Field(None, description="文档ID（上传完成时返回）")
    message: str = Field(..., description="提示信息")


class FileListItem(BaseModel):
    """文档列表项"""
    id: str = Field(..., description="文档ID")
    filename: str = Field(..., description="文件名")
    file_size: int = Field(..., description="文件大小（字节）")
    file_type: str = Field(..., description="文件类型")
    status: str = Field(..., description="处理状态")
    description: Optional[str] = Field(None, description="文件描述")
    content_length: Optional[int] = Field(None, description="文本内容长度")
    chunk_count: int = Field(0, description="分块数量")
    created_at: str = Field(..., description="创建时间")
    parsed_at: Optional[str] = Field(None, description="解析完成时间")
    indexed_at: Optional[str] = Field(None, description="索引完成时间")


class FileListResponse(BaseModel):
    """文档列表响应"""
    total: int = Field(..., description="总文档数量")
    documents: List[FileListItem] = Field(..., description="文档列表")
    skip: int = Field(..., description="跳过的数量")
    limit: int = Field(..., description="返回的数量限制")


class FileDeleteResponse(BaseModel):
    """文件删除响应"""
    success: bool = Field(..., description="是否删除成功")
    message: str = Field(..., description="提示信息")


class FileUploadStatus(BaseModel):
    """文件上传状态"""
    status: str = Field(..., description="上传状态：not_started, in_progress, completed, error")
    uploaded_chunks: int = Field(0, description="已上传的分块数量")
    total_chunks: Optional[int] = Field(None, description="总分块数量")
    message: str = Field(..., description="提示信息")


class DocumentProcessResponse(BaseModel):
    """文档处理响应"""
    success: bool = Field(..., description="是否处理成功")
    message: str = Field(..., description="提示信息")
    document_id: str = Field(..., description="文档ID")