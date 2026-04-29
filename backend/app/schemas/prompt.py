# -*- coding: utf-8 -*-
from typing import Optional
from pydantic import BaseModel, ConfigDict, Field
from datetime import datetime

class PromptTemplateBase(BaseModel):
    name: str = Field(..., description="模板名称")
    content: str = Field(..., description="提示词内容")
    description: Optional[str] = Field(None, description="模板描述")
    is_active: bool = Field(True, description="是否激活")
    is_system: bool = Field(False, description="是否为系统内置")
    category: str = Field("general", description="分类")

class PromptTemplateCreate(PromptTemplateBase):
    pass

class PromptTemplateUpdate(BaseModel):
    name: Optional[str] = None
    content: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None
    category: Optional[str] = None

class PromptTemplateResponse(PromptTemplateBase):
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None
    creator_id: Optional[int] = None
    model_config = ConfigDict(from_attributes=True)
