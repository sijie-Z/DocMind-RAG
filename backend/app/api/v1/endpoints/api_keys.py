"""API Key 管理端点（issue #480 §5.1 / issue #496）。

- ``POST   /api/v1/api-keys``：创建（明文只在本次响应返回一次）；
- ``GET    /api/v1/api-keys``：列出自己的 key；超管可按 ``org_id`` 查组织内列表；
- ``DELETE /api/v1/api-keys/{key_id}``：吊销自己的 key（软删除：revoked_at）。

认证：**仅 JWT 会话**。``dm_`` API Key 访问本模块一律 403（``require_jwt_user``）——
管理 key 必须回到人的会话，泄露的 key 不得自行铸造/吊销其它 key。

scope → 端点映射（issue #496；``dm_`` key 需显式携带，JWT 用户无 ``api_key_scopes``
属性 → 视为全量，沿用既有 RBAC）：

============ =====================================================
scope          端点
============ =====================================================
chat:read      GET /chat/conversations[/{id}]、GET /agent/sessions[/{id}]
chat:write     POST /agent/chat、POST /chat/stream
knowledge:read GET /knowledge/*、GET /documents/*
knowledge:write POST /documents/upload（显式请求才签发，见下）
rpc            POST /api/v1/rpc（仅 ``dm_`` key 可用）
（仅 JWT）      /api/v1/api-keys 全模块（API key 一律 403）
============ =====================================================

范围说明（issue #496）：未列出的端点（会话写/删除、``POST /knowledge/search``、
``/agent/tasks*``、``/chat/parse-upload`` 等）沿用既有认证 + RBAC，不在本 issue
的 scope 映射内；``POST /sessions/{id}/fork``（sessions.py）待后续 issue 收口。

响应体不出现 ``key_hash``；任何错误信息不携带明文 key。
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user, get_user_org_id, require_jwt_user
from app.exceptions import AuthorizationError, NotFoundError, ValidationError
from app.models.api_key import ApiKey
from app.models.user import User
from app.services.api_key_service import DEFAULT_SCOPES, api_key_service, valid_scopes

router = APIRouter(dependencies=[Depends(require_jwt_user)])
logger = logging.getLogger(__name__)

# issue #496：``POST /documents/upload`` 门禁要求 ``knowledge:write``，而
# ``api_key_service.valid_scopes()`` 词表尚未登记该项（本 PR 白名单不含该文件）。
# 在端点层把它并入**可签发**集合：默认签发集合（``DEFAULT_SCOPES``）保持不变，
# 只有显式请求的 key 才获得知识库写权限。收尾时应把词表收敛回单一事实来源。
SCOPES_ISSUABLE: frozenset[str] = frozenset(valid_scopes()) | {"knowledge:write"}


class ApiKeyCreate(BaseModel):
    """创建请求：scopes 必须是已知 scope 的子集。"""

    name: str = Field(..., min_length=1, max_length=100)
    scopes: list[str] | None = Field(
        default=None, description=f"默认 {list(DEFAULT_SCOPES)}；只能是已知 scope 的子集"
    )
    expires_at: datetime | None = Field(default=None, description="过期时间（UTC，可选）")
    org_id: int | None = Field(default=None, description="绑定组织（缺省=用户主组织）")

    @field_validator("scopes")
    @classmethod
    def _validate_scopes(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        unknown = sorted(set(value) - SCOPES_ISSUABLE)
        if unknown:
            raise ValueError(f"未知 scope: {', '.join(unknown)}")
        if not value:
            raise ValueError("scopes 不能为空列表")
        return sorted(set(value))


def _serialize_key(record: ApiKey) -> dict:
    """列表投影：绝不包含 key_hash / 明文。"""
    return {
        "id": record.id,
        "name": record.name,
        "prefix": record.prefix,
        "scopes": list(record.scopes or []),
        "org_id": record.org_id,
        "expires_at": record.expires_at.isoformat() if record.expires_at else None,
        "last_used_at": record.last_used_at.isoformat() if record.last_used_at else None,
        "revoked_at": record.revoked_at.isoformat() if record.revoked_at else None,
        "created_at": record.created_at.isoformat() if record.created_at else None,
    }


@router.post("", response_model=dict)
async def create_api_key(
    body: ApiKeyCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """创建 API key；明文 ``api_key`` 只在本响应出现一次。"""
    if body.org_id is not None:
        if not getattr(current_user, "is_superuser", False) and body.org_id != (
            get_user_org_id(current_user)
        ):
            raise AuthorizationError("只能为自己所属组织创建 key")
    try:
        record, raw_key = await api_key_service.create_key(
            db,
            user=current_user,
            name=body.name,
            scopes=body.scopes,
            org_id=body.org_id,
            expires_at=body.expires_at,
        )
    except Exception as exc:
        await db.rollback()
        logger.error("Create api key failed for user=%s: %s", current_user.id, exc)
        raise ValidationError("创建 API Key 失败") from exc

    data = _serialize_key(record)
    data["api_key"] = raw_key
    return {"success": True, "data": data}


@router.get("", response_model=dict)
async def list_api_keys(
    org_id: int | None = Query(default=None, description="超管：按组织查询"),
    include_revoked: bool = Query(default=False),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """列出 key：普通用户只看自己的；超管可用 ``org_id`` 看组织内列表。"""
    if org_id is not None:
        if not getattr(current_user, "is_superuser", False):
            raise AuthorizationError("仅超管可按组织查询 API Key")
        records = await api_key_service.list_keys(
            db, org_id=org_id, include_revoked=include_revoked
        )
    else:
        records = await api_key_service.list_keys(
            db, user_id=current_user.id, include_revoked=include_revoked
        )
    return {"success": True, "data": [_serialize_key(r) for r in records]}


@router.delete("/{key_id}", response_model=dict)
async def revoke_api_key(
    key_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """吊销自己的 key；不存在/非本人一律 404（不泄露他人 key 是否存在）。"""
    record = await api_key_service.get_key(db, key_id)
    if record is None or record.user_id != current_user.id:
        raise NotFoundError("API Key 不存在", resource="api_key", resource_id=key_id)
    if record.revoked_at is None:
        await api_key_service.revoke_key(db, record)
    return {"success": True, "data": {"id": record.id, "revoked": True}}
