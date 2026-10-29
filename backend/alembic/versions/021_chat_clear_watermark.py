"""会话清空水位：messages_cleared_at。

Revision ID: 021_chat_clear_watermark
Revises: 020_review_document_meta

清空消息保留会话；新增水位供保存侧判断「本轮运行开始后是否被清空」，
避免在途 RAG/Agent 的迟到保存把旧问答追加回已清空会话。
downgrade 仅移除本列，不动任何消息数据。
"""

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision = "021_chat_clear_watermark"
down_revision = "020_review_document_meta"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_sessions",
        sa.Column(
            "messages_cleared_at",
            sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"),
            nullable=True,
            comment="最近一次清空消息时间（迟到保存不得越过）",
        ),
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("chat_sessions") as batch:
            batch.drop_column("messages_cleared_at")
    else:
        op.drop_column("chat_sessions", "messages_cleared_at")
