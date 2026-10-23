"""API Key 服务（issue #480 §5.1）。

- 生成 ``dm_`` + ``secrets.token_urlsafe(32)``，bcrypt 哈希后入库；
- 认证按明文前缀定位候选行，逐个 bcrypt 校验；吊销/过期/禁用用户统一失败；
- 明文只在创建时返回一次，任何列表/日志不出现哈希以外的凭据。
"""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import UTC, datetime

import bcrypt
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.api_key import ApiKey
from app.models.organization import user_organization
from app.models.user import User

logger = logging.getLogger(__name__)

API_KEY_PREFIX = "dm_"
PREFIX_LENGTH = 12  # "dm_" + 9 位随机，列宽 12

# 默认 scope 集合（issue §5.1）：聊天读写、知识检索、JSON-RPC。
# 具体默认值可由 ``API_KEY_DEFAULT_SCOPES`` 配置覆盖（见 base.py）。
DEFAULT_SCOPES: tuple[str, ...] = ("chat:read", "chat:write", "knowledge:read", "rpc")
VALID_SCOPES: frozenset[str] = frozenset(DEFAULT_SCOPES)

# 固定 dummy 哈希：前缀不存在时也执行一次 bcrypt，消除时序侧信道。
_DUMMY_HASH = bcrypt.hashpw(b"dummy-api-key-for-timing", bcrypt.gensalt()).decode("utf-8")


def _default_scopes() -> list[str]:
    """配置默认值（``API_KEY_DEFAULT_SCOPES``）；配置为空时回退常量。"""
    configured = getattr(settings, "API_KEY_DEFAULT_SCOPES", None)
    return list(configured) if configured else list(DEFAULT_SCOPES)


class ApiKeyService:
    """API Key 的创建、认证与吊销。"""

    @staticmethod
    def generate_raw_key() -> str:
        return API_KEY_PREFIX + secrets.token_urlsafe(32)

    @staticmethod
    def hash_key(raw_key: str) -> str:
        return bcrypt.hashpw(raw_key.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    @staticmethod
    def _prefix_of(raw_key: str) -> str:
        return raw_key[:PREFIX_LENGTH]

    async def create_key(
        self,
        db: AsyncSession,
        *,
        user: User,
        name: str,
        scopes: list[str] | None = None,
        org_id: int | None = None,
        expires_at: datetime | None = None,
    ) -> tuple[ApiKey, str]:
        """创建 key；返回 (行, 明文)。明文只在这一次调用返回。"""
        raw_key = self.generate_raw_key()
        record = ApiKey(
            id=str(uuid.uuid4()),
            user_id=user.id,
            org_id=org_id if org_id is not None else user.organization_id,
            name=name[:100],
            prefix=self._prefix_of(raw_key),
            key_hash=self.hash_key(raw_key),
            scopes=list(scopes if scopes is not None else _default_scopes()),
            expires_at=expires_at,
            created_at=datetime.now(UTC),
        )
        db.add(record)
        await db.commit()
        await db.refresh(record)
        return record, raw_key

    async def authenticate(self, raw_key: str, db: AsyncSession) -> User | None:
        """按前缀查候选行并校验；失败返回 None（调用方统一 401）。"""
        if not raw_key.startswith(API_KEY_PREFIX):
            return None

        prefix = self._prefix_of(raw_key)
        result = await db.execute(
            select(ApiKey).where(ApiKey.prefix == prefix, ApiKey.revoked_at.is_(None))
        )
        candidates = list(result.scalars())
        if not candidates:
            # 时序对齐：无候选也做一次真实 bcrypt。
            bcrypt.checkpw(raw_key.encode("utf-8"), _DUMMY_HASH.encode("utf-8"))
            return None

        now = datetime.now(UTC)
        matched: ApiKey | None = None
        for candidate in candidates:
            # 过期判定放在 Python 侧：SQLite/MySQL 对 aware datetime 的比较语义
            # 不一致，避免依赖方言。
            expires_at = candidate.expires_at
            if expires_at is not None:
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=UTC)
                if expires_at <= now:
                    continue
            try:
                if not bcrypt.checkpw(raw_key.encode("utf-8"), candidate.key_hash.encode("utf-8")):
                    continue
            except ValueError:
                continue
            matched = candidate
            break

        if matched is None:
            return None

        user = await db.get(User, matched.user_id)
        if user is None or not user.is_active:
            return None

        # 组织回退必须回源成员表校验（与 JWT 的 token_organization_id 同纪律）：
        # key 有 org 而用户主组织为空时，只有仍是该组织成员才允许借用该组织。
        if matched.org_id is not None and user.organization_id is None:
            member = await db.execute(
                select(user_organization.c.user_id).where(
                    user_organization.c.user_id == user.id,
                    user_organization.c.organization_id == matched.org_id,
                )
            )
            if member.first() is None:
                logger.warning(
                    "api_key_org_membership_missing: user=%s org=%s",
                    user.id,
                    matched.org_id,
                )
                return None

        # 非列属性：供 require_scope 与 get_user_org_id 消费。
        user.api_key_id = matched.id
        user.api_key_scopes = list(matched.scopes or [])
        user.token_organization_id = matched.org_id

        await db.execute(update(ApiKey).where(ApiKey.id == matched.id).values(last_used_at=now))
        return user

    async def list_keys(
        self,
        db: AsyncSession,
        *,
        user_id: int | None = None,
        org_id: int | None = None,
        include_revoked: bool = False,
    ) -> list[ApiKey]:
        stmt = select(ApiKey)
        if user_id is not None:
            stmt = stmt.where(ApiKey.user_id == user_id)
        if org_id is not None:
            stmt = stmt.where(ApiKey.org_id == org_id)
        if not include_revoked:
            stmt = stmt.where(ApiKey.revoked_at.is_(None))
        stmt = stmt.order_by(ApiKey.created_at.desc())
        result = await db.execute(stmt)
        return list(result.scalars())

    async def get_key(self, db: AsyncSession, key_id: str) -> ApiKey | None:
        result = await db.execute(select(ApiKey).where(ApiKey.id == key_id))
        return result.scalar_one_or_none()

    async def revoke_key(self, db: AsyncSession, record: ApiKey) -> None:
        record.revoked_at = datetime.now(UTC)
        await db.commit()


api_key_service = ApiKeyService()
