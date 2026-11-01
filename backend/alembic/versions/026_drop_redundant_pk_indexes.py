"""删除主键列上与 PRIMARY 完全冗余的二级索引（Batch 13）。

背景：历史上 21 个模型的 PK 声明了 ``index=True``，其中 14 张表的迁移也显式
建了 ``ix_<table>_id``。主键查询一律走 PRIMARY；这些二级索引只带来写放大，
并可能被优化器误选 —— Batch 10 已在 ``documents`` 上实测过由此引发的锁序
死锁（1213），当时只处理了 documents（迁移 025）。

本迁移收口其余迁移路径上真实存在的 14 个冗余索引；模型侧同步移除 PK 上的
``index=True``，使迁移与 ``create_all`` 两条建库路径在索引层一致（此前
chat_messages/users 等表只在 create_all 路径会多出 id 索引）。

MySQL DDL 非事务：upgrade/downgrade 均按实际存在性幂等执行（重跑不会因
1091/1061 失败）。downgrade 忠实恢复 003/004/005/008/011 建立的普通索引。
"""

import sqlalchemy as sa

from alembic import op

revision = "026_drop_redundant_pk_indexes"
down_revision = "025_drop_documents_id_index"
branch_labels = None
depends_on = None

# （表名, 索引名）：均由既有迁移建立，且与 PRIMARY 完全冗余（仅覆盖 id 列）。
REDUNDANT_PK_INDEXES = (
    ("agent_tasks", "ix_agent_tasks_id"),
    ("knowledge_processing_jobs", "ix_knowledge_processing_jobs_id"),
    ("notifications", "ix_notifications_id"),
    ("organization_settings", "ix_organization_settings_id"),
    ("permissions", "ix_permissions_id"),
    ("prompt_template_versions", "ix_prompt_template_versions_id"),
    ("prompt_templates", "ix_prompt_templates_id"),
    ("roles", "ix_roles_id"),
    ("scheduled_tasks", "ix_scheduled_tasks_id"),
    ("system_manuals", "ix_system_manuals_id"),
    ("token_usage_records", "ix_token_usage_records_id"),
    ("user_activity_logs", "ix_user_activity_logs_id"),
    ("user_login_sessions", "ix_user_login_sessions_id"),
    ("user_settings", "ix_user_settings_id"),
)


def _has_index(table_name: str, index_name: str) -> bool:
    indexes = sa.inspect(op.get_bind()).get_indexes(table_name)
    return any(index["name"] == index_name for index in indexes)


def upgrade() -> None:
    for table_name, index_name in REDUNDANT_PK_INDEXES:
        if _has_index(table_name, index_name):
            op.drop_index(index_name, table_name=table_name)


def downgrade() -> None:
    for table_name, index_name in REDUNDANT_PK_INDEXES:
        if not _has_index(table_name, index_name):
            op.create_index(index_name, table_name, ["id"])
