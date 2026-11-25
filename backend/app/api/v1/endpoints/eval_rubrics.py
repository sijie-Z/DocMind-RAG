"""Rubric 评测 API：评分表 CRUD / 不可变版本 / 只读 diff / 归档 / 运行。

产品语义（与既有评测面的分工，docstring 与 UI 文案都必须保持一致）：

- 本模块评的是**答案**是否满足用户可编辑的加权 yes/no 评分表；
- ``/knowledge/eval/*`` 的检索评测评的是**检索**是否找对文档（recall@k / MRR），
  两者不互相替代，也不共用运行记录；
- ``judge`` 模式是**模型判定**（LLM 评委），不是确定性真值；``fake`` 模式回放
  调用方给定的判定，仅供测试/CI 校验链路，同样不是人工真值；
- 与人工金标的一致率只在用例携带 ``gold_verdicts`` 时给出，缺失时
  ``aggregate.calibration`` 显式为 ``null``（绝不展示成 100% 准确）。

权限（与既有评测端点同一读写分离口径）：
- 读：API Key scope ``knowledge:read`` + RBAC ``SEARCH_KNOWLEDGE_BASE``；
- 写：API Key scope ``knowledge:write`` + RBAC ``SEARCH_KNOWLEDGE_BASE``。
所有读写以 ``require_organization`` 解析的有效组织为租户边界，跨组织一律 404。

审计只记 org/user/评分表 id/版本号/条数/模式/状态等元数据；问题、答案、
判定与金标正文绝不进审计明细。
"""

import json
import logging
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import (
    get_current_user,
    permission_required,
    require_organization,
    require_scope,
)
from app.exceptions import (
    AppError,
    AuthorizationError,
    ConflictError,
    ExternalServiceError,
    NotFoundError,
    ServiceUnavailableError,
)
from app.models.rbac import PermissionType
from app.models.user import User
from app.schemas.eval_rubric import (
    RubricBootstrap,
    RubricCreate,
    RubricRunRequest,
    RubricUpdate,
    RubricVersionCreate,
)
from app.services import rubric_eval_service as rubric_service
from app.services.audit_service import audit_service

router = APIRouter()
logger = logging.getLogger(__name__)

_READ_DEPS = [
    Depends(require_scope("knowledge:read")),
    Depends(permission_required([PermissionType.SEARCH_KNOWLEDGE_BASE])),
]
_WRITE_DEPS = [
    Depends(require_scope("knowledge:write")),
    Depends(permission_required([PermissionType.SEARCH_KNOWLEDGE_BASE])),
]


def _audit_detail(organization_id: int, user_id: int, **extra: Any) -> str:
    """审计 detail：只记元数据（org/user/id/版本号/条数/模式/状态）。"""
    return json.dumps(
        {"organization_id": organization_id, "user_id": user_id, **extra},
        ensure_ascii=False,
        sort_keys=True,
    )


async def _judge_client_and_model(db: AsyncSession, current_user: User) -> tuple[Any, str | None]:
    """解析本次 judge 运行的 LLM 客户端与模型名（个人配置优先，回退服务端）。

    客户端缺失 → 503（与 RAG 评估端点的「LLM 未配置」同一语义）；评委调用
    期的失败则由 ``execute_run`` 落成失败运行并映射为 502。
    """
    from app.services.llm_settings_service import resolve_for_user

    resolution = await resolve_for_user(db, current_user)
    if resolution.client is None:
        raise ServiceUnavailableError(
            "LLM 服务未配置，rubric 评委无法执行",
            error_code="LLM_NOT_CONFIGURED",
        )
    return resolution.client, resolution.model


# ── 评分表库 ────────────────────────────────────────────────────────────────


@router.get("/rubrics", response_model=dict, dependencies=_READ_DEPS)
async def list_rubrics(
    status: Literal["active", "archived", "all"] = Query(
        "active", description="评分表状态过滤：默认只看活跃"
    ),
    q: str | None = Query(None, max_length=100, description="按名称模糊过滤"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出本组织的评分表（默认活跃；归档须显式筛选）。"""
    try:
        org_id = await require_organization(current_user, db)
        data = await rubric_service.list_rubrics(
            db, organization_id=org_id, status=status, q=q, page=page, page_size=page_size
        )
        return {"success": True, "data": data}
    except (NotFoundError, AuthorizationError):
        raise
    except AppError:
        raise
    except Exception as e:
        logger.error(f"List eval rubrics failed: {type(e).__name__}")
        raise AppError("获取评分表列表失败") from e


@router.post("/rubrics", response_model=dict, dependencies=_WRITE_DEPS)
async def create_rubric(
    body: RubricCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """创建评分表（可选同时写入首版 v1）；同名评分表 409。

    表头与首版在同一事务内提交：任一失败整体回滚，避免留下 latest_version=0
    的孤立评分表（本批不提供物理删除）。
    """
    try:
        try:
            org_id = await require_organization(current_user, db)
            rubric = await rubric_service.create_rubric(
                db,
                organization_id=org_id,
                created_by=current_user.id,
                name=body.name,
                description=body.description,
                commit=False,
            )
            version = None
            if body.items is not None:
                version = await rubric_service.create_version(
                    db,
                    rubric=rubric,
                    items=[item.model_dump() for item in body.items],
                    created_by=current_user.id,
                    change_note=body.change_note,
                    commit=False,
                )
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise ConflictError("同名评分表已存在", error_code="EVAL_RUBRIC_NAME_CONFLICT") from exc
        except AppError:
            await db.rollback()
            raise
        except Exception:
            await db.rollback()
            raise
        if version is not None:
            await db.refresh(version)
        await db.refresh(rubric)
        await audit_service.log_activity(
            user_id=current_user.id,
            action="create_eval_rubric",
            target_type="eval_rubric",
            target_id=rubric.id,
            detail=_audit_detail(
                org_id,
                current_user.id,
                version_no=version.version_no if version else None,
                item_count=version.item_count if version else 0,
            ),
            db=db,
        )
        data = rubric_service.rubric_to_dict(rubric)
        if version is not None:
            data["version"] = rubric_service.version_to_dict(version)
        return {"success": True, "data": data}
    except AppError:
        # 放行 try 体内直接抛出的 4xx（issue #98 缺陷模式，CI 静态门禁）。
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Create eval rubric failed: {type(e).__name__}")
        raise AppError("创建评分表失败") from e


@router.post("/rubrics/bootstrap", response_model=dict, dependencies=_WRITE_DEPS)
async def bootstrap_rubric(
    body: RubricBootstrap,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """用服务端默认评分项初始化评分表 v1；同名评分表 409。"""
    try:
        org_id = await require_organization(current_user, db)
        rubric, version = await rubric_service.bootstrap_rubric(
            db,
            organization_id=org_id,
            created_by=current_user.id,
            name=body.name,
        )
        await audit_service.log_activity(
            user_id=current_user.id,
            action="bootstrap_eval_rubric",
            target_type="eval_rubric",
            target_id=rubric.id,
            detail=_audit_detail(
                org_id,
                current_user.id,
                version_no=version.version_no,
                item_count=version.item_count,
                source="bootstrap",
            ),
            db=db,
        )
        data = rubric_service.rubric_to_dict(rubric)
        data["version"] = rubric_service.version_to_dict(version, include_items=False)
        return {"success": True, "data": data}
    except (NotFoundError, AuthorizationError):
        raise
    except AppError:
        raise
    except Exception as e:
        logger.error(f"Bootstrap eval rubric failed: {type(e).__name__}")
        raise AppError("初始化评分表失败") from e


@router.get("/rubrics/{rubric_id}", response_model=dict, dependencies=_READ_DEPS)
async def get_rubric(
    rubric_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """评分表头 + 最新版本内容（无版本时 version=null）。"""
    try:
        org_id = await require_organization(current_user, db)
        rubric = await rubric_service.get_rubric(db, organization_id=org_id, rubric_id=rubric_id)
        data = rubric_service.rubric_to_dict(rubric)
        data["version"] = None
        latest_no = int(rubric.latest_version or 0)
        if latest_no > 0:
            version = await rubric_service.get_version(
                db, organization_id=org_id, rubric=rubric, version_no=latest_no
            )
            data["version"] = rubric_service.version_to_dict(version)
        return {"success": True, "data": data}
    except (NotFoundError, AuthorizationError):
        raise
    except AppError:
        raise
    except Exception as e:
        logger.error(f"Get eval rubric failed: {type(e).__name__}")
        raise AppError("获取评分表失败") from e


@router.put("/rubrics/{rubric_id}", response_model=dict, dependencies=_WRITE_DEPS)
async def update_rubric(
    rubric_id: str,
    body: RubricUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """更新评分表头（名称/说明）；归档评分表 409。评分项走版本接口。"""
    try:
        org_id = await require_organization(current_user, db)
        rubric = await rubric_service.get_rubric(db, organization_id=org_id, rubric_id=rubric_id)
        updated = await rubric_service.update_rubric(
            db,
            rubric=rubric,
            name=body.name,
            description=body.description,
            clear_description=body.clear_description,
            updated_by=current_user.id,
        )
        await audit_service.log_activity(
            user_id=current_user.id,
            action="update_eval_rubric",
            target_type="eval_rubric",
            target_id=updated.id,
            detail=_audit_detail(org_id, current_user.id),
            db=db,
        )
        return {"success": True, "data": rubric_service.rubric_to_dict(updated)}
    except (NotFoundError, AuthorizationError):
        raise
    except AppError:
        raise
    except Exception as e:
        logger.error(f"Update eval rubric failed: {type(e).__name__}")
        raise AppError("更新评分表失败") from e


@router.post("/rubrics/{rubric_id}/archive", response_model=dict, dependencies=_WRITE_DEPS)
async def archive_rubric(
    rubric_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """归档评分表（只读终态）：不可再新增版本、不可再发起运行。不删除任何行。"""
    try:
        org_id = await require_organization(current_user, db)
        rubric = await rubric_service.get_rubric(db, organization_id=org_id, rubric_id=rubric_id)
        archived = await rubric_service.archive_rubric(db, rubric=rubric, user_id=current_user.id)
        await audit_service.log_activity(
            user_id=current_user.id,
            action="archive_eval_rubric",
            target_type="eval_rubric",
            target_id=archived.id,
            detail=_audit_detail(org_id, current_user.id),
            db=db,
        )
        return {"success": True, "data": rubric_service.rubric_to_dict(archived)}
    except (NotFoundError, AuthorizationError):
        raise
    except AppError:
        raise
    except Exception as e:
        logger.error(f"Archive eval rubric failed: {type(e).__name__}")
        raise AppError("归档评分表失败") from e


@router.get("/rubrics/{rubric_id}/versions", response_model=dict, dependencies=_READ_DEPS)
async def list_rubric_versions(
    rubric_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """版本历史（倒序；列表不带评分项正文，查看单版走详情端点）。"""
    try:
        org_id = await require_organization(current_user, db)
        rubric = await rubric_service.get_rubric(db, organization_id=org_id, rubric_id=rubric_id)
        data = await rubric_service.list_versions(
            db, organization_id=org_id, rubric=rubric, page=page, page_size=page_size
        )
        return {"success": True, "data": data}
    except (NotFoundError, AuthorizationError):
        raise
    except AppError:
        raise
    except Exception as e:
        logger.error(f"List eval rubric versions failed: {type(e).__name__}")
        raise AppError("获取评分表版本历史失败") from e


@router.post("/rubrics/{rubric_id}/versions", response_model=dict, dependencies=_WRITE_DEPS)
async def create_rubric_version(
    rubric_id: str,
    body: RubricVersionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """新增不可变版本（编辑=新增，旧版本永不改写）；归档评分表 409。"""
    try:
        org_id = await require_organization(current_user, db)
        rubric = await rubric_service.get_rubric(db, organization_id=org_id, rubric_id=rubric_id)
        version = await rubric_service.create_version(
            db,
            rubric=rubric,
            items=[item.model_dump() for item in body.items],
            created_by=current_user.id,
            change_note=body.change_note,
        )
        await audit_service.log_activity(
            user_id=current_user.id,
            action="create_eval_rubric_version",
            target_type="eval_rubric_version",
            target_id=version.id,
            detail=_audit_detail(
                org_id,
                current_user.id,
                rubric_id=rubric.id,
                version_no=version.version_no,
                item_count=version.item_count,
                sha=version.payload_sha256[:12],
            ),
            db=db,
        )
        return {"success": True, "data": rubric_service.version_to_dict(version)}
    except (NotFoundError, AuthorizationError):
        raise
    except AppError:
        raise
    except Exception as e:
        logger.error(f"Create eval rubric version failed: {type(e).__name__}")
        raise AppError("创建评分表版本失败") from e


@router.get(
    "/rubrics/{rubric_id}/versions/{version_no}", response_model=dict, dependencies=_READ_DEPS
)
async def get_rubric_version(
    rubric_id: str,
    version_no: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """查看单个版本（含评分项；intact=false 表示内容哈希校验失败）。"""
    try:
        org_id = await require_organization(current_user, db)
        rubric = await rubric_service.get_rubric(db, organization_id=org_id, rubric_id=rubric_id)
        version = await rubric_service.get_version(
            db, organization_id=org_id, rubric=rubric, version_no=version_no
        )
        return {"success": True, "data": rubric_service.version_to_dict(version)}
    except (NotFoundError, AuthorizationError):
        raise
    except AppError:
        raise
    except Exception as e:
        logger.error(f"Get eval rubric version failed: {type(e).__name__}")
        raise AppError("获取评分表版本失败") from e


@router.get("/rubrics/{rubric_id}/diff", response_model=dict, dependencies=_READ_DEPS)
async def diff_rubric_versions(
    rubric_id: str,
    base_version: int = Query(..., ge=1, description="基准版本号"),
    target_version: int = Query(..., ge=1, description="对比版本号"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """只读版本 diff（按评分项 key 对齐、逐字段给出两侧值）；不改写任何行。"""
    try:
        org_id = await require_organization(current_user, db)
        rubric = await rubric_service.get_rubric(db, organization_id=org_id, rubric_id=rubric_id)
        base_row = await rubric_service.get_version(
            db, organization_id=org_id, rubric=rubric, version_no=base_version
        )
        target_row = await rubric_service.get_version(
            db, organization_id=org_id, rubric=rubric, version_no=target_version
        )
        data = rubric_service.diff_versions(base_row, target_row)
        data["rubric_id"] = rubric.id
        return {"success": True, "data": data}
    except (NotFoundError, AuthorizationError):
        raise
    except AppError:
        raise
    except Exception as e:
        logger.error(f"Diff eval rubric versions failed: {type(e).__name__}")
        raise AppError("对比评分表版本失败") from e


# ── 运行 ────────────────────────────────────────────────────────────────────


@router.post("/rubrics/{rubric_id}/runs", response_model=dict, dependencies=_WRITE_DEPS)
async def create_rubric_run(
    rubric_id: str,
    body: RubricRunRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """运行评分表：成功返回逐条判定 + 聚合 + 校准；评委失败 → 502（失败运行已落库）。

    写入端点：一条运行会发起最多 50 次 LLM 调用，必须与「创建评分表」同级
    的写权限（knowledge:write），不能被只读 key 放大成成本入口。
    """
    try:
        org_id = await require_organization(current_user, db)
        rubric = await rubric_service.get_rubric(db, organization_id=org_id, rubric_id=rubric_id)
        verdict_fn = None
        judge_model = None
        if body.mode == rubric_service.RUN_MODE_JUDGE:
            client, model = await _judge_client_and_model(db, current_user)
            judge_model = model
            verdict_fn = rubric_service.make_llm_verdict_fn(client, model or "")
        run = await rubric_service.execute_run(
            db,
            organization_id=org_id,
            rubric=rubric,
            version_no=body.rubric_version_no,
            created_by=current_user.id,
            mode=body.mode,
            raw_cases=[case.model_dump() for case in body.cases],
            verdict_fn=verdict_fn,
            judge_model=judge_model,
        )
        await audit_service.log_activity(
            user_id=current_user.id,
            action="run_eval_rubric",
            target_type="eval_rubric_run",
            target_id=run.id,
            detail=_audit_detail(
                org_id,
                current_user.id,
                rubric_id=rubric.id,
                version_no=run.rubric_version_no,
                mode=run.mode,
                status=run.status,
                cases=run.case_count,
                error_code=run.error_code,
            ),
            request=request,
            db=db,
        )
        if run.status == rubric_service.RUN_STATUS_FAILED:
            # 失败运行已持久化（可回查机器码），但本次请求没有分数可交付：
            # 评委是外部 LLM 服务 → 502 语义（对齐 RAG 评估端点的先例），
            # 绝不返回 200 + 空分数让客户端把「测量失败」当成「0 分」。
            raise ExternalServiceError(
                "rubric 评委不可用或输出不符合契约，本次运行未产生分数",
                error_code=run.error_code or rubric_service.ERROR_CODE_JUDGE_UNAVAILABLE,
                detail={"run_id": run.id},
            )
        return {"success": True, "data": rubric_service.run_detail_dict(run)}
    except (NotFoundError, AuthorizationError):
        raise
    except AppError:
        raise
    except Exception as e:
        logger.error(f"Run eval rubric failed: {type(e).__name__}")
        raise AppError("运行评分表评测失败") from e


@router.get("/runs", response_model=dict, dependencies=_READ_DEPS)
async def list_rubric_runs(
    rubric_id: str | None = Query(None, max_length=36, description="按评分表过滤"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出本组织的评分表运行（可按评分表过滤）；跨组织记录绝不返回。"""
    try:
        org_id = await require_organization(current_user, db)
        rubric = None
        if rubric_id is not None:
            rubric = await rubric_service.get_rubric(
                db, organization_id=org_id, rubric_id=rubric_id
            )
        data = await rubric_service.list_runs(
            db, organization_id=org_id, rubric=rubric, page=page, page_size=page_size
        )
        return {"success": True, "data": data}
    except (NotFoundError, AuthorizationError):
        raise
    except AppError:
        raise
    except Exception as e:
        logger.error(f"List eval rubric runs failed: {type(e).__name__}")
        raise AppError("获取评分表运行历史失败") from e


@router.get("/runs/{run_id}", response_model=dict, dependencies=_READ_DEPS)
async def get_rubric_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """读取本组织单次运行（含逐条结果与校准）；跨组织一律 404。"""
    try:
        org_id = await require_organization(current_user, db)
        run = await rubric_service.get_run(db, organization_id=org_id, run_id=run_id)
        return {"success": True, "data": rubric_service.run_detail_dict(run)}
    except (NotFoundError, AuthorizationError):
        raise
    except AppError:
        raise
    except Exception as e:
        logger.error(f"Get eval rubric run failed: {type(e).__name__}")
        raise AppError("获取评分表运行详情失败") from e
