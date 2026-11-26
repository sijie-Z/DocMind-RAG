"""工件只读分享链接（批次九）：绑定单个不可变工件版本。

新增 ``agent_artifact_share``：一行 = 一个工件版本的一条分享记录。设计边界：

- **绑定具体版本**：``artifact_id`` 指向 ``agent_artifact`` 中的一行（不可变
  内容行），不跟随版本链头。工件产生新版本后，旧分享仍只暴露它绑定的那一版；
- **token 只存摘要**：``token_hash`` 是 token 原文的 SHA-256（与
  ``chat_sessions.share_token_hash`` 同一套 ``share_link_service`` 算法），
  明文只在创建/轮换响应中返回一次；
- **一版本一记录**：``uq_agent_artifact_share_artifact`` 唯一约束保证同一
  artifact 版本至多一条分享行；重复创建走「轮换 token」而非堆行；
- ``expires_at`` / ``revoked_at`` 可空：未设置过期、未撤销。失效判断在服务层
  统一处理（过期/撤销/不存在一律同等 404，不区分原因）；
- ``agent_artifact`` 没有删除路径，因此 ``artifact_id`` 外键不会触发级联清理；
  ``organization_id`` / ``user_id`` / ``created_by`` 均建外键，用于租户隔离与
  组织删除保护（未转移时 MySQL 以 1451 拒绝删组织）。

关键约束：
- ``created_at`` 使用 MySQL DATETIME(fsp=6) 变体，与 ORM 模型一致；
- 除 ``created_at`` 外无 server_default（默认值由服务层提供，避免批次五式
  迁移库 ↔ create_all 库列级漂移）；
- MySQL DDL 非事务，upgrade 按对象实际存在性幂等执行（与 039-044 先例一致）；
  downgrade 只 drop_table，不逐条 drop_index（MySQL 1553）。
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision = "045_artifact_share"
down_revision = "044_answer_quality_eval"
branch_labels = None
depends_on = None

TABLE = "agent_artifact_share"
ARTIFACT_TABLE = "agent_artifact"


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
                comment="工件所有者（分享管理仅限本人）",
            ),
            sa.Column(
                "artifact_id",
                sa.String(length=36),
                sa.ForeignKey(f"{ARTIFACT_TABLE}.id"),
                nullable=False,
                comment="绑定的工件版本行（不可变内容，不跟随链头）",
            ),
            sa.Column(
                "token_hash",
                sa.String(length=64),
                nullable=False,
                comment="分享 token 的 SHA-256 摘要（原文不落库）",
            ),
            sa.Column(
                "created_by",
                sa.Integer(),
                sa.ForeignKey("users.id"),
                nullable=False,
                comment="分享创建人（当前恒等于 user_id，保留审计语义）",
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"),
                server_default=sa.func.now(),
                nullable=False,
                comment="分享创建/最近轮换时间",
            ),
            sa.Column(
                "expires_at",
                sa.DateTime(timezone=True),
                nullable=True,
                comment="过期时间（空 = 不过期；最长 90 天，服务层校验）",
            ),
            sa.Column(
                "revoked_at",
                sa.DateTime(timezone=True),
                nullable=True,
                comment="撤销时间（非空即失效；行保留供审计）",
            ),
            sa.UniqueConstraint("artifact_id", name="uq_agent_artifact_share_artifact"),
            sa.UniqueConstraint("token_hash", name="uq_agent_artifact_share_token_hash"),
            comment="工件只读分享链接（绑定单个不可变工件版本）",
        )
    indexes = _indexes(TABLE)
    # ORM 的 index=True 单列索引：create_all 会生成，迁移必须一并对齐。
    for column in ("organization_id", "user_id"):
        name = f"ix_agent_artifact_share_{column}"
        if name not in indexes:
            op.create_index(name, TABLE, [column])


def downgrade() -> None:
    # 索引随 drop_table 一并删除（MySQL 1553：先删 FK 依赖的二级索引再删表会失败）。
    if TABLE in _tables():
        op.drop_table(TABLE)
