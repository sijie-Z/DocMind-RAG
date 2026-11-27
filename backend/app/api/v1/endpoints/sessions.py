"""会话分叉 / 谱系端点（issue #476 / #499）。

规范路由（前端在用，保持稳定）：

- ``POST /api/v1/sessions/{id}/fork``：从 ``seq <= boundary`` 前缀分叉子会话
  （含边界消息）；可选 checkpoint 克隆 / seed / 幂等键。
- ``GET  /api/v1/sessions/{id}/lineage``：祖先链（根→父）+ 直接子级 + 谱系元数据。

兼容别名（issue #499 契约路径，旧客户端可用）：

- ``POST /api/v1/agent/sessions/{id}/fork``
- ``GET  /api/v1/agent/sessions/{id}/lineage``

别名路由 ``include_in_schema=False`` 且响应带 ``Deprecation: true`` 头；行为与
规范路由完全一致（同一实现），废弃窗口内保留。

鉴权/租户：复用 ``app.services.session_service`` 的 org + user 可见范围
（超管旁路、历史 NULL 组织、无组织非超管 fail-closed 到本人历史会话），
越权/跨组织一律 404（不 403，避免枚举）。

审计口径与既有端点一致（``audit_service.log_activity``）：action=session_fork，
target_type=chat_session，target_id=子会话 id，detail 记录父/子/边界/是否克隆
checkpoint（避免"伪历史"无法追溯）。
"""

import json
import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user
from app.exceptions import NotFoundError, ValidationError
from app.models.user import User
from app.schemas.chat import ChatShareCreate
from app.schemas.session_fork import SessionForkRequest
from app.services import session_fork_service
from app.services.audit_service import audit_service
from app.services.session_service import (
    load_accessible_session,
    resolve_session_access_scope,
)
from app.services.share_link_service import new_share_token, share_token_digest

router = APIRouter()
compat_router = APIRouter()
logger = logging.getLogger(__name__)


class ForkRequest(SessionForkRequest):
    """fork 请求体（issue #499）。

    在既有 ``SessionForkRequest`` 上追加 ``idempotency_key``：body 键优先于
    ``Idempotency-Key`` 头（两者都生效；只带其一时行为不变）。

    ``message_id`` / ``include_checkpoint`` 的描述在子类里覆盖，避免 OpenAPI
    继续展示 issue #476 的旧语义（"在该 USER 之前分叉"/克隆为 PAUSED）。
    """

    message_id: str | None = Field(
        None,
        max_length=36,
        description=(
            "边界消息 ID：复制 seq <= 该消息（含边界消息）；ASSISTANT 必须是轮次"
            "最终消息。默认最后一条已结算消息"
        ),
    )
    include_checkpoint: bool = Field(
        False,
        description="是否深拷最近一个已结算 checkpoint 到新 PENDING 任务（可 resume 继续）",
    )
    idempotency_key: str | None = Field(
        None,
        max_length=128,
        description="幂等键（优先于 Idempotency-Key 头）：同一用户重复请求返回同一子会话",
    )


class BranchLabelRequest(BaseModel):
    """分支标签写入：空白/None 清除标签。"""

    branch_label: str | None = Field(
        None,
        max_length=session_fork_service.BRANCH_LABEL_MAX_CHARS,
        description="分支展示标签；空/null 清除",
    )


class BranchSummaryRequest(BaseModel):
    """分支摘要生成：fake=确定性抽取（测试/离线），llm=调用已配置模型。"""

    mode: str = Field(
        "llm",
        description="fake / llm；llm 不可用时显式 503 SUMMARY_MODEL_UNAVAILABLE",
    )


def _fork_data(result: session_fork_service.ForkResult) -> dict:
    """把 ForkResult 投影为响应 data 段（字段即契约，前端据此展示谱系徽标）。

    issue #499：追加契约字段 ``session_id`` / ``messages_copied`` /
    ``checkpoint_task_id``；旧字段（``id`` / ``copied_message_count`` /
    ``checkpoint`` 嵌套）保留一版，兼容现有前端与脚本。
    """
    session = result.session
    fork_meta = session.settings.get("fork") if isinstance(session.settings, dict) else None
    if not isinstance(fork_meta, dict):
        fork_meta = {}
    return {
        # ── issue #499 契约字段（顶层）────────────────────────────────────
        "session_id": session.id,
        "messages_copied": result.copied_message_count,
        "checkpoint_task_id": result.checkpoint_task_id,
        # ── issue #476 兼容字段（保留一版）────────────────────────────────
        "id": session.id,
        "title": session.title,
        "parent_session_id": session.parent_session_id,
        "root_session_id": session.root_session_id or session.id,
        "fork_point_message_id": session.fork_point_message_id,
        "fork_reason": session.fork_reason,
        "message_count": session.message_count,
        "copied_message_count": result.copied_message_count,
        "boundary_seq": result.boundary_seq,
        "is_seeded": result.is_seeded,
        "checkpoint": {
            "cloned": result.checkpoint_task_id is not None,
            "task_id": result.checkpoint_task_id,
            "source_task_id": fork_meta.get("checkpoint_source_task_id"),
        },
        "idempotent_replay": result.idempotent,
    }


def _mark_deprecated(response: Response) -> None:
    """兼容别名路由的统一废弃标记（issue #499）。"""
    response.headers["Deprecation"] = "true"
    response.headers["Warning"] = '299 - "Deprecated API; use /api/v1/sessions/... instead"'


async def _execute_fork(
    *,
    session_id: str,
    request: Request,
    body: ForkRequest | None,
    header_idempotency_key: str | None,
    current_user: User,
    db: AsyncSession,
) -> dict:
    """fork 的唯一实现（规范路由与兼容别名共用，避免行为漂移）。"""
    scope = await resolve_session_access_scope(current_user, db)
    payload = body or ForkRequest()
    # issue #499：body 幂等键优先，Idempotency-Key 头兜底。
    idempotency_key = (
        (payload.idempotency_key or "").strip() or (header_idempotency_key or "").strip() or None
    )
    result = await session_fork_service.fork_session(
        db,
        source_id=session_id,
        user_id=current_user.id,
        organization_id=scope.org_id,
        scope=scope,
        message_id=payload.message_id,
        title=payload.title,
        include_checkpoint=payload.include_checkpoint,
        seed=payload.seed,
        reason=payload.reason,
        idempotency_key=idempotency_key,
    )

    fork_meta = (
        result.session.settings.get("fork") if isinstance(result.session.settings, dict) else {}
    )
    if not isinstance(fork_meta, dict):
        fork_meta = {}
    await audit_service.log_activity(
        user_id=current_user.id,
        action="session_fork",
        target_type="chat_session",
        target_id=result.session.id,
        detail=json.dumps(
            {
                "parent_session_id": result.session.parent_session_id,
                "child_session_id": result.session.id,
                "root_session_id": result.session.root_session_id,
                "boundary_message_id": fork_meta.get("boundary_message_id"),
                "fork_point_message_id": result.fork_point_message_id,
                "copied_message_count": result.copied_message_count,
                "boundary_seq": result.boundary_seq,
                "checkpoint_cloned": result.checkpoint_task_id is not None,
                "checkpoint_source_task_id": fork_meta.get("checkpoint_source_task_id"),
                "is_seeded": result.is_seeded,
                "idempotent_replay": result.idempotent,
            },
            ensure_ascii=False,
        ),
        request=request,
    )

    return {
        "success": True,
        "message": "分叉成功",
        "data": _fork_data(result),
    }


async def _execute_lineage(
    *,
    session_id: str,
    current_user: User,
    db: AsyncSession,
) -> dict:
    scope = await resolve_session_access_scope(current_user, db)
    data = await session_fork_service.get_lineage(
        db,
        session_id=session_id,
        user_id=current_user.id,
        organization_id=scope.org_id,
        scope=scope,
    )
    return {"success": True, "message": "获取成功", "data": data}


@router.post("/{session_id}/fork", response_model=dict, dependencies=[Depends(get_current_user)])
async def fork_session_endpoint(
    session_id: str,
    request: Request,
    body: ForkRequest | None = None,
    idempotency_key: str | None = Header(
        None,
        alias="Idempotency-Key",
        max_length=128,
        description="幂等键：同一用户重复请求返回同一子会话（body idempotency_key 优先）",
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """分叉会话（issue #499 规范路由）。

    - 200：子会话已创建（或幂等重放同一子会话）；
    - 400：seed 超限 / 边界消息 pending / 复制数超配额；
    - 404：源会话/边界消息不存在、他人或跨组织；
    - 409：边界非法 / 源会话运行中且无 checkpoint 可克隆 / 没有可用 checkpoint /
      同幂等键的首个请求仍在处理中（FORK_IN_PROGRESS，可重试）。
    """
    return await _execute_fork(
        session_id=session_id,
        request=request,
        body=body,
        header_idempotency_key=idempotency_key,
        current_user=current_user,
        db=db,
    )


@router.get("/{session_id}/lineage", response_model=dict, dependencies=[Depends(get_current_user)])
async def get_session_lineage(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """会话谱系：祖先链 / 直接子级 / 切点元数据（父会话删除后仍可查）。"""
    return await _execute_lineage(session_id=session_id, current_user=current_user, db=db)


# ── 会话树导航（批次八）────────────────────────────────────────────────────
# 只读树/分支上下文与显式标签/摘要写入；既有 fork/lineage 端点保持不动。


@router.get("/{session_id}/tree", response_model=dict, dependencies=[Depends(get_current_user)])
async def get_session_tree_endpoint(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """会话树：根 + 可见分支（切点、标签、摘要、按分支工件统计）。

    越权/跨组织 404（不泄露存在性）；只读，不创建任何会话。
    """
    scope = await resolve_session_access_scope(current_user, db)
    data = await session_fork_service.get_session_tree(
        db,
        session_id=session_id,
        user_id=current_user.id,
        organization_id=scope.org_id,
        scope=scope,
    )
    return {"success": True, "message": "获取成功", "data": data}


@router.get(
    "/{session_id}/branch-context",
    response_model=dict,
    dependencies=[Depends(get_current_user)],
)
async def get_session_branch_context_endpoint(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """选中分支的消息 + 摘要 + 工件上下文（**只读**，原位切换不 fork）。"""
    scope = await resolve_session_access_scope(current_user, db)
    data = await session_fork_service.get_branch_context(
        db,
        session_id=session_id,
        user_id=current_user.id,
        organization_id=scope.org_id,
        scope=scope,
    )
    return {"success": True, "message": "获取成功", "data": data}


@router.put(
    "/{session_id}/branch-label",
    response_model=dict,
    dependencies=[Depends(get_current_user)],
)
async def set_session_branch_label_endpoint(
    session_id: str,
    body: BranchLabelRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """设置/清除分支标签（空白/null 清除；超长 400）。"""
    scope = await resolve_session_access_scope(current_user, db)
    data = await session_fork_service.set_branch_label(
        db,
        session_id=session_id,
        user_id=current_user.id,
        label=body.branch_label,
        organization_id=scope.org_id,
        scope=scope,
    )
    return {"success": True, "message": "更新成功", "data": data}


@router.post(
    "/{session_id}/summary",
    response_model=dict,
    dependencies=[Depends(get_current_user)],
)
async def summarize_session_branch_endpoint(
    session_id: str,
    body: BranchSummaryRequest | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """生成并持久化分支摘要。

    - 200：已生成（或空分支如实返回 ``generated=false``，无摘要）；
    - 400：模式非法；
    - 404：会话不存在/他人/跨组织；
    - 503 ``SUMMARY_MODEL_UNAVAILABLE``：LLM 未配置或调用失败（**绝不 fake 成功**）。
    """
    scope = await resolve_session_access_scope(current_user, db)
    payload = body or BranchSummaryRequest()
    data = await session_fork_service.summarize_branch(
        db,
        session_id=session_id,
        user_id=current_user.id,
        mode=payload.mode,
        organization_id=scope.org_id,
        scope=scope,
    )
    return {
        "success": True,
        "message": "摘要已生成" if data.get("generated") else "没有可摘要的消息",
        "data": data,
    }


@router.post(
    "/tree/{root_session_id}/share",
    response_model=dict,
    dependencies=[Depends(get_current_user)],
)
async def share_session_tree_endpoint(
    root_session_id: str,
    response: Response,
    body: ChatShareCreate | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """为会话树根会话创建/轮换只读分享链接。

    复用既有分享机制（``share_token_hash`` + 公开端点
    ``GET /api/v1/chat/shares/{token}``）：不新增第二套 token/鉴权模型。
    token 原文只在本次响应返回；再次调用会轮换（旧链接立即失效）。
    越权/跨组织 404（不泄露存在性）。

    批次十二：可选 ``expires_in_days``（1-90）；缺省/null = 永不过期。
    过期后公开读取与撤销同一 404；审计只记根会话 id 与过期时间，不落 token。
    """
    scope = await resolve_session_access_scope(current_user, db)
    session = await load_accessible_session(db, root_session_id, current_user, scope)
    if session is None:
        raise NotFoundError("会话不存在")
    if session.parent_session_id is not None or session.root_session_id not in (None, session.id):
        # 只允许分享树根：避免前端在树未加载/竞态时把子分支当根分享出去。
        raise ValidationError("只能分享会话树的根会话", field="root_session_id")
    rotated = session.share_token_hash is not None
    token = new_share_token()
    created_at = datetime.now(UTC)
    expires_at = (
        created_at + timedelta(days=body.expires_in_days)
        if body is not None and body.expires_in_days is not None
        else None
    )
    session.share_token_hash = share_token_digest(token)
    session.share_created_at = created_at
    session.share_expires_at = expires_at
    await db.commit()
    await _audit_tree_share_action(
        user_id=current_user.id,
        action="rotate_session_tree_share" if rotated else "share_session_tree",
        root_session_id=session.id,
        expires_at=expires_at,
    )
    # token 属于凭证：响应不得被任何中间缓存留存。
    response.headers["Cache-Control"] = "no-store"
    return {
        "success": True,
        "message": "分享链接已创建",
        "data": {
            "root_session_id": session.id,
            "token": token,
            "url": f"/share/{token}",
            "createdAt": created_at.isoformat(),
            "expiresAt": expires_at.isoformat() if expires_at else None,
        },
    }


async def _audit_tree_share_action(
    *,
    user_id: int,
    action: str,
    root_session_id: str,
    expires_at: datetime | None,
) -> None:
    """会话树分享审计（best-effort）：detail 只含根会话与过期时间，不落 token。"""
    try:
        await audit_service.log_activity(
            user_id=user_id,
            action=action,
            target_type="chat_session",
            target_id=root_session_id,
            detail=json.dumps(
                {
                    "conversation_id": root_session_id,
                    "expires_at": expires_at.isoformat() if expires_at else None,
                },
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
    except Exception as e:  # noqa: BLE001 — 审计是旁路，任何失败都不得反噬请求
        logger.warning(f"Audit session tree share action failed: {type(e).__name__}: {e}")


# ── 兼容别名（issue #499）：/api/v1/agent/sessions/{id}/fork|lineage ──────────


@compat_router.post(
    "/sessions/{session_id}/fork",
    response_model=dict,
    include_in_schema=False,
    deprecated=True,
    dependencies=[Depends(get_current_user)],
)
async def fork_session_compat_endpoint(
    session_id: str,
    request: Request,
    response: Response,
    body: ForkRequest | None = None,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key", max_length=128),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """兼容别名：``POST /api/v1/agent/sessions/{id}/fork``（响应带 Deprecation 头）。"""
    _mark_deprecated(response)
    return await _execute_fork(
        session_id=session_id,
        request=request,
        body=body,
        header_idempotency_key=idempotency_key,
        current_user=current_user,
        db=db,
    )


@compat_router.get(
    "/sessions/{session_id}/lineage",
    response_model=dict,
    include_in_schema=False,
    deprecated=True,
    dependencies=[Depends(get_current_user)],
)
async def get_session_lineage_compat(
    session_id: str,
    response: Response,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """兼容别名：``GET /api/v1/agent/sessions/{id}/lineage``（带 Deprecation 头）。"""
    _mark_deprecated(response)
    return await _execute_lineage(session_id=session_id, current_user=current_user, db=db)
