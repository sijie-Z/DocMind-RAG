# -*- coding: utf-8 -*-
"""
派聪明AI知识库系统 - 用户设置 Schemas
"""
from datetime import datetime
from typing import Optional, Dict, Any
from pydantic import BaseModel, ConfigDict


class UserSettingsBase(BaseModel):
    """用户设置基础模型"""
    language: str = "zh"
    theme: str = "light"
    preferences: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(from_attributes=True)


class UserSettingsUpdate(BaseModel):
    """用户设置更新模型 — 所有字段可选"""
    language: Optional[str] = None
    theme: Optional[str] = None
    preferences: Optional[Dict[str, Any]] = None

    model_config = ConfigDict(from_attributes=True)


class UserSettingsResponse(UserSettingsBase):
    """用户设置响应模型"""
    id: int
    user_id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
