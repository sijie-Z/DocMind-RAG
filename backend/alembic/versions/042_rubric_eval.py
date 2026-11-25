"""Rubric 评测：评分表头 + 不可变版本 + 评测运行（竞品对标）。

新增三张表：
- ``eval_rubric``：组织级评分表头（名称/说明/状态/最新版本号）；
- ``eval_rubric_version``：不可变版本行（payload_json + payload_sha256），
  每次编辑追加一行，旧行永不改写；
- ``eval_rubric_run``：一次运行快照（冻结版本号/用例集哈希/逐条与聚合结果）。

关键约束：
- 三张表都以 ``organization_id`` 外键引用 organizations（组织删除保护）；
- ``eval_rubric_run`` 的 aggregate/results 可空：失败运行保留机器码但不产生
  任何分数（「测量失败」≠ 0 分）；
- 不改写任何既有表、不回填数据；MySQL DDL 非事务，upgrade/downgrade 按对象
  实际存在性幂等执行（与 039/040 先例一致）；
- downgrade 不逐个 drop_index（MySQL 1553：drop_table 会随表删除索引）。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "042_rubric_eval"
down_revision = "041_agent_artifact"
branch_labels = None
depends_on = None

RUBRIC = "eval_rubric"
VERSION = "eval_rubric_version"
RUN = "eval_rubric_run"


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    tables = _tables()
    if RUBRIC not in tables:
        op.create_table(
            RUBRIC,
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "organization_id",
                sa.Integer(),
                sa.ForeignKey("organizations.id"),
                nullable=False,
                comment="所属组织（租户隔离键）",
            ),
            sa.Column("name", sa.String(length=100), nullable=False, comment="评分表名称"),
            sa.Column("description", sa.String(length=500), nullable=True, comment="评分表说明"),
            sa.Column("status", sa.String(length=16), nullable=False, comment="active/archived"),
            sa.Column(
                "latest_version",
                sa.Integer(),
                nullable=False,
                comment="最新版本号（0=尚无版本）",
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
            sa.UniqueConstraint("organization_id", "name", name="uq_eval_rubric_org_name"),
            comment="组织级 rubric 评测评分表头",
        )
    rubric_indexes = _indexes(RUBRIC)
    if "ix_eval_rubric_org_created" not in rubric_indexes:
        op.create_index("ix_eval_rubric_org_created", RUBRIC, ["organization_id", "created_at"])
    if "ix_eval_rubric_org_status" not in rubric_indexes:
        op.create_index("ix_eval_rubric_org_status", RUBRIC, ["organization_id", "status"])

    if VERSION not in _tables():
        op.create_table(
            VERSION,
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "rubric_id",
                sa.String(length=36),
                sa.ForeignKey(RUBRIC + ".id"),
                nullable=False,
            ),
            sa.Column(
                "organization_id",
                sa.Integer(),
                sa.ForeignKey("organizations.id"),
                nullable=False,
                comment="所属组织（冗余保存，供组织删除保护与跨组织防护）",
            ),
            sa.Column("version_no", sa.Integer(), nullable=False, comment="版本号"),
            sa.Column("payload_json", sa.JSON(), nullable=False, comment="评分项列表"),
            sa.Column("payload_sha256", sa.String(length=64), nullable=False, comment="内容哈希"),
            sa.Column("item_count", sa.Integer(), nullable=False, comment="评分项数"),
            sa.Column("source", sa.String(length=16), nullable=False, comment="manual/bootstrap"),
            sa.Column("change_note", sa.String(length=200), nullable=True, comment="变更说明"),
            sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
                comment="创建时间",
            ),
            sa.UniqueConstraint("rubric_id", "version_no", name="uq_eval_rubric_version_no"),
            comment="rubric 不可变版本行",
        )
    version_indexes = _indexes(VERSION)
    if "ix_eval_rubric_version_rubric_id" not in version_indexes:
        op.create_index("ix_eval_rubric_version_rubric_id", VERSION, ["rubric_id"])
    if "ix_eval_rubric_version_organization_id" not in version_indexes:
        op.create_index("ix_eval_rubric_version_organization_id", VERSION, ["organization_id"])
    if "ix_eval_rubric_version_rubric_created" not in version_indexes:
        op.create_index(
            "ix_eval_rubric_version_rubric_created",
            VERSION,
            ["rubric_id", "created_at"],
        )

    if RUN not in _tables():
        op.create_table(
            RUN,
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "organization_id",
                sa.Integer(),
                sa.ForeignKey("organizations.id"),
                nullable=False,
                comment="所属组织（租户隔离键）",
            ),
            sa.Column(
                "rubric_id",
                sa.String(length=36),
                sa.ForeignKey(RUBRIC + ".id"),
                nullable=False,
                comment="评分表 id",
            ),
            sa.Column(
                "rubric_version_no",
                sa.Integer(),
                nullable=False,
                comment="本次运行使用的不可变版本号",
            ),
            sa.Column(
                "dataset_hash",
                sa.String(length=64),
                nullable=False,
                comment="用例集输入哈希（排序稳定）",
            ),
            sa.Column("mode", sa.String(length=16), nullable=False, comment="judge/fake"),
            sa.Column("status", sa.String(length=16), nullable=False, comment="completed/failed"),
            sa.Column(
                "aggregate",
                sa.JSON(),
                nullable=True,
                comment="聚合分数 + 逐问题通过率 + 校准报告（失败运行为空）",
            ),
            sa.Column(
                "results",
                sa.JSON(),
                nullable=True,
                comment="逐条结果（失败运行可为空；仅组织内授权用户可见）",
            ),
            sa.Column("case_count", sa.Integer(), nullable=False),
            sa.Column(
                "judge_model",
                sa.String(length=128),
                nullable=False,
                comment="评委模型名；fake 模式固定为 fake",
            ),
            sa.Column("error_code", sa.String(length=64), nullable=True, comment="失败机器码"),
            sa.Column(
                "created_by",
                sa.Integer(),
                sa.ForeignKey("users.id"),
                nullable=False,
                comment="运行发起人",
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
                comment="创建时间",
            ),
            sa.Column(
                "completed_at",
                sa.DateTime(timezone=True),
                nullable=True,
                comment="完成时间（成功与失败都记录）",
            ),
            comment="rubric 评测运行（不可变快照）",
        )
    run_indexes = _indexes(RUN)
    if "ix_eval_rubric_run_org_created" not in run_indexes:
        op.create_index("ix_eval_rubric_run_org_created", RUN, ["organization_id", "created_at"])
    if "ix_eval_rubric_run_rubric_created" not in run_indexes:
        op.create_index("ix_eval_rubric_run_rubric_created", RUN, ["rubric_id", "created_at"])


def downgrade() -> None:
    # 逆序删表：运行 → 版本 → 评分表头。索引随 drop_table 一并删除
    # （MySQL 1553：先删 FK 依赖的二级索引再删表会失败）。
    if RUN in _tables():
        op.drop_table(RUN)
    if VERSION in _tables():
        op.drop_table(VERSION)
    if RUBRIC in _tables():
        op.drop_table(RUBRIC)
