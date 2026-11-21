"""成本归因：token_usage_records 增加 task_id/request_id/feature/prompt_version。

四个维度列全部 nullable，旧行保持 NULL；SQLite/MySQL 均可升级/降级，
列与索引均做存在性探测以兼容半应用状态。down_revision 接 029 head。
"""

import sqlalchemy as sa

from alembic import op

revision = "030_cost_attribution"
down_revision = "029_chat_session_share_token"
branch_labels = None
depends_on = None

TABLE = "token_usage_records"
NEW_COLUMNS: tuple[tuple[str, sa.types.TypeEngine, str], ...] = (
    ("task_id", sa.String(64), "Agent 任务 ID"),
    ("request_id", sa.String(64), "HTTP 请求 ID（X-Request-ID）"),
    ("feature", sa.String(50), "功能来源: rag_chat, agent, tool_call"),
    ("prompt_version", sa.String(100), "提示词版本"),
)
INDEXED_COLUMNS = ("task_id", "request_id", "feature")


def _has_column(name: str) -> bool:
    return any(c["name"] == name for c in sa.inspect(op.get_bind()).get_columns(TABLE))


def _has_index(name: str) -> bool:
    return any(i["name"] == name for i in sa.inspect(op.get_bind()).get_indexes(TABLE))


def upgrade() -> None:
    for name, col_type, comment in NEW_COLUMNS:
        if not _has_column(name):
            op.add_column(
                TABLE,
                sa.Column(name, col_type, nullable=True, comment=comment),
            )
    for name in INDEXED_COLUMNS:
        index_name = f"ix_{TABLE}_{name}"
        if _has_column(name) and not _has_index(index_name):
            op.create_index(index_name, TABLE, [name])


def downgrade() -> None:
    for name in INDEXED_COLUMNS:
        index_name = f"ix_{TABLE}_{name}"
        if _has_index(index_name):
            op.drop_index(index_name, table_name=TABLE)
    for name, _col_type, _comment in reversed(NEW_COLUMNS):
        if _has_column(name):
            op.drop_column(TABLE, name)
