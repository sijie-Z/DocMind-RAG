"""知识库实体（多库）的 API 请求/响应模型。"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200, description="知识库名称")
    description: str | None = Field(None, description="知识库描述")


class KnowledgeBaseUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200, description="知识库名称")
    description: str | None = Field(None, description="知识库描述")


class KnowledgeBaseResponse(BaseModel):
    id: str
    organization_id: int
    name: str
    description: str | None
    created_by: int | None
    created_at: datetime
    updated_at: datetime | None

    model_config = ConfigDict(from_attributes=True)


class KnowledgeBaseListResponse(BaseModel):
    success: bool = True
    data: list[KnowledgeBaseResponse]
    total: int
    page: int
    page_size: int
