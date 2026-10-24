"""Webhook runtime 数据模型（issue #478）。

两张表：

- ``webhook_rules``：外部事件 → Agent 会话的规则（组织隔离；secret 只存 Fernet
  密文，轮换期保留旧密文 24h）。
- ``webhook_deliveries``：入站投递的持久化 outbox。端点只写一行 ``received`` 并
  快速返回，后台 poll 循环用 ``FOR UPDATE SKIP LOCKED`` 领取处理；投递状态机
  ``received → processing → succeeded / failed``（failed 可自动重试，终态可被
  发送方用同一幂等键重投）。

与 issue §5.1 的字段差异（有意为之，见 ADR-005）：``webhook_deliveries`` 增加
``payload`` 列。issue 的字段清单只有 ``payload_hash``，但异步 outbox 处理必须
在请求结束之后还原 payload（渲染 prompt / 解析 session_key），没有该列时
poller 无法处理任何投递。``payload`` 存原始 JSON 文本，``payload_hash`` 仍是
原始字节的 sha256（审计/去重辅助；幂等键是 ``webhook-id`` 本身，见 issue #497）。

``processing_token`` / ``lease_version`` 为 issue #497 新增的租约 fencing 列
（迁移 ``015_webhook_lease_fencing``）：领取时轮换 token 并代数 +1，完成/失败
写回都带 token + status 条件更新，超时重领后的旧 worker 写不出任何状态或消息。
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects import mysql
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from app.core.database import Base

# MySQL 的 TEXT 只有 64 KiB，而 webhook body 上限默认 1 MiB（WEBHOOK_MAX_BODY_BYTES）。
# payload 必须用 MEDIUMTEXT（16 MiB），否则合法签名、未超限的请求会在落库时
# DataError 500（评审 blocker B1）。SQLite 无长度限制，保持 TEXT。
_PAYLOAD_TEXT = Text().with_variant(mysql.MEDIUMTEXT(), "mysql")


def _uuid_str() -> str:
    return str(uuid.uuid4())


class WebhookProvider(enum.Enum):
    """规则提供方（本期 generic 走 Standard Webhooks；github 保留适配位）。"""

    GENERIC = "generic"
    GITHUB = "github"


class WebhookSessionMode(enum.Enum):
    """投递 → 会话的映射方式。"""

    NEW = "new"  # 每次投递建新会话
    REUSE = "reuse"  # 按 (rule_id, payload.session_key) 复用


class WebhookDeliveryStatus(enum.Enum):
    """投递状态机。

    received   — 已受理，等待 poll 领取
    processing — poll 已领取（processing_started_at 即租约起点，超 10 分钟可重领）
    succeeded  — 会话已成功落库（终态；同幂等键重投返回 200 duplicate）
    failed     — 处理失败；next_attempt_at 非空 = 还会自动重试，
                 next_attempt_at 为空 = 三次失败后的终态（发送方同 id 重投可重置）
    """

    RECEIVED = "received"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class WebhookRule(Base):
    __tablename__ = "webhook_rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    org_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("organizations.id"), index=True, comment="组织ID"
    )
    created_by: Mapped[int] = mapped_column(
        Integer, ForeignKey("users.id"), comment="服务账号（规则创建者，会话以此身份运行）"
    )
    name: Mapped[str] = mapped_column(String(100), comment="规则名")
    provider: Mapped[WebhookProvider] = mapped_column(
        Enum(WebhookProvider), default=WebhookProvider.GENERIC, comment="提供方"
    )
    # 只存 Fernet 密文；明文 secret 仅在创建/轮换响应中出现一次。
    secret_encrypted: Mapped[str] = mapped_column(Text, comment="Fernet 密文（当前 secret）")
    secret_prev_encrypted: Mapped[str | None] = mapped_column(
        Text, comment="轮换期旧 secret 密文（24h 内验签接受）"
    )
    secret_rotated_at: Mapped[datetime | None] = mapped_column(DateTime, comment="最近一次轮换时间")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否启用")
    prompt_template: Mapped[str] = mapped_column(
        Text, comment="提示词模板；仅允许 {payload}/{rule_id}/{delivery_id}"
    )
    session_mode: Mapped[WebhookSessionMode] = mapped_column(
        Enum(WebhookSessionMode), default=WebhookSessionMode.NEW, comment="会话模式"
    )
    rate_limit_per_minute: Mapped[int] = mapped_column(
        Integer, default=60, comment="每规则+IP 每分钟限流"
    )
    max_body_bytes: Mapped[int] = mapped_column(
        Integer, default=1048576, comment="该规则允许的最大请求体字节数"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), comment="创建时间"
    )
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, onupdate=func.now(), comment="更新时间"
    )

    deliveries: Mapped[list[WebhookDelivery]] = relationship(
        "WebhookDelivery", back_populates="rule", cascade="all, delete-orphan"
    )


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        # DB 唯一约束是幂等的最终仲裁（Redis SET NX 只是快路径）。
        UniqueConstraint("rule_id", "idempotency_key", name="uq_webhook_deliveries_rule_idem"),
        # poll 领取条件：status + 到点时间 / 处理租约超时。
        Index("ix_webhook_deliveries_status_next_attempt", "status", "next_attempt_at"),
        Index("ix_webhook_deliveries_status_processing_started", "status", "processing_started_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid_str)
    rule_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("webhook_rules.id", ondelete="CASCADE"), index=True, comment="规则ID"
    )
    org_id: Mapped[int] = mapped_column(
        Integer, index=True, comment="组织ID（冗余，便于按组织查询）"
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(128), comment="幂等键（Standard Webhooks 必需头 webhook-id，≤128）"
    )
    status: Mapped[WebhookDeliveryStatus] = mapped_column(
        Enum(WebhookDeliveryStatus), default=WebhookDeliveryStatus.RECEIVED, comment="投递状态"
    )
    payload_hash: Mapped[str] = mapped_column(String(64), comment="原始 body 的 sha256")
    # issue §5.1 未列该列；没有它 outbox poller 无法在请求结束后还原 payload。
    payload: Mapped[str] = mapped_column(_PAYLOAD_TEXT, comment="原始 JSON 文本（poller 渲染用）")
    session_id: Mapped[str | None] = mapped_column(String(36), comment="处理出的会话ID")
    error: Mapped[str | None] = mapped_column(Text, comment="失败摘要（脱敏、限长）")
    attempts: Mapped[int] = mapped_column(Integer, default=0, comment="已开始的尝试次数")
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime, comment="下次自动重试时间；NULL 且 failed = 终态"
    )
    processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime, comment="processing 租约起点（超 10 分钟可重领）"
    )
    # issue #497 §4：租约 fencing。每次领取轮换 token 并 lease_version+1；完成/
    # 失败写回都带 (processing_token, status) 条件，旧 worker 的迟到写回落空。
    processing_token: Mapped[str | None] = mapped_column(
        String(36), comment="当前处理租约 token（fencing；每次领取轮换）"
    )
    lease_version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0", default=0, comment="租约代数（每次领取+1）"
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), comment="受理时间"
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, comment="处理完成时间")
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, onupdate=func.now())

    rule: Mapped[WebhookRule] = relationship("WebhookRule", back_populates="deliveries")
