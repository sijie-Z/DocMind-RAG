"""Add agent_tasks table for persistent agent task lifecycle checkpointing.

Revision ID: 004_add_agent_tasks
Revises: 003_add_prompt_versions_token_usage
"""

import sqlalchemy as sa

from alembic import op

revision = "004_add_agent_tasks"
down_revision = "003_add_prompt_versions_token_usage"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "agent_tasks",
        sa.Column("id", sa.String(36), primary_key=True, index=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), index=True, nullable=False),
        sa.Column("organization_id", sa.Integer(), sa.ForeignKey("organizations.id"), index=True, nullable=True),
        sa.Column("status", sa.Enum("pending", "running", "waiting_tool", "waiting_human", "failed", "completed", "abandoned", name="taskstatus"), nullable=False, index=True),
        sa.Column("query", sa.Text(), nullable=False),
        sa.Column("plan_id", sa.String(36), index=True),
        sa.Column("current_step_id", sa.String(36)),
        sa.Column("context_snapshot", sa.JSON()),
        sa.Column("completed_steps", sa.Integer(), server_default="0"),
        sa.Column("total_steps", sa.Integer()),
        sa.Column("progress", sa.Float(), server_default="0.0"),
        sa.Column("last_error", sa.Text()),
        sa.Column("last_error_type", sa.String(50)),
        sa.Column("retry_count", sa.Integer(), server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), onupdate=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("expires_at", sa.DateTime(timezone=True), index=True),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )


def downgrade():
    op.drop_table("agent_tasks")
