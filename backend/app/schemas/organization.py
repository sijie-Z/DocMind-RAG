# -*- coding: utf-8 -*-
"""
派聪明AI知识库系统 - 组织数据架构
"""

from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field

class OrganizationBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    color: Optional[str] = Field("#18a058", max_length=20)
    is_private: bool = False
    parent_id: Optional[int] = None
    sort_order: int = 0

class OrganizationCreate(OrganizationBase):
    pass

class OrganizationUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=500)
    color: Optional[str] = Field(None, max_length=20)
    is_private: Optional[bool] = None
    parent_id: Optional[int] = None
    sort_order: Optional[int] = None

class OrganizationOut(OrganizationBase):
    id: int
    level: int
    owner_id: int
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)

class OrganizationTree(OrganizationOut):
    children: List['OrganizationTree'] = []

# 递归模型需要更新引用
OrganizationTree.model_rebuild()
