"""合同审查规则/playbook 版本快照（竞品对标 P1）。

新增 ``review_rule_set`` 表保存建案时固化的规则快照（version / payload_json /
payload_sha256 / published_at / organization_id），案件以 nullable FK 引用；
旧案件仍为 NULL，读取时回落当前 checklist，判定与文档/证据哈希不变。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "032_review_rule_snapshot"
down_revision = "031_review_severity"
branch_labels = None
depends_on = None


def _is_sqlite() -> bool:
    return op.get_bind().dialect.name == "sqlite"


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}


def _foreign_keys(table: str) -> set[str]:
    return {fk["name"] for fk in sa.inspect(op.get_bind()).get_foreign_keys(table) if fk["name"]}


def upgrade() -> None:
    # 幂等分步：MySQL DDL 非事务，上一次执行可能建了表却未推进版本戳。
    # 与 025 先例一致——已存在的对象跳过，缺失的补齐，不因 1050/1061 失败。
    if "review_rule_set" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "review_rule_set",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "organization_id",
                sa.Integer(),
                sa.ForeignKey("organizations.id"),
                nullable=True,
                comment="所属组织（旧数据可空）",
            ),
            sa.Column("version", sa.String(length=32), nullable=False, comment="规则版本标签"),
            sa.Column("payload_json", sa.JSON(), nullable=False, comment="规则快照 JSON"),
            sa.Column(
                "payload_sha256", sa.String(length=64), nullable=False, comment="快照内容哈希"
            ),
            sa.Column(
                "published_at", sa.DateTime(timezone=True), nullable=True, comment="发布时间"
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
                comment="创建时间",
            ),
            sa.UniqueConstraint(
                "organization_id", "payload_sha256", name="uq_review_rule_set_org_digest"
            ),
        )
    if "ix_review_rule_set_org_created" not in _indexes("review_rule_set"):
        op.create_index(
            "ix_review_rule_set_org_created", "review_rule_set", ["organization_id", "created_at"]
        )

    if "rule_snapshot_id" not in _columns("review_case"):
        op.add_column(
            "review_case",
            sa.Column(
                "rule_snapshot_id",
                sa.String(length=36),
                nullable=True,
                comment="规则快照 ID",
            ),
        )
    if "ix_review_case_rule_snapshot_id" not in _indexes("review_case"):
        op.create_index("ix_review_case_rule_snapshot_id", "review_case", ["rule_snapshot_id"])
    if not _is_sqlite():
        fk_name = "fk_review_case_rule_snapshot_id_review_rule_set"
        if fk_name not in _foreign_keys("review_case"):
            op.create_foreign_key(
                fk_name,
                "review_case",
                "review_rule_set",
                ["rule_snapshot_id"],
                ["id"],
            )


def downgrade() -> None:
    # 同样分步幂等：先摘外键/索引再删列，最后删表（MySQL 1553：索引仍被
    # 外键引用时不能直接删）。缺失的对象跳过，不因 1091/1146 失败。
    if "rule_snapshot_id" in _columns("review_case"):
        fk_name = "fk_review_case_rule_snapshot_id_review_rule_set"
        if not _is_sqlite() and fk_name in _foreign_keys("review_case"):
            op.drop_constraint(fk_name, "review_case", type_="foreignkey")
        if "ix_review_case_rule_snapshot_id" in _indexes("review_case"):
            op.drop_index("ix_review_case_rule_snapshot_id", table_name="review_case")
        op.drop_column("review_case", "rule_snapshot_id")
    if "review_rule_set" in sa.inspect(op.get_bind()).get_table_names():
        if "ix_review_rule_set_org_created" in _indexes("review_rule_set"):
            op.drop_index("ix_review_rule_set_org_created", table_name="review_rule_set")
        op.drop_table("review_rule_set")
