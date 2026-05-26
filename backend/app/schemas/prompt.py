from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PromptTemplateBase(BaseModel):
    name: str = Field(..., description="模板名称")
    content: str = Field(..., description="提示词内容")
    description: str | None = Field(None, description="模板描述")
    is_active: bool = Field(True, description="是否激活")
    is_system: bool = Field(False, description="是否为系统内置")
    category: str = Field("general", description="分类")

class PromptTemplateCreate(PromptTemplateBase):
    pass

class PromptTemplateUpdate(BaseModel):
    name: str | None = None
    content: str | None = None
    description: str | None = None
    is_active: bool | None = None
    category: str | None = None

class PromptTemplateResponse(PromptTemplateBase):
    id: int
    created_at: datetime
    updated_at: datetime | None = None
    creator_id: int | None = None
    model_config = ConfigDict(from_attributes=True)
