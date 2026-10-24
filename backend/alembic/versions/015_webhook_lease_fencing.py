"""Webhook 投递租约 fencing（issue #497 §4）。

Revision ID: 015_webhook_lease_fencing
Revises: 014_api_keys

变更（纯增量，向后兼容滚动发布）：
- ``webhook_deliveries.processing_token``（String(36) NULL）：当前处理租约 token，
  每次 poll 领取（含超时重领 / failed 到点重试）轮换为新 UUID；
- ``webhook_deliveries.lease_version``（Integer NOT NULL DEFAULT 0）：租约代数，
  每次领取 +1，便于审计「第几次被领走」。

完成/失败写回使用 ``WHERE id=:id AND processing_token=:token AND status=...``
条件更新（见 ``app/services/webhook_service.py``）；超时重领后旧 worker 的
迟到写回 rowcount 为 0，不再写状态/会话/消息（防双写）。

与 ``app/models/webhook.py`` 严格同形：``tests/integration/test_schema_path_parity.py``
在真实 MySQL 上逐列比对迁移路径与 ``create_all`` 路径（COLUMN_TYPE /
IS_NULLABLE / COLUMN_DEFAULT / EXTRA）。``lease_version`` 在两侧都带
``server_default="0"``，存量行升级后即为 0。

downgrade 对称删除两列（仅丢 fencing 信息，投递行与会话不受影响）。
"""

import sqlalchemy as sa

from alembic import op

revision = "015_webhook_lease_fencing"
down_revision = "014_api_keys"
branch_labels = None
depends_on = None


def _dialect_name() -> str:
    return op.get_bind().dialect.name


def upgrade() -> None:
    op.add_column(
        "webhook_deliveries",
        sa.Column(
            "processing_token",
            sa.String(length=36),
            nullable=True,
            comment="当前处理租约 token（fencing；每次领取轮换）",
        ),
    )
    op.add_column(
        "webhook_deliveries",
        sa.Column(
            "lease_version",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="租约代数（每次领取+1）",
        ),
    )


def downgrade() -> None:
    if _dialect_name() == "sqlite":
        with op.batch_alter_table("webhook_deliveries") as batch:
            batch.drop_column("lease_version")
            batch.drop_column("processing_token")
    else:
        op.drop_column("webhook_deliveries", "lease_version")
        op.drop_column("webhook_deliveries", "processing_token")
