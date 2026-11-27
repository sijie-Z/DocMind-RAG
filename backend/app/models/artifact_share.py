"""工件只读分享链接持久层（批次九）。

一行 = 一个**具体工件版本**的分享记录（``artifact_id`` 指向 ``agent_artifact``
中不可变的内容行，不跟随版本链头）。token 只存 SHA-256 摘要，明文只在创建/
轮换响应中返回一次；失效状态由 ``expires_at`` / ``revoked_at`` 表达。

生命周期：撤销只写 ``revoked_at``（行保留供审计），不提供删除路径；同一
artifact 版本至多一条记录（``uq_agent_artifact_share_artifact``），重复创建
走「轮换 token」——旧 token 的摘要被覆盖，旧链接立即失效。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.database import Base


class AgentArtifactShare(Base):
    """一个工件版本的只读分享链接（token 摘要 + 过期/撤销状态）。"""

    __tablename__ = "agent_artifact_share"
    __table_args__ = (
        # 一版本一记录：重复创建 = 轮换同一行的 token（不堆历史链接）。
        UniqueConstraint("artifact_id", name="uq_agent_artifact_share_artifact"),
        # token 摘要全局唯一：公开端点按摘要等值查询，命中即唯一定位。
        UniqueConstraint("token_hash", name="uq_agent_artifact_share_token_hash"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    organization_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("organizations.id"),
        nullable=False,
        index=True,
        comment="所属组织（租户隔离键）",
    )
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id"),
        nullable=False,
        index=True,
        comment="工件所有者（分享管理仅限本人）",
    )
    artifact_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("agent_artifact.id"),
        nullable=False,
        comment="绑定的工件版本行（不可变内容，不跟随链头）",
    )
    token_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, comment="分享 token 的 SHA-256 摘要（原文不落库）"
    )
    created_by: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("users.id"),
        nullable=False,
        comment="分享创建人（当前恒等于 user_id，保留审计语义）",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"),
        server_default=func.now(),
        nullable=False,
        comment="分享创建/最近轮换时间",
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="过期时间（空 = 不过期；最长 90 天，服务层校验）",
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="撤销时间（非空即失效；行保留供审计）",
    )
    # 访问统计（批次十一）：只存聚合值，不存访问者身份/IP/UA/正文。
    # 历史行保持 NULL，服务层统一按 0 处理（不 backfill）。
    access_count: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="公开正文成功读取的累计次数（NULL 视为 0）",
    )
    last_accessed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"),
        nullable=True,
        comment="最近一次成功读取公开正文的时间",
    )
