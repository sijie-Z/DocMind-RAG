"""B4（D5/D6）：组织设置表 + 提示词三层 scope。

本迁移（接在现有单头 ``010_agent_tool_calls`` 之后）：

1. 新建 ``organization_settings``：每个组织至多一行的设定覆盖集（默认模型 /
   Provider 引用、检索 top_k、隐私策略开关、月度 token 配额、工具白名单）。
   所有覆盖列可空：NULL = 未覆盖，回退平台默认——「生效值 / 来源」两层信息
   不落库也会丢，因此不写死默认值。
2. ``prompt_templates`` 增加 ``scope``（system / organization / personal），
   NOT NULL + server_default='organization'；迁移把 ``is_system = 1`` 的存量行
   回填为 ``system``（其余保持 organization）。个人模板由新代码显式写入。

兼容窗口（旧进程 / 新列并存 / 回滚）：
- **升级是纯增量**：新表与新列不影响旧代码读写；旧实例不读
  ``organization_settings``，也不读 ``scope``。滚动发布期间新旧实例可并存，
  各自的 prompt 读写语义不变。
- **新代码先于迁移运行会读不到新表**：``organization_settings`` 的读取点
  （检索 top_k、隐私策略、默认模型）会因缺表报错。发布顺序必须是「先跑本
  迁移，再放流量到新实例」；与 010 系列同一纪律。
- **个人模板在滚动窗口内的可见性**：旧实例不认识 ``scope='personal'``，会把
  个人模板当成普通组织模板（按 organization_id 可见）。因此窗口内不要创建
  个人模板，或先把所有实例升级完再开放个人层；个人模板本身只影响自己，无
  跨租户泄露。
- **downgrade 有损**：删除 ``organization_settings`` 会丢失所有组织覆盖值
  （仅配置数据，可重新录入）；删除 ``scope`` 后个人模板行退化为组织模板
  （对全组织可见）。回滚前应先删除/停用所有 personal 模板并导出设置表；
  旧代码不依赖这两者，回滚本身可安全执行。
- MySQL 的 DDL 非事务化：若加列/建表部分失败而版本行未推进，先用
  ``alembic stamp`` 对齐或人工补齐缺失 DDL 再重试，不要盲目重放。
- SQLite（测试/本地）与 MySQL 语义等价；MySQL 走 InnoDB。

Revision ID: 011_organization_settings
Revises: 010_agent_tool_calls
"""

import sqlalchemy as sa

from alembic import op

revision = "011_organization_settings"
down_revision = "010_agent_tool_calls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "organization_settings",
        sa.Column("id", sa.Integer(), autoincrement=True, primary_key=True),
        sa.Column(
            "organization_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("default_provider", sa.String(32), nullable=True),
        sa.Column("default_model", sa.String(100), nullable=True),
        sa.Column("retrieval_top_k", sa.Integer(), nullable=True),
        sa.Column("allow_privacy_mode", sa.Boolean(), nullable=True),
        sa.Column("monthly_token_quota", sa.Integer(), nullable=True),
        sa.Column("tool_whitelist", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("organization_id", name="uq_organization_settings_org"),
        mysql_engine="InnoDB",
    )
    op.create_index(
        "ix_organization_settings_id", "organization_settings", ["id"]
    )

    # SQLite 不支持直接 ADD COLUMN 之外的部分 ALTER；batch 模式在两种方言上
    # 等价（同 010_tenant_boundary_org / 010_knowledge_job_local_fallback）。
    with op.batch_alter_table("prompt_templates") as batch:
        batch.add_column(
            sa.Column(
                "scope",
                sa.String(16),
                nullable=False,
                server_default="organization",
                comment="模板层级：system / organization / personal",
            )
        )
    # 存量系统模板回填（server_default 已把其他行填为 organization）。
    op.execute(
        "UPDATE prompt_templates SET scope = 'system' WHERE is_system = 1"
    )


def downgrade() -> None:
    # 先删列再删表，与 upgrade 相反。
    with op.batch_alter_table("prompt_templates") as batch:
        batch.drop_column("scope")
    op.drop_index(
        "ix_organization_settings_id", table_name="organization_settings"
    )
    op.drop_table("organization_settings")
