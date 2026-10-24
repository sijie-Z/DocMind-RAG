"""fork 序号计数器 + 幂等键唯一约束（issue #499）。

Revision ID: 016_fork_seq_idempotency
Revises: 015_webhook_lease_fencing

本迁移是 issue #499（Session Fork 契约收口）的增量：

1. ``chat_sessions.next_message_seq``：消息序号分配计数器（NOT NULL DEFAULT 0），
   回填 ``MAX(chat_messages.seq)``。``chat_seq.next_message_seq`` 改为在会话行锁内
   递增该列（不再每次 max 扫描；仍保留 max 兜底以兼容带外写入/历史行）。
2. ``chat_sessions.idempotency_key`` + 唯一约束 ``(user_id, idempotency_key)``：
   fork 幂等的数据库最终防线（并发双建只允许一个成功，冲突方读回既有子会话）。
   MySQL 与 SQLite 的 UNIQUE 对 NULL 都不做唯一性约束，因此未带幂等键的
   会话行（含全部存量行）不参与该约束，无需部分索引。

兼容窗口：纯增量列/约束，旧代码不读新列可继续运行；``server_default='0'``
保证插入路径无需感知新列。

downgrade 有损：删除两列与唯一约束（幂等键/计数器丢失；fork 行为退回
issue #476 的 check-then-insert）。
"""

import sqlalchemy as sa

from alembic import op

revision = "016_fork_seq_idempotency"
# 同步轮：#497 已合并，016 接在 015_webhook_lease_fencing 之后（develop 单 head）。
down_revision = "015_webhook_lease_fencing"
branch_labels = None
depends_on = None


def _dialect_name() -> str:
    return op.get_bind().dialect.name


def _backfill_next_message_seq() -> None:
    """按每个会话现有 MAX(seq) 回填计数器（空会话为 0）。"""
    op.get_bind().execute(
        sa.text(
            "UPDATE chat_sessions SET next_message_seq = COALESCE("
            "(SELECT MAX(chat_messages.seq) FROM chat_messages "
            "WHERE chat_messages.session_id = chat_sessions.id), 0)"
        )
    )


def upgrade() -> None:
    op.add_column(
        "chat_sessions",
        sa.Column(
            "next_message_seq",
            sa.Integer(),
            nullable=False,
            server_default="0",
            comment="消息序号分配计数器（最后已分配 seq）",
        ),
    )
    _backfill_next_message_seq()
    op.add_column(
        "chat_sessions",
        sa.Column("idempotency_key", sa.String(length=128), nullable=True),
    )
    if _dialect_name() == "sqlite":
        with op.batch_alter_table("chat_sessions") as batch:
            batch.create_unique_constraint(
                "uq_chat_sessions_user_idempotency_key", ["user_id", "idempotency_key"]
            )
    else:
        op.create_unique_constraint(
            "uq_chat_sessions_user_idempotency_key",
            "chat_sessions",
            ["user_id", "idempotency_key"],
        )


def downgrade() -> None:
    if _dialect_name() == "sqlite":
        with op.batch_alter_table("chat_sessions") as batch:
            batch.drop_constraint("uq_chat_sessions_user_idempotency_key", type_="unique")
            batch.drop_column("idempotency_key")
            batch.drop_column("next_message_seq")
    else:
        op.drop_constraint("uq_chat_sessions_user_idempotency_key", "chat_sessions", type_="unique")
        op.drop_column("chat_sessions", "idempotency_key")
        op.drop_column("chat_sessions", "next_message_seq")
