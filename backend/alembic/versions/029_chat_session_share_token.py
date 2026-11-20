"""会话只读分享快照：chat_sessions 增加分享 token 摘要与创建时间。

share_token 只在创建/撤销响应中原文返回一次；数据库仅保存 SHA-256 摘要，
因此日志、审计表与数据库泄露都不能直接还原可用的分享链接。撤销即置 NULL，
令牌立即失效；会话删除后行消失，摘要随之消失。

SQLite/MySQL 均可升级/降级；列与索引均做存在性探测，兼容半应用状态。
"""

import sqlalchemy as sa

from alembic import op

revision = "029_chat_session_share_token"
down_revision = "028_document_visibility_acl"
branch_labels = None
depends_on = None

TABLE = "chat_sessions"
INDEX = "ix_chat_sessions_share_token_hash"


def _has_column(name: str) -> bool:
    return any(c["name"] == name for c in sa.inspect(op.get_bind()).get_columns(TABLE))


def _has_index() -> bool:
    return any(i["name"] == INDEX for i in sa.inspect(op.get_bind()).get_indexes(TABLE))


def upgrade() -> None:
    if not _has_column("share_token_hash"):
        op.add_column(
            TABLE,
            sa.Column(
                "share_token_hash", sa.String(64), nullable=True, comment="分享 token SHA-256 摘要"
            ),
        )
    if not _has_column("share_created_at"):
        op.add_column(
            TABLE,
            sa.Column(
                "share_created_at",
                sa.DateTime(timezone=True),
                nullable=True,
                comment="分享链接创建时间",
            ),
        )
    if not _has_index():
        op.create_index(INDEX, TABLE, ["share_token_hash"])


def downgrade() -> None:
    if _has_index():
        op.drop_index(INDEX, table_name=TABLE)
    if _has_column("share_created_at"):
        op.drop_column(TABLE, "share_created_at")
    if _has_column("share_token_hash"):
        op.drop_column(TABLE, "share_token_hash")
