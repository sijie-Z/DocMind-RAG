"""User response schemas — single source of truth for User serialization."""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class UserInfoResponse(BaseModel):
    """Canonical User response schema used across all endpoints."""
    id: int
    username: str
    email: str
    full_name: str | None = None
    nickname: str | None = None
    phone: str | None = None
    avatar_url: str | None = None
    bio: str | None = None
    organization_id: int | None = None
    role: str = "user"
    is_active: bool = True
    is_superuser: bool = False
    created_at: datetime | None = None
    updated_at: datetime | None = None
    last_login_at: datetime | None = None
    preferences: str | None = None
    api_key: str | None = None
    department: str | None = None
    position: str | None = None

    model_config = ConfigDict(from_attributes=True)


class UserStatsResponse(BaseModel):
    conversation_count: int
    message_count: int
    file_count: int
    knowledge_count: int
    storage_used: int = 0
    storage_limit: int = 0
    activity_trend: list[int] = []


class UserUpdateProfile(BaseModel):
    full_name: str | None = None
    email: str | None = None
    phone: str | None = None
    avatar_url: str | None = None
    bio: str | None = None
    preferences: dict[str, Any] | None = None
    model_config = ConfigDict(from_attributes=True)


class UserUpdatePassword(BaseModel):
    old_password: str
    new_password: str


class UserSessionResponse(BaseModel):
    id: int
    device_name: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    is_active: bool
    last_seen_at: datetime | None = None
    created_at: datetime | None = None


class UserAuditLogResponse(BaseModel):
    id: int
    action: str
    target_type: str | None = None
    target_id: str | None = None
    details: str | None = None
    ip_address: str | None = None
    created_at: datetime | None = None
