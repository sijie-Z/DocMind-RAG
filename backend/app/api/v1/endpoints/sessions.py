"""会话分叉 / 谱系端点（issue #476）。

- ``POST /api/v1/sessions/{id}/fork``：从已结算边界分叉子会话；可选
  checkpoint 克隆 / seed / 幂等键。
- ``GET  /api/v1/sessions/{id}/lineage``：祖先链（根→父）+ 直接子级 + 谱系元数据。

鉴权/租户：复用 ``app.services.session_service`` 的 org + user 可见范围
（超管旁路、历史 NULL 组织、无组织非超管 fail-closed 到本人历史会话），
越权/跨组织一律 404（不 403，避免枚举）。

审计口径与既有端点一致（``audit_service.log_activity``）：action=session_fork，
target_type=chat_session，target_id=子会话 id，detail 记录父/子/边界/是否克隆
checkpoint（避免"伪历史"无法追溯）。
"""

import json
import logging

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.schemas.session_fork import SessionForkRequest
from app.services import session_fork_service
from app.services.audit_service import audit_service
from app.services.session_service import resolve_session_access_scope

router = APIRouter()
logger = logging.getLogger(__name__)


def _fork_data(result: session_fork_service.ForkResult) -> dict:
    """把 ForkResult 投影为响应 data 段（字段即契约，前端据此展示谱系徽标）。"""
    session = result.session
    fork_meta = session.settings.get("fork") if isinstance(session.settings, dict) else None
    if not isinstance(fork_meta, dict):
        fork_meta = {}
    return {
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


@router.post("/{session_id}/fork", response_model=dict, dependencies=[Depends(get_current_user)])
async def fork_session_endpoint(
    session_id: str,
    request: Request,
    body: SessionForkRequest | None = None,
    idempotency_key: str | None = Header(
        None,
        alias="Idempotency-Key",
        max_length=128,
        description="幂等键：同一用户/源会话下重复请求返回同一子会话",
    ),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """分叉会话（issue #476）。

    - 200：子会话已创建（或幂等重放同一子会话）；
    - 400：seed 超限；404：源会话/边界消息不存在、他人或跨组织；
    - 409：边界非法 / 消息数超配额 / 源会话运行中且无 checkpoint 可克隆 /
      没有可用的已结算 checkpoint。
    """
    scope = await resolve_session_access_scope(current_user, db)
    payload = body or SessionForkRequest()
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
        idempotency_key=(idempotency_key or "").strip() or None,
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


@router.get("/{session_id}/lineage", response_model=dict, dependencies=[Depends(get_current_user)])
async def get_session_lineage(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """会话谱系：祖先链 / 直接子级 / 切点元数据（父会话删除后仍可查）。"""
    scope = await resolve_session_access_scope(current_user, db)
    data = await session_fork_service.get_lineage(
        db,
        session_id=session_id,
        user_id=current_user.id,
        organization_id=scope.org_id,
        scope=scope,
    )
    return {"success": True, "message": "获取成功", "data": data}
