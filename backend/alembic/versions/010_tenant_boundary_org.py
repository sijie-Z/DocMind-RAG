"""A2 租户边界：workflows / prompt_templates 补 organization_id（P0 安全）。

Revision ID: 010_tenant_boundary_org
Revises: 009_agent_task_plan_approval

背景：
- ``workflows`` 此前没有组织列，端点用 ``role != "admin"`` 旁路：
  任何全局 admin 都能跨组织读、改、删、执行工作流；
- ``prompt_templates`` 同样没有组织列，所有组织共享同一份列表，
  系统模板也没有只读边界。

本迁移：
1. 两张表各加 nullable 的 ``organization_id``（FK ``organizations.id``，带索引）。
   列必须可空：存量行在回填前没有归属，且系统模板按约定保持 NULL。
2. 回填（幂等，只处理 NULL 行）：
   - ``workflows``：取 ``created_by`` 用户在 ``users.organization_id`` 的组织；
     主组织为空时回退到 ``user_organization`` 中该用户最早加入的组织；
   - ``prompt_templates``：非系统模板按 ``creator_id`` 同上回填；
     ``is_system = 1`` 的系统模板保持 NULL（全组织只读可见）。
3. downgrade 仅删除新增的索引/外键/列，不动业务数据。

兼容窗口（旧客户端 / 旧数据 / 回滚）：
- 新列 nullable 且旧应用代码不读它：滚动升级期间旧实例照常读写，不因缺列报错；
- 新代码把 ``organization_id IS NULL`` 的工作流视为「历史遗留、不可见」——
  列表/详情/更新/删除均不命中，绝不把无主行错算进任何租户。运维可在窗口内
  重跑本迁移的 ``upgrade()``（幂等）或手工 ``UPDATE`` 指定组织后再放开；
- downgrade 可随时执行：删除的只是本轮新增的归属列，业务表与数据原样保留；
  回滚后旧代码行为恢复（含 admin 跨组织旁路），因此回滚时须同时回退应用版本，
  不建议单独降级本迁移。
"""

import sqlalchemy as sa

from alembic import op

revision = "010_tenant_boundary_org"
down_revision = "009_agent_task_plan_approval"
branch_labels = None
depends_on = None

# 主组织优先、成员表兜底：两段 UPDATE 均幂等（仅处理 NULL 行）。
# 用相关子查询而非 UPDATE ... JOIN：SQLite（CI backend-tests 的迁移冒烟路径）
# 不支持 MySQL 的 UPDATE JOIN 语法，标准 SQL 在两种方言上行为一致。
_BACKFILL_PRIMARY_ORG = """
UPDATE {table}
SET organization_id = (
    SELECT u.organization_id FROM users u WHERE u.id = {table}.{owner_col}
)
WHERE organization_id IS NULL
  AND {owner_col} IS NOT NULL
  AND EXISTS (
      SELECT 1 FROM users u2
      WHERE u2.id = {table}.{owner_col} AND u2.organization_id IS NOT NULL
  )
"""

_BACKFILL_MEMBERSHIP_ORG = """
UPDATE {table}
SET organization_id = (
    SELECT uo.organization_id FROM user_organization uo
    WHERE uo.user_id = {table}.{owner_col}
    ORDER BY uo.organization_id
    LIMIT 1
)
WHERE organization_id IS NULL
  AND {owner_col} IS NOT NULL
  AND EXISTS (
      SELECT 1 FROM user_organization uo2 WHERE uo2.user_id = {table}.{owner_col}
  )
"""


def _add_org_column(table: str, constraint_name: str) -> None:
    with op.batch_alter_table(table) as batch:
        batch.add_column(sa.Column("organization_id", sa.Integer(), nullable=True))
        batch.create_index(f"ix_{table}_organization_id", ["organization_id"])
        batch.create_foreign_key(
            constraint_name, "organizations", ["organization_id"], ["id"]
        )


def _drop_org_column(table: str, constraint_name: str) -> None:
    with op.batch_alter_table(table) as batch:
        batch.drop_constraint(constraint_name, type_="foreignkey")
        batch.drop_index(f"ix_{table}_organization_id")
        batch.drop_column("organization_id")


def _backfill(table: str, owner_col: str, only_where: str = "") -> None:
    bind = op.get_bind()
    for stmt in (_BACKFILL_PRIMARY_ORG, _BACKFILL_MEMBERSHIP_ORG):
        sql = stmt.format(table=table, owner_col=owner_col)
        if only_where:
            sql += f" AND {only_where}"
        bind.execute(sa.text(sql))


def upgrade() -> None:
    _add_org_column("workflows", "fk_workflows_organization_id_organizations")
    _add_org_column("prompt_templates", "fk_prompt_templates_organization_id_organizations")

    _backfill("workflows", "created_by")
    # 系统内置模板保持 organization_id = NULL（全组织只读可见）
    _backfill("prompt_templates", "creator_id", only_where="is_system = 0")


def downgrade() -> None:
    _drop_org_column("prompt_templates", "fk_prompt_templates_organization_id_organizations")
    _drop_org_column("workflows", "fk_workflows_organization_id_organizations")
