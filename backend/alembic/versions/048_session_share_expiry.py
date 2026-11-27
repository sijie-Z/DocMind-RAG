"""会话分享过期（批次十二）：``chat_sessions.share_expires_at``。

在既有会话分享机制（``share_token_hash`` + ``share_created_at``）上追加一个
可空列，全部历史行保持 NULL、**不回填、不强制过期**：

- ``share_expires_at IS NULL`` = 永不过期（批次八及以前的链接保持永久有效）；
- 非 NULL 且已到点 = 公开读取与撤销/不存在同一 404，不泄露失效原因；
- 撤销分享时由服务层同时清空 token/created/expires 三列。

设计口径（与批次十一 047 一致）：

- DDL 无 ``server_default``：默认值（NULL = 永久）由服务层表达，避免迁移库与
  ``create_all`` 库列级漂移；
- MySQL 使用 ``DATETIME(fsp=6)`` 变体与 ORM 对齐；SQLite 保留完整精度；
- 幂等 DDL（MySQL 非事务，按列实际存在性执行）；
- downgrade 只删除本迁移追加的一列，不触碰任何既有列/索引/表，也不改写历史行。
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision = "048_session_share_expiry"
down_revision = "047_artifact_share_analytics"
branch_labels = None
depends_on = None

TABLE = "chat_sessions"
COLUMN = "share_expires_at"


def _columns() -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(TABLE)}


def upgrade() -> None:
    if COLUMN not in _columns():
        op.add_column(
            TABLE,
            sa.Column(
                COLUMN,
                sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"),
                nullable=True,
                comment="分享链接过期时间（NULL=永不过期）",
            ),
        )


def downgrade() -> None:
    if COLUMN in _columns():
        op.drop_column(TABLE, COLUMN)
