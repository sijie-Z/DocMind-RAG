"""documents 增加 is_enabled（文档级启停，Batch 14）。

背景：对标 RAGFlow / Dify / FastGPT 的「禁用 / 归档文档」——企业场景下
合同旧版本、过期制度需要「留档但不出现在检索召回里」。此前只能删除或
全量重建索引。

语义：
- ``is_enabled = 0`` 的文档**不参与检索**（ES filter），但内容、分块、
  向量全部保留，随时可恢复；
- 默认值 1（存量文档全部保持启用，行为零变化）；
- 带索引：检索 filter 为 ``term is_enabled``。

MySQL DDL 非事务：upgrade/downgrade 均按实际存在性幂等执行。
"""

import sqlalchemy as sa

from alembic import op

revision = "027_documents_is_enabled"
down_revision = "026_drop_redundant_pk_indexes"
branch_labels = None
depends_on = None

TABLE = "documents"
COLUMN = "is_enabled"
INDEX = "ix_documents_is_enabled"


def _has_column() -> bool:
    cols = sa.inspect(op.get_bind()).get_columns(TABLE)
    return any(c["name"] == COLUMN for c in cols)


def _has_index() -> bool:
    indexes = sa.inspect(op.get_bind()).get_indexes(TABLE)
    return any(i["name"] == INDEX for i in indexes)


def upgrade() -> None:
    if not _has_column():
        op.add_column(
            TABLE,
            sa.Column(
                COLUMN,
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("1"),
                comment="是否启用（停用后不参与检索）",
            ),
        )
    if not _has_index():
        op.create_index(INDEX, TABLE, [COLUMN])


def downgrade() -> None:
    if _has_index():
        op.drop_index(INDEX, table_name=TABLE)
    if _has_column():
        op.drop_column(TABLE, COLUMN)
