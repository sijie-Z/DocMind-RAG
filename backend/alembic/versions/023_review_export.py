"""核验单导出快照（合同核验场景 Batch 4）。

Revision ID: 023_review_export
Revises: 022_review_case_evidence

只新增 review_export 表；导出后不可变（无 update/delete 接口），决策变化
只能新增行。downgrade 删除本表。
"""

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision = "023_review_export"
down_revision = "022_review_case_evidence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "review_export",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("case_id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("needs_human_count", sa.Integer(), nullable=False),
        sa.Column("unidentified_count", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["case_id"], ["review_case.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        comment="核验单导出快照（不可变）",
    )
    op.create_index("ix_review_export_case_created", "review_export", ["case_id", "created_at"])


def downgrade() -> None:
    # 与 022 同口径：MySQL 中外键会绑定到索引，只删表避免 1553。
    op.drop_table("review_export")
