"""分块人工修正与启停的独立审计表（批次四 chunk 可视化）。

新增 ``document_chunk_overrides``：一行对应一个分块，保存人工检索文本、
分块级启停标记、是否应用到检索、创建/修改人与时间。原分块行的
``chunk_text`` / ``original_text`` / ``meta_data`` 与合同证据快照完全不改；
strict 原文 replay 仍以 parser 原文为唯一证据来源。

升级幂等沿用 034/035 先例：表或索引已存在则跳过；downgrade 只 drop_table。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "036_chunk_override"
down_revision = "035_retrieval_eval_run"
branch_labels = None
depends_on = None

TABLE = "document_chunk_overrides"
INDEXES = (
    "ix_document_chunk_overrides_document_chunk_id",
    "ix_document_chunk_overrides_document_id",
    "ix_document_chunk_overrides_organization_id",
)
UNIQUE = "uq_document_chunk_override_chunk"


def _inspector():
    return sa.inspect(op.get_bind())


def upgrade() -> None:
    if TABLE not in _inspector().get_table_names():
        op.create_table(
            TABLE,
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "document_chunk_id",
                sa.String(length=36),
                sa.ForeignKey("document_chunks.id", ondelete="CASCADE"),
                nullable=False,
                comment="目标分块 DB id",
            ),
            sa.Column(
                "document_id",
                sa.String(length=36),
                sa.ForeignKey("documents.id", ondelete="CASCADE"),
                nullable=False,
                comment="所属文档",
            ),
            sa.Column(
                "organization_id",
                sa.Integer(),
                sa.ForeignKey("organizations.id"),
                nullable=False,
                comment="所属组织（租户隔离键）",
            ),
            sa.Column("override_text", sa.Text(), nullable=True, comment="人工检索文本"),
            sa.Column(
                "is_enabled",
                sa.Boolean(),
                server_default=sa.text("1"),
                nullable=False,
                comment="分块级启停；false 不参与检索召回",
            ),
            sa.Column(
                "apply_to_retrieval",
                sa.Boolean(),
                server_default=sa.text("0"),
                nullable=False,
                comment="人工文本是否用于检索；false 时只保留草稿",
            ),
            sa.Column(
                "created_by",
                sa.Integer(),
                sa.ForeignKey("users.id"),
                nullable=False,
                comment="首次创建人",
            ),
            sa.Column(
                "updated_by",
                sa.Integer(),
                sa.ForeignKey("users.id"),
                nullable=False,
                comment="最后修改人",
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
                comment="创建时间",
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
                comment="最后修改时间",
            ),
            sa.UniqueConstraint("document_chunk_id", name=UNIQUE),
        )
    existing = {index["name"] for index in _inspector().get_indexes(TABLE)}
    for column in ("document_chunk_id", "document_id", "organization_id"):
        index = f"ix_{TABLE}_{column}"
        if index not in existing:
            op.create_index(index, TABLE, [column])


def downgrade() -> None:
    if TABLE in _inspector().get_table_names():
        op.drop_table(TABLE)
