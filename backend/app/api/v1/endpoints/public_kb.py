"""公开知识库只读检索端点（批次十三）。

权限口径：
- 管理面（``/api/v1/kb-publications*``）：登录用户 + 组织管理员
  （``require_org_admin``）。黑名单外一律 404/403，不回显存在性。
- 公开面（``/api/v1/public/kb/{token}``）：**token 即全部权限**，没有 JWT/
  scope/组织依赖；禁用/撤销/过期/不存在统一 404，不区分原因。

部署开关 ``PUBLIC_KB_ENABLED`` 默认 false：关闭时**创建发布**与**公开读取**都按
404 处理；状态/撤销/白名单维护不受影响（撤下已发出的链接必须始终可用）。

红线：公开检索只查 ``document_publication`` 显式白名单（见
``public_kb_service``），绝不使用 ``document_acl.get_blocked_document_ids(
viewer=None)`` 这类匿名 fail-open 路径；``documents.visibility`` 语义不变。

限流为进程内固定窗口桶（每 token + 每 IP 每分钟上限），超限 429；不信任
``X-Forwarded-For``。多副本部署时每副本各自计数（与 public_artifacts 的已知
边界一致）。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_db
from app.core.security import get_current_user, require_org_admin, require_organization
from app.exceptions import (
    AppError,
    ExternalServiceError,
    NotFoundError,
    RateLimitError,
)
from app.models.user import User
from app.rag.errors import RetrievalUnavailableError
from app.schemas.public_kb import (
    PublicationCreate,
    PublicationDocumentsUpdate,
    PublicationRotateRequest,
    PublicKbSearchRequest,
)
from app.services import public_kb_service as service
from app.services.audit_service import audit_service
from app.services.share_link_service import share_token_digest

logger = logging.getLogger(__name__)

# 管理面：挂载于 /kb-publications。
router = APIRouter()
# 公开面：挂载于 /public（与 public_artifacts 共存，路径再带 /kb 段）。
public_router = APIRouter()

_RATE_WINDOW_SECONDS = 60
# 固定窗口内存桶：key -> (window_start, count)。
_RATE_BUCKETS: dict[str, tuple[int, int]] = {}
_MAX_BUCKET_KEYS = 4096
INVALID_BUCKET_MIN = 300
"""无效 token 的每 IP 桶下限（高于有效链接桶，仅约束扫描）。"""


def _require_enabled() -> None:
    """开关关闭时创建与公开读取按不存在处理（404，而非 403/503）。"""
    if not settings.PUBLIC_KB_ENABLED:
        raise NotFoundError(service.PUBLICATION_INVALID)


def _client_ip(request: Request) -> str:
    client = request.client
    return client.host if client is not None and client.host else "unknown"


def _consume_bucket(key: str, limit: int) -> None:
    now = int(time.time())
    window_start = now - (now % _RATE_WINDOW_SECONDS)
    current = _RATE_BUCKETS.get(key)
    count = 1 if current is None or current[0] != window_start else current[1] + 1
    _RATE_BUCKETS[key] = (window_start, count)
    if count > limit:
        raise RateLimitError()
    # 有界内存：先清过期桶；仍超上限则淘汰任意一个旧键，保证 O(1) 有界。
    if len(_RATE_BUCKETS) > _MAX_BUCKET_KEYS:
        for bucket_key, (start, _count) in list(_RATE_BUCKETS.items()):
            if start != window_start:
                _RATE_BUCKETS.pop(bucket_key, None)
        while len(_RATE_BUCKETS) > _MAX_BUCKET_KEYS:
            _RATE_BUCKETS.pop(next(iter(_RATE_BUCKETS)))


def _check_link_rate_limit(token: str) -> None:
    """有效链接：按 token 摘要限流，单客户端不影响其它链接。"""
    _consume_bucket(
        f"kb-token:{share_token_digest(token)}",
        max(1, int(settings.PUBLIC_KB_RATE_LIMIT_PER_MINUTE)),
    )


def _check_invalid_rate_limit(request: Request) -> None:
    """无效 token：按 IP 用更高阈值限流，仅约束扫描，不阻断有效链接。"""
    limit = max(1, int(settings.PUBLIC_KB_RATE_LIMIT_PER_MINUTE))
    _consume_bucket(f"kb-invalid-ip:{_client_ip(request)}", max(INVALID_BUCKET_MIN, limit * 5))


async def _admin_org(db: AsyncSession, current_user: User) -> int:
    """管理面统一门禁：有效组织 + 该组织管理员（超管旁路）。"""
    org_id = await require_organization(current_user, db)
    await require_org_admin(db, current_user, org_id)
    return org_id


async def _audit(
    *,
    user_id: int,
    action: str,
    publication_id: str,
    detail: dict[str, Any],
) -> None:
    """审计只记元数据（组织/发布/KB/数量/状态）；绝不记 token 或正文。

    审计服务自身已捕获异常；这里再包一层，保证审计失败不反噬业务响应。
    """
    payload = json.dumps(detail, ensure_ascii=False, sort_keys=True)
    try:
        await audit_service.log_activity(
            user_id=user_id,
            action=action,
            target_type="kb_publication",
            target_id=publication_id,
            detail=payload,
        )
    except Exception as e:  # noqa: BLE001 — 审计是旁路
        logger.warning("Write kb publication audit failed: %s", type(e).__name__)


async def _view_for(
    db: AsyncSession,
    publication,
    *,
    token: str | None = None,
    meta: tuple[str | None, int] | None = None,
) -> dict[str, Any]:
    """meta 预计算：创建/轮换在 commit 前算好，避免响应读失败丢失一次性 token。"""
    kb_name, count = meta if meta is not None else await service.publication_meta(db, publication)
    data = service.publication_view(publication, knowledge_base_name=kb_name, document_count=count)
    if token is not None:
        data["token"] = token
        data["url"] = f"/public/kb/{token}"
    return data


def _set_public_headers(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex"
    response.headers["X-Content-Type-Options"] = "nosniff"


# ── 管理面 ──────────────────────────────────────────────────────────────────


@router.post("", response_model=dict)
async def create_publication(
    payload: PublicationCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """创建发布；token 明文只在本次响应出现一次。"""
    _require_enabled()
    try:
        org_id = await _admin_org(db, current_user)
        publication, token = await service.create_publication(
            db,
            organization_id=org_id,
            knowledge_base_id=payload.knowledge_base_id,
            title=payload.title,
            description=payload.description,
            expires_in_days=payload.expires_in_days,
            created_by=current_user.id,
            document_ids=payload.document_ids,
        )
        # 一次性 token 不能因 commit 后的展示查询失败而丢失：先在事务内取好元数据。
        meta = await service.publication_meta(db, publication)
        await db.commit()
        data = await _view_for(db, publication, token=token, meta=meta)
        await _audit(
            user_id=current_user.id,
            action="create_kb_publication",
            publication_id=publication.id,
            detail={
                "organization_id": org_id,
                "user_id": current_user.id,
                "knowledge_base_id": publication.knowledge_base_id,
                "expires_at": data["expires_at"],
                "document_count": data["document_count"],
            },
        )
        return {"success": True, "data": data}
    except AppError:
        raise
    except Exception as e:  # noqa: BLE001 — 统一失败信封
        logger.error("Create kb publication failed: %s", type(e).__name__)
        raise AppError("创建公开知识库失败") from e


@router.get("", response_model=dict)
async def list_publications(
    knowledge_base_id: str | None = Query(
        None, max_length=36, description="按知识库过滤（空 = 本组织全部）"
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """本组织发布列表；状态投影不含 token。"""
    try:
        org_id = await _admin_org(db, current_user)
        data = await service.list_publications(
            db, organization_id=org_id, knowledge_base_id=knowledge_base_id
        )
        return {"success": True, "data": data}
    except AppError:
        raise
    except Exception as e:  # noqa: BLE001
        logger.error("List kb publications failed: %s", type(e).__name__)
        raise AppError("获取公开知识库列表失败") from e


@router.get("/candidates", response_model=dict)
async def list_publication_candidates(
    knowledge_base_id: str = Query(..., min_length=1, max_length=36),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """白名单选择器候选：本组织同 KB、已索引且启用的文档（有界）。"""
    try:
        org_id = await _admin_org(db, current_user)
        data = await service.list_candidates(
            db, organization_id=org_id, knowledge_base_id=knowledge_base_id
        )
        return {"success": True, "data": data}
    except AppError:
        raise
    except Exception as e:  # noqa: BLE001
        logger.error("List kb publication candidates failed: %s", type(e).__name__)
        raise AppError("获取候选文档失败") from e


@router.get("/{publication_id}", response_model=dict)
async def get_publication_status(
    publication_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """发布状态（不含 token）；跨组织/不存在同形 404。"""
    try:
        org_id = await _admin_org(db, current_user)
        publication = await service.get_publication(
            db, publication_id=publication_id, organization_id=org_id
        )
        data = await _view_for(db, publication)
        return {"success": True, "data": data}
    except AppError:
        raise
    except Exception as e:  # noqa: BLE001
        logger.error("Get kb publication failed: %s", type(e).__name__)
        raise AppError("获取公开知识库失败") from e


@router.post("/{publication_id}/rotate", response_model=dict)
async def rotate_publication(
    publication_id: str,
    payload: PublicationRotateRequest | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """轮换 token：旧链接立即失效，新 token 只返回一次。撤销状态保持不变。"""
    try:
        org_id = await _admin_org(db, current_user)
        publication, token = await service.rotate_publication(
            db,
            publication_id=publication_id,
            organization_id=org_id,
            expires_in_days=payload.expires_in_days if payload else None,
            operator_id=current_user.id,
        )
        # 轮换已使旧 token 失效：元数据必须先取好，避免 commit 后查询失败吞掉新 token。
        meta = await service.publication_meta(db, publication)
        await db.commit()
        data = await _view_for(db, publication, token=token, meta=meta)
        await _audit(
            user_id=current_user.id,
            action="rotate_kb_publication",
            publication_id=publication.id,
            detail={
                "organization_id": org_id,
                "user_id": current_user.id,
                "expires_at": data["expires_at"],
            },
        )
        return {"success": True, "data": data}
    except AppError:
        raise
    except Exception as e:  # noqa: BLE001
        logger.error("Rotate kb publication failed: %s", type(e).__name__)
        raise AppError("轮换公开知识库链接失败") from e


@router.post("/{publication_id}/revoke", response_model=dict)
async def revoke_publication(
    publication_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """撤销发布（幂等）；撤销后公开面统一 404。"""
    try:
        org_id = await _admin_org(db, current_user)
        publication = await service.revoke_publication(
            db,
            publication_id=publication_id,
            organization_id=org_id,
            operator_id=current_user.id,
        )
        await db.commit()
        data = await _view_for(db, publication)
        await _audit(
            user_id=current_user.id,
            action="revoke_kb_publication",
            publication_id=publication.id,
            detail={"organization_id": org_id, "user_id": current_user.id},
        )
        return {"success": True, "data": data}
    except AppError:
        raise
    except Exception as e:  # noqa: BLE001
        logger.error("Revoke kb publication failed: %s", type(e).__name__)
        raise AppError("撤销公开知识库失败") from e


@router.post("/{publication_id}/documents", response_model=dict)
async def set_publication_documents(
    publication_id: str,
    payload: PublicationDocumentsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """批量设置白名单（replace/add/remove）；整体校验通过才写入。"""
    try:
        org_id = await _admin_org(db, current_user)
        await service.set_documents(
            db,
            publication_id=publication_id,
            organization_id=org_id,
            document_ids=payload.document_ids,
            mode=payload.mode,
            operator_id=current_user.id,
        )
        await db.commit()
        documents = await service.list_documents(
            db, publication_id=publication_id, organization_id=org_id
        )
        await _audit(
            user_id=current_user.id,
            action="set_kb_publication_documents",
            publication_id=publication_id,
            detail={
                "organization_id": org_id,
                "user_id": current_user.id,
                "mode": payload.mode,
                "document_count": len(documents),
            },
        )
        return {
            "success": True,
            "data": {"documents": documents, "document_count": len(documents)},
        }
    except AppError:
        raise
    except Exception as e:  # noqa: BLE001
        logger.error("Set kb publication documents failed: %s", type(e).__name__)
        raise AppError("设置公开知识库文档失败") from e


@router.get("/{publication_id}/documents", response_model=dict)
async def list_publication_documents(
    publication_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """管理面：当前白名单（含 ``searchable`` 标记）。"""
    try:
        org_id = await _admin_org(db, current_user)
        documents = await service.list_documents(
            db, publication_id=publication_id, organization_id=org_id
        )
        return {
            "success": True,
            "data": {"documents": documents, "document_count": len(documents)},
        }
    except AppError:
        raise
    except Exception as e:  # noqa: BLE001
        logger.error("List kb publication documents failed: %s", type(e).__name__)
        raise AppError("获取公开知识库文档失败") from e


# ── 公开面（无登录；token 即权限）────────────────────────────────────────────


@public_router.get("/kb/{token}", response_model=dict)
async def get_public_kb(
    token: str,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """公开元数据（标题/描述/过期时间）；无内部标识。"""
    _require_enabled()
    try:
        try:
            publication = await service.resolve_public(db, token)
        except NotFoundError:
            _check_invalid_rate_limit(request)
            raise
        _check_link_rate_limit(token)
        _set_public_headers(response)
        return {"success": True, "data": service.public_metadata(publication)}
    except NotFoundError:
        raise
    except AppError:
        raise
    except Exception as e:  # noqa: BLE001 — 映射为统一失败信封
        logger.error("Get public kb failed: %s", type(e).__name__)
        raise AppError("获取公开知识库失败") from e


@public_router.post("/kb/{token}/search", response_model=dict)
async def search_public_kb(
    token: str,
    payload: PublicKbSearchRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """匿名只读检索：只查显式白名单文档，返回 title/snippet/score。"""
    _require_enabled()
    try:
        try:
            publication = await service.resolve_public(db, token)
        except NotFoundError:
            _check_invalid_rate_limit(request)
            raise
        _check_link_rate_limit(token)
        results = await service.public_search(
            db,
            publication=publication,
            query=payload.query,
            top_k=payload.top_k,
        )
        _set_public_headers(response)
        return {
            "success": True,
            "data": {"results": results, "total": len(results)},
        }
    except NotFoundError:
        raise
    except (RateLimitError, ExternalServiceError):
        raise
    except RetrievalUnavailableError as e:
        # 检索后端故障必须与「没搜到」区分（issue #292 同口径）。
        logger.error("Public kb retrieval unavailable: kind=%s", getattr(e, "kind", "-"))
        raise ExternalServiceError(
            "知识库检索服务暂不可用，请稍后重试",
            error_code="RETRIEVAL_UNAVAILABLE",
        ) from e
    except AppError:
        raise
    except Exception as e:  # noqa: BLE001 — 映射为统一失败信封
        logger.error("Search public kb failed: %s", type(e).__name__)
        raise AppError("检索公开知识库失败") from e
