"""
知识库相关的Pydantic模型
"""
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field
from enum import Enum
from datetime import datetime


class SearchMode(str, Enum):
    """搜索模式"""
    HYBRID = "hybrid"  # 混合搜索（关键词+向量）
    VECTOR = "vector"  # 纯向量搜索
    KEYWORD = "keyword"  # 纯关键词搜索


class KnowledgeSearchRequest(BaseModel):
    """知识库搜索请求"""
    query: str = Field(..., min_length=1, description="搜索查询")
    organization_id: int = Field(..., description="组织ID")
    top_k: int = Field(10, ge=1, le=50, description="返回结果数量")
    search_mode: SearchMode = Field(SearchMode.HYBRID, description="搜索模式")


class SearchResult(BaseModel):
    """搜索结果项"""
    id: str = Field(..., description="结果ID")
    text: str = Field(..., description="匹配文本内容")
    score: float = Field(..., description="匹配得分")
    metadata: Dict[str, Any] = Field(..., description="元数据")
    source: Dict[str, Any] = Field(..., description="来源信息")
    has_keyword: Optional[bool] = Field(None, description="是否命中关键词")
    has_vector: Optional[bool] = Field(None, description="是否命中向量")
    rewrite_hits: Optional[int] = Field(None, description="命中改写查询次数")
    fresh_factor: Optional[float] = Field(None, description="新鲜度提升因子")


class KnowledgeSearchResponse(BaseModel):
    """知识库搜索响应"""
    query: str = Field(..., description="搜索查询")
    results: List[SearchResult] = Field(..., description="搜索结果列表")
    total_count: int = Field(..., description="结果总数")
    search_mode: str = Field(..., description="搜索模式")
    search_time: Optional[float] = Field(None, description="搜索耗时（秒）")


class SearchSuggestionResponse(BaseModel):
    """搜索建议响应"""
    query: str = Field(..., description="原始查询")
    suggestions: List[str] = Field(..., description="建议列表")


class KnowledgeStats(BaseModel):
    """知识库统计信息"""
    total_documents: int = Field(..., description="总文档数量")
    total_size: int = Field(..., description="总文件大小（字节）")
    total_content_length: int = Field(..., description="总文本内容长度")
    total_chunks: int = Field(..., description="总分块数量")
    status_distribution: Dict[str, int] = Field(..., description="状态分布")
    type_distribution: Dict[str, int] = Field(..., description="文件类型分布")
    average_file_size: float = Field(..., description="平均文件大小")
    average_content_length: float = Field(..., description="平均内容长度")


class KnowledgeStatsResponse(BaseModel):
    """知识库统计响应"""
    organization_id: int = Field(..., description="组织ID")
    stats: KnowledgeStats = Field(..., description="统计信息")
    updated_at: datetime = Field(..., description="更新时间")


class KnowledgeBuildResponse(BaseModel):
    """知识库构建响应"""
    success: bool = Field(..., description="是否构建成功")
    document_id: str = Field(..., description="文档ID")
    message: str = Field(..., description="提示信息")
    build_time: Optional[float] = Field(None, description="构建耗时（秒）")


class BatchDeleteRequest(BaseModel):
    """批量删除请求"""
    document_ids: List[str] = Field(..., min_length=1, description="文档ID列表")
    organization_id: Optional[int] = Field(None, description="组织ID")


class DocumentChunkInfo(BaseModel):
    """文档分块信息"""
    id: str = Field(..., description="分块ID")
    chunk_index: int = Field(..., description="分块序号")
    chunk_text: str = Field(..., description="分块文本内容")
    chunk_length: int = Field(..., description="分块长度")
    page_number: Optional[int] = Field(None, description="页码")
    section_title: Optional[str] = Field(None, description="章节标题")
    embedding_id: Optional[str] = Field(None, description="向量嵌入ID")


class DocumentDetail(BaseModel):
    """文档详情"""
    id: str = Field(..., description="文档ID")
    filename: str = Field(..., description="文件名")
    file_size: int = Field(..., description="文件大小")
    file_type: str = Field(..., description="文件类型")
    status: str = Field(..., description="处理状态")
    content_length: Optional[int] = Field(None, description="文本内容长度")
    chunk_count: int = Field(..., description="分块数量")
    description: Optional[str] = Field(None, description="文件描述")
    title: Optional[str] = Field(None, description="文档标题")
    author: Optional[str] = Field(None, description="作者")
    created_at: datetime = Field(..., description="创建时间")
    parsed_at: Optional[datetime] = Field(None, description="解析完成时间")
    indexed_at: Optional[datetime] = Field(None, description="索引完成时间")
    chunks: List[DocumentChunkInfo] = Field(default_factory=list, description="分块列表")


class SimilarityResponse(BaseModel):
    """文本相似度计算响应"""
    text1: str = Field(..., description="文本1")
    text2: str = Field(..., description="文本2")
    similarity: float = Field(..., description="相似度得分（-1到1）")
    similarity_percentage: float = Field(..., description="相似度百分比（0到100）")
