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
3. ``prompt_templates.name`` 的唯一性从「全局」改为「组织内」：
   新增复合唯一约束 ``uq_prompt_templates_org_name (organization_id, name)``。
   其他组织的同名模板不再冲突；系统模板（organization_id=NULL）不受该约束，
   重名由 seed / create 的可见性冲突检查兜底。
   注：迁移 003 建表时并未落地全局 name 唯一约束（只在模型声明中）；本迁移
   直接创建复合约束即可。加约束前会预检存量数据，若同一组织内已有重名，
   迁移显式失败并点名冲突组织，避免静默截断。
4. downgrade 仅删除新增的索引/外键/复合唯一约束/列，不动业务数据。

兼容窗口（旧客户端 / 旧数据 / 回滚）：
- 新列 nullable 且旧应用代码不读它：滚动升级期间旧实例照常读写，不因缺列报错；
- 新代码把 ``organization_id IS NULL`` 的工作流视为「历史遗留、不可见」——
  列表/详情/更新/删除均不命中（超管例外：全局可见），绝不把无主行错算进
  任何租户。运维可在窗口内重跑**回填 UPDATE**（该部分是幂等的），或手工
  ``UPDATE`` 指定组织后再放开。注意：``upgrade()`` 的 DDL 部分（加列/索引/
  外键/唯一约束）**不幂等**；若发生非事务 DDL 部分失败、版本行尚未推进，请先用
  ``alembic stamp`` 对齐版本或人工补齐缺失的 DDL 后再重试，不要盲目重放。
- 非系统且无法回填归属的历史提示词保持 ``organization_id = NULL``：新代码
  只把 ``is_system = True`` 的 NULL 行当系统模板全组织只读可见；这类孤儿行
  仅超管可见（隔离），不会再泄露给所有租户；
- downgrade 会删除复合唯一约束（回到迁移 003 的「无 name 唯一约束」形态）；
  业务数据原样保留。回滚后旧应用代码的重名保护仍由应用层检查承担，
  因此回滚窗口内应同时回退应用版本。
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
    ORDER BY uo.created_at, uo.organization_id
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


def _assert_no_duplicate_prompt_names() -> None:
    """加复合唯一约束前的预检：同一组织内不得已有重名（显式失败，不静默截断）。"""
    dup = op.get_bind().execute(sa.text(
        "SELECT organization_id, name, COUNT(*) AS c FROM prompt_templates "
        "WHERE organization_id IS NOT NULL "
        "GROUP BY organization_id, name HAVING COUNT(*) > 1 LIMIT 1"
    )).fetchone()
    if dup is not None:
        raise RuntimeError(
            "prompt_templates 存在同组织重名，无法建立 (organization_id, name) "
            f"唯一约束: organization_id={dup[0]} name={dup[1]} count={dup[2]}；"
            "请先重命名或删除重复行后重试迁移"
        )


def upgrade() -> None:
    _add_org_column("workflows", "fk_workflows_organization_id_organizations")
    _add_org_column("prompt_templates", "fk_prompt_templates_organization_id_organizations")

    _backfill("workflows", "created_by")
    # 系统内置模板保持 organization_id = NULL（全组织只读可见）
    _backfill("prompt_templates", "creator_id", only_where="is_system = 0")

    _assert_no_duplicate_prompt_names()
    with op.batch_alter_table("prompt_templates") as batch:
        batch.create_unique_constraint(
            "uq_prompt_templates_org_name", ["organization_id", "name"]
        )


def downgrade() -> None:
    with op.batch_alter_table("prompt_templates") as batch:
        batch.drop_constraint("uq_prompt_templates_org_name", type_="unique")
    _drop_org_column("prompt_templates", "fk_prompt_templates_organization_id_organizations")
    _drop_org_column("workflows", "fk_workflows_organization_id_organizations")
