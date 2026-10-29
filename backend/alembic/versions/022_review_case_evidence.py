"""核验案件 / 核验项 / 证据快照（合同核验场景 Batch 2）。

Revision ID: 022_review_case_evidence
Revises: 021_chat_clear_watermark

只新增三张表，不改 documents/review_document_meta。review_evidence 的
document_id / chunk_id_at_capture 刻意不建外键：证据快照必须在文档重建或
删除后仍可读，实时正文是否变化由服务层按 quote_sha256 比对标注 stale。
downgrade 按依赖逆序删除本批三张表。
"""

import sqlalchemy as sa

from alembic import op

revision = "022_review_case_evidence"
down_revision = "021_chat_clear_watermark"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "review_case",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("policy_document_id", sa.String(length=36), nullable=False),
        sa.Column("base_document_id", sa.String(length=36), nullable=False),
        sa.Column("revised_document_id", sa.String(length=36), nullable=False),
        sa.Column("checklist_version", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("blocking_reasons", sa.JSON(), nullable=True),
        sa.Column("readiness_snapshot", sa.JSON(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=False),
        sa.Column("assignee_id", sa.Integer(), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_by", sa.Integer(), nullable=True),
        sa.Column("lock_version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["assignee_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["completed_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        comment="核验案件（制度/模板 + 合同 V1/V2）",
    )
    op.create_index("ix_review_case_org_created", "review_case", ["organization_id", "created_at"])
    op.create_index("ix_review_case_org_status", "review_case", ["organization_id", "status"])

    op.create_table(
        "review_item",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("case_id", sa.String(length=36), nullable=False),
        sa.Column("item_key", sa.String(length=32), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("change_type", sa.String(length=16), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("facts", sa.JSON(), nullable=True),
        sa.Column("decided_by", sa.Integer(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["case_id"], ["review_case.id"]),
        sa.ForeignKeyConstraint(["decided_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("case_id", "item_key", name="uq_review_item_case_key"),
        comment="固定核验项",
    )
    op.create_index("ix_review_item_case_id", "review_item", ["case_id"])

    op.create_table(
        "review_evidence",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("case_id", sa.String(length=36), nullable=False),
        sa.Column("item_id", sa.String(length=36), nullable=False),
        sa.Column("side", sa.String(length=16), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("selected", sa.Boolean(), nullable=False),
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("doc_role", sa.String(length=16), nullable=False),
        sa.Column("doc_version_label", sa.String(length=64), nullable=True),
        sa.Column("doc_version_seq", sa.Integer(), nullable=True),
        sa.Column("document_sha256", sa.String(length=64), nullable=False),
        sa.Column("chunk_id_at_capture", sa.String(length=36), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("clause_no", sa.JSON(), nullable=True),
        sa.Column("section_no", sa.JSON(), nullable=True),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("char_start", sa.Integer(), nullable=True),
        sa.Column("char_end", sa.Integer(), nullable=True),
        sa.Column("quote_text", sa.Text(), nullable=False),
        sa.Column("quote_sha256", sa.String(length=64), nullable=False),
        sa.Column("text_provenance", sa.String(length=32), nullable=False),
        sa.Column(
            "captured_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["case_id"], ["review_case.id"]),
        sa.ForeignKeyConstraint(["item_id"], ["review_item.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "item_id",
            "side",
            "chunk_id_at_capture",
            name="uq_review_evidence_item_chunk",
        ),
        comment="证据快照（parser 原文 + 定位 + 哈希）",
    )
    op.create_index("ix_review_evidence_case_id", "review_evidence", ["case_id"])
    op.create_index("ix_review_evidence_item_id", "review_evidence", ["item_id"])


def downgrade() -> None:
    op.drop_index("ix_review_evidence_item_id", table_name="review_evidence")
    op.drop_index("ix_review_evidence_case_id", table_name="review_evidence")
    op.drop_table("review_evidence")
    op.drop_index("ix_review_item_case_id", table_name="review_item")
    op.drop_table("review_item")
    op.drop_index("ix_review_case_org_status", table_name="review_case")
    op.drop_index("ix_review_case_org_created", table_name="review_case")
    op.drop_table("review_case")
