"""HITL：agent_tasks.status 枚举扩展 WAITING_APPROVAL / CANCELLED。

Revises: 006_align_schema_with_models

背景（app/agent/approval.py、app/models/agent_task.py）：
- `waiting_approval`：审批门挂起的诚实任务态（执行器停在待执行工具之前）；
- `cancelled`：协作式取消的终态（不伪装 completed）。

口径与 006 一致：SQLAlchemy Enum 按**成员名**入库（大写），列在 MySQL 是
ENUM 类型 —— 扩展必须 MODIFY 列类型；SQLite（batch）走表重建，语义等价。

upgrade 非破坏：新枚举是旧枚举的超集，零数据转换。
downgrade **有损**：新态在旧枚举里没有对应值，先转数据再收窄——
    CANCELLED        → ABANDONED（语义最近的旧终态：用户主动放弃）
    WAITING_APPROVAL → RUNNING  （回退为"陈旧 RUNNING" = 可被 resume 接管）
"""

import sqlalchemy as sa

from alembic import op

revision = "007_agent_task_hitl_states"
down_revision = "006_align_schema_with_models"
branch_labels = None
depends_on = None

# 成员顺序与 app/models/agent_task.py 的 TaskStatus 定义顺序保持一致
# （create_all 与迁移产出的列定义同形）。
_OLD_STATUS_NAMES = (
    "PENDING", "RUNNING", "WAITING_TOOL", "WAITING_HUMAN",
    "FAILED", "COMPLETED", "ABANDONED",
)
_NEW_STATUS_NAMES = (
    "PENDING", "RUNNING", "WAITING_TOOL", "WAITING_APPROVAL", "WAITING_HUMAN",
    "FAILED", "COMPLETED", "ABANDONED", "CANCELLED",
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
    # 1) 摘枚举约束为宽松 varchar：避免收窄瞬间对 CANCELLED/WAITING_APPROVAL
    #    的静默截断（006 同款防截断纪律）。
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
        "UPDATE agent_tasks SET status = 'ABANDONED' WHERE status = 'CANCELLED'"
    ))
    bind.execute(sa.text(
        "UPDATE agent_tasks SET status = 'RUNNING' WHERE status = 'WAITING_APPROVAL'"
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
