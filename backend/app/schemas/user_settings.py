"""
DocMind AI知识库系统 - 用户设置 Schemas
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class UserSettingsBase(BaseModel):
    """用户设置基础模型"""

    language: str = "zh"
    theme: str = "light"
    preferences: dict[str, Any] | None = None

    model_config = ConfigDict(from_attributes=True)


class UserSettingsUpdate(BaseModel):
    """用户设置更新模型 — 所有字段可选"""

    language: str | None = None
    theme: str | None = None
    preferences: dict[str, Any] | None = None

    model_config = ConfigDict(from_attributes=True)


class UserSettingsResponse(UserSettingsBase):
    """用户设置响应模型"""

    id: int
    user_id: int
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ProviderSettingsUpdate(BaseModel):
    """个人 AI Provider 设置更新模型。

    - ``use_server_default=True``：清除个人配置，回退服务端默认（其余字段忽略）；
    - ``api_key`` 留空或回传掩码占位值时保留已存 key（掩码回写保护）；
    - ``base_url`` 留空时使用 provider 内置地址（custom 必填）。
    """

    provider: str | None = None
    model: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    use_server_default: bool = False
