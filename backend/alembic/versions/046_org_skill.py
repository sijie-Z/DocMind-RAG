"""评审门控的组织技能库（批次十）：技能头 + 不可变版本。

新增两张表：

- ``org_skill``：组织级技能头（名称/说明/状态/最新版本号/当前启用版本指针）；
- ``org_skill_version``：不可变版本行（结构化 payload + sha256 + 审核状态）。

关键约束：

- 两张表都以 ``organization_id`` 外键引用 organizations（组织删除保护）；
- ``org_skill_version`` 以 ``(skill_id, version_no)`` 唯一，版本行不可改写；
- ``(organization_id, review_status)`` 索引服务待审队列与运行时筛选；
- 除 ``created_at`` 外无 server_default（默认值由服务层/ORM 提供，避免迁移库
  与 create_all 库列级漂移）；
- MySQL DDL 非事务，upgrade 按对象实际存在性幂等执行（与 039-045 先例一致）；
  downgrade 逆序 drop_table，不逐条 drop_index（MySQL 1553）。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "046_org_skill"
down_revision = "045_artifact_share"
branch_labels = None
depends_on = None

SKILL = "org_skill"
VERSION = "org_skill_version"
ORG_REVIEW_INDEX = "ix_org_skill_version_org_review"


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    if SKILL not in _tables():
        op.create_table(
            SKILL,
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "organization_id",
                sa.Integer(),
                sa.ForeignKey("organizations.id"),
                nullable=False,
                comment="所属组织（租户隔离键）",
            ),
            sa.Column(
                "name",
                sa.String(length=100),
                nullable=False,
                comment="技能名称（组织内唯一）",
            ),
            sa.Column(
                "description",
                sa.String(length=300),
                nullable=True,
                comment="技能说明（仅 UI 展示，不进入 system prompt）",
            ),
            sa.Column("status", sa.String(length=16), nullable=False, comment="active/archived"),
            sa.Column(
                "latest_version",
                sa.Integer(),
                nullable=False,
                comment="最新版本号（0=尚无版本）",
            ),
            sa.Column(
                "enabled_version_no",
                sa.Integer(),
                nullable=True,
                comment="当前启用版本号；NULL=未启用（唯一运行时指针）",
            ),
            sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True, comment="归档时间"),
            sa.Column("archived_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("lock_version", sa.Integer(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
                comment="创建时间",
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("organization_id", "name", name="uq_org_skill_org_name"),
            comment="组织级技能头（只归档，不物理删除）",
        )

    if VERSION not in _tables():
        op.create_table(
            VERSION,
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "skill_id",
                sa.String(length=36),
                sa.ForeignKey(SKILL + ".id"),
                nullable=False,
                comment="所属技能 id",
            ),
            sa.Column(
                "organization_id",
                sa.Integer(),
                sa.ForeignKey("organizations.id"),
                nullable=False,
                comment="所属组织（与技能头冗余保存，供组织删除保护与跨组织防护）",
            ),
            sa.Column("version_no", sa.Integer(), nullable=False, comment="版本号（每个技能自增）"),
            sa.Column(
                "payload_json",
                sa.JSON(),
                nullable=False,
                comment="结构化 payload（trigger_phrases + tool_sequence）",
            ),
            sa.Column(
                "payload_sha256",
                sa.String(length=64),
                nullable=False,
                comment="payload_json 规范化后的内容哈希",
            ),
            sa.Column(
                "source",
                sa.String(length=16),
                nullable=False,
                comment="run_proposal/manual",
            ),
            sa.Column(
                "provenance_json",
                sa.JSON(),
                nullable=True,
                comment="来源元数据（如来源任务 id；不存 payload 正文）",
            ),
            sa.Column(
                "review_status",
                sa.String(length=16),
                nullable=False,
                comment="pending/approved/rejected",
            ),
            sa.Column("reviewed_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True, comment="审核时间"),
            sa.Column(
                "review_note",
                sa.String(length=200),
                nullable=True,
                comment="审核备注（仅元数据）",
            ),
            sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
                comment="创建时间",
            ),
            sa.UniqueConstraint("skill_id", "version_no", name="uq_org_skill_version_no"),
            comment="组织技能不可变版本行",
        )
    if ORG_REVIEW_INDEX not in _indexes(VERSION):
        op.create_index(ORG_REVIEW_INDEX, VERSION, ["organization_id", "review_status"])


def downgrade() -> None:
    # 逆序删表：版本 → 技能头。索引随 drop_table 一并删除
    # （MySQL 1553：先删 FK 依赖的二级索引再删表会失败）。
    if VERSION in _tables():
        op.drop_table(VERSION)
    if SKILL in _tables():
        op.drop_table(SKILL)
