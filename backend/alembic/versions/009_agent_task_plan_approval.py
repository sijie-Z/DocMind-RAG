"""B（计划审批）：agent_tasks.status 枚举扩展 WAITING_PLAN_APPROVAL / REJECTED。

Revision ID: 009_agent_task_plan_approval
Revises: 008_scheduled_tasks（同轮在途分支的迁移；本地独立验证本迁移时需先存在
008 文件——嫁接 008 迁移 + scheduled_task 模型，见 _orchestrate 轮次证据；
本仓 migration-mysql CI 在合并后的链上跑全链往返）

背景（app/agent/checkpoint.py 的计划审批写路径、app/models/agent_task.py）：
- `waiting_plan_approval`：plan_approval_mode=always 时 planner 产出计划后的
  诚实暂停态（等人类批准 / 编辑步骤后批准 / 拒绝）；
- `rejected`：计划被人类拒绝且重规划机会（上限 1 次）用尽的诚实终态——
  不是 failed（机器没坏）、不是 cancelled（不是中途收回）——是"看过，不要"。

口径与 007 一致：SQLAlchemy Enum 按**成员名**入库（大写），列在 MySQL 是
ENUM 类型 —— 扩展必须 MODIFY 列类型；SQLite（batch）走表重建，语义等价。

upgrade 非破坏：新枚举是旧枚举的超集，零数据转换。
downgrade **有损**（与 007 同款四步：放宽 → 校验归一 → 数据映射 → 收窄）：
    REJECTED              → ABANDONED（语义最近的旧终态：用户主动放弃——
                            007 的 CANCELLED→ABANDONED 同口径）
    WAITING_PLAN_APPROVAL → RUNNING  （回退为"陈旧 RUNNING" = 可被 resume 接管）
"""

import sqlalchemy as sa

from alembic import op

revision = "009_agent_task_plan_approval"
down_revision = "008_scheduled_tasks"
branch_labels = None
depends_on = None

# 成员顺序与 app/models/agent_task.py 的 TaskStatus 定义顺序保持一致
# （create_all 与迁移产出的列定义同形；check_schema.py 逐列比对不折叠大小写）。
_OLD_STATUS_NAMES = (
    "PENDING", "RUNNING", "WAITING_TOOL", "WAITING_APPROVAL", "WAITING_HUMAN",
    "FAILED", "COMPLETED", "ABANDONED", "CANCELLED",
)
_NEW_STATUS_NAMES = (
    "PENDING", "RUNNING", "WAITING_TOOL", "WAITING_APPROVAL", "WAITING_PLAN_APPROVAL",
    "WAITING_HUMAN", "FAILED", "COMPLETED", "ABANDONED", "CANCELLED", "REJECTED",
)


def _old_enum() -> sa.Enum:
    return sa.Enum(*_OLD_STATUS_NAMES, name="taskstatus")


def _new_enum() -> sa.Enum:
    return sa.Enum(*_NEW_STATUS_NAMES, name="taskstatus")


def upgrade() -> None:
    with op.batch_alter_table("agent_tasks") as batch:
        batch.alter_column(
            "status",
            existing_type=_old_enum(),
            type_=_new_enum(),
            existing_nullable=False,
            nullable=False,
        )


def downgrade() -> None:
    bind = op.get_bind()
    # 1) 摘枚举约束为宽松 varchar：避免收窄瞬间对 WAITING_PLAN_APPROVAL /
    #    REJECTED 的静默截断（007 同款防截断纪律）。
    with op.batch_alter_table("agent_tasks") as batch:
        batch.alter_column(
            "status",
            existing_type=_new_enum(),
            type_=sa.String(32),
            existing_nullable=False,
            nullable=False,
        )
    # 2) 校验 + 大小写归一：列内容必须都在**新**枚举集合内（这批值刚被允许过）。
    rows = bind.execute(sa.text("SELECT DISTINCT status FROM agent_tasks")).fetchall()
    seen = {str(r[0]) for r in rows}
    unknown = seen - set(_NEW_STATUS_NAMES)
    if unknown:
        raise RuntimeError(f"agent_tasks.status 存在无法识别的存量值: {sorted(unknown)}")
    for name in _NEW_STATUS_NAMES:
        bind.execute(
            sa.text("UPDATE agent_tasks SET status = :canon WHERE UPPER(status) = :up"),
            {"canon": name, "up": name},
        )
    # 3) 新态数据转换 → 旧枚举内的语义最近值（有损，见模块 docstring）。
    bind.execute(sa.text(
        "UPDATE agent_tasks SET status = 'ABANDONED' WHERE status = 'REJECTED'"
    ))
    bind.execute(sa.text(
        "UPDATE agent_tasks SET status = 'RUNNING' WHERE status = 'WAITING_PLAN_APPROVAL'"
    ))
    # 4) 收窄回旧枚举。
    with op.batch_alter_table("agent_tasks") as batch:
        batch.alter_column(
            "status",
            existing_type=sa.String(32),
            type_=_old_enum(),
            existing_nullable=False,
            nullable=False,
        )
