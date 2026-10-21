"""组织设置（B4 / D5）API 模型。"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


class OrganizationSettingsUpdate(BaseModel):
    """部分更新：省略的字段保持不变；显式 null 清除覆盖（回退平台默认）。"""

    default_provider: str | None = Field(None, max_length=32)
    default_model: str | None = Field(None, max_length=100)
    retrieval_top_k: int | None = Field(None, ge=1, le=50)
    allow_privacy_mode: bool | None = None
    monthly_token_quota: int | None = Field(None, ge=0)
    tool_whitelist: list[str] | None = None

    @field_validator("default_provider", "default_model")
    @classmethod
    def _strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        # 空串等价于清除覆盖，避免管理端输入框留空后写入无意义空值。
        return stripped or None

    @field_validator("tool_whitelist")
    @classmethod
    def _clean_tool_whitelist(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        cleaned: list[str] = []
        for item in value:
            name = item.strip()
            if not name:
                raise ValueError("工具白名单不能包含空名称")
            if name not in cleaned:
                cleaned.append(name)
        return cleaned


class EffectiveSettingEntry(BaseModel):
    """生效值 + 来源 + 是否需重启（管理端三列展示的契约）。"""

    value: Any = Field(..., description="生效值（组织覆盖或平台默认）")
    source: str = Field(..., description="来源：organization / default")
    requires_restart: bool = Field(..., description="变更后是否需要重启进程")


class OrganizationUsage(BaseModel):
    month_tokens: int = Field(..., description="本月已用 token（input+output）")
    monthly_token_quota: int | None = Field(None, description="月度配额；null=不限额")


class OrganizationSettingsResponse(BaseModel):
    organization_id: int
    settings: dict[str, EffectiveSettingEntry]
    usage: OrganizationUsage


class OrganizationSettingsAuditItem(BaseModel):
    id: int
    user_id: int
    username: str | None = None
    action: str
    target_type: str | None = None
    target_id: str | None = None
    detail: str | None = None
    created_at: datetime | None = None


class OrganizationSettingsAuditResponse(BaseModel):
    items: list[OrganizationSettingsAuditItem]
