"""合同审查模板/playbook 用户可编辑库 + 版本历史（竞品批次五）。

新增两张表：
- ``review_template``：组织级模板头（名称/说明/状态/最新版本号）；
- ``review_template_version``：不可变版本行（items_json + payload_sha256），
  每次编辑追加一行，旧行永不改写。

``review_case`` 追加两列可空溯源指针 ``template_id`` / ``template_version_no``：
规则内容仍冻结在既有 ``rule_snapshot_id`` 快照中，这两列只回答「案件来自哪个
模板版本」。刻意不建外键——模板生命周期操作不得被案件反向外键阻塞；组织隔离
由服务层校验。

设计约束：
- 不提供物理删除（模板/版本是审计资产），归档用 ``status``/``archived_at``；
- 不改写 ``review_rule_set``、``review_case`` 既有数据，也不回填；
- MySQL DDL 非事务：upgrade/downgrade 按实际存在性幂等执行（与 032 先例一致）。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "038_review_template"
down_revision = "037_index_error_codes"
branch_labels = None
depends_on = None


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _columns(table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    tables = _tables()
    if "review_template" not in tables:
        op.create_table(
            "review_template",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "organization_id",
                sa.Integer(),
                sa.ForeignKey("organizations.id"),
                nullable=False,
                comment="所属组织（租户隔离键）",
            ),
            sa.Column("name", sa.String(length=100), nullable=False, comment="模板名称"),
            sa.Column("description", sa.String(length=500), nullable=True, comment="模板说明"),
            sa.Column("status", sa.String(length=16), nullable=False, comment="active/archived"),
            sa.Column(
                "latest_version",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
                comment="最新版本号（0=尚无版本）",
            ),
            sa.Column("created_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("updated_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True, comment="归档时间"),
            sa.Column("archived_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("lock_version", sa.Integer(), nullable=False, server_default=sa.text("1")),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
                comment="创建时间",
            ),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("organization_id", "name", name="uq_review_template_org_name"),
            comment="组织级合同审查模板头",
        )
    if "ix_review_template_org_created" not in _indexes("review_template"):
        op.create_index(
            "ix_review_template_org_created", "review_template", ["organization_id", "created_at"]
        )
    if "ix_review_template_org_status" not in _indexes("review_template"):
        op.create_index(
            "ix_review_template_org_status", "review_template", ["organization_id", "status"]
        )

    if "review_template_version" not in _tables():
        op.create_table(
            "review_template_version",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "template_id",
                sa.String(length=36),
                sa.ForeignKey("review_template.id"),
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
            sa.Column(
                "version_label", sa.String(length=64), nullable=False, comment="展示用版本标签"
            ),
            sa.Column("items_json", sa.JSON(), nullable=False, comment="规则项列表"),
            sa.Column("item_count", sa.Integer(), nullable=False, comment="规则项数"),
            sa.Column("payload_sha256", sa.String(length=64), nullable=False, comment="内容哈希"),
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
            sa.UniqueConstraint("template_id", "version_no", name="uq_review_template_version_no"),
            comment="模板不可变版本行",
        )
    version_indexes = _indexes("review_template_version")
    if "ix_review_template_version_template_id" not in version_indexes:
        op.create_index(
            "ix_review_template_version_template_id",
            "review_template_version",
            ["template_id"],
        )
    if "ix_review_template_version_organization_id" not in version_indexes:
        op.create_index(
            "ix_review_template_version_organization_id",
            "review_template_version",
            ["organization_id"],
        )
    if "ix_review_template_version_template_created" not in version_indexes:
        op.create_index(
            "ix_review_template_version_template_created",
            "review_template_version",
            ["template_id", "created_at"],
        )

    case_columns = _columns("review_case")
    if "template_id" not in case_columns:
        op.add_column(
            "review_case",
            sa.Column(
                "template_id",
                sa.String(length=36),
                nullable=True,
                comment="建案时选用的模板 id（溯源用，可空）",
            ),
        )
    if "template_version_no" not in case_columns:
        op.add_column(
            "review_case",
            sa.Column(
                "template_version_no",
                sa.Integer(),
                nullable=True,
                comment="建案时选用的模板版本号（溯源用，可空）",
            ),
        )
    if "ix_review_case_template_id" not in _indexes("review_case"):
        op.create_index("ix_review_case_template_id", "review_case", ["template_id"])


def downgrade() -> None:
    # 逆序分步幂等：先摘案件列/索引，再删版本索引与表，最后删模板表。
    if "template_id" in _columns("review_case"):
        if "ix_review_case_template_id" in _indexes("review_case"):
            op.drop_index("ix_review_case_template_id", table_name="review_case")
        op.drop_column("review_case", "template_id")
    if "template_version_no" in _columns("review_case"):
        op.drop_column("review_case", "template_version_no")

    if "review_template_version" in _tables():
        version_indexes = _indexes("review_template_version")
        for name in (
            "ix_review_template_version_template_created",
            "ix_review_template_version_organization_id",
            "ix_review_template_version_template_id",
        ):
            if name in version_indexes:
                op.drop_index(name, table_name="review_template_version")
        op.drop_table("review_template_version")

    if "review_template" in _tables():
        template_indexes = _indexes("review_template")
        for name in ("ix_review_template_org_status", "ix_review_template_org_created"):
            if name in template_indexes:
                op.drop_index(name, table_name="review_template")
        op.drop_table("review_template")
