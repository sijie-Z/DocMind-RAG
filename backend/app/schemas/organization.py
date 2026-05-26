"""
派聪明AI知识库系统 - 组织数据架构
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class OrganizationBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)
    color: str | None = Field("#18a058", max_length=20)
    is_private: bool = False
    parent_id: int | None = None
    sort_order: int = 0

class OrganizationCreate(OrganizationBase):
    pass

class OrganizationUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = Field(None, max_length=500)
    color: str | None = Field(None, max_length=20)
    is_private: bool | None = None
    parent_id: int | None = None
    sort_order: int | None = None

class OrganizationOut(OrganizationBase):
    id: int
    level: int
    owner_id: int
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)

class OrganizationTree(OrganizationOut):
    children: list['OrganizationTree'] = []

# 递归模型需要更新引用
OrganizationTree.model_rebuild()
