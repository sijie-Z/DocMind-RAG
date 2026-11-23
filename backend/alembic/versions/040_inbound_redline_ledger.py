"""对方回传 DOCX 修订台账（竞品批次六 · 特性 B）。

新增两张表：
- ``review_redline_submission``：一次上传的解析头（文件哈希/计数/告警/溯源）；
- ``review_redline_row``：逐条插入/删除/批注/无法解析记录，保存对方 DOCX
  OOXML 原文（before/after 文本 + 哈希 + 定位），以及人工处置覆盖层。

关键约束：
- ``evidence_id`` 刻意**不建外键**：证据快照与修订台账生命周期独立；
- ``text_provenance`` 固定为 ``counterparty_docx_ooxml``，与证据表的
  ``parser_original`` 严格区分（服务层负责校验，DB 只存字符串）；
- ``created_at`` 使用 MySQL DATETIME(fsp=6) 变体，与 ORM 模型一致；
- 不改写任何既有表；MySQL DDL 非事务，upgrade/downgrade 按对象实际
  存在性幂等执行（与 039 先例一致）。
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision = "040_inbound_redline_ledger"
down_revision = "039_review_template"
branch_labels = None
depends_on = None

SUBMISSION = "review_redline_submission"
ROW = "review_redline_row"


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    tables = _tables()
    if SUBMISSION not in tables:
        op.create_table(
            SUBMISSION,
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "organization_id",
                sa.Integer(),
                sa.ForeignKey("organizations.id"),
                nullable=False,
                comment="所属组织（租户隔离键）",
            ),
            sa.Column(
                "case_id",
                sa.String(length=36),
                sa.ForeignKey("review_case.id"),
                nullable=False,
            ),
            sa.Column("sequence_no", sa.Integer(), nullable=False, comment="同案内自增序号"),
            sa.Column("filename", sa.String(length=255), nullable=False),
            sa.Column("file_sha256", sa.String(length=64), nullable=False),
            sa.Column("file_size", sa.Integer(), nullable=False),
            sa.Column("storage_object_name", sa.String(length=512), nullable=False),
            sa.Column("parser_version", sa.String(length=32), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False, comment="parsed/partial"),
            sa.Column("revision_count", sa.Integer(), nullable=False),
            sa.Column("comment_count", sa.Integer(), nullable=False),
            sa.Column("unparsed_count", sa.Integer(), nullable=False),
            sa.Column("parse_warnings", sa.JSON(), nullable=False, comment="解析告警列表"),
            sa.Column(
                "uploaded_by",
                sa.Integer(),
                sa.ForeignKey("users.id"),
                nullable=False,
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.UniqueConstraint(
                "case_id", "sequence_no", name="uq_review_redline_submission_case_seq"
            ),
            comment="对方 DOCX 修订台账解析批次（不可变快照）",
        )
    submission_indexes = _indexes(SUBMISSION)
    # organization_id 在 ORM 声明了 index=True，create_all 会生成单列索引，
    # 迁移必须一并对齐，否则迁移库与 create_all 库 schema 不一致。
    if "ix_review_redline_submission_organization_id" not in submission_indexes:
        op.create_index(
            "ix_review_redline_submission_organization_id", SUBMISSION, ["organization_id"]
        )
    if "ix_review_redline_submission_org_created" not in submission_indexes:
        op.create_index(
            "ix_review_redline_submission_org_created",
            SUBMISSION,
            ["organization_id", "created_at"],
        )
    if "ix_review_redline_submission_case_created" not in submission_indexes:
        op.create_index(
            "ix_review_redline_submission_case_created",
            SUBMISSION,
            ["case_id", "created_at"],
        )

    if ROW not in _tables():
        op.create_table(
            ROW,
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "submission_id",
                sa.String(length=36),
                sa.ForeignKey(f"{SUBMISSION}.id"),
                nullable=False,
            ),
            sa.Column(
                "case_id",
                sa.String(length=36),
                sa.ForeignKey("review_case.id"),
                nullable=False,
            ),
            sa.Column(
                "organization_id",
                sa.Integer(),
                sa.ForeignKey("organizations.id"),
                nullable=False,
                comment="所属组织（租户隔离键）",
            ),
            sa.Column("ordinal", sa.Integer(), nullable=False),
            sa.Column("revision_type", sa.String(length=16), nullable=False),
            sa.Column("pair_key", sa.String(length=64), nullable=True),
            sa.Column("before_text", sa.String(length=4000), nullable=True),
            sa.Column("after_text", sa.String(length=4000), nullable=True),
            sa.Column("before_sha256", sa.String(length=64), nullable=True),
            sa.Column("after_sha256", sa.String(length=64), nullable=True),
            sa.Column("author", sa.String(length=200), nullable=True),
            sa.Column("authored_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("comment_id", sa.String(length=64), nullable=True),
            sa.Column("comment_author", sa.String(length=200), nullable=True),
            sa.Column("comment_date", sa.DateTime(timezone=True), nullable=True),
            sa.Column("location", sa.JSON(), nullable=False, comment="段落/字符定位"),
            sa.Column("clause_no", sa.JSON(), nullable=True),
            sa.Column("section_no", sa.JSON(), nullable=True),
            sa.Column("unparsed_reason", sa.JSON(), nullable=True),
            sa.Column(
                "text_provenance",
                sa.String(length=32),
                nullable=False,
                comment="恒为 counterparty_docx_ooxml",
            ),
            sa.Column(
                "item_id",
                sa.String(length=36),
                sa.ForeignKey("review_item.id"),
                nullable=True,
            ),
            # evidence_id 故意不建外键：证据快照生命周期独立于修订台账。
            sa.Column("evidence_id", sa.String(length=36), nullable=True),
            sa.Column("disposition", sa.String(length=24), nullable=False),
            sa.Column("note", sa.String(length=2000), nullable=True),
            sa.Column(
                "disposition_by",
                sa.Integer(),
                sa.ForeignKey("users.id"),
                nullable=True,
            ),
            sa.Column("disposition_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("lock_version", sa.Integer(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            comment="对方 DOCX 修订台账逐条记录（OOXML 原文 + 人工处置）",
        )
    row_indexes = _indexes(ROW)
    if "ix_review_redline_row_organization_id" not in row_indexes:
        op.create_index("ix_review_redline_row_organization_id", ROW, ["organization_id"])
    if "ix_review_redline_row_submission_ordinal" not in row_indexes:
        op.create_index(
            "ix_review_redline_row_submission_ordinal", ROW, ["submission_id", "ordinal"]
        )
    if "ix_review_redline_row_case_item" not in row_indexes:
        op.create_index("ix_review_redline_row_case_item", ROW, ["case_id", "item_id"])
    if "ix_review_redline_row_org_created" not in row_indexes:
        op.create_index("ix_review_redline_row_org_created", ROW, ["organization_id", "created_at"])


def downgrade() -> None:
    # 逆序删表：先删子表 row（持有指向 submission 的外键），再删 submission。
    # MySQL 不允许先删 FK 依赖的二级索引（1553）；drop_table 会随表删除这些
    # 索引，SQLite 回滚同样如此，无需也不应逐个 drop_index。
    tables = _tables()
    if ROW in tables:
        op.drop_table(ROW)
    if SUBMISSION in tables:
        op.drop_table(SUBMISSION)
