"""答案质量评测 API：运行创建 / 历史列表 / 详情 / 趋势。

产品语义（与既有评测面的分工，docstring 与 UI 文案一致）：

- 本模块评测**答案质量**：复用 ``app.rag.evaluator`` 的 faithfulness /
  relevancy / context precision 连续分评委；
- ``/knowledge/eval/*`` 评的是**检索**是否找对文档（recall@k / MRR）；
- ``/eval/rubrics/*`` 评的是答案是否满足用户可编辑的加权 yes/no 评分表。
三者判定对象与指标不同，刻意不共用运行表。

权限（与既有评测端点同一读写分离口径）：
- 读：API Key scope ``knowledge:read`` + RBAC ``SEARCH_KNOWLEDGE_BASE``；
- 写：API Key scope ``knowledge:write`` + RBAC ``SEARCH_KNOWLEDGE_BASE``。
所有读写以 ``require_organization`` 解析的有效组织为租户边界，跨组织一律 404。

隐私与失败：运行行只保存指标、有界评审理由、来源哈希/脱敏预览，绝不保存
原始 query/answer/context；评委不可用时失败运行已落库（含机器码），请求映射
为 502，绝不返回 0 分。
"""

import json
import logging
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Request
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
    ExternalServiceError,
    NotFoundError,
    ServiceUnavailableError,
)
from app.models.rbac import PermissionType
from app.models.user import User
from app.rag import evaluator as rag_evaluator
from app.schemas.answer_quality_eval import AnswerQualityRunRequest
from app.services import answer_quality_eval_service as eval_service
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
    """审计 detail：只记元数据（org/user/run/模式/状态/条数/模型版本）。"""
    return json.dumps(
        {"organization_id": organization_id, "user_id": user_id, **extra},
        ensure_ascii=False,
        sort_keys=True,
    )


async def _judge_evaluator_and_model(
    db: AsyncSession, current_user: User
) -> tuple[Any, str | None]:
    """解析本次 judge 运行的 LLM 客户端/模型并构造评分核心。

    客户端缺失 → 503 ``LLM_NOT_CONFIGURED``（配置错误，未发起评测，不产生
    运行行）；评委调用期的失败由 ``execute_run`` 落成失败运行并映射为 502。
    """
    from app.services.llm_settings_service import resolve_for_user

    resolution = await resolve_for_user(db, current_user)
    if resolution.client is None:
        raise ServiceUnavailableError(
            "LLM 服务未配置，答案质量评委无法执行",
            error_code="LLM_NOT_CONFIGURED",
        )
    evaluator = rag_evaluator.RAGEvaluator(client=resolution.client, model=resolution.model)
    return evaluator, resolution.model


# ── 运行历史 ────────────────────────────────────────────────────────────────


@router.post("/runs", response_model=dict, dependencies=_WRITE_DEPS)
async def create_answer_quality_run(
    body: AnswerQualityRunRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """执行并持久化一次答案质量评测；评委不可用 → 502（失败运行已落库）。

    例外：LLM 未配置属于请求前的配置错误，返回 503 且不产生运行行——这是
    「没有发起评测」，不是「评测失败」。
    """
    try:
        org_id = await require_organization(current_user, db)
        evaluator = None
        judge_model = None
        if body.mode == eval_service.RUN_MODE_JUDGE:
            evaluator, judge_model = await _judge_evaluator_and_model(db, current_user)
        run = await eval_service.execute_run(
            db,
            organization_id=org_id,
            user_id=current_user.id,
            created_by=current_user.id,
            raw_cases=[case.model_dump() for case in body.cases],
            mode=body.mode,
            evaluator=evaluator,
            judge_model=judge_model,
        )
        await audit_service.log_activity(
            user_id=current_user.id,
            action="run_answer_quality_eval",
            target_type="answer_quality_eval_run",
            target_id=run.id,
            detail=_audit_detail(
                org_id,
                current_user.id,
                run_id=run.id,
                mode=run.mode,
                status=run.status,
                cases=run.case_count,
                judge_model=run.judge_model,
                prompt_version=run.prompt_version,
                dataset_hash=run.dataset_hash[:12],
                error_code=run.error_code,
            ),
            request=request,
            db=db,
        )
        if run.status == eval_service.RUN_STATUS_FAILED:
            # 失败运行已持久化（可回查机器码），但本次请求没有分数可交付：
            # 评委是外部 LLM 服务 → 502 语义；绝不 200 + 空分数伪装成 0 分。
            raise ExternalServiceError(
                "答案质量评委不可用，本次运行未产生分数",
                error_code=run.error_code or eval_service.ERROR_CODE_JUDGE_UNAVAILABLE,
                detail={"run_id": run.id},
            )
        return {"success": True, "data": eval_service.run_detail_dict(run)}
    except (NotFoundError, AuthorizationError):
        raise
    except AppError:
        raise
    except Exception as e:
        logger.error(f"Run answer quality eval failed: {type(e).__name__}")
        raise AppError("运行答案质量评测失败") from e


@router.get("/runs/trend", response_model=dict, dependencies=_READ_DEPS)
async def answer_quality_trend(
    user_id: int | None = Query(None, ge=1, description="按运行归属用户过滤"),
    status: Literal["completed", "failed", "all"] = Query("all", description="运行状态过滤"),
    date_from: date | None = Query(None, description="起始日期（含，UTC）"),
    date_to: date | None = Query(None, description="结束日期（含，UTC）"),
    limit: int = Query(200, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """按时间正序返回运行点与汇总；失败运行计数但不污染指标均值。"""
    try:
        org_id = await require_organization(current_user, db)
        data = await eval_service.trend(
            db,
            organization_id=org_id,
            user_id=user_id,
            status=status,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
        )
        return {"success": True, "data": data}
    except (NotFoundError, AuthorizationError):
        raise
    except AppError:
        raise
    except Exception as e:
        logger.error(f"Answer quality trend failed: {type(e).__name__}")
        raise AppError("获取答案质量评测趋势失败") from e


@router.get("/runs", response_model=dict, dependencies=_READ_DEPS)
async def list_answer_quality_runs(
    user_id: int | None = Query(None, ge=1, description="按运行归属用户过滤"),
    status: Literal["completed", "failed", "all"] = Query("all", description="运行状态过滤"),
    date_from: date | None = Query(None, description="起始日期（含，UTC）"),
    date_to: date | None = Query(None, description="结束日期（含，UTC）"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列出本组织的运行历史（可按用户/日期/状态过滤）；跨组织绝不返回。"""
    try:
        org_id = await require_organization(current_user, db)
        data = await eval_service.list_runs(
            db,
            organization_id=org_id,
            user_id=user_id,
            status=status,
            date_from=date_from,
            date_to=date_to,
            page=page,
            page_size=page_size,
        )
        return {"success": True, "data": data}
    except (NotFoundError, AuthorizationError):
        raise
    except AppError:
        raise
    except Exception as e:
        logger.error(f"List answer quality runs failed: {type(e).__name__}")
        raise AppError("获取答案质量评测历史失败") from e


@router.get("/runs/{run_id}", response_model=dict, dependencies=_READ_DEPS)
async def get_answer_quality_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """读取本组织单次运行（含聚合与逐条结果）；跨组织一律 404。"""
    try:
        org_id = await require_organization(current_user, db)
        run = await eval_service.get_run(db, organization_id=org_id, run_id=run_id)
        return {"success": True, "data": eval_service.run_detail_dict(run)}
    except (NotFoundError, AuthorizationError):
        raise
    except AppError:
        raise
    except Exception as e:
        logger.error(f"Get answer quality run failed: {type(e).__name__}")
        raise AppError("获取答案质量评测详情失败") from e
