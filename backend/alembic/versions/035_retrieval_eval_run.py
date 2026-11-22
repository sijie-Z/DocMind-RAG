"""检索评测运行历史（竞品对标下一批）。

新增 ``retrieval_eval_run`` 表：显式 opt-in 持久化的评测运行快照，
保存组织/创建人/top_k/用例集合/哈希/计数/指标/逐条结果，供组织内
列表、详情、导出与趋势对比。默认 ``/eval/run`` 仍不写库。

幂等分步沿用 034 先例：表或索引已存在则跳过；downgrade 只 drop_table，
索引随表删除（MySQL 外键需要 organization_id 左前缀索引，不能先删索引）。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "035_retrieval_eval_run"
down_revision = "034_retrieval_eval_case"
branch_labels = None
depends_on = None

TABLE = "retrieval_eval_run"
INDEX = "ix_retrieval_eval_run_org_created"


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
            sa.Column(
                "created_by",
                sa.Integer(),
                sa.ForeignKey("users.id"),
                nullable=False,
                comment="运行发起人",
            ),
            sa.Column("top_k", sa.Integer(), nullable=False, comment="本次运行检索深度"),
            sa.Column(
                "case_ids", sa.JSON(), nullable=False, comment="本次运行的用例 id（用例顺序）"
            ),
            sa.Column(
                "case_scope",
                sa.String(length=16),
                nullable=False,
                comment="用例范围：all=全量 / subset=显式子集",
            ),
            sa.Column(
                "case_set_hash",
                sa.String(length=64),
                nullable=False,
                comment="用例集合输入哈希（id+query+金标，排序稳定）",
            ),
            sa.Column("counts", sa.JSON(), nullable=False, comment="计数：cases/evaluated/errors"),
            sa.Column(
                "metrics",
                sa.JSON(),
                nullable=False,
                comment="指标：recall_at_k/mrr/document_hit_at_k/hit_at_k",
            ),
            sa.Column("error_case_ids", sa.JSON(), nullable=False, comment="失败用例 id 列表"),
            sa.Column("results", sa.JSON(), nullable=False, comment="逐条结果"),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
                comment="创建时间",
            ),
        )
    if INDEX not in _indexes(TABLE):
        op.create_index(INDEX, TABLE, ["organization_id", "created_at"])


def downgrade() -> None:
    if TABLE in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table(TABLE)
