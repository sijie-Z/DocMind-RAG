"""工件只读分享（批次九）：所有者管理 + 公开 token 解析。

授权模型：
- **管理面**（创建/轮换/状态/撤销）只认 artifact 的**所有者本人**：查询条件同时
  限定 ``organization_id`` 与 ``user_id``。超管也不绕过（不是组织内任意管理员）。
  跨组织/他人/不存在一律 404，不回显存在性。
- **公开面**只认 token：SHA-256 摘要等值查询；撤销/过期/不存在一律同一个 404
  （不区分原因，避免把「链接曾经存在」变成信息口）。

token 复用 ``share_link_service`` 的生成与摘要算法（与聊天会话分享同一套），
明文只在创建/轮换响应返回一次，DB 只存摘要。分享绑定**具体工件版本行**
（``artifact_id``），不跟随版本链头——工件产生新版本后旧分享仍指向原版本。

失效状态用列表达（``revoked_at`` / ``expires_at``），不提供删除路径：撤销行
保留供审计，重新创建 = 轮换同一行的 token。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import NotFoundError, ValidationError
from app.models.artifact import AgentArtifact
from app.models.artifact_share import AgentArtifactShare
from app.services.share_link_service import new_share_token, share_token_digest

# 过期上限：分享是临时协作手段，不允许变相永久公开。
MAX_EXPIRY_DAYS = 90
# token 长度上限：正常 token ~32 字符；超长输入直接按无效处理（不做哈希）。
MAX_TOKEN_CHARS = 128

_SHARE_INVALID = "分享链接不存在或已失效"


def _now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite 读回的 DateTime 可能不带时区；统一按 UTC 解释后比较。"""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _validate_expiry(expires_in_days: int | None) -> datetime | None:
    """把可选的「天数」换算成过期时间戳；越界一律 400（最多 90 天）。"""
    if expires_in_days is None:
        return None
    if isinstance(expires_in_days, bool) or not isinstance(expires_in_days, int):
        raise ValidationError("过期天数必须是整数", field="expires_in_days", found=expires_in_days)
    if expires_in_days < 1 or expires_in_days > MAX_EXPIRY_DAYS:
        raise ValidationError(
            f"过期天数必须在 1 到 {MAX_EXPIRY_DAYS} 之间",
            field="expires_in_days",
            min=1,
            max=MAX_EXPIRY_DAYS,
            found=expires_in_days,
        )
    return _now() + timedelta(days=expires_in_days)


def share_is_active(share: AgentArtifactShare) -> bool:
    """分享当前是否可公开读取：未撤销且未过期。"""
    if share.revoked_at is not None:
        return False
    expires_at = _as_utc(share.expires_at)
    return expires_at is None or expires_at > _now()


def share_status(share: AgentArtifactShare | None) -> dict[str, object]:
    """管理面对外的状态投影（不含 token 摘要）。"""
    if share is None:
        return {
            "shared": False,
            "created_at": None,
            "expires_at": None,
            "revoked_at": None,
        }
    return {
        "shared": share_is_active(share),
        "created_at": _iso(share.created_at),
        "expires_at": _iso(share.expires_at),
        "revoked_at": _iso(share.revoked_at),
    }


async def _owned_artifact(
    session: AsyncSession,
    *,
    artifact_id: str,
    organization_id: int,
    user_id: int,
) -> AgentArtifact:
    """严格按所有者取工件（组织 + 用户双重限定）；他人/跨组织/不存在均 404。"""
    row = (
        (
            await session.execute(
                select(AgentArtifact).where(
                    AgentArtifact.id == artifact_id,
                    AgentArtifact.organization_id == organization_id,
                    AgentArtifact.user_id == user_id,
                    AgentArtifact.status == "ready",
                )
            )
        )
        .scalars()
        .first()
    )
    if row is None:
        raise NotFoundError("工件不存在", resource="artifact", resource_id=artifact_id)
    return row


async def _share_for_artifact(session: AsyncSession, artifact_id: str) -> AgentArtifactShare | None:
    return (
        (
            await session.execute(
                select(AgentArtifactShare).where(AgentArtifactShare.artifact_id == artifact_id)
            )
        )
        .scalars()
        .first()
    )


async def create_or_rotate_share(
    session: AsyncSession,
    *,
    artifact_id: str,
    organization_id: int,
    user_id: int,
    expires_in_days: int | None = None,
) -> tuple[AgentArtifactShare, str]:
    """创建或轮换分享链接；返回 ``(分享行, token 明文)``。

    同一 artifact 版本重复创建 → 覆盖 ``token_hash``（旧链接立即失效），并
    重置撤销/过期状态。token 明文只在此处返回一次。
    """
    artifact = await _owned_artifact(
        session,
        artifact_id=artifact_id,
        organization_id=organization_id,
        user_id=user_id,
    )
    expires_at = _validate_expiry(expires_in_days)
    token = new_share_token()
    digest = share_token_digest(token)
    now = _now()

    share = await _share_for_artifact(session, artifact.id)
    if share is None:
        share = AgentArtifactShare(
            id=str(uuid.uuid4()),
            organization_id=organization_id,
            user_id=user_id,
            artifact_id=artifact.id,
            token_hash=digest,
            created_by=user_id,
            created_at=now,
            expires_at=expires_at,
            revoked_at=None,
        )
        session.add(share)
        try:
            await session.flush()
        except IntegrityError:
            # 并发双创建：对方已插入同一 artifact 的行 → 回滚后转为轮换。
            await session.rollback()
            share = await _share_for_artifact(session, artifact_id)
            if share is None:  # pragma: no cover — 约束冲突后行必然存在
                raise
            share.token_hash = digest
            share.expires_at = expires_at
            share.revoked_at = None
            share.created_by = user_id
            share.created_at = now
            await session.flush()
    else:
        # 轮换：旧 token 摘要被覆盖 → 旧链接立即失效；撤销状态一并重置。
        share.token_hash = digest
        share.expires_at = expires_at
        share.revoked_at = None
        share.created_by = user_id
        share.created_at = now
        await session.flush()
    return share, token


async def get_share_status(
    session: AsyncSession,
    *,
    artifact_id: str,
    organization_id: int,
    user_id: int,
) -> dict[str, object]:
    """所有者查询分享状态（不返回 token，token 只在创建/轮换时给一次）。"""
    artifact = await _owned_artifact(
        session,
        artifact_id=artifact_id,
        organization_id=organization_id,
        user_id=user_id,
    )
    return share_status(await _share_for_artifact(session, artifact.id))


async def revoke_share(
    session: AsyncSession,
    *,
    artifact_id: str,
    organization_id: int,
    user_id: int,
) -> dict[str, object]:
    """撤销分享（幂等：已撤销再撤销仍成功）；从未分享过 → 404。"""
    artifact = await _owned_artifact(
        session,
        artifact_id=artifact_id,
        organization_id=organization_id,
        user_id=user_id,
    )
    share = await _share_for_artifact(session, artifact.id)
    if share is None:
        raise NotFoundError("分享不存在", resource="artifact_share", resource_id=artifact_id)
    if share.revoked_at is None:
        share.revoked_at = _now()
        await session.flush()
    return share_status(share)


async def resolve_public_share(
    session: AsyncSession, token: str
) -> tuple[AgentArtifact, AgentArtifactShare]:
    """按 token 解析公开分享；无效一律同一个 404。

    只对摘要做等值查询（token 原文不进入日志/响应）；撤销、过期、token 不存在、
    工件不可读全部映射为同一条 ``NotFoundError``。返回的工件是**分享绑定的那
    一版**（``artifact_id``），与版本链头无关。
    """
    if not token or len(token) > MAX_TOKEN_CHARS:
        raise NotFoundError(_SHARE_INVALID)
    share = (
        (
            await session.execute(
                select(AgentArtifactShare).where(
                    AgentArtifactShare.token_hash == share_token_digest(token)
                )
            )
        )
        .scalars()
        .first()
    )
    if share is None or not share_is_active(share):
        raise NotFoundError(_SHARE_INVALID)
    artifact = (
        (
            await session.execute(
                select(AgentArtifact).where(
                    AgentArtifact.id == share.artifact_id,
                    AgentArtifact.organization_id == share.organization_id,
                    AgentArtifact.status == "ready",
                )
            )
        )
        .scalars()
        .first()
    )
    if artifact is None:
        raise NotFoundError(_SHARE_INVALID)
    return artifact, share


def public_artifact_dict(
    artifact: AgentArtifact, share: AgentArtifactShare, token: str
) -> dict[str, object]:
    """公开元数据投影：只含展示所需字段。

    **刻意不含** id / organization_id / user_id / conversation_id / task_id /
    agent_id / 内容哈希 / 版本链 / 来源工具——链接持有者不需要也无法借此定位
    内部对象。``content_url`` 用 token 本身作为能力凭证。
    """
    return {
        "title": artifact.title,
        "kind": artifact.kind,
        "filename": artifact.filename,
        "mime_type": artifact.mime_type,
        "version_no": artifact.version_no,
        "created_at": _iso(artifact.created_at),
        "content_url": f"/api/v1/public/artifacts/{token}/content",
        "expires_at": _iso(share.expires_at),
    }
