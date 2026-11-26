"""组织技能库 API（批次十）：提议 / 审核 / 启用 / 禁用 / 回滚 / 归档。

权限口径：

- 读（列表/详情/版本/diff）：API Key scope ``chat:read`` + 组织成员边界
  （``require_organization``，跨组织一律 404）。
- 提议：API Key scope ``chat:write`` + 组织成员；提议只写 pending 版本，
  不会立即进入运行时。
- 审核/启用/禁用/回滚/归档：``require_org_admin`` + ``require_jwt_user``
  —— API Key 一律 403（治理动作必须回到人的会话）；普通成员 403。

审计只记 org/user/技能 id/版本号/状态等元数据，**绝不记 payload 或触发短语
文本**。所有状态变更均走 ``org_skill_service``，端点层不复制状态机。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import (
    get_current_user,
    require_jwt_user,
    require_org_admin,
    require_organization,
    require_scope,
)
from app.exceptions import AppError
from app.models.user import User
from app.schemas.org_skill import (
    SkillEnableRequest,
    SkillProposalCreate,
    SkillReviewRequest,
    SkillRollbackRequest,
    SkillStatus,
)
from app.services import org_skill_service as skill_service
from app.services.audit_service import audit_service

router = APIRouter()
logger = logging.getLogger(__name__)

_READ_DEPS = [Depends(require_scope("chat:read"))]
_WRITE_DEPS = [Depends(require_scope("chat:write"))]


def _audit_detail(organization_id: int, user_id: int, **extra: Any) -> str:
    """审计 detail：只记元数据（org/user/技能 id/版本号/状态）。"""
    return json.dumps(
        {"organization_id": organization_id, "user_id": user_id, **extra},
        ensure_ascii=False,
        sort_keys=True,
    )


async def _admin_gate(db: AsyncSession, current_user: User, organization_id: int) -> None:
    await require_org_admin(db, current_user, organization_id)


# ── 列表 / 详情 / 版本 / diff ───────────────────────────────────────────────


@router.get("", response_model=dict, dependencies=_READ_DEPS)
@router.get("/", response_model=dict, dependencies=_READ_DEPS, include_in_schema=False)
async def list_org_skills(
    status: SkillStatus = Query("active", description="active/archived/all"),
    q: str | None = Query(None, max_length=100, description="按名称模糊过滤"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出本组织技能（默认活跃）；带待审数。"""
    try:
        org_id = await require_organization(current_user, db)
        data = await skill_service.list_skills(
            db, organization_id=org_id, status=status, q=q, page=page, page_size=page_size
        )
        return {"success": True, "data": data}
    except AppError:
        raise
    except Exception as e:
        logger.error("List org skills failed: %s", type(e).__name__)
        raise AppError("获取技能列表失败") from e


@router.get("/{skill_id}", response_model=dict, dependencies=_READ_DEPS)
async def get_org_skill(
    skill_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """技能详情（含全部版本；跨组织 404）。"""
    try:
        org_id = await require_organization(current_user, db)
        data = await skill_service.get_skill_detail(db, organization_id=org_id, skill_id=skill_id)
        return {"success": True, "data": data}
    except AppError:
        raise
    except Exception as e:
        logger.error("Get org skill failed: %s", type(e).__name__)
        raise AppError("获取技能详情失败") from e


@router.get(
    "/{skill_id}/versions/{version_no}",
    response_model=dict,
    dependencies=_READ_DEPS,
)
async def get_org_skill_version(
    skill_id: str,
    version_no: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """单个不可变版本（含结构化 payload；哈希完整性在启用/运行时匹配时校验）。"""
    try:
        org_id = await require_organization(current_user, db)
        data = await skill_service.get_version_detail(
            db, organization_id=org_id, skill_id=skill_id, version_no=version_no
        )
        return {"success": True, "data": data}
    except AppError:
        raise
    except Exception as e:
        logger.error("Get org skill version failed: %s", type(e).__name__)
        raise AppError("获取技能版本失败") from e


@router.get("/{skill_id}/diff", response_model=dict, dependencies=_READ_DEPS)
async def diff_org_skill_versions(
    skill_id: str,
    base_version_no: int = Query(..., ge=1),
    target_version_no: int = Query(..., ge=1),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """两个不可变版本的结构化 diff（触发短语/工具序列增删）。"""
    try:
        org_id = await require_organization(current_user, db)
        data = await skill_service.diff_skill_versions(
            db,
            organization_id=org_id,
            skill_id=skill_id,
            base_version_no=base_version_no,
            target_version_no=target_version_no,
        )
        return {"success": True, "data": data}
    except AppError:
        raise
    except Exception as e:
        logger.error("Diff org skill versions failed: %s", type(e).__name__)
        raise AppError("获取技能版本差异失败") from e


# ── 提议 ────────────────────────────────────────────────────────────────────


@router.post("/proposals", response_model=dict, dependencies=_WRITE_DEPS)
async def create_org_skill_proposal(
    body: SkillProposalCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """提议 pending 版本（手工结构化 payload 或从本人完成的任务派生）。"""
    try:
        org_id = await require_organization(current_user, db)
        skill, version = await skill_service.create_proposal(
            db,
            organization_id=org_id,
            user_id=current_user.id,
            payload=body.payload,
            source_task_id=body.source_task_id,
            skill_id=body.skill_id,
            name=body.name,
            description=body.description,
        )
        await audit_service.log_activity(
            user_id=current_user.id,
            action="propose_org_skill_version",
            target_type="org_skill",
            target_id=skill.id,
            detail=_audit_detail(
                org_id,
                current_user.id,
                skill_id=skill.id,
                version_no=version.version_no,
                source=version.source,
                review_status=version.review_status,
            ),
            request=request,
            db=db,
        )
        return {
            "success": True,
            "data": {
                "skill": skill_service.skill_to_dict(skill),
                "version": skill_service.version_to_dict(version),
            },
        }
    except AppError:
        raise
    except Exception as e:
        await db.rollback()
        logger.error("Create org skill proposal failed: %s", type(e).__name__)
        raise AppError("创建技能提议失败") from e


# ── 审核 ────────────────────────────────────────────────────────────────────


async def _review(
    *,
    skill_id: str,
    version_no: int,
    body: SkillReviewRequest,
    decision: str,
    request: Request,
    db: AsyncSession,
    current_user: User,
) -> dict[str, Any]:
    org_id = await require_organization(current_user, db)
    await _admin_gate(db, current_user, org_id)
    version = await skill_service.review_version(
        db,
        organization_id=org_id,
        skill_id=skill_id,
        version_no=version_no,
        reviewer_id=current_user.id,
        decision=decision,
        note=body.note,
    )
    await audit_service.log_activity(
        user_id=current_user.id,
        action=f"{decision}_org_skill_version",
        target_type="org_skill_version",
        target_id=f"{skill_id}:{version_no}",
        detail=_audit_detail(
            org_id,
            current_user.id,
            skill_id=skill_id,
            version_no=version_no,
            review_status=version.review_status,
        ),
        request=request,
        db=db,
    )
    return {"success": True, "data": skill_service.version_to_dict(version)}


@router.post(
    "/{skill_id}/versions/{version_no}/approve",
    response_model=dict,
    dependencies=_WRITE_DEPS,
)
async def approve_org_skill_version(
    skill_id: str,
    version_no: int,
    body: SkillReviewRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_jwt_user),
):
    """批准 pending 版本（仅组织管理员 + JWT；作者不可自审）。"""
    try:
        return await _review(
            skill_id=skill_id,
            version_no=version_no,
            body=body,
            decision="approved",
            request=request,
            db=db,
            current_user=current_user,
        )
    except AppError:
        raise
    except Exception as e:
        await db.rollback()
        logger.error("Approve org skill version failed: %s", type(e).__name__)
        raise AppError("批准技能版本失败") from e


@router.post(
    "/{skill_id}/versions/{version_no}/reject",
    response_model=dict,
    dependencies=_WRITE_DEPS,
)
async def reject_org_skill_version(
    skill_id: str,
    version_no: int,
    body: SkillReviewRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_jwt_user),
):
    """拒绝 pending 版本（仅组织管理员 + JWT；作者不可自审）。"""
    try:
        return await _review(
            skill_id=skill_id,
            version_no=version_no,
            body=body,
            decision="rejected",
            request=request,
            db=db,
            current_user=current_user,
        )
    except AppError:
        raise
    except Exception as e:
        await db.rollback()
        logger.error("Reject org skill version failed: %s", type(e).__name__)
        raise AppError("拒绝技能版本失败") from e


# ── 启用 / 禁用 / 回滚 / 归档 ─────────────────────────────────────────────


@router.post("/{skill_id}/enable", response_model=dict, dependencies=_WRITE_DEPS)
async def enable_org_skill(
    skill_id: str,
    body: SkillEnableRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_jwt_user),
):
    """启用 approved 版本（仅组织管理员 + JWT）。"""
    try:
        org_id = await require_organization(current_user, db)
        await _admin_gate(db, current_user, org_id)
        skill = await skill_service.enable_version(
            db,
            organization_id=org_id,
            skill_id=skill_id,
            version_no=body.version_no,
            user_id=current_user.id,
        )
        await audit_service.log_activity(
            user_id=current_user.id,
            action="enable_org_skill",
            target_type="org_skill",
            target_id=skill.id,
            detail=_audit_detail(
                org_id,
                current_user.id,
                skill_id=skill.id,
                version_no=body.version_no,
                enabled_version_no=skill.enabled_version_no,
            ),
            request=request,
            db=db,
        )
        return {"success": True, "data": skill_service.skill_to_dict(skill)}
    except AppError:
        raise
    except Exception as e:
        await db.rollback()
        logger.error("Enable org skill failed: %s", type(e).__name__)
        raise AppError("启用技能失败") from e


@router.post("/{skill_id}/disable", response_model=dict, dependencies=_WRITE_DEPS)
async def disable_org_skill(
    skill_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_jwt_user),
):
    """关闭运行时注入（仅组织管理员 + JWT；技能与版本行保留）。"""
    try:
        org_id = await require_organization(current_user, db)
        await _admin_gate(db, current_user, org_id)
        skill = await skill_service.disable_skill(
            db, organization_id=org_id, skill_id=skill_id, user_id=current_user.id
        )
        await audit_service.log_activity(
            user_id=current_user.id,
            action="disable_org_skill",
            target_type="org_skill",
            target_id=skill.id,
            detail=_audit_detail(org_id, current_user.id, skill_id=skill.id),
            request=request,
            db=db,
        )
        return {"success": True, "data": skill_service.skill_to_dict(skill)}
    except AppError:
        raise
    except Exception as e:
        await db.rollback()
        logger.error("Disable org skill failed: %s", type(e).__name__)
        raise AppError("禁用技能失败") from e


@router.post("/{skill_id}/rollback", response_model=dict, dependencies=_WRITE_DEPS)
async def rollback_org_skill(
    skill_id: str,
    body: SkillRollbackRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_jwt_user),
):
    """回滚到更早的 approved 版本（仅组织管理员 + JWT）。"""
    try:
        org_id = await require_organization(current_user, db)
        await _admin_gate(db, current_user, org_id)
        skill = await skill_service.rollback_skill(
            db,
            organization_id=org_id,
            skill_id=skill_id,
            user_id=current_user.id,
            target_version_no=body.version_no,
        )
        await audit_service.log_activity(
            user_id=current_user.id,
            action="rollback_org_skill",
            target_type="org_skill",
            target_id=skill.id,
            detail=_audit_detail(
                org_id,
                current_user.id,
                skill_id=skill.id,
                enabled_version_no=skill.enabled_version_no,
            ),
            request=request,
            db=db,
        )
        return {"success": True, "data": skill_service.skill_to_dict(skill)}
    except AppError:
        raise
    except Exception as e:
        await db.rollback()
        logger.error("Rollback org skill failed: %s", type(e).__name__)
        raise AppError("回滚技能失败") from e


@router.post("/{skill_id}/archive", response_model=dict, dependencies=_WRITE_DEPS)
async def archive_org_skill(
    skill_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_jwt_user),
):
    """归档技能（仅组织管理员 + JWT；只读终态，不删除任何行）。"""
    try:
        org_id = await require_organization(current_user, db)
        await _admin_gate(db, current_user, org_id)
        skill = await skill_service.archive_skill(
            db, organization_id=org_id, skill_id=skill_id, user_id=current_user.id
        )
        await audit_service.log_activity(
            user_id=current_user.id,
            action="archive_org_skill",
            target_type="org_skill",
            target_id=skill.id,
            detail=_audit_detail(org_id, current_user.id, skill_id=skill.id, status=skill.status),
            request=request,
            db=db,
        )
        return {"success": True, "data": skill_service.skill_to_dict(skill)}
    except AppError:
        raise
    except Exception as e:
        await db.rollback()
        logger.error("Archive org skill failed: %s", type(e).__name__)
        raise AppError("归档技能失败") from e
