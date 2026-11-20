"""文档可见性与 ACL：visibility + document_acl 表（Batch 14）。

两档最小版（对标 Onyx 的文档级权限）：
- ``visibility=organization``（默认）：组织内全员可见，行为与旧版完全一致；
- ``visibility=private``：仅上传者 + ``document_acl`` 中显式授权的用户可见。

只新增字段与表，不改既有数据：存量文档 visibility 均为 organization。
MySQL DDL 非事务：upgrade/downgrade 按实际存在性幂等执行。
"""

import sqlalchemy as sa

from alembic import op

revision = "028_document_visibility_acl"
down_revision = "027_documents_is_enabled"
branch_labels = None
depends_on = None

DOCUMENTS = "documents"
ACL_TABLE = "document_acl"
COLUMN = "visibility"
COLUMN_INDEX = "ix_documents_visibility"
ACL_DOC_INDEX = "ix_document_acl_document_id"
ACL_SUBJECT_INDEX = "ix_document_acl_subject_id"
ACL_UQ = "uq_document_acl_doc_subject"


def _has_column() -> bool:
    return any(c["name"] == COLUMN for c in sa.inspect(op.get_bind()).get_columns(DOCUMENTS))


def _has_index(table: str, name: str) -> bool:
    return any(i["name"] == name for i in sa.inspect(op.get_bind()).get_indexes(table))


def _has_table(table: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table)


def upgrade() -> None:
    if not _has_column():
        op.add_column(
            DOCUMENTS,
            sa.Column(
                COLUMN,
                sa.Enum("organization", "private", name="documentvisibility"),
                nullable=False,
                server_default="organization",
                comment="可见性：organization=组织可见，private=上传者+授权人",
            ),
        )
    if not _has_index(DOCUMENTS, COLUMN_INDEX):
        op.create_index(COLUMN_INDEX, DOCUMENTS, [COLUMN])

    if not _has_table(ACL_TABLE):
        op.create_table(
            ACL_TABLE,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column(
                "document_id",
                sa.String(36),
                sa.ForeignKey("documents.id", ondelete="CASCADE"),
                nullable=False,
                comment="文档ID",
            ),
            sa.Column(
                "subject_type",
                sa.String(20),
                nullable=False,
                comment="授权主体类型（当前仅 user）",
            ),
            sa.Column(
                "subject_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
                comment="被授权用户ID",
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
                comment="授权时间",
            ),
            sa.UniqueConstraint("document_id", "subject_id", name=ACL_UQ),
        )
    if _has_table(ACL_TABLE):
        if not _has_index(ACL_TABLE, ACL_DOC_INDEX):
            op.create_index(ACL_DOC_INDEX, ACL_TABLE, ["document_id"])
        if not _has_index(ACL_TABLE, ACL_SUBJECT_INDEX):
            op.create_index(ACL_SUBJECT_INDEX, ACL_TABLE, ["subject_id"])


def downgrade() -> None:
    if _has_table(ACL_TABLE):
        op.drop_table(ACL_TABLE)
    if _has_index(DOCUMENTS, COLUMN_INDEX):
        op.drop_index(COLUMN_INDEX, table_name=DOCUMENTS)
    if _has_column():
        op.drop_column(DOCUMENTS, COLUMN)
    # MySQL 的 ENUM 类型随列删除一起消失；PostgreSQL 需显式 DROP TYPE。
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        sa.Enum(name="documentvisibility").drop(bind, checkfirst=True)
