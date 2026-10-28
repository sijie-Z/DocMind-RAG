"""已删除会话墓碑：防止迟到 finalize 用同一 id 重建会话。

Revision ID: 019_deleted_chat_sessions
Revises: 018_rag_source_provenance

删除会话是硬删除（消息 + 会话行）。本表与删除在同一事务写入墓碑；
保存侧与 ``get_or_create_session`` 在“缺失会话是否允许创建”之前查询墓碑，
命中即拒绝，避免 DELETE 之后迟到的保存把会话按原 id 复活。

刻意不加外键：父行已删除，墓碑必须独立存活；user_id/organization_id 仅审计。
downgrade 仅删除本表，不影响任何业务数据。
"""

import sqlalchemy as sa

from alembic import op

revision = "019_deleted_chat_sessions"
down_revision = "018_rag_source_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "deleted_chat_sessions",
        sa.Column("session_id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=True),
        sa.Column(
            "deleted_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        comment="已删除会话墓碑（防迟到保存复活）",
    )
    op.create_index(
        "ix_deleted_chat_sessions_user_id",
        "deleted_chat_sessions",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_deleted_chat_sessions_user_id", table_name="deleted_chat_sessions")
    op.drop_table("deleted_chat_sessions")
