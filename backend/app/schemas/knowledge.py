"""
知识库相关的Pydantic模型
"""
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class SearchMode(str, Enum):
    """搜索模式（本次请求强制采用的检索臂组合，issue #225）"""
    HYBRID = "hybrid"  # 混合搜索（关键词+向量两臂 + RRF 融合）
    VECTOR = "vector"  # 纯向量搜索（仅向量臂）
    KEYWORD = "keyword"  # 纯关键词搜索（仅关键词臂）


class KnowledgeSearchRequest(BaseModel):
    """知识库搜索请求"""
    query: str = Field(..., min_length=1, description="搜索查询")
    organization_id: int = Field(..., description="组织ID")
    # B4（D5）：显式值优先；缺省（None）时由端点解析「组织设置 > 平台默认
    # settings.TOP_K_RESULTS=10」。此前的 default_factory 把 10 固化成请求
    # 默认值，组织设置永远无法生效——改为 None 才能区分「用户没说」与
    # 「用户明确要 10」（不改变既有默认语义：未设置组织值时仍是 10）。
    top_k: int | None = Field(
        default=None,
        ge=1, le=50,
        description="返回结果数量；缺省 = 组织设置，组织未设置时为平台默认 10",
    )
    # issue #225 决策 7A：None = 自动路由（系统按查询复杂度选策略，缺省行为）；
    # 显式取值强制本次请求的检索臂组合（hybrid=两臂融合 / vector=仅向量臂 /
    # keyword=仅关键词臂）。此前默认 HYBRID 且被链路丢弃——字段存在但说了不算。
    search_mode: SearchMode | None = Field(
        None, description="搜索模式；缺省（null）= 自动路由，显式值强制本次检索臂组合"
    )


class SearchResult(BaseModel):
    """搜索结果项"""
    id: str = Field(..., description="结果ID")
    text: str = Field(..., description="匹配文本内容")
    score: float = Field(..., description="匹配得分")
    metadata: dict[str, Any] = Field(..., description="元数据")
    source: dict[str, Any] = Field(..., description="来源信息")
    has_keyword: bool | None = Field(None, description="是否命中关键词")
    has_vector: bool | None = Field(None, description="是否命中向量")
    rewrite_hits: int | None = Field(None, description="命中改写查询次数")
    fresh_factor: float | None = Field(None, description="新鲜度提升因子")


class KnowledgeSearchResponse(BaseModel):
    """知识库搜索响应"""
    query: str = Field(..., description="搜索查询")
    results: list[SearchResult] = Field(..., description="搜索结果列表")
    total_count: int = Field(..., description="结果总数")
    search_mode: str = Field(..., description="本次实际生效的搜索模式（显式值或自动路由结果）")
    search_time: float | None = Field(None, description="搜索耗时（秒）")
    # 空结果的原因。没有它时「没匹配到」与「索引是空的」在响应里同形（issue #182）：
    #   ok / empty_index / index_error / unknown
    index_status: str = Field(
        "ok",
        description=(
            "索引状态：ok(索引有数据) / empty_index(索引为空) / "
            "index_error(索引配置错误) / unknown(诊断本身失败)"
        ),
    )
    index_doc_count: int | None = Field(
        None,
        description=(
            "你所在组织在索引中的 chunk 数（按 organization_id 过滤，issue #257）；"
            "仅在结果为空时诊断，其余情况为 null"
        ),
    )


class SearchSuggestionResponse(BaseModel):
    """搜索建议响应"""
    query: str = Field(..., description="原始查询")
    suggestions: list[str] = Field(..., description="建议列表")


class KnowledgeStats(BaseModel):
    """知识库统计信息"""
    total_documents: int = Field(..., description="总文档数量")
    total_size: int = Field(..., description="总文件大小（字节）")
    total_content_length: int = Field(..., description="总文本内容长度")
    total_chunks: int = Field(..., description="总分块数量")
    status_distribution: dict[str, int] = Field(..., description="状态分布")
    type_distribution: dict[str, int] = Field(..., description="文件类型分布")
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
    build_time: float | None = Field(None, description="构建耗时（秒）")


class BatchDeleteRequest(BaseModel):
    """批量删除请求"""
    document_ids: list[str] = Field(..., min_length=1, description="文档ID列表")
    organization_id: int | None = Field(None, description="组织ID")


class DocumentChunkInfo(BaseModel):
    """文档分块信息"""
    id: str = Field(..., description="分块ID")
    chunk_index: int = Field(..., description="分块序号")
    chunk_text: str = Field(..., description="分块文本内容")
    chunk_length: int = Field(..., description="分块长度")
    page_number: int | None = Field(None, description="页码")
    section_title: str | None = Field(None, description="章节标题")
    embedding_id: str | None = Field(None, description="向量嵌入ID")


class DocumentDetail(BaseModel):
    """文档详情"""
    id: str = Field(..., description="文档ID")
    filename: str = Field(..., description="文件名")
    file_size: int = Field(..., description="文件大小")
    file_type: str = Field(..., description="文件类型")
    status: str = Field(..., description="处理状态")
    content_length: int | None = Field(None, description="文本内容长度")
    chunk_count: int = Field(..., description="分块数量")
    description: str | None = Field(None, description="文件描述")
    title: str | None = Field(None, description="文档标题")
    author: str | None = Field(None, description="作者")
    created_at: datetime = Field(..., description="创建时间")
    parsed_at: datetime | None = Field(None, description="解析完成时间")
    indexed_at: datetime | None = Field(None, description="索引完成时间")
    chunks: list[DocumentChunkInfo] = Field(default_factory=list, description="分块列表")


class SimilarityResponse(BaseModel):
    """文本相似度计算响应"""
    text1: str = Field(..., description="文本1")
    text2: str = Field(..., description="文本2")
    similarity: float = Field(..., description="相似度得分（-1到1）")
    similarity_percentage: float = Field(..., description="相似度百分比（0到100）")
