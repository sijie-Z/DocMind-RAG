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
from collections.abc import AsyncGenerator
from typing import Any

import anyio
from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.token_usage import record_token_usage
from app.api.v1.streaming import ClosingStreamingResponse
from app.core.config import settings
from app.core.database import get_db
from app.core.llm_failures import UserLLMRequestError
from app.core.security import get_current_user, require_org_admin, require_organization
from app.exceptions import (
    AppError,
    ExternalServiceError,
    NotFoundError,
    RateLimitError,
    ServiceUnavailableError,
)
from app.models.user import User
from app.rag.errors import LLMNotConfiguredError, RetrievalUnavailableError
from app.schemas.public_kb import (
    PublicationChatSettingsUpdate,
    PublicationCreate,
    PublicationDocumentsUpdate,
    PublicationRotateRequest,
    PublicKbChatRequest,
    PublicKbSearchRequest,
)
from app.services import openai_compat_service as compat
from app.services import public_kb_service as service
from app.services.audit_service import audit_service
from app.services.organization_settings_service import organization_settings_service
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


def _check_chat_rate_limit(token: str) -> None:
    """公开聊天：独立于只读检索的每 token 桶（默认 10/min）。"""
    _consume_bucket(
        f"kb-chat-token:{share_token_digest(token)}",
        max(1, int(settings.PUBLIC_KB_CHAT_RATE_LIMIT_PER_MINUTE)),
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


def _public_headers(*, stream: bool = False) -> dict[str, str]:
    """公开面统一安全头；SSE 额外关闭反向代理缓冲。"""
    headers = {
        "Cache-Control": "no-store",
        "Referrer-Policy": "no-referrer",
        "X-Robots-Tag": "noindex",
        "X-Content-Type-Options": "nosniff",
    }
    if stream:
        headers["X-Accel-Buffering"] = "no"
    return headers


def _set_public_headers(response: Response) -> None:
    response.headers.update(_public_headers())


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


@router.post("/{publication_id}/chat-settings", response_model=dict)
async def update_publication_chat_settings(
    publication_id: str,
    payload: PublicationChatSettingsUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """管理面：发布级聊天开关 + 日预算（空预算 = 全局默认；0 = 禁止聊天）。"""
    try:
        org_id = await _admin_org(db, current_user)
        publication = await service.update_chat_settings(
            db,
            publication_id=publication_id,
            organization_id=org_id,
            chat_enabled=payload.chat_enabled,
            daily_token_budget=payload.daily_token_budget,
        )
        await db.commit()
        data = await _view_for(db, publication)
        await _audit(
            user_id=current_user.id,
            action="update_kb_publication_chat",
            publication_id=publication.id,
            detail={
                "organization_id": org_id,
                "user_id": current_user.id,
                "chat_enabled": bool(publication.chat_enabled),
                "daily_token_budget": publication.daily_token_budget,
            },
        )
        return {"success": True, "data": data}
    except AppError:
        raise
    except Exception as e:  # noqa: BLE001
        logger.error("Update kb publication chat settings failed: %s", type(e).__name__)
        raise AppError("更新公开知识库聊天设置失败") from e


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


# ── 公开面：匿名聊天（批次十七）─────────────────────────────────────────────

_CHAT_GENERIC_ERROR = "生成回答失败，请稍后重试"
_CHAT_UNAVAILABLE = "聊天服务暂不可用，请稍后再试"


def _sse_event(event: str, payload: dict[str, Any]) -> str:
    """单帧 SSE：``event:`` 名 + JSON ``data:``（与前端解析器约定一致）。"""
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


def _chat_model() -> str:
    """本次生成/记账使用的模型（进程默认模型，与 pipeline 实际一致）。"""
    return compat.effective_model(None)


def _chat_error(exc: Exception) -> tuple[int, str, str]:
    """把生成期异常映射为对公开访客安全的 (status, code, message)。"""
    if isinstance(exc, LLMNotConfiguredError):
        return 503, "LLM_NOT_CONFIGURED", _CHAT_UNAVAILABLE
    if isinstance(exc, UserLLMRequestError):
        return 502, "PUBLIC_KB_CHAT_GENERATION_FAILED", _CHAT_GENERIC_ERROR
    # 未预期异常同样只回固定文案，绝不回显上游异常正文。
    return 502, "PUBLIC_KB_CHAT_GENERATION_FAILED", _CHAT_GENERIC_ERROR


def _chat_app_error(exc: Exception) -> AppError:
    status, code, message = _chat_error(exc)
    if status == 503:
        return ServiceUnavailableError(message, error_code=code)
    return ExternalServiceError(message, error_code=code)


def _chat_usage_pair(usage: tuple[int, int] | None) -> tuple[int, int]:
    if not usage:
        return 0, 0
    try:
        return max(0, int(usage[0])), max(0, int(usage[1]))
    except (TypeError, ValueError, IndexError):
        return 0, 0


async def _finalize_chat(
    *,
    db: AsyncSession,
    publication,
    usage: tuple[int, int] | None,
    stream: bool,
    source_count: int,
) -> None:
    """断连/完成统一收口：token 记账 + 审计（均无正文/明文 token）。

    记账归属 = 发布创建人（``publication.created_by``）+ 发布所属组织；
    ``feature=public_kb_chat:<pub_id>`` 同时是日预算的统计键。记账/审计失败
    只 warning，不得反噬已生成的回答。
    """
    input_tokens, output_tokens = _chat_usage_pair(usage)
    if input_tokens > 0 or output_tokens > 0:
        try:
            await record_token_usage(
                db=db,
                user_id=publication.created_by,
                organization_id=publication.organization_id,
                model=_chat_model(),
                source=service.CHAT_SOURCE,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                feature=f"{service.CHAT_FEATURE_PREFIX}{publication.id}",
            )
        except Exception as e:  # noqa: BLE001 — 记账是旁路
            logger.warning("Public kb chat token usage failed: %s", type(e).__name__)

    detail = json.dumps(
        {
            "publication_id": publication.id,
            "organization_id": publication.organization_id,
            "stream": bool(stream),
            "source_count": max(0, int(source_count)),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    try:
        await audit_service.log_activity(
            user_id=publication.created_by,
            action="public_kb_chat",
            target_type="kb_publication",
            target_id=publication.id,
            detail=detail,
        )
    except Exception as e:  # noqa: BLE001 — 审计是旁路
        logger.warning("Write public kb chat audit failed: %s", type(e).__name__)


async def _stream_chat(
    *,
    db: AsyncSession,
    publication,
    message: str,
    history: list[dict[str, str]],
    history_mask_mapping: dict[str, str],
    context: list[dict[str, Any]],
    citations: list[dict[str, Any]],
) -> AsyncGenerator[str, None]:
    """SSE：sources → delta* → done / error。

    所有 404/429 已在生成器启动前完成；本生成器只处理生成期错误。断连时
    ``finally`` 内显式关闭上游生成器（否则 pipeline 的 token 估算拖到 GC），
    并在 shield 内完成记账/审计——取消不得吞掉已产生的用量。
    """
    completion: AsyncGenerator[str, None] | None = None
    usage: tuple[int, int] | None = None
    try:
        yield _sse_event("sources", {"sources": citations})
        if not context:
            # 零白名单/零命中：不调用 LLM（不产生可被幻觉利用的空上下文回答）。
            yield _sse_event("done", {"done": True, "source_count": 0})
            return
        try:
            completion = compat.iter_completion_chunks(
                query=message,
                context_results=context,
                history=history,
                system_prompt=service.CHAT_SYSTEM_PROMPT,
                model=_chat_model(),
                enable_masking=True,
                extra_mask_mapping=history_mask_mapping,
            )
            async for text in completion:
                if text:
                    yield _sse_event("delta", {"delta": text})
            usage = compat.current_token_usage()
        except Exception as exc:  # noqa: BLE001 — 生成错误必须落成 SSE error
            _status, code, message_text = _chat_error(exc)
            logger.warning("Public kb chat stream failed: %s", type(exc).__name__)
            yield _sse_event("error", {"error_code": code, "message": message_text})
            return
        yield _sse_event("done", {"done": True, "source_count": len(citations)})
    finally:
        with anyio.CancelScope(shield=True):
            if completion is not None:
                try:
                    await completion.aclose()
                except Exception:  # noqa: BLE001 — 关闭失败不得阻断记账
                    logger.warning("Public kb chat completion close failed", exc_info=True)
            final_usage = usage if usage is not None else compat.current_token_usage()
            await _finalize_chat(
                db=db,
                publication=publication,
                usage=final_usage,
                stream=True,
                source_count=len(citations),
            )


async def _non_stream_chat(
    *,
    db: AsyncSession,
    publication,
    message: str,
    history: list[dict[str, str]],
    history_mask_mapping: dict[str, str],
    context: list[dict[str, Any]],
    citations: list[dict[str, Any]],
) -> dict[str, Any]:
    """非流式：完整生成后返回 ``{answer, sources}``；空上下文不调用 LLM。"""
    if not context:
        await _finalize_chat(
            db=db,
            publication=publication,
            usage=None,
            stream=False,
            source_count=0,
        )
        return {"answer": "", "sources": []}
    try:
        answer = await compat.collect_completion(
            query=message,
            context_results=context,
            history=history,
            system_prompt=service.CHAT_SYSTEM_PROMPT,
            model=_chat_model(),
            enable_masking=True,
            extra_mask_mapping=history_mask_mapping,
        )
    except Exception as exc:  # noqa: BLE001 — 统一映射为 AppError 信封
        # 上游失败前也可能已经消耗 token：如实入账，不丢掉失败调用的成本。
        await _finalize_chat(
            db=db,
            publication=publication,
            usage=compat.current_token_usage(),
            stream=False,
            source_count=len(citations),
        )
        logger.warning("Public kb chat completion failed: %s", type(exc).__name__)
        raise _chat_app_error(exc) from exc
    await _finalize_chat(
        db=db,
        publication=publication,
        usage=compat.current_token_usage(),
        stream=False,
        source_count=len(citations),
    )
    return {"answer": answer, "sources": citations}


@public_router.post("/kb/{token}/chat")
async def chat_public_kb(
    token: str,
    payload: PublicKbChatRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    """匿名聊天：全局/发布开关、撤销/过期统一 404；预算/配额/限流首帧前 429。

    红线：只检索显式白名单文档；不创建 chat_sessions/chat_messages/记忆；
    引用只含 rank/title/snippet/score；审计/token 账目无正文与 token 明文。
    """
    # 404/429 也必须带 no-store/no-referrer/noindex/nosniff（token 链接不得被
    # 缓存或经 Referer 泄露）：异常 handler 读取该 state 合并响应头。
    request.state.public_headers = _public_headers()
    _require_enabled()
    if not settings.PUBLIC_KB_CHAT_ENABLED:
        raise NotFoundError(service.PUBLICATION_INVALID)
    try:
        try:
            publication = await service.resolve_public_chat(db, token)
        except NotFoundError:
            _check_invalid_rate_limit(request)
            raise
        # 有效链接的每 token 桶；无效 token 走 IP 高阈值桶（仅约束扫描）。
        _check_chat_rate_limit(token)
        message = service.validate_chat_message(payload.message)
        history = service.normalize_chat_history(payload.history)
        # 匿名端点的客户端历史同样要在离开服务器前掩码（pipeline 只掩码
        # context/query）；映射用于输出时还原引用型占位符。
        history, history_mask_mapping = service.mask_chat_history(history)
        # 首帧前：日预算 + 组织月度配额（两者都是 429，但错误码/语义不同）。
        await service.ensure_daily_budget(db, publication)
        await organization_settings_service.ensure_monthly_quota(
            db, publication.organization_id, user=None
        )
        context, citations = await service.public_chat_context(
            db, publication=publication, query=message
        )
    except AppError:
        raise
    except RetrievalUnavailableError as e:
        # 检索后端故障必须与「没有相关内容」区分（issue #292 同口径）。
        logger.error("Public kb chat retrieval unavailable: kind=%s", getattr(e, "kind", "-"))
        raise ExternalServiceError(
            "知识库检索服务暂不可用，请稍后重试",
            error_code="RETRIEVAL_UNAVAILABLE",
        ) from e
    except Exception as e:  # noqa: BLE001 — 统一失败信封
        logger.error("Public kb chat failed: %s", type(e).__name__)
        raise AppError("公开知识库聊天失败") from e

    if payload.stream:
        # 匿名长连接不占用 DB 连接：预检/检索已完成，流式生成器只读已加载的
        # publication 快照，记账/审计各用独立 session（record_token_usage 忽略
        # 传入的 db）。先释放请求 session，避免少量匿名长流耗尽连接池。
        await db.close()
        return ClosingStreamingResponse(
            _stream_chat(
                db=db,
                publication=publication,
                message=message,
                history=history,
                history_mask_mapping=history_mask_mapping,
                context=context,
                citations=citations,
            ),
            media_type="text/event-stream",
            headers=_public_headers(stream=True),
        )

    _set_public_headers(response)
    # 非流式同样在生成前释放请求 session：匿名者可用 stream=false 让连接在
    # LLM 生成期间一直挂起；记账/审计各自使用独立 session，publication 的
    # 已加载字段足够完成收口。
    await db.close()
    data = await _non_stream_chat(
        db=db,
        publication=publication,
        message=message,
        history=history,
        history_mask_mapping=history_mask_mapping,
        context=context,
        citations=citations,
    )
    return {"success": True, "data": data}
