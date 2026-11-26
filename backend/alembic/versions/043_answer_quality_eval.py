"""答案质量评测历史：单表不可变运行快照（faithfulness/relevancy/context-precision）。

背景：``POST /monitoring/rag-eval`` / ``rag-eval-batch`` 只返回分数不落库，
无法回答「上周质量是多少、失败了几次、换了评委模型后有改善吗」。本迁移新增
``answer_quality_eval_run``，把每一次答案质量评委运行固化成不可变快照。

关键约束：
- ``aggregate`` / ``results`` 可空：评委不可用等失败运行只保留机器码，不产生
  任何分数（「测量失败」≠ 0 分）；
- 逐条结果只存指标、有界评审理由与来源哈希/脱敏预览，**绝不存原始
  query/answer/context 文本**；
- 幂等键 ``uq_answer_quality_eval_run_config``：
  ``(organization_id, user_id, dataset_hash, judge_model, prompt_version, mode,
  status)``。同一组织/用户/输入/评委配置至多一条成功运行和一条失败运行：
  重复提交成功配置是幂等重放；失败后再跑同一配置若成功则新增 completed 行
  （允许恢复），若再次失败则回读既有 failed 行（不重复堆积）；
- MySQL DDL 非事务，upgrade/downgrade 按对象实际存在性幂等执行（与 039-042
  先例一致）；downgrade 只 drop_table，不逐条 drop_index（MySQL 1553）。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "043_answer_quality_eval"
down_revision = "042_rubric_eval"
branch_labels = None
depends_on = None

TABLE = "answer_quality_eval_run"


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    if TABLE not in _tables():
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
                "user_id",
                sa.Integer(),
                sa.ForeignKey("users.id"),
                nullable=False,
                comment="运行归属用户（列表/趋势的用户过滤键）",
            ),
            sa.Column(
                "dataset_hash",
                sa.String(length=64),
                nullable=False,
                comment="评测输入哈希（case_id+问题+答案+上下文+回放分数，排序稳定）",
            ),
            sa.Column(
                "judge_model",
                sa.String(length=128),
                nullable=False,
                comment="评委模型名；fake 模式固定为 fake",
            ),
            sa.Column(
                "prompt_version",
                sa.String(length=64),
                nullable=False,
                comment="评委提示词版本（评分口径可追溯）",
            ),
            sa.Column("mode", sa.String(length=16), nullable=False, comment="judge/fake"),
            sa.Column(
                "status", sa.String(length=16), nullable=False, comment="completed/failed"
            ),
            sa.Column("case_count", sa.Integer(), nullable=False, comment="本次用例数"),
            sa.Column(
                "aggregate",
                sa.JSON(),
                nullable=True,
                comment="聚合指标（失败运行为空，绝不写 0 分）",
            ),
            sa.Column(
                "results",
                sa.JSON(),
                nullable=True,
                comment="逐条指标/理由/来源哈希（失败运行为空；不含原始文本）",
            ),
            sa.Column("error_code", sa.String(length=64), nullable=True, comment="失败机器码"),
            sa.Column(
                "created_by",
                sa.Integer(),
                sa.ForeignKey("users.id"),
                nullable=False,
                comment="运行发起人",
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
                comment="创建时间",
            ),
            sa.Column(
                "completed_at",
                sa.DateTime(timezone=True),
                nullable=True,
                comment="完成时间（成功与失败都记录）",
            ),
            sa.UniqueConstraint(
                "organization_id",
                "user_id",
                "dataset_hash",
                "judge_model",
                "prompt_version",
                "mode",
                "status",
                name="uq_answer_quality_eval_run_config",
            ),
            comment="答案质量评测运行（不可变快照）",
        )
    indexes = _indexes(TABLE)
    if "ix_answer_quality_eval_run_org_created" not in indexes:
        op.create_index(
            "ix_answer_quality_eval_run_org_created", TABLE, ["organization_id", "created_at"]
        )
    if "ix_answer_quality_eval_run_org_user_created" not in indexes:
        op.create_index(
            "ix_answer_quality_eval_run_org_user_created",
            TABLE,
            ["organization_id", "user_id", "created_at"],
        )
    if "ix_answer_quality_eval_run_org_status" not in indexes:
        op.create_index(
            "ix_answer_quality_eval_run_org_status", TABLE, ["organization_id", "status"]
        )


def downgrade() -> None:
    # 索引随 drop_table 一并删除（MySQL 1553：先删 FK 依赖的二级索引再删表会失败）。
    if TABLE in _tables():
        op.drop_table(TABLE)
