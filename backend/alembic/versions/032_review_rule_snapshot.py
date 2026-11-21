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


def upgrade() -> None:
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
        op.create_index("ix_review_case_rule_snapshot_id", "review_case", ["rule_snapshot_id"])
        if not _is_sqlite():
            op.create_foreign_key(
                "fk_review_case_rule_snapshot_id_review_rule_set",
                "review_case",
                "review_rule_set",
                ["rule_snapshot_id"],
                ["id"],
            )


def downgrade() -> None:
    existing = _columns("review_case")
    if "rule_snapshot_id" in existing:
        if not _is_sqlite():
            op.drop_constraint(
                "fk_review_case_rule_snapshot_id_review_rule_set",
                "review_case",
                type_="foreignkey",
            )
        op.drop_index("ix_review_case_rule_snapshot_id", table_name="review_case")
        op.drop_column("review_case", "rule_snapshot_id")
    op.drop_index("ix_review_rule_set_org_created", table_name="review_rule_set")
    op.drop_table("review_rule_set")
