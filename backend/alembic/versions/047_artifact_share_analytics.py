"""工件分享访问统计（批次十一）：聚合计数 + 最后访问时间。

在 ``agent_artifact_share`` 上追加两列，全部可空、**不回填、不改写历史行**：

- ``access_count``：公开正文被成功读取的累计次数（NULL 由服务层视为 0）；
- ``last_accessed_at``：最近一次成功读取公开正文的时间（MySQL DATETIME(fsp=6)，
  与 ORM 对齐）。

设计口径：

- **只存聚合值**：不建访问明细表，不存访问者 IP/UA/身份/正文——分享是只读能力
  凭证，访问统计只需回答「有没有人看、最后什么时候看」；
- 历史行保持 NULL（不 backfill）：服务层统一按 0 处理，避免迁移期间对业务表
  做全量 UPDATE；
- DDL 无 ``server_default``：默认值由服务层提供，避免迁移库与 ``create_all``
  库列级漂移（批次五 CI 漂移的教训）；
- MySQL DDL 非事务，upgrade/downgrade 均按列实际存在性幂等执行
  （与 031/043 先例一致）；
- downgrade 只删除本迁移追加的两列，不触碰任何既有列/索引/表。
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision = "047_artifact_share_analytics"
down_revision = "046_org_skill"
branch_labels = None
depends_on = None

TABLE = "agent_artifact_share"

# 追加列（顺序即 downgrade 的逆序删除顺序）。
_COLUMNS = (
    (
        "access_count",
        lambda: sa.Column(
            "access_count",
            sa.Integer(),
            nullable=True,
            comment="公开正文成功读取的累计次数（NULL 视为 0）",
        ),
    ),
    (
        "last_accessed_at",
        lambda: sa.Column(
            "last_accessed_at",
            sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"),
            nullable=True,
            comment="最近一次成功读取公开正文的时间",
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
