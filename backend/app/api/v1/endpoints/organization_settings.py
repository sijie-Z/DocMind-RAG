"""组织设置（B4 / D5）：生效值查询、管理端更新与审计读取。

鉴权口径（沿用 A2）：
- GET（生效值）：组织成员即可读——普通成员的聊天隐私开关需要知道组织策略；
- PUT / 审计读取：仅组织管理员（``users.role == "admin"``）或超管；
- 非超管一律要求目标组织 = 当前组织或 ``user_organization`` 成员关系，
  跨组织 403（不泄露存在性之外的信息）；
- 所有写入都通过 ``audit_service`` 落 ``user_activity_logs``（action =
  update_organization_settings，target_type = organization_settings）。
"""
import json

from fastapi import APIRouter, Depends, Request
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user
from app.exceptions import AuthorizationError, NotFoundError
from app.models.organization import Organization, user_organization
from app.models.user import User
from app.models.user_audit import UserActivityLog
from app.schemas.organization_settings import (
    OrganizationSettingsAuditItem,
    OrganizationSettingsAuditResponse,
    OrganizationSettingsResponse,
    OrganizationSettingsUpdate,
)
from app.services.audit_service import audit_service
from app.services.organization_settings_service import (
    organization_settings_service,
)

router = APIRouter()

AUDIT_ACTION = "update_organization_settings"
AUDIT_TARGET_TYPE = "organization_settings"


async def _ensure_membership(
    db: AsyncSession, current_user: User, organization_id: int
) -> None:
    """超管旁路；其余用户必须是该组织成员（当前组织或成员关系）。"""
    if getattr(current_user, "is_superuser", False):
        return
    if current_user.organization_id == organization_id:
        return
    membership = await db.execute(
        select(user_organization.c.user_id).where(
            user_organization.c.user_id == current_user.id,
            user_organization.c.organization_id == organization_id,
        )
    )
    if membership.scalar_one_or_none() is None:
        raise AuthorizationError("无权访问该组织的设置")


def _ensure_org_admin(current_user: User) -> None:
    if getattr(current_user, "is_superuser", False):
        return
    if (getattr(current_user, "role", "") or "").lower() != "admin":
        raise AuthorizationError("需要组织管理员权限")


async def _org_or_404(db: AsyncSession, organization_id: int) -> Organization:
    org = await db.get(Organization, organization_id)
    if org is None:
        raise NotFoundError("组织不存在")
    return org


async def _build_response(
    db: AsyncSession, organization_id: int
) -> OrganizationSettingsResponse:
    settings_map = await organization_settings_service.get_effective_settings(
        db, organization_id
    )
    usage = await organization_settings_service.get_monthly_usage(
        db, organization_id
    )
    quota = settings_map["monthly_token_quota"]["value"]
    return OrganizationSettingsResponse(
        organization_id=organization_id,
        settings=settings_map,
        usage={"month_tokens": usage, "monthly_token_quota": quota},
    )


@router.get(
    "/{organization_id}/settings",
    response_model=OrganizationSettingsResponse,
    summary="获取组织生效设置（成员可读）",
)
async def get_organization_settings(
    organization_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    await _ensure_membership(db, current_user, organization_id)
    await _org_or_404(db, organization_id)
    return await _build_response(db, organization_id)


@router.put(
    "/{organization_id}/settings",
    response_model=OrganizationSettingsResponse,
    summary="更新组织设置（管理员，审计）",
)
async def update_organization_settings(
    organization_id: int,
    payload: OrganizationSettingsUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _ensure_org_admin(current_user)
    await _ensure_membership(db, current_user, organization_id)
    await _org_or_404(db, organization_id)

    updates = payload.model_dump(exclude_unset=True)
    _, changes = await organization_settings_service.update_settings(
        db, organization_id, updates
    )

    if changes:
        await audit_service.log_activity(
            user_id=current_user.id,
            action=AUDIT_ACTION,
            target_type=AUDIT_TARGET_TYPE,
            target_id=str(organization_id),
            detail=json.dumps(changes, ensure_ascii=False, default=str),
            request=request,
            db=db,
        )

    return await _build_response(db, organization_id)


@router.get(
    "/{organization_id}/settings/audit",
    response_model=OrganizationSettingsAuditResponse,
    summary="组织设置变更审计（管理员）",
)
async def get_organization_settings_audit(
    organization_id: int,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _ensure_org_admin(current_user)
    await _ensure_membership(db, current_user, organization_id)
    await _org_or_404(db, organization_id)

    stmt = (
        select(UserActivityLog, User.username)
        .outerjoin(User, User.id == UserActivityLog.user_id)
        .where(
            UserActivityLog.action == AUDIT_ACTION,
            UserActivityLog.target_type == AUDIT_TARGET_TYPE,
            UserActivityLog.target_id == str(organization_id),
        )
        .order_by(desc(UserActivityLog.id))
        .limit(max(1, min(int(limit), 200)))
    )
    rows = (await db.execute(stmt)).all()
    items = [
        OrganizationSettingsAuditItem(
            id=log.id,
            user_id=log.user_id,
            username=username,
            action=log.action,
            target_type=log.target_type,
            target_id=log.target_id,
            detail=log.detail,
            created_at=log.created_at,
        )
        for log, username in rows
    ]
    return OrganizationSettingsAuditResponse(items=items)
