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
    # B4（D6）：创建时的模板层级。默认 organization（保持既有创建语义）；
    # personal 仅创建者可见/可激活。system 由 seed 端点独占，客户端传入会被拒。
    scope: str = Field(
        "organization", description="模板层级：organization / personal（system 保留）"
    )


class PromptTemplateUpdate(BaseModel):
    name: str | None = None
    content: str | None = None
    description: str | None = None
    is_active: bool | None = None
    category: str | None = None


class PromptTemplateUpdateWithNote(PromptTemplateUpdate):
    change_note: str | None = Field(None, description="变更说明")


class PromptTemplateResponse(PromptTemplateBase):
    id: int
    scope: str = Field("organization", description="模板层级：system / organization / personal")
    created_at: datetime
    updated_at: datetime | None = None
    creator_id: int | None = None
    version: int = Field(1, description="当前版本号")
    model_config = ConfigDict(from_attributes=True)


class PromptTemplateVersionResponse(BaseModel):
    id: int
    prompt_id: int
    version: int
    name: str
    content: str
    description: str | None = None
    change_note: str | None = None
    created_at: datetime
    creator_id: int | None = None
    model_config = ConfigDict(from_attributes=True)
