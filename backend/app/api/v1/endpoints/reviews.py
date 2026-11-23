# backend/app/api/v1/endpoints/reviews.py
"""合同核验 API（Batch 2–4）：建案/列表/详情/证据选择/分析/决策/核验单导出。

证据只来自 parser 原文快照；legacy 分块选择时与 Batch 1 同口径返回 409
``SOURCE_ORIGINAL_UNAVAILABLE``，绝不回退检索增强文本。

批次五追加：组织级模板/playbook 库（``/reviews/templates``）——模板头 CRUD、
不可变版本（新增/列表/查看）、只读 diff 与归档。模板只提供规则内容，建案
仍固化为不可变 ``ReviewRuleSet`` 快照；模板编辑不改变任何历史案件。
"""

import json
import logging
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.exc import IntegrityError
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
from app.schemas.review import (
    ReviewCaseCreate,
    ReviewDecisionUpsert,
    ReviewEvidenceSelect,
    ReviewTemplateBootstrap,
    ReviewTemplateCreate,
    ReviewTemplateUpdate,
    ReviewTemplateVersionCreate,
)
from app.services import review_analysis_service as analysis_service
from app.services import review_case_service as case_service
from app.services import review_document_service as document_service
from app.services import review_export_service as export_service
from app.services import review_template_service as template_service
from app.services.audit_service import audit_service
from app.services.review_rule_snapshot import case_rule_metadata

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
    """建案：三份文档必须同组织/角色正确；不满足时案件以 blocked 落库带原因码。

    可选 ``template_id``/``template_version_no``：规则项从该模板版本读取并继续
    固化为不可变规则快照；模板/文档任一跨组织都按 404/403 处理。
    """
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
            template_id=body.template_id,
            template_version_no=body.template_version_no,
        )
        return {"success": True, "data": await case_service.case_detail(db, case)}
    except (NotFoundError, AuthorizationError):
        raise
    except AppError:
        raise
    except Exception as e:
        logger.error(f"Create review case failed: {type(e).__name__}: {e}")
        raise AppError("创建核验案件失败") from e


def _template_audit_detail(organization_id: int, user_id: int, **extra) -> str:
    """模板审计 detail：只记 org/user/id/版本号/项数/哈希前缀与字段名。

    规则项内容（关键词/label）属于用户业务配置，不进审计明细；排障时按
    模板 id + 版本号回查版本行即可（版本行本身就带 payload_sha256）。
    """
    return json.dumps(
        {"organization_id": organization_id, "user_id": user_id, **extra},
        ensure_ascii=False,
        sort_keys=True,
    )


# ── 模板/playbook 库（批次五）──────────────────────────────────────────────
# 路由必须声明在 ``/{review_id}`` 之前：``templates`` 是固定段，晚声明会被
# 当成案件 id（与 ``/candidates`` 同一原因）。版本行不可变，读取/对比全只读。


@router.get("/templates", response_model=dict, dependencies=_READ_DEPS)
async def list_review_templates(
    status: Literal["active", "archived", "all"] = Query(
        "active", description="模板状态过滤：默认只看活跃模板"
    ),
    q: str | None = Query(None, max_length=100, description="按模板名称模糊过滤"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出本组织的模板（默认活跃；归档模板须显式筛选）。"""
    try:
        org_id = await require_organization(current_user, db)
        data = await template_service.list_templates(
            db,
            organization_id=org_id,
            status=status,
            q=q,
            page=page,
            page_size=page_size,
        )
        return {"success": True, "data": data}
    except (NotFoundError, AuthorizationError):
        raise
    except AppError:
        raise
    except Exception as e:
        logger.error(f"List review templates failed: {type(e).__name__}")
        raise AppError("获取核验模板列表失败") from e


@router.post("/templates", response_model=dict, dependencies=_WRITE_DEPS)
async def create_review_template(
    body: ReviewTemplateCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """创建模板（可选同时写入首版 v1）；同名模板 409。

    模板头与首版在同一事务内提交：任一失败整体回滚，避免留下
    ``latest_version=0`` 的孤立模板（本批不提供物理删除入口）。
    """
    try:
        org_id = await require_organization(current_user, db)
        template = await template_service.create_template(
            db,
            organization_id=org_id,
            created_by=current_user.id,
            name=body.name,
            description=body.description,
            commit=False,
        )
        version = None
        if body.items is not None:
            version = await template_service.create_version(
                db,
                template=template,
                items=[item.model_dump() for item in body.items],
                created_by=current_user.id,
                change_note=body.change_note,
                commit=False,
            )
        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise ConflictError(
                "同名模板已存在", error_code="REVIEW_TEMPLATE_NAME_CONFLICT"
            ) from exc
        except Exception:
            await db.rollback()
            raise
        if version is not None:
            await db.refresh(version)
        await db.refresh(template)
        await audit_service.log_activity(
            user_id=current_user.id,
            action="create_review_template",
            target_type="review_template",
            target_id=template.id,
            detail=_template_audit_detail(
                org_id,
                current_user.id,
                version_no=version.version_no if version else None,
                item_count=version.item_count if version else 0,
            ),
            db=db,
        )
        data = template_service.template_to_dict(template)
        if version is not None:
            data["version"] = template_service.version_to_dict(version)
        return {"success": True, "data": data}
    except (NotFoundError, AuthorizationError):
        await db.rollback()
        raise
    except AppError:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Create review template failed: {type(e).__name__}")
        raise AppError("创建核验模板失败") from e


@router.post(
    "/templates/bootstrap",
    response_model=dict,
    dependencies=_WRITE_DEPS,
)
async def bootstrap_review_template(
    body: ReviewTemplateBootstrap,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """从进程级 checklist（默认 4 项 + REVIEW_CHECKLIST_JSON）初始化模板 v1。

    只读环境配置、不改环境；同名模板 409。供存量部署把现行清单固化成可
    版本化的起点，之后的新版本均在库内编辑。
    """
    try:
        org_id = await require_organization(current_user, db)
        template, version = await template_service.bootstrap_from_checklist(
            db,
            organization_id=org_id,
            created_by=current_user.id,
            name=body.name,
        )
        await audit_service.log_activity(
            user_id=current_user.id,
            action="bootstrap_review_template",
            target_type="review_template",
            target_id=template.id,
            detail=_template_audit_detail(
                org_id,
                current_user.id,
                version_no=version.version_no,
                version_id=version.id,
                item_count=version.item_count,
                source="bootstrap",
            ),
            db=db,
        )
        data = template_service.template_to_dict(template)
        data["version"] = template_service.version_to_dict(version, include_items=False)
        return {"success": True, "data": data}
    except (NotFoundError, AuthorizationError):
        raise
    except AppError:
        raise
    except Exception as e:
        logger.error(f"Bootstrap review template failed: {type(e).__name__}")
        raise AppError("初始化核验模板失败") from e


@router.get("/templates/{template_id}", response_model=dict, dependencies=_READ_DEPS)
async def get_review_template(
    template_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """模板头 + 最新版本内容（无版本时 version=null）。"""
    try:
        org_id = await require_organization(current_user, db)
        template = await template_service.get_template(
            db, organization_id=org_id, template_id=template_id
        )
        data = template_service.template_to_dict(template)
        data["version"] = None
        latest_no = int(template.latest_version or 0)
        if latest_no > 0:
            version = await template_service.get_template_version(
                db, organization_id=org_id, template=template, version_no=latest_no
            )
            data["version"] = template_service.version_to_dict(version)
        return {"success": True, "data": data}
    except (NotFoundError, AuthorizationError):
        raise
    except AppError:
        raise
    except Exception as e:
        logger.error(f"Get review template failed: {type(e).__name__}")
        raise AppError("获取核验模板失败") from e


@router.put("/templates/{template_id}", response_model=dict, dependencies=_WRITE_DEPS)
async def update_review_template(
    template_id: str,
    body: ReviewTemplateUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """更新模板头（名称/说明）；归档模板 409。规则内容走版本接口，不在此处。"""
    try:
        org_id = await require_organization(current_user, db)
        template = await template_service.get_template(
            db, organization_id=org_id, template_id=template_id
        )
        updated = await template_service.update_template(
            db,
            template=template,
            name=body.name,
            description=body.description,
            clear_description=body.clear_description,
            updated_by=current_user.id,
        )
        await audit_service.log_activity(
            user_id=current_user.id,
            action="update_review_template",
            target_type="review_template",
            target_id=updated.id,
            detail=_template_audit_detail(
                org_id,
                current_user.id,
                fields=sorted(
                    key
                    for key, present in (
                        ("name", body.name is not None),
                        ("description", body.description is not None or body.clear_description),
                    )
                    if present
                ),
            ),
            db=db,
        )
        return {"success": True, "data": template_service.template_to_dict(updated)}
    except (NotFoundError, AuthorizationError):
        raise
    except AppError:
        raise
    except Exception as e:
        logger.error(f"Update review template failed: {type(e).__name__}")
        raise AppError("更新核验模板失败") from e


@router.post("/templates/{template_id}/archive", response_model=dict, dependencies=_WRITE_DEPS)
async def archive_review_template(
    template_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """归档模板（只读终态）：不可再新增版本、不可再被新案件选择。

    **不删除任何行**：模板版本是审计资产，且历史案件已固化到规则快照；
    归档不触碰任何案件或快照。
    """
    try:
        org_id = await require_organization(current_user, db)
        template = await template_service.get_template(
            db, organization_id=org_id, template_id=template_id
        )
        archived = await template_service.archive_template(
            db, template=template, user_id=current_user.id
        )
        await audit_service.log_activity(
            user_id=current_user.id,
            action="archive_review_template",
            target_type="review_template",
            target_id=archived.id,
            detail=_template_audit_detail(org_id, current_user.id),
            db=db,
        )
        return {"success": True, "data": template_service.template_to_dict(archived)}
    except (NotFoundError, AuthorizationError):
        raise
    except AppError:
        raise
    except Exception as e:
        logger.error(f"Archive review template failed: {type(e).__name__}")
        raise AppError("归档核验模板失败") from e


@router.get("/templates/{template_id}/versions", response_model=dict, dependencies=_READ_DEPS)
async def list_review_template_versions(
    template_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """版本历史（倒序；列表不带规则正文，查看单版走详情端点）。"""
    try:
        org_id = await require_organization(current_user, db)
        template = await template_service.get_template(
            db, organization_id=org_id, template_id=template_id
        )
        data = await template_service.list_versions(
            db,
            organization_id=org_id,
            template=template,
            page=page,
            page_size=page_size,
        )
        return {"success": True, "data": data}
    except (NotFoundError, AuthorizationError):
        raise
    except AppError:
        raise
    except Exception as e:
        logger.error(f"List review template versions failed: {type(e).__name__}")
        raise AppError("获取模板版本历史失败") from e


@router.post(
    "/templates/{template_id}/versions",
    response_model=dict,
    dependencies=_WRITE_DEPS,
)
async def create_review_template_version(
    template_id: str,
    body: ReviewTemplateVersionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """新增不可变版本（编辑=新增，旧版本永不改写）；归档模板 409。"""
    try:
        org_id = await require_organization(current_user, db)
        template = await template_service.get_template(
            db, organization_id=org_id, template_id=template_id
        )
        version = await template_service.create_version(
            db,
            template=template,
            items=[item.model_dump() for item in body.items],
            created_by=current_user.id,
            change_note=body.change_note,
        )
        await audit_service.log_activity(
            user_id=current_user.id,
            action="create_review_template_version",
            target_type="review_template_version",
            target_id=version.id,
            detail=_template_audit_detail(
                org_id,
                current_user.id,
                template_id=template.id,
                version_no=version.version_no,
                item_count=version.item_count,
                sha=version.payload_sha256[:12],
            ),
            db=db,
        )
        return {"success": True, "data": template_service.version_to_dict(version)}
    except (NotFoundError, AuthorizationError):
        raise
    except AppError:
        raise
    except Exception as e:
        logger.error(f"Create review template version failed: {type(e).__name__}")
        raise AppError("创建模板版本失败") from e


@router.get(
    "/templates/{template_id}/versions/{version_no}",
    response_model=dict,
    dependencies=_READ_DEPS,
)
async def get_review_template_version(
    template_id: str,
    version_no: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """查看单个版本（含规则项；intact=false 表示内容哈希校验失败）。"""
    try:
        org_id = await require_organization(current_user, db)
        template = await template_service.get_template(
            db, organization_id=org_id, template_id=template_id
        )
        version = await template_service.get_template_version(
            db, organization_id=org_id, template=template, version_no=version_no
        )
        return {"success": True, "data": template_service.version_to_dict(version)}
    except (NotFoundError, AuthorizationError):
        raise
    except AppError:
        raise
    except Exception as e:
        logger.error(f"Get review template version failed: {type(e).__name__}")
        raise AppError("获取模板版本失败") from e


@router.get(
    "/templates/{template_id}/diff",
    response_model=dict,
    dependencies=_READ_DEPS,
)
async def diff_review_template_versions(
    template_id: str,
    base_version: int = Query(..., ge=1, description="基准版本号"),
    target_version: int = Query(..., ge=1, description="对比版本号"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """只读版本 diff（按 item key 对齐、关键词按集合语义）；不改写任何行。"""
    try:
        org_id = await require_organization(current_user, db)
        template = await template_service.get_template(
            db, organization_id=org_id, template_id=template_id
        )
        base_row = await template_service.get_template_version(
            db, organization_id=org_id, template=template, version_no=base_version
        )
        target_row = await template_service.get_template_version(
            db, organization_id=org_id, template=template, version_no=target_version
        )
        data = template_service.diff_versions(base_row, target_row)
        data["template_id"] = template.id
        return {"success": True, "data": data}
    except (NotFoundError, AuthorizationError):
        raise
    except AppError:
        raise
    except Exception as e:
        logger.error(f"Diff review template versions failed: {type(e).__name__}")
        raise AppError("对比模板版本失败") from e


@router.get("", response_model=dict, dependencies=_READ_DEPS)
async def list_review_cases(
    status: str | None = Query(None, max_length=16, description="按状态过滤"),
    archived: Literal["active", "archived", "all"] = Query(
        "active", description="归档过滤：默认只看活跃案件"
    ),
    q: str | None = Query(None, max_length=200, description="标题包含匹配或案件 ID 前缀搜索"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出本组织的核验案件（按创建时间倒序；默认不含已归档）。

    返回当前页的核验进度/差异计数，以及不含 status 自身筛选的状态计数，
    供列表页做服务端搜索与筛选器计数。
    """
    try:
        org_id = await require_organization(current_user, db)
        data = await case_service.list_cases(
            db,
            organization_id=org_id,
            status=status,
            archived=archived,
            q=q,
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
            db,
            organization_id=org_id,
            viewer_user_id=None if current_user.is_superuser else current_user.id,
            q=q,
            page=page,
            page_size=page_size,
        )
        return {"success": True, "data": data}
    except (NotFoundError, AuthorizationError):
        raise
    except AppError:
        raise
    except Exception as e:
        logger.error(f"List review candidates failed: {type(e).__name__}: {e}")
        raise AppError("获取候选文档失败") from e


@router.get("/evidence/{evidence_id}", response_model=dict, dependencies=_READ_DEPS)
async def get_review_evidence(
    evidence_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """证据永久链接：按 evidence id 返回快照与当前文档/chunk 漂移状态。

    快照以案件权限为准（案件详情本就返回全部 quote）；当前正文只作为对照。
    """
    try:
        evidence, case, item = await case_service.get_evidence_for_user(
            db, current_user, evidence_id
        )
        return {
            "success": True,
            "data": await case_service.evidence_detail(db, evidence, case, item),
        }
    except (NotFoundError, AuthorizationError):
        raise
    except AppError:
        raise
    except Exception as e:
        logger.error(f"Get review evidence failed: {type(e).__name__}: {e}")
        raise AppError("获取证据快照失败") from e


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
        # 规则元数据放在导出响应的元数据里，不进入已固化的 payload / payload_sha256。
        rule_metadata = await case_rule_metadata(db, case, export.payload["items"])
        return {
            "success": True,
            "data": export_service.export_summary(export, rule_metadata=rule_metadata),
        }
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
            content = export_service.render_markdown(
                export.payload,
                export_meta={
                    "export_id": export.id,
                    "created_at": export.created_at,
                    "payload_sha256": export.payload_sha256,
                },
            )
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
