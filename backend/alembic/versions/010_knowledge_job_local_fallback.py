"""A3-P0：knowledge_processing_jobs 增加本机处理标记 local_fallback。

背景：Kafka 不可用时上传/重建/文件服务走进程内处理，修前只有
``asyncio.create_task``——进程退出即丢任务，文档永久 PENDING/PARSING。
本列把「这条任务由本进程处理」持久化：启动时按
``local_fallback = true AND status IN ('QUEUED', 'PROCESSING')`` 对账重跑。

兼容窗口：服务端默认值 0（false），旧版本进程插入的行自动落 false、不会
被新版本启动对账误调度；旧版本进程不读该列，行为与升级前一致。回滚只需
downgrade 删除本列——已被标记为 true 的未完成任务会失去对账依据，但数据
不损坏（文档/任务行仍在，可经知识库重建端点手动恢复）。

Revision ID: 010_knowledge_job_local_fallback
Revises: 010_tenant_boundary_org（A2 租户边界；单 head 链 009→A2→A3）
"""

import sqlalchemy as sa

from alembic import op

revision = "010_knowledge_job_local_fallback"
down_revision = "010_tenant_boundary_org"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("knowledge_processing_jobs") as batch:
        batch.add_column(
            sa.Column(
                "local_fallback",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
                comment="本机处理标记；启动对账回收未完成的本机任务（A3-P0）",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("knowledge_processing_jobs") as batch:
        batch.drop_column("local_fallback")
