"""Agent task 级会话消息幂等（single_final/v1）。

Revision ID: 017_agent_message_task_id
Revises: 016_fork_seq_idempotency

- chat_messages.agent_task_id nullable；旧行与旧调用方保持 NULL。
- (session_id, agent_task_id, message_type) UNIQUE；MySQL/SQLite 的 NULL 均可多值。
- downgrade 删除约束与列，不影响既有消息正文或 seq。
"""

import sqlalchemy as sa

from alembic import op

revision = "017_agent_message_task_id"
down_revision = "016_fork_seq_idempotency"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "chat_messages",
        sa.Column("agent_task_id", sa.String(length=36), nullable=True),
    )
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("chat_messages") as batch:
            batch.create_unique_constraint(
                "uq_chat_messages_session_task_type",
                ["session_id", "agent_task_id", "message_type"],
            )
    else:
        op.create_unique_constraint(
            "uq_chat_messages_session_task_type",
            "chat_messages",
            ["session_id", "agent_task_id", "message_type"],
        )


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("chat_messages") as batch:
            batch.drop_constraint("uq_chat_messages_session_task_type", type_="unique")
            batch.drop_column("agent_task_id")
    else:
        op.drop_constraint("uq_chat_messages_session_task_type", "chat_messages", type_="unique")
        op.drop_column("chat_messages", "agent_task_id")
