"""检索评测用例去重键与组织内唯一索引（bad-case 捕获闭环）。

新增 ``retrieval_eval_case.dedup_key``（可空 VARCHAR(64)）与
``(organization_id, dedup_key)`` 唯一索引：

- 新键 = sha256(query 去首尾空白 + 金标集合排序去重)，由
  ``app.services.retrieval_eval_service.case_dedup_key`` 单点计算；
- 存量行**不回填**（保持 NULL）：迁移里按新口径回填会让历史重复行撞唯一
  索引而失败，而删除/合并用户数据必须是显式产品动作，不能藏在迁移里。
  MySQL/SQLite 的唯一索引都允许多行 NULL，因此历史行不受约束；新建/更新
  路径始终写键，去重从本迁移起生效。
- MySQL DDL 非事务：upgrade/downgrade 按对象实际存在性幂等执行。
- downgrade 先显式 DROP INDEX 再 DROP COLUMN：该索引不是 organization_id
  外键所依赖的唯一索引（外键仍由 ix_retrieval_eval_case_org_created 的
  左前缀支撑），顺序反过来也可，但先删索引是 MySQL 的稳妥写法。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "038_eval_case_dedup"
down_revision = "037_index_error_codes"
branch_labels = None
depends_on = None

TABLE = "retrieval_eval_case"
COLUMN = "dedup_key"
INDEX = "uq_retrieval_eval_case_org_dedup"


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    if COLUMN not in _columns(TABLE):
        op.add_column(
            TABLE,
            sa.Column(
                COLUMN,
                sa.String(length=64),
                nullable=True,
                comment="组织内去重键：sha256(query + 金标集合)；历史行可为 NULL",
            ),
        )
    if INDEX not in _indexes(TABLE):
        op.create_index(INDEX, TABLE, ["organization_id", COLUMN], unique=True)


def downgrade() -> None:
    if INDEX in _indexes(TABLE):
        op.drop_index(INDEX, table_name=TABLE)
    if COLUMN in _columns(TABLE):
        op.drop_column(TABLE, COLUMN)
