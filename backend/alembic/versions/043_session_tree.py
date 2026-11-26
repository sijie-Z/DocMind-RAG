"""会话树导航：分支标签 / 分支摘要（竞品批次八 · 会话树）。

在 ``chat_sessions`` 上追加四列，全部可空、**不回填、不改写历史行**：

- ``branch_label``：分支展示标签（用户命名，<=100 字符）；
- ``branch_summary``：分支摘要正文（仅在用户显式生成摘要时写入）；
- ``summary_model``：摘要来源（``fake`` = 确定性抽取式；或 LLM 模型名）；
- ``summarized_at``：摘要生成时间（MySQL DATETIME(fsp=6)，与 ORM 对齐）。

设计口径：
- 四列都是**展示层派生信息**，不参与 fork 边界/谱系/鉴权语义；缺失时
  （历史行）读取端按 ``None`` 回落，不触发任何回填；
- DDL 无 ``server_default``：默认值由 ORM/服务层提供，避免迁移库与
  ``create_all`` 库列级漂移（批次五 CI 漂移的教训）；
- MySQL DDL 非事务，upgrade/downgrade 均按列实际存在性幂等执行
  （与 031/041/042 先例一致）；
- downgrade 只删除本迁移追加的四列，不触碰任何既有列/索引。
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision = "043_session_tree"
down_revision = "042_rubric_eval"
branch_labels = None
depends_on = None

TABLE = "chat_sessions"

# 追加列（顺序即 downgrade 的逆序删除顺序）。
_COLUMNS = (
    (
        "branch_label",
        lambda: sa.Column(
            "branch_label",
            sa.String(length=100),
            nullable=True,
            comment="分支展示标签",
        ),
    ),
    (
        "branch_summary",
        lambda: sa.Column(
            "branch_summary",
            sa.Text(),
            nullable=True,
            comment="分支摘要（显式生成）",
        ),
    ),
    (
        "summary_model",
        lambda: sa.Column(
            "summary_model",
            sa.String(length=200),
            nullable=True,
            comment="摘要来源（fake 或模型名）",
        ),
    ),
    (
        "summarized_at",
        lambda: sa.Column(
            "summarized_at",
            sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"),
            nullable=True,
            comment="摘要生成时间",
        ),
    ),
)


def _columns() -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(TABLE)}


def upgrade() -> None:
    existing = _columns()
    for name, factory in _COLUMNS:
        if name not in existing:
            op.add_column(TABLE, factory())


def downgrade() -> None:
    existing = _columns()
    # 逆序删除；只删本迁移追加的列。
    for name, _factory in reversed(_COLUMNS):
        if name in existing:
            op.drop_column(TABLE, name)
