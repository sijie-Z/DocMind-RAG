"""Agent 工件（artifact）持久层（竞品批次七 · 版本化工件卡 + 预览）。

新增一张表 ``agent_artifact``：一行 = 一份**不可变的工件内容**（内容寻址的
sha256 + 确定性 MinIO 对象名），通过自引用 ``replaces_artifact_id`` 串成版本
链。设计取舍（为什么单表而非 artifact + artifact_version）见模型模块 docstring。

关键约束：
- ``conversation_id`` / ``task_id`` 刻意**不建外键**：会话/任务行可被清理，
  工件必须继续可读（与 040 的 ``evidence_id`` 同口径）；
- ``(task_id, source_call_id)`` 唯一：同一工具调用的重复落账（resume 重放）
  由 DB 兜底，服务层撞约束时返回既有行（幂等而非报错）；
- ``status`` / ``version_no`` 由 ORM/服务层提供默认值，**DDL 无 server_default**
  （批次五 CI 漂移的教训：迁移库与 create_all 库必须列级一致）；
- ``created_at`` 使用 MySQL DATETIME(fsp=6) 变体，与 ORM 模型一致；
- MySQL DDL 非事务，upgrade 按对象实际存在性幂等执行（与 039/040 先例一致）。
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision = "041_agent_artifact"
down_revision = "040_inbound_redline_ledger"
branch_labels = None
depends_on = None

TABLE = "agent_artifact"


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    if TABLE not in _tables():
        op.create_table(
            TABLE,
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "organization_id",
                sa.Integer(),
                sa.ForeignKey("organizations.id"),
                nullable=False,
                comment="所属组织（租户隔离键）",
            ),
            sa.Column(
                "user_id",
                sa.Integer(),
                sa.ForeignKey("users.id"),
                nullable=False,
                comment="创建者（读取按用户隔离）",
            ),
            # 会话/任务是溯源信息，不建外键（行可能被清理，工件须继续可读）。
            sa.Column("conversation_id", sa.String(length=36), nullable=True),
            sa.Column("task_id", sa.String(length=36), nullable=True),
            sa.Column("agent_id", sa.String(length=64), nullable=True),
            sa.Column("kind", sa.String(length=32), nullable=False),
            sa.Column("title", sa.String(length=200), nullable=False),
            sa.Column("filename", sa.String(length=255), nullable=True),
            sa.Column("mime_type", sa.String(length=128), nullable=False),
            sa.Column("content_sha256", sa.String(length=64), nullable=False),
            sa.Column("file_size", sa.Integer(), nullable=False),
            sa.Column("storage_object_name", sa.String(length=512), nullable=True),
            sa.Column("source_tool", sa.String(length=128), nullable=True),
            sa.Column("source_call_id", sa.String(length=64), nullable=True),
            sa.Column("version_no", sa.Integer(), nullable=False),
            sa.Column(
                "replaces_artifact_id",
                sa.String(length=36),
                sa.ForeignKey(f"{TABLE}.id"),
                nullable=True,
            ),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("task_id", "source_call_id", name="uq_agent_artifact_task_call"),
            comment="Agent 工件（不可变内容行 + 版本链）",
        )
    indexes = _indexes(TABLE)
    # ORM 的 index=True 单列索引：create_all 会生成，迁移必须一并对齐
    # （否则迁移库与 create_all 库 schema 漂移，check_schema 报红）。
    for column in (
        "organization_id",
        "user_id",
        "conversation_id",
        "task_id",
        "replaces_artifact_id",
    ):
        name = f"ix_agent_artifact_{column}"
        if name not in indexes:
            op.create_index(name, TABLE, [column])
    if "ix_agent_artifact_org_created" not in indexes:
        op.create_index("ix_agent_artifact_org_created", TABLE, ["organization_id", "created_at"])
    if "ix_agent_artifact_org_sha256" not in indexes:
        op.create_index(
            "ix_agent_artifact_org_sha256", TABLE, ["organization_id", "content_sha256"]
        )


def downgrade() -> None:
    # MySQL 不允许先删 FK 依赖的二级索引（1553）；drop_table 会随表删除这些
    # 索引，SQLite 回滚同样如此，无需也不应逐个 drop_index。自引用外键在
    # 整表删除时一并消失。
    if TABLE in _tables():
        op.drop_table(TABLE)
