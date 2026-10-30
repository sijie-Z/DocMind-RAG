"""核验案件归档出口（合同核验场景 Batch 7）。

Revision ID: 024_review_case_archive
Revises: 023_review_export

review_case 增加 ``archived_at`` / ``archived_by`` 两列：归档是只读终态，
归档案件不再参与删除守卫（其引用文档可被物理删除），案件与既有导出快照
仍可读。同时为三份文档引用列建单列索引（删除守卫反查），并为默认列表
过滤 (organization_id, archived_at) 建复合索引。

SQLite 无法对已有表追加外键：升级/降级在 SQLite 上跳过 ``archived_by``
外键（SQLite 测试本来不启用外键约束），MySQL 上按模型声明建外键。
"""

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision = "024_review_case_archive"
down_revision = "023_review_export"
branch_labels = None
depends_on = None


def _is_sqlite() -> bool:
    return op.get_bind().dialect.name == "sqlite"


def upgrade() -> None:
    op.add_column(
        "review_case",
        sa.Column(
            "archived_at",
            sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"),
            nullable=True,
            comment="归档时间（只读终态）",
        ),
    )
    op.add_column(
        "review_case",
        sa.Column("archived_by", sa.Integer(), nullable=True, comment="归档人"),
    )
    if not _is_sqlite():
        op.create_foreign_key(
            "fk_review_case_archived_by_users", "review_case", "users", ["archived_by"], ["id"]
        )
    op.create_index(
        "ix_review_case_org_archived", "review_case", ["organization_id", "archived_at"]
    )
    op.create_index("ix_review_case_policy_document_id", "review_case", ["policy_document_id"])
    op.create_index("ix_review_case_base_document_id", "review_case", ["base_document_id"])
    op.create_index("ix_review_case_revised_document_id", "review_case", ["revised_document_id"])


def downgrade() -> None:
    op.drop_index("ix_review_case_revised_document_id", table_name="review_case")
    op.drop_index("ix_review_case_base_document_id", table_name="review_case")
    op.drop_index("ix_review_case_policy_document_id", table_name="review_case")
    op.drop_index("ix_review_case_org_archived", table_name="review_case")
    if _is_sqlite():
        # SQLite 的 ALTER TABLE 不支持 DROP COLUMN 之前的外键处理，走 batch 重建。
        with op.batch_alter_table("review_case") as batch:
            batch.drop_column("archived_by")
            batch.drop_column("archived_at")
    else:
        op.drop_constraint("fk_review_case_archived_by_users", "review_case", type_="foreignkey")
        op.drop_column("review_case", "archived_by")
        op.drop_column("review_case", "archived_at")
