"""删除 documents.id 上的冗余二级索引（Batch 10）。

Revision ID: 025_drop_redundant_documents_id_index
Revises: 024_review_case_archive

`ix_documents_id`（unique, on id）与 PRIMARY 完全冗余，删除它是纯卫生项。
真正的死锁根因是「锁定读若由任一二级索引驱动，会先锁二级索引条目再锁主键
行，而并发 DELETE 持主键行后要删二级索引条目」的锁序颠倒（真实 MySQL 实测
1213；去掉 ix_documents_id 后优化器改用 ix_documents_status 复现同一死锁）。
根治手段是建案锁读强制 `FORCE INDEX (PRIMARY)`（见 review_case_service），
本迁移只负责清掉冗余索引（减少写放大与优化器误选）。

downgrade 重建同名唯一索引（无数据影响）。
"""

import sqlalchemy as sa

from alembic import op

revision = "025_drop_documents_id_index"
down_revision = "024_review_case_archive"
branch_labels = None
depends_on = None


def _has_index(name: str) -> bool:
    indexes = sa.inspect(op.get_bind()).get_indexes("documents")
    return any(index["name"] == name for index in indexes)


def upgrade() -> None:
    # 幂等：MySQL DDL 非事务，若上一次执行已删除索引但版本戳未推进
    # （实测一次驱动抖动），重跑时不得因 1091 失败。
    if _has_index("ix_documents_id"):
        op.drop_index("ix_documents_id", table_name="documents")


def downgrade() -> None:
    if not _has_index("ix_documents_id"):
        op.create_index("ix_documents_id", "documents", ["id"], unique=True)
