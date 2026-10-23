"""API Key 数据模型（issue #480）。

对外集成的可认证凭据：只存 bcrypt 哈希，明文仅在创建时返回一次。
旧 ``users.api_key``（明文 ``sk-`` 列）标记 deprecated，新认证只接受 ``dm_`` 前缀。
"""

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.database import Base


class ApiKey(Base):
    """用户 API Key（``dm_`` 前缀 + bcrypt 哈希）。"""

    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    org_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("organizations.id"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(100))
    # 明文前 8 位（``dm_`` + 5 位随机），用于按前缀定位候选行（列宽留余量）。
    prefix: Mapped[str] = mapped_column(String(12), index=True)
    key_hash: Mapped[str] = mapped_column(String(255))
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试辅助
        return f"<ApiKey(id={self.id}, user_id={self.user_id}, prefix='{self.prefix}')>"
