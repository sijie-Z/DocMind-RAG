"""文档核验身份表：角色 / 版本族 / 版本序 / 内容哈希。

Revision ID: 020_review_document_meta
Revises: 019_deleted_chat_sessions

只新增 side table，不改 documents 主表。删除文档时由知识库删除链清理本表行
（FK 无 CASCADE，与既有子表一致）。downgrade 仅删除本表。
"""

import sqlalchemy as sa

from alembic import op

revision = "020_review_document_meta"
down_revision = "019_deleted_chat_sessions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "review_document_meta",
        sa.Column("document_id", sa.String(length=36), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("doc_role", sa.String(length=16), nullable=False),
        sa.Column("family_key", sa.String(length=128), nullable=True),
        sa.Column("version_label", sa.String(length=64), nullable=True),
        sa.Column("version_seq", sa.Integer(), nullable=True),
        sa.Column("supersedes_document_id", sa.String(length=36), nullable=True),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("registered_by", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["registered_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("document_id"),
        sa.UniqueConstraint(
            "organization_id",
            "doc_role",
            "family_key",
            "version_label",
            name="uq_review_document_meta_identity",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "doc_role",
            "family_key",
            "version_seq",
            name="uq_review_document_meta_version_seq",
        ),
        comment="文档核验身份（角色/版本族/版本序/内容哈希）",
    )
    op.create_index(
        "ix_review_document_meta_organization_id",
        "review_document_meta",
        ["organization_id"],
    )
    op.create_index(
        "ix_review_document_meta_family",
        "review_document_meta",
        ["organization_id", "doc_role", "family_key", "version_seq"],
    )


def downgrade() -> None:
    op.drop_index("ix_review_document_meta_family", table_name="review_document_meta")
    op.drop_index("ix_review_document_meta_organization_id", table_name="review_document_meta")
    op.drop_table("review_document_meta")
