"""检索评测测试集（竞品对标 P2）。

新增 ``retrieval_eval_case`` 表：保存「query → 期望文档 id 集合」的评测
用例，按组织隔离。运行评测只读本表（默认不写结果），不改既有检索/判定。

幂等分步（与 032 先例一致）：MySQL DDL 非事务，上一次执行可能建了表却未
推进版本戳——已存在的对象跳过；downgrade 先摘索引再删表。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "034_retrieval_eval_case"
down_revision = "033_knowledge_base"
branch_labels = None
depends_on = None

TABLE = "retrieval_eval_case"
INDEX = "ix_retrieval_eval_case_org_created"


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    if TABLE not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            TABLE,
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "organization_id",
                sa.Integer(),
                sa.ForeignKey("organizations.id"),
                nullable=False,
                comment="所属组织（租户隔离键）",
            ),
            sa.Column("name", sa.String(length=255), nullable=False, comment="用例名称"),
            sa.Column("query", sa.Text(), nullable=False, comment="检索 query"),
            sa.Column(
                "expected_document_ids",
                sa.JSON(),
                nullable=False,
                comment="期望命中的文档 id 列表（文档级金标）",
            ),
            sa.Column("tags", sa.JSON(), nullable=True, comment="标签列表"),
            sa.Column(
                "created_by",
                sa.Integer(),
                sa.ForeignKey("users.id"),
                nullable=False,
                comment="创建人",
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
                comment="创建时间",
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True, comment="更新时间"),
        )
    if INDEX not in _indexes(TABLE):
        op.create_index(INDEX, TABLE, ["organization_id", "created_at"])


def downgrade() -> None:
    if TABLE in sa.inspect(op.get_bind()).get_table_names():
        if INDEX in _indexes(TABLE):
            op.drop_index(INDEX, table_name=TABLE)
        op.drop_table(TABLE)
