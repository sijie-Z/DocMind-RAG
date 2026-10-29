# backend/app/api/v1/endpoints/reviews.py
"""合同核验案件 API（Batch 2）：建案 / 列表 / 详情 / 人工选择证据。

证据只来自 parser 原文快照；legacy 分块选择时与 Batch 1 同口径返回 409
``SOURCE_ORIGINAL_UNAVAILABLE``，绝不回退检索增强文本。
"""

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import (
    get_current_user,
    get_document_for_user,
    permission_required,
    require_organization,
    require_scope,
)
from app.exceptions import AppError, AuthorizationError, ConflictError, NotFoundError
from app.models.rbac import PermissionType
from app.models.user import User
from app.rag.source_provenance import SourceProvenanceUnavailableError
from app.schemas.review import ReviewCaseCreate, ReviewEvidenceSelect
from app.services import review_analysis_service as analysis_service
from app.services import review_case_service as case_service

router = APIRouter()
logger = logging.getLogger(__name__)

_READ_DEPS = [
    Depends(require_scope("knowledge:read")),
    Depends(permission_required([PermissionType.DOCUMENT_READ])),
]
_WRITE_DEPS = [
    Depends(require_scope("knowledge:write")),
    Depends(permission_required([PermissionType.DOCUMENT_UPDATE])),
]


@router.post("", response_model=dict, dependencies=_WRITE_DEPS)
async def create_review_case(
    body: ReviewCaseCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """建案：三份文档必须同组织/角色正确；不满足时案件以 blocked 落库带原因码。"""
    try:
        docs = {
            "policy": await get_document_for_user(db, current_user, body.policy_document_id),
            "base": await get_document_for_user(db, current_user, body.base_document_id),
            "revised": await get_document_for_user(db, current_user, body.revised_document_id),
        }
        case = await case_service.create_case(
            db,
            docs=docs,
            title=body.title,
            checklist_version=body.checklist_version,
            created_by=current_user.id,
            assignee_id=body.assignee_id,
        )
        return {"success": True, "data": await case_service.case_detail(db, case)}
    except (NotFoundError, AuthorizationError):
        raise
    except AppError:
        raise
    except Exception as e:
        logger.error(f"Create review case failed: {type(e).__name__}: {e}")
        raise AppError("创建核验案件失败") from e


@router.get("", response_model=dict, dependencies=_READ_DEPS)
async def list_review_cases(
    status: str | None = Query(None, max_length=16, description="按状态过滤"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出本组织的核验案件（按创建时间倒序）。"""
    try:
        org_id = await require_organization(current_user, db)
        data = await case_service.list_cases(
            db,
            organization_id=org_id,
            status=status,
            page=page,
            page_size=page_size,
        )
        return {"success": True, "data": data}
    except (NotFoundError, AuthorizationError):
        raise
    except AppError:
        raise
    except Exception as e:
        logger.error(f"List review cases failed: {type(e).__name__}: {e}")
        raise AppError("获取核验案件列表失败") from e


@router.get("/{review_id}", response_model=dict, dependencies=_READ_DEPS)
async def get_review_case(
    review_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """案件详情：文档身份/readiness 快照、4 个核验项、证据快照与实时漂移标记。"""
    try:
        case = await case_service.get_case_for_user(db, current_user, review_id)
        return {"success": True, "data": await case_service.case_detail(db, case)}
    except (NotFoundError, AuthorizationError):
        raise
    except AppError:
        raise
    except Exception as e:
        logger.error(f"Get review case failed: {type(e).__name__}: {e}")
        raise AppError("获取核验案件失败") from e


@router.put("/{review_id}/items/{item_id}/evidence", response_model=dict, dependencies=_WRITE_DEPS)
async def select_review_evidence(
    review_id: str,
    item_id: str,
    body: ReviewEvidenceSelect,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """人工选择/改选证据：只接受本案件文档内的 parser 原文分块。"""
    try:
        case = await case_service.get_case_for_user(db, current_user, review_id)
        item = await case_service.get_item_for_case(db, case, item_id)
        row = await case_service.select_evidence(
            db,
            case,
            item,
            side=body.side,
            chunk_id=body.chunk_id,
            role=body.role,
            selected=body.selected,
        )
        return {"success": True, "data": case_service.evidence_to_dict(row, stale=False)}
    except (NotFoundError, AuthorizationError):
        raise
    except SourceProvenanceUnavailableError as e:
        raise ConflictError(
            "该分块缺少 parser 原文，不能作为核验证据；请先重建文档",
            error_code="SOURCE_ORIGINAL_UNAVAILABLE",
            detail=e.as_dict(),
        ) from e
    except AppError:
        raise
    except Exception as e:
        logger.error(f"Select review evidence failed: {type(e).__name__}: {e}")
        raise AppError("选择核验证据失败") from e


@router.post("/{review_id}/analyze", response_model=dict, dependencies=_WRITE_DEPS)
async def analyze_review_case(
    review_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """确定性抽取 4 类文本事实并做 V1↔V2 对齐（不调用 LLM/网络，幂等）。"""
    try:
        case = await case_service.get_case_for_user(db, current_user, review_id)
        return {"success": True, "data": await analysis_service.analyze_case(db, case)}
    except (NotFoundError, AuthorizationError):
        raise
    except SourceProvenanceUnavailableError as e:
        raise ConflictError(
            "案件文档缺少 parser 原文，无法执行核验分析；请先重建文档",
            error_code="SOURCE_ORIGINAL_UNAVAILABLE",
            detail=e.as_dict(),
        ) from e
    except AppError:
        raise
    except Exception as e:
        logger.error(f"Analyze review case failed: {type(e).__name__}: {e}")
        raise AppError("执行核验分析失败") from e
