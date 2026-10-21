"""定时任务调度域：scheduled_tasks 表（harness 维度 7：后台/主动任务）。

Revision ID: 008_scheduled_tasks
Revises: 007_agent_task_hitl_states（同轮在途分支的迁移；本地独立验证本迁移时
需先存在 007 文件，本仓 migration-mysql CI 在合并后的链上跑全链往返）

口径与 004/006 对齐：
- SQLAlchemy Enum 按**成员名**入库（CRON/INTERVAL/ONCE 大写）；
- 只加 Python 侧默认值的列（enabled/run_count/skipped_overlap_count/timezone）
  迁移侧不写 server_default——两条建库路径（alembic vs create_all）逐列相等
  是 check_schema.py 与 tests/integration/test_schema_path_parity.py 的硬门禁；
- created_at 的 server_default=now() 与 agent_tasks（004）同写法。

downgrade 直接删表（新表无外部引用，零数据转换）。
"""

import sqlalchemy as sa

from alembic import op

revision = "008_scheduled_tasks"
down_revision = "007_agent_task_hitl_states"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "scheduled_tasks",
        sa.Column("id", sa.String(36), primary_key=True, index=True),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            index=True,
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            index=True,
            nullable=True,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column(
            "schedule_type",
            sa.Enum("CRON", "INTERVAL", "ONCE", name="scheduletype"),
            nullable=False,
        ),
        sa.Column("cron_expr", sa.String(100), nullable=True),
        sa.Column("interval_seconds", sa.Integer(), nullable=True),
        sa.Column("once_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("next_run_at", sa.DateTime(timezone=True), index=True, nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(20), nullable=True),
        sa.Column("last_task_id", sa.String(36), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("run_count", sa.Integer(), nullable=False),
        sa.Column("skipped_overlap_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), onupdate=sa.func.now()),
        mysql_engine="InnoDB",
    )


def downgrade():
    op.drop_table("scheduled_tasks")
