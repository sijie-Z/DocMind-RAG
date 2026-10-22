"""会话 fork/seed 数据模型（issue #476）。

Revision ID: 012_session_fork
Revises: 011_organization_settings

变更（与 app/models/chat.py、app/models/agent_task.py 严格同形——schema 两条
建库路径由 tests/integration/test_schema_path_parity.py 逐列比对）：

1. ``chat_messages.seq``：会话内单调序号（NOT NULL）+ 唯一约束
   ``uq_chat_messages_session_seq (session_id, seq)``。
   **回填策略**：存量行按 ``(created_at, id)`` 升序、按会话分区编号 1..N。
   先加可空列 → Python 侧回填（逐方言都安全；MySQL 禁止 UPDATE 目标表出现在
   子查询里，窗口函数方案在 SQLite/MySQL 的 UPDATE 语法也不通用）→ 收紧
   NOT NULL → 建唯一约束。
2. ``chat_sessions``：``parent_session_id`` / ``root_session_id`` /
   ``fork_point_message_id`` / ``fork_reason``。``root_session_id`` 对存量行
   回填为自身 id（根会话 = 自身；fork 子会话指向最初会话）。parent/root 建索引；
   fork_point 有意不加外键——父会话删除后谱系仍需可查。
3. ``agent_tasks.session_id``：run 与聊天会话的绑定（可空：历史任务行早于该列，
   无法可靠回填——只有快照信封里有 session_id，不作为可查询列）。建索引。
4. ``agent_tasks.status`` 枚举扩展 ``PAUSED``（fork 克隆出的快照任务：可 resume，
   但不属于"运行中任务"）。成员顺序与模型定义一致，PAUSED 追加在末尾
   （MySQL ENUM 顺序参与 COLUMN_TYPE 比对，不能插队）。

兼容窗口：纯增量列/约束，旧代码不读新列即可继续运行；唯一约束要求新代码
（及任何带外写入）必须给 seq 赋值——本项目全部写入点已经过
``app.services.chat_seq.next_message_seq``。

downgrade 有损：PAUSED 行先转 ``WAITING_HUMAN``（语义最近的旧可恢复态：
"等待人工决定是否继续"），再收窄枚举；seq / 谱系列被删除（分叉子会话退化为
普通会话，父/子字段丢失；消息本身保留）。
"""

import sqlalchemy as sa

from alembic import op

revision = "012_session_fork"
down_revision = "011_organization_settings"
branch_labels = None
depends_on = None

# 与 app/models/agent_task.py 的 TaskStatus 定义顺序一致（create_all 与迁移
# 产出的 MySQL ENUM 列定义必须同形；PAUSED 追加在末尾）。
_OLD_STATUS_NAMES = (
    "PENDING",
    "RUNNING",
    "WAITING_TOOL",
    "WAITING_APPROVAL",
    "WAITING_PLAN_APPROVAL",
    "WAITING_HUMAN",
    "FAILED",
    "COMPLETED",
    "ABANDONED",
    "CANCELLED",
    "REJECTED",
)
_NEW_STATUS_NAMES = (*_OLD_STATUS_NAMES, "PAUSED")


def _old_enum() -> sa.Enum:
    return sa.Enum(*_OLD_STATUS_NAMES, name="taskstatus")


def _new_enum() -> sa.Enum:
    return sa.Enum(*_NEW_STATUS_NAMES, name="taskstatus")


def _dialect_name() -> str:
    return op.get_bind().dialect.name


def _backfill_message_seq() -> None:
    """按 (created_at, id) 回填每个会话的 seq（1..N）。"""
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, session_id FROM chat_messages ORDER BY session_id, created_at, id")
    ).fetchall()
    params: list[dict] = []
    current_session: str | None = None
    seq = 0
    for row_id, session_id in rows:
        if session_id != current_session:
            current_session = session_id
            seq = 1
        else:
            seq += 1
        params.append({"seq": seq, "id": row_id})
    if params:
        # executemany：一次往返写完全部回填（空表时跳过）。
        bind.execute(sa.text("UPDATE chat_messages SET seq = :seq WHERE id = :id"), params)


def upgrade() -> None:
    # ── 1) chat_messages.seq ────────────────────────────────────────────
    op.add_column("chat_messages", sa.Column("seq", sa.Integer(), nullable=True))
    _backfill_message_seq()
    if _dialect_name() == "sqlite":
        with op.batch_alter_table("chat_messages") as batch:
            batch.alter_column("seq", existing_type=sa.Integer(), nullable=False)
            batch.create_unique_constraint("uq_chat_messages_session_seq", ["session_id", "seq"])
    else:
        op.alter_column("chat_messages", "seq", existing_type=sa.Integer(), nullable=False)
        op.create_unique_constraint(
            "uq_chat_messages_session_seq", "chat_messages", ["session_id", "seq"]
        )

    # ── 2) chat_sessions 谱系列 ─────────────────────────────────────────
    op.add_column(
        "chat_sessions", sa.Column("parent_session_id", sa.String(length=36), nullable=True)
    )
    op.add_column(
        "chat_sessions", sa.Column("root_session_id", sa.String(length=36), nullable=True)
    )
    op.add_column(
        "chat_sessions",
        sa.Column("fork_point_message_id", sa.String(length=36), nullable=True),
    )
    op.add_column("chat_sessions", sa.Column("fork_reason", sa.String(length=100), nullable=True))
    # 存量会话：根会话 = 自身（与模型/服务"root_session_id or self.id"口径一致）。
    op.get_bind().execute(
        sa.text("UPDATE chat_sessions SET root_session_id = id WHERE root_session_id IS NULL")
    )
    op.create_index("ix_chat_sessions_parent_session_id", "chat_sessions", ["parent_session_id"])
    op.create_index("ix_chat_sessions_root_session_id", "chat_sessions", ["root_session_id"])

    # ── 3) agent_tasks.session_id ───────────────────────────────────────
    op.add_column("agent_tasks", sa.Column("session_id", sa.String(length=36), nullable=True))
    op.create_index("ix_agent_tasks_session_id", "agent_tasks", ["session_id"])

    # ── 4) agent_tasks.status + PAUSED ─────────────────────────────────
    with op.batch_alter_table("agent_tasks") as batch:
        batch.alter_column(
            "status",
            existing_type=_old_enum(),
            type_=_new_enum(),
            existing_nullable=False,
            nullable=False,
        )


def downgrade() -> None:
    bind = op.get_bind()

    # PAUSED 在旧枚举里没有对应值：先转成语义最近的旧可恢复态（等待人工决定）。
    bind.execute(sa.text("UPDATE agent_tasks SET status = 'WAITING_HUMAN' WHERE status = 'PAUSED'"))
    with op.batch_alter_table("agent_tasks") as batch:
        batch.alter_column(
            "status",
            existing_type=_new_enum(),
            type_=_old_enum(),
            existing_nullable=False,
            nullable=False,
        )
    op.drop_index("ix_agent_tasks_session_id", table_name="agent_tasks")
    op.drop_column("agent_tasks", "session_id")

    op.drop_index("ix_chat_sessions_root_session_id", table_name="chat_sessions")
    op.drop_index("ix_chat_sessions_parent_session_id", table_name="chat_sessions")
    if _dialect_name() == "sqlite":
        with op.batch_alter_table("chat_sessions") as batch:
            for column in (
                "fork_reason",
                "fork_point_message_id",
                "root_session_id",
                "parent_session_id",
            ):
                batch.drop_column(column)
        with op.batch_alter_table("chat_messages") as batch:
            batch.drop_constraint("uq_chat_messages_session_seq", type_="unique")
            batch.drop_column("seq")
    else:
        for column in (
            "fork_reason",
            "fork_point_message_id",
            "root_session_id",
            "parent_session_id",
        ):
            op.drop_column("chat_sessions", column)
        op.drop_constraint("uq_chat_messages_session_seq", "chat_messages", type_="unique")
        op.drop_column("chat_messages", "seq")
