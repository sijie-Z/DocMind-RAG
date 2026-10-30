# backend/app/api/v1/endpoints/reviews.py
"""合同核验 API（Batch 2–4）：建案/列表/详情/证据选择/分析/决策/核验单导出。

证据只来自 parser 原文快照；legacy 分块选择时与 Batch 1 同口径返回 409
``SOURCE_ORIGINAL_UNAVAILABLE``，绝不回退检索增强文本。
"""

import json
import logging
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
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
from app.schemas.review import ReviewCaseCreate, ReviewDecisionUpsert, ReviewEvidenceSelect
from app.services import review_analysis_service as analysis_service
from app.services import review_case_service as case_service
from app.services import review_document_service as document_service
from app.services import review_export_service as export_service
from app.services.audit_service import audit_service

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
    archived: Literal["active", "archived", "all"] = Query(
        "active", description="归档过滤：默认只看活跃案件"
    ),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出本组织的核验案件（按创建时间倒序；默认不含已归档）。"""
    try:
        org_id = await require_organization(current_user, db)
        data = await case_service.list_cases(
            db,
            organization_id=org_id,
            status=status,
            archived=archived,
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


@router.get("/candidates", response_model=dict, dependencies=_READ_DEPS)
async def list_review_candidates(
    q: str | None = Query(None, max_length=200, description="按文件名/标题模糊过滤"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """建案候选文档：本组织文档 + 核验身份；未登记身份的也返回（前端提示登记）。

    注意：本路由必须声明在 ``/{review_id}`` 之前，否则 "candidates" 会被当成案件 id。
    """
    try:
        org_id = await require_organization(current_user, db)
        data = await document_service.list_candidates(
            db, organization_id=org_id, q=q, page=page, page_size=page_size
        )
        return {"success": True, "data": data}
    except (NotFoundError, AuthorizationError):
        raise
    except AppError:
        raise
    except Exception as e:
        logger.error(f"List review candidates failed: {type(e).__name__}: {e}")
        raise AppError("获取候选文档失败") from e


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


@router.put("/{review_id}/items/{item_id}/decision", response_model=dict, dependencies=_WRITE_DEPS)
async def decide_review_item(
    review_id: str,
    item_id: str,
    body: ReviewDecisionUpsert,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """逐项人工决策（verified/needs_human/not_applicable）；completed 后改决策回 in_review。"""
    try:
        case = await case_service.get_case_for_user(db, current_user, review_id)
        item = await case_service.get_item_for_case(db, case, item_id)
        row = await export_service.decide_item(
            db,
            case,
            item,
            status=body.status,
            note=body.note,
            user_id=current_user.id,
        )
        return {"success": True, "data": export_service.decision_to_dict(row)}
    except (NotFoundError, AuthorizationError):
        raise
    except AppError:
        raise
    except Exception as e:
        logger.error(f"Decide review item failed: {type(e).__name__}: {e}")
        raise AppError("提交核验决策失败") from e


@router.post("/{review_id}/export", response_model=dict, dependencies=_WRITE_DEPS)
async def export_review_case(
    review_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """生成不可变核验单快照；未决项 409 REVIEW_EXPORT_BLOCKED。"""
    try:
        case = await case_service.get_case_for_user(db, current_user, review_id)
        export = await export_service.export_case(db, case, user_id=current_user.id)
        return {"success": True, "data": export_service.export_summary(export)}
    except (NotFoundError, AuthorizationError):
        raise
    except AppError:
        raise
    except Exception as e:
        logger.error(f"Export review case failed: {type(e).__name__}: {e}")
        raise AppError("导出核验单失败") from e


@router.post("/{review_id}/archive", response_model=dict, dependencies=_WRITE_DEPS)
async def archive_review_case(
    review_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """归档案件（只读终态）：仅 blocked/completed；归档后不再阻止引用文档删除。"""
    try:
        case = await case_service.get_case_for_user(db, current_user, review_id)
        archived = await case_service.archive_case(db, case, user_id=current_user.id)
        await audit_service.log_activity(
            user_id=current_user.id,
            action="archive_review_case",
            target_type="review_case",
            target_id=archived.id,
            detail=f"status={archived.status}; title={archived.title}",
            db=db,
        )
        return {"success": True, "data": await case_service.case_detail(db, archived)}
    except (NotFoundError, AuthorizationError):
        raise
    except AppError:
        raise
    except Exception as e:
        logger.error(f"Archive review case failed: {type(e).__name__}: {e}")
        raise AppError("归档核验案件失败") from e


@router.get("/{review_id}/exports", response_model=dict, dependencies=_READ_DEPS)
async def list_review_exports(
    review_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出核验单导出历史（不可变快照）。"""
    try:
        case = await case_service.get_case_for_user(db, current_user, review_id)
        exports = await export_service.list_exports(db, case)
        return {
            "success": True,
            "data": {"items": [export_service.export_summary(row) for row in exports]},
        }
    except (NotFoundError, AuthorizationError):
        raise
    except AppError:
        raise
    except Exception as e:
        logger.error(f"List review exports failed: {type(e).__name__}: {e}")
        raise AppError("获取核验单导出历史失败") from e


@router.get("/{review_id}/exports/{export_id}/download", dependencies=_READ_DEPS)
async def download_review_export(
    review_id: str,
    export_id: str,
    format: Literal["json", "markdown"] = Query("json"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """下载核验单快照（JSON/Markdown）；按组织授权并写审计。"""
    try:
        case = await case_service.get_case_for_user(db, current_user, review_id)
        export = await export_service.get_export_for_case(db, case, export_id)
        await export_service.audit_download(db, case, export, user_id=current_user.id)
        stem = f"review-{case.id[:8]}-{export.id[:8]}"
        if format == "markdown":
            content = export_service.render_markdown(export.payload)
            media_type = "text/markdown; charset=utf-8"
            filename = f"{stem}.md"
        else:
            content = json.dumps(export.payload, ensure_ascii=False, indent=2)
            media_type = "application/json"
            filename = f"{stem}.json"
        return Response(
            content=content,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except (NotFoundError, AuthorizationError):
        raise
    except AppError:
        raise
    except Exception as e:
        logger.error(f"Download review export failed: {type(e).__name__}: {e}")
        raise AppError("下载核验单失败") from e
