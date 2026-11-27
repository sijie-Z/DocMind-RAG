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

访问统计（批次十一）：公开**正文**成功读取后 best-effort 累加
``access_count`` 并更新 ``last_accessed_at``；只存聚合值，不存访问者身份/IP/
UA/正文。元数据端点不计数（一次浏览只算一次）。计数失败只记 warning，不影响
公开读取的 200 响应。
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import NotFoundError, ValidationError
from app.models.artifact import AgentArtifact
from app.models.artifact_share import AgentArtifactShare
from app.services.share_link_service import new_share_token, share_token_digest

logger = logging.getLogger(__name__)

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
    """ISO 输出统一带 UTC 偏移（DB 回读的 naive 值按 UTC 解释）。

    分享时间会直接展示给用户：创建/轮换响应里是 aware datetime，而 MySQL
    DATETIME / SQLite 回读是 naive；若不归一，前端 ``new Date()`` 会按浏览器
    本地时区解释 naive 字符串，导致过期提示随查看路径漂移。
    """
    normalized = _as_utc(value)
    return normalized.isoformat() if normalized else None


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
    """管理面对外的状态投影（不含 token 摘要）。

    访问统计与状态一起返回：历史行 ``access_count`` 为 NULL 时按 0 披露
    （「从未被访问」与「计数未知」对所有者是同一件事）。
    """
    if share is None:
        return {
            "shared": False,
            "created_at": None,
            "expires_at": None,
            "revoked_at": None,
            "access_count": 0,
            "last_accessed_at": None,
        }
    return {
        "shared": share_is_active(share),
        "created_at": _iso(share.created_at),
        "expires_at": _iso(share.expires_at),
        "revoked_at": _iso(share.revoked_at),
        "access_count": share.access_count or 0,
        "last_accessed_at": _iso(share.last_accessed_at),
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
) -> tuple[AgentArtifactShare, str, bool]:
    """创建或轮换分享链接；返回 ``(分享行, token 明文, 是否为轮换)``。

    同一 artifact 版本重复创建 → 覆盖 ``token_hash``（旧链接立即失效），并
    重置撤销/过期状态。token 明文只在此处返回一次。``rotated`` 供调用方
    区分审计动作（``share_artifact`` / ``rotate_artifact_share``）。
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
    rotated = share is not None
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
            # 竞赛失败方覆盖的是对方刚生效的 token（对方链接立即失效），
            # 语义上是轮换，审计动作也按轮换记。
            await session.rollback()
            rotated = True
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
    return share, token, rotated


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
) -> tuple[AgentArtifactShare, dict[str, object]]:
    """撤销分享（幂等：已撤销再撤销仍成功）；从未分享过 → 404。

    返回 ``(分享行, 状态投影)``：分享行供调用方写审计（share_id），状态供响应。
    """
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
    return share, share_status(share)


async def record_public_access(
    session: AsyncSession,
    share: AgentArtifactShare,
    now: datetime | None = None,
) -> None:
    """公开正文成功读取后累加访问统计（best-effort，绝不抛出）。

    用 SQL 表达式 ``COALESCE(access_count, 0) + 1`` 原子自增，避免 ORM 读改写
    在并发访问下丢失计数；``last_accessed_at`` 同步更新为该次访问时间。

    语义边界：只记录「正文被成功读取」这一聚合事实，不记录访问者身份/IP/UA/
    正文。失败只写 warning 并回滚本会话，调用方必须继续返回正常响应。
    本方法自带提交：公开读取路径没有其它待提交写操作，计数是独立事务。
    """
    try:
        await session.execute(
            update(AgentArtifactShare)
            .where(AgentArtifactShare.id == share.id)
            .values(
                access_count=func.coalesce(AgentArtifactShare.access_count, 0) + 1,
                last_accessed_at=now or _now(),
            )
            .execution_options(synchronize_session=False)
        )
        await session.commit()
    except Exception as e:  # noqa: BLE001 — 计数是旁路，任何失败都不得影响读取
        logger.warning(f"Record artifact share access failed: {type(e).__name__}: {e}")
        try:
            await session.rollback()
        except Exception as rollback_error:  # pragma: no cover — 会话已不可用时仅记录
            logger.warning(f"Rollback after access record failure failed: {rollback_error}")


async def list_active_shares(
    session: AsyncSession,
    *,
    organization_id: int,
    user_id: int,
) -> list[dict[str, object]]:
    """当前用户在本组织内的有效分享（未撤销、未过期、工件可读）。

    严格 owner-only（组织 + 用户双限定），与状态/撤销同一授权口径。返回展示
    白名单：标题/类型/版本/大小/分享 id/时间/计数；**不含** token 明文或摘要、
    组织/用户/会话/任务/区链等内部字段。``artifact_id`` 仅用于所有者从列表
    发起撤销（撤销端点按工件 id 定位）。
    """
    now = _now()
    rows = (
        await session.execute(
            select(AgentArtifactShare, AgentArtifact)
            .join(AgentArtifact, AgentArtifact.id == AgentArtifactShare.artifact_id)
            .where(
                AgentArtifactShare.organization_id == organization_id,
                AgentArtifactShare.user_id == user_id,
                AgentArtifactShare.revoked_at.is_(None),
                or_(AgentArtifactShare.expires_at.is_(None), AgentArtifactShare.expires_at > now),
                AgentArtifact.organization_id == organization_id,
                AgentArtifact.status == "ready",
            )
            .order_by(AgentArtifactShare.created_at.desc())
        )
    ).all()
    return [
        {
            "share_id": share.id,
            "artifact_id": artifact.id,
            "title": artifact.title,
            "kind": artifact.kind,
            "version_no": artifact.version_no,
            "file_size": artifact.file_size,
            "created_at": _iso(share.created_at),
            "expires_at": _iso(share.expires_at),
            "access_count": share.access_count or 0,
            "last_accessed_at": _iso(share.last_accessed_at),
        }
        for share, artifact in rows
    ]


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
