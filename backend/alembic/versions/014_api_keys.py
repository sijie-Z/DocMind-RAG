"""API Key 表（issue #480 §5.1）。

Revision ID: 014_api_keys
Revises: 012_session_fork

变更：
- 新建 ``api_keys``：bcrypt 哈希 + 明文前缀定位；``scopes`` 为 JSON 数组。
- 与 ``app/models/api_key.py`` 严格同形（create_all 与迁移两条建库路径的
  COLUMN_TYPE / 默认值 / 可空性必须一致，见 tests/integration/test_schema_path_parity.py）。
- 与 ``users.api_key``（旧的明文 ``sk-`` 列）完全独立；旧列保留为 deprecated，
  本迁移不动它。

兼容窗口：纯增量建表；旧代码不读该表，可滚动发布。downgrade 直接删表
（丢失已签发 key 的哈希与审计元数据——回滚前如需保留，先导出）。

依赖：``013_webhooks``（#478）——同步合并轮已把 ``down_revision`` 从
``012_session_fork`` 顺延到 013，保持单 head（禁止多 head）。
"""

import sqlalchemy as sa

from alembic import op

revision = "014_api_keys"
down_revision = "013_webhooks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "org_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=True,
            index=True,
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("prefix", sa.String(12), nullable=False, index=True),
        sa.Column("key_hash", sa.String(255), nullable=False),
        sa.Column("scopes", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("api_keys")
