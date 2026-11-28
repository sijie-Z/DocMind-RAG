"""公开知识库聊天（批次十七）：``kb_publication`` 增加聊天开关与日预算。

新增两列：

- ``chat_enabled``：发布级聊天开关，``NOT NULL DEFAULT 0``（默认关闭）。
  公开聊天端点要求全局开关 + 发布开关同时开启；任一关闭统一 404。
- ``daily_token_budget``：该发布每日 token 预算（``NULL`` = 使用全局默认
  ``PUBLIC_KB_CHAT_DAILY_TOKENS_DEFAULT``；``0`` = 禁止聊天）。

同时把 ``token_usage_records.feature`` 从 ``VARCHAR(50)`` 扩到 ``VARCHAR(64)``：
本批记账键 ``feature='public_kb_chat:<uuid>'`` 长 51 字符，超出原列宽会让
MySQL 严格模式拒写（记账被吞→日预算/组织配额永久为 0），非严格模式则截断后
查询永远不命中——两种都是静默失效。SQLite 不强制 VARCHAR 长度，故只在
MySQL 方言执行 alter_column；宽列对旧值完全向后兼容。

幂等与降级口径（与 047/048 先例一致）：

- MySQL DDL 非事务：upgrade 按列实际存在性逐列探测，半应用状态可重复执行；
- downgrade 只删本迁移追加的两列（逆序），不逐条 drop_index（MySQL 1553），
  也不触碰 049 创建的表与 ``token_usage_records``（列加宽不回缩：收缩可能
  截断已写入的 51 字符 feature，且宽列兼容旧代码）；
- ``chat_enabled`` 的 ``server_default=0`` 同时存在于 ORM 模型（避免迁移库与
  create_all 库列级漂移）：既能为存量行回填 false，也不会让新插入缺值。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "050_public_kb_chat"
down_revision = "049_public_kb"
branch_labels = None
depends_on = None

PUBLICATION = "kb_publication"
TOKEN_USAGE = "token_usage_records"
FEATURE_COLUMN = "feature"
FEATURE_LENGTH = 64
OLD_FEATURE_LENGTH = 50

# 逆序删除：先删后加列，再删先加列（本迁移两列互不依赖，但保持可读顺序）。
_COLUMNS: tuple[tuple[str, sa.types.TypeEngine, bool, str], ...] = (
    (
        "chat_enabled",
        sa.Boolean(),
        False,
        "发布级聊天开关（false 时公开聊天 404；默认关闭）",
    ),
    (
        "daily_token_budget",
        sa.Integer(),
        True,
        "每日 token 预算（NULL = 全局默认；0 = 禁止聊天）",
    ),
)


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns() -> set[str]:
    if PUBLICATION not in _tables():
        return set()
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(PUBLICATION)}


def _column_length(table: str, name: str) -> int | None:
    if table not in _tables():
        return None
    for column in sa.inspect(op.get_bind()).get_columns(table):
        if column["name"] == name:
            length = getattr(column["type"], "length", None)
            return int(length) if length is not None else None
    return None


def upgrade() -> None:
    existing = _columns()
    for name, column_type, nullable, comment in _COLUMNS:
        if name in existing:
            continue
        if name == "chat_enabled":
            op.add_column(
                PUBLICATION,
                sa.Column(
                    name,
                    column_type,
                    nullable=nullable,
                    server_default=sa.text("0"),
                    comment=comment,
                ),
            )
        else:
            op.add_column(
                PUBLICATION,
                sa.Column(name, column_type, nullable=nullable, comment=comment),
            )

    # 51 字符的 public_kb_chat:<uuid> 需要 VARCHAR(64)。SQLite 不强制 VARCHAR
    # 长度，改列类型需要重建表——只对 MySQL 执行；ORM 侧统一声明 64。
    if op.get_bind().dialect.name == "mysql":
        current_length = _column_length(TOKEN_USAGE, FEATURE_COLUMN)
        if current_length is not None and current_length < FEATURE_LENGTH:
            op.alter_column(
                TOKEN_USAGE,
                FEATURE_COLUMN,
                existing_type=sa.String(length=OLD_FEATURE_LENGTH),
                type_=sa.String(length=FEATURE_LENGTH),
                existing_nullable=True,
            )


def downgrade() -> None:
    existing = _columns()
    if PUBLICATION not in _tables():
        return
    for name, _column_type, _nullable, _comment in reversed(_COLUMNS):
        if name in existing:
            op.drop_column(PUBLICATION, name)
