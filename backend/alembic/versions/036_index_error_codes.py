"""结构化索引错误码与阶段进度（竞品批次四）。

为 ``documents`` 与 ``knowledge_processing_jobs`` 各增加四个并行字段：
``index_stage`` / ``index_progress`` / ``index_error_code`` / ``index_error_detail``。

设计约束：
- 既有 ``status`` / ``parse_error`` / ``error_message`` / ``index_status``（检索空结果
  诊断）的语义与取值**完全不变**；本迁移只加列，不改数据。
- stage 与 error_code 是闭集（见 ``app.services.index_progress``），用有界
  ``VARCHAR`` 落库；detail 只存脱敏后的有界运维提示，不存异常原文/文档正文/查询。
- 存量行 stage/error_code/detail 为 NULL、progress 为 0 —— 表示「旧任务无结构化
  信息」，前端按无结构化字段处理，不猜测状态。

MySQL DDL 非事务：upgrade/downgrade 按实际存在性幂等执行。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "036_index_error_codes"
down_revision = "035_retrieval_eval_run"
branch_labels = None
depends_on = None

TABLES = ("documents", "knowledge_processing_jobs")
COLUMNS = (
    ("index_stage", sa.String(16)),
    ("index_progress", sa.Integer()),
    ("index_error_code", sa.String(64)),
    ("index_error_detail", sa.String(256)),
)


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    for table in TABLES:
        existing = _columns(table)
        for name, column_type in COLUMNS:
            if name in existing:
                continue
            if name == "index_progress":
                op.add_column(
                    table,
                    sa.Column(
                        name,
                        column_type,
                        nullable=False,
                        server_default=sa.text("0"),
                        comment="阶段边界进度 0-100",
                    ),
                )
            else:
                op.add_column(
                    table,
                    sa.Column(
                        name,
                        column_type,
                        nullable=True,
                        comment={
                            "index_stage": "处理阶段闭集：parse/chunk/embed/index",
                            "index_error_code": "稳定错误码闭集",
                            "index_error_detail": "脱敏且有界的运维提示",
                        }[name],
                    ),
                )


def downgrade() -> None:
    for table in TABLES:
        existing = _columns(table)
        for name, _column_type in reversed(COLUMNS):
            if name in existing:
                op.drop_column(table, name)
