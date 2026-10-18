"""C3a：工具调用配对持久化 —— agent_tool_calls / agent_tool_results 建表。

背景（app/models/agent_tool_call.py、app/agent/tool_ledger.py）：
工具调用配对（tool/call 与 tool/result）此前只在内存环形缓冲与 SSE 事件流里，
进程崩溃 / 取消 / resume 后无账可核。本迁移新增两张账目表：

- ``agent_tool_calls``   —— 每次调用的登记（REGISTERED）→ 派发（DISPATCHED）
  → 终态，含崩溃/取消时补写的合成终态；
- ``agent_tool_results`` —— 工具返回落账（append-only），``tool_call_pk``
  关联 call 记录。

唯一约束（防重复落账）：calls(task_id, call_id)、calls(task_id,
source_event_seq)、results(task_id, call_id)、results(task_id,
source_event_seq)。状态列用 String 而非 MySQL ENUM（状态集合会演进，避免
每次扩展都 MODIFY 列——同族教训见 007/009）。

兼容窗口 / 回滚说明：
- **升级是纯增量**：只建两张新表，不动既有表与数据；旧版本代码不读这两张表，
  滚动发布期间新旧实例可同时运行（新实例在迁移执行前写账本会 fail-open：
  只记 warning、不阻塞执行；迁移跑完后自动恢复记账）。
- **回滚有损但可安全执行**：downgrade 会 drop 两张表，丢失已记录的工具调用
  账目（仅审计数据，不参与任何正常业务读写）；旧代码不依赖这些表，回滚后
  服务不受影响。若需保留证据，回滚前先导出两张表。
- SQLite（测试/本地）与 MySQL 语义等价；MySQL 走 InnoDB。

Revision ID: 010_agent_tool_calls
Revises: 010_knowledge_job_local_fallback
"""

import sqlalchemy as sa

from alembic import op

revision = "010_agent_tool_calls"
down_revision = "010_knowledge_job_local_fallback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_tool_calls",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column(
            "task_id", sa.String(36),
            sa.ForeignKey("agent_tasks.id", ondelete="CASCADE"),
            index=True, nullable=False,
        ),
        sa.Column("call_id", sa.String(64), nullable=False),
        sa.Column("tool_name", sa.String(128), nullable=False),
        sa.Column("arguments", sa.Text(), nullable=False),
        sa.Column("source_event_seq", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, index=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), onupdate=sa.func.now()),
        sa.UniqueConstraint("task_id", "call_id", name="uq_agent_tool_calls_task_call"),
        sa.UniqueConstraint("task_id", "source_event_seq", name="uq_agent_tool_calls_task_seq"),
        mysql_engine="InnoDB",
    )
    op.create_table(
        "agent_tool_results",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column(
            "task_id", sa.String(36),
            sa.ForeignKey("agent_tasks.id", ondelete="CASCADE"),
            index=True, nullable=False,
        ),
        sa.Column("call_id", sa.String(64), nullable=False),
        sa.Column(
            "tool_call_pk", sa.Integer(),
            sa.ForeignKey("agent_tool_calls.id", ondelete="CASCADE"),
            index=True, nullable=False,
        ),
        sa.Column("source_event_seq", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("result_summary", sa.Text(), nullable=False),
        sa.Column("error_type", sa.String(64), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.UniqueConstraint("task_id", "call_id", name="uq_agent_tool_results_task_call"),
        sa.UniqueConstraint("task_id", "source_event_seq", name="uq_agent_tool_results_task_seq"),
        mysql_engine="InnoDB",
    )


def downgrade() -> None:
    # 先删 results（FK 指向 calls），再删 calls。
    op.drop_table("agent_tool_results")
    op.drop_table("agent_tool_calls")
