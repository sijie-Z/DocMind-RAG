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

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.schemas.session_fork import SessionForkRequest
from app.services import session_fork_service
from app.services.audit_service import audit_service
from app.services.session_service import resolve_session_access_scope

router = APIRouter()
compat_router = APIRouter()
logger = logging.getLogger(__name__)


class ForkRequest(SessionForkRequest):
    """fork 请求体（issue #499）。

    在既有 ``SessionForkRequest`` 上追加 ``idempotency_key``：body 键优先于
    ``Idempotency-Key`` 头（两者都生效；只带其一时行为不变）。
    """

    idempotency_key: str | None = Field(
        None,
        max_length=128,
        description="幂等键（优先于 Idempotency-Key 头）：同一用户重复请求返回同一子会话",
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


# ── 兼容别名（issue #499）：/api/v1/agent/sessions/{id}/fork|lineage ──────────


@compat_router.post(
    "/{session_id}/fork",
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
    "/{session_id}/lineage",
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
