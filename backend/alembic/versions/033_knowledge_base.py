"""多知识库：新增 knowledge_bases 表与 documents.knowledge_base_id（竞品对标 P2）。

- 知识库按 ``organization_id`` 严格归属；旧数据没有知识库行，行为不变。
- ``documents.knowledge_base_id`` 可空：NULL = 旧语义「组织内全部文档」，
  不隐式归入任何库；检索侧只在显式给出 kb_id 时收窄文档集合。
- MySQL DDL 非事务，upgrade/downgrade 均按对象存在性幂等执行（沿用 027/032 先例）。
- downgrade 直接 drop_table，由数据库随表删除 KB 索引；不显式 drop_index
  （create_all 路径下复合索引是 organization_id 外键的支撑索引，MySQL 会报 1553）。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "033_knowledge_base"
down_revision = "032_review_rule_snapshot"
branch_labels = None
depends_on = None

TABLE = "knowledge_bases"
DOC_TABLE = "documents"
COLUMN = "knowledge_base_id"
INDEX = "ix_documents_knowledge_base_id"
KB_ORG_INDEX = "ix_knowledge_bases_org_created"
FK = "fk_documents_knowledge_base_id_knowledge_bases"


def _is_sqlite() -> bool:
    return op.get_bind().dialect.name == "sqlite"


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}


def _foreign_keys(table: str) -> set[str]:
    return {fk["name"] for fk in sa.inspect(op.get_bind()).get_foreign_keys(table) if fk["name"]}


def upgrade() -> None:
    if TABLE not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            TABLE,
            sa.Column("id", sa.String(length=36), primary_key=True, comment="知识库ID"),
            sa.Column(
                "organization_id",
                sa.Integer(),
                sa.ForeignKey("organizations.id"),
                nullable=False,
                comment="所属组织（租户根）",
            ),
            sa.Column("name", sa.String(length=200), nullable=False, comment="知识库名称"),
            sa.Column("description", sa.Text(), nullable=True, comment="知识库描述"),
            sa.Column(
                "created_by",
                sa.Integer(),
                sa.ForeignKey("users.id"),
                nullable=True,
                comment="创建者用户ID",
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
                nullable=True,
                comment="更新时间",
            ),
        )
    if KB_ORG_INDEX not in _indexes(TABLE):
        op.create_index(KB_ORG_INDEX, TABLE, ["organization_id", "created_at"])

    if COLUMN not in _columns(DOC_TABLE):
        op.add_column(
            DOC_TABLE,
            sa.Column(
                COLUMN,
                sa.String(length=36),
                nullable=True,
                comment="所属知识库（NULL=组织级默认库）",
            ),
        )
    if INDEX not in _indexes(DOC_TABLE):
        op.create_index(INDEX, DOC_TABLE, [COLUMN])
    if not _is_sqlite() and FK not in _foreign_keys(DOC_TABLE):
        op.create_foreign_key(FK, DOC_TABLE, TABLE, [COLUMN], ["id"])


def downgrade() -> None:
    if COLUMN in _columns(DOC_TABLE):
        if not _is_sqlite() and FK in _foreign_keys(DOC_TABLE):
            op.drop_constraint(FK, DOC_TABLE, type_="foreignkey")
        if INDEX in _indexes(DOC_TABLE):
            op.drop_index(INDEX, table_name=DOC_TABLE)
        op.drop_column(DOC_TABLE, COLUMN)
    if TABLE in sa.inspect(op.get_bind()).get_table_names():
        # MySQL 的 organization_id 外键依赖 (organization_id, created_at) 索引的
        # 左前缀；单独 DROP INDEX 会报 1553。drop_table 会随表删除该表索引。
        op.drop_table(TABLE)
