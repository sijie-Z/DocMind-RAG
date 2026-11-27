"""公开知识库发布（批次十三）：``kb_publication`` + ``document_publication``。

新增两张表：

- ``kb_publication``：一次发布 = 组织 + 知识库 + token 摘要 + 标题/描述 +
  启用/过期/撤销状态。``token_hash`` 唯一（SHA-256，原文不落库）；
  ``knowledge_base_id`` **刻意不建外键** —— 知识库删除是既有管理动作（删除时
  把文档 ``knowledge_base_id`` 置空），发布记录不得让该动作在 MySQL 上以
  1451 失败；公开检索每次都会重新比对文档的 ``knowledge_base_id``，悬空发布
  不会召回任何文档。
- ``document_publication``：显式白名单行，``(publication_id, document_id)``
  唯一；``document_id`` 建外键，文档删除链路通过
  ``knowledge_service._DOCUMENT_CHILD_MODELS`` 清理白名单行。

关键约束（与 045/046 先例一致）：

- ``organization_id`` / ``created_by`` 建外键：租户隔离与组织删除保护
  （未转移时 MySQL 1451 拒绝删组织）；
- 除 ``created_at`` 外无 server_default（默认值由服务层/ORM 提供，避免迁移库
  与 create_all 库列级漂移）；
- ``created_at`` 使用 MySQL DATETIME(fsp=6) 变体，与 ORM 模型一致；
- MySQL DDL 非事务，upgrade 按对象实际存在性幂等执行；
  downgrade 逆序 drop_table，不逐条 drop_index（MySQL 1553）。
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision = "049_public_kb"
down_revision = "048_session_share_expiry"
branch_labels = None
depends_on = None

PUBLICATION = "kb_publication"
DOCUMENT_PUBLICATION = "document_publication"


def _tables() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in sa.inspect(op.get_bind()).get_indexes(table)}


def upgrade() -> None:
    if PUBLICATION not in _tables():
        op.create_table(
            PUBLICATION,
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "organization_id",
                sa.Integer(),
                sa.ForeignKey("organizations.id"),
                nullable=False,
                comment="所属组织（租户隔离键 / 组织删除保护）",
            ),
            sa.Column(
                "knowledge_base_id",
                sa.String(length=36),
                nullable=False,
                comment="发布的知识库 id（刻意不建外键：知识库删除不受发布记录阻塞）",
            ),
            sa.Column(
                "token_hash",
                sa.String(length=64),
                nullable=False,
                comment="公开 token 的 SHA-256 摘要（原文不落库）",
            ),
            sa.Column("title", sa.String(length=100), nullable=False, comment="公开页标题"),
            sa.Column(
                "description",
                sa.String(length=500),
                nullable=True,
                comment="公开页描述（可空；仅展示）",
            ),
            sa.Column(
                "enabled",
                sa.Boolean(),
                nullable=False,
                comment="启用开关（false 时公开面 404）",
            ),
            sa.Column(
                "expires_at",
                sa.DateTime(timezone=True),
                nullable=True,
                comment="过期时间（空 = 不过期；最长 90 天，服务层校验）",
            ),
            sa.Column(
                "created_by",
                sa.Integer(),
                sa.ForeignKey("users.id"),
                nullable=False,
                comment="发布创建/最近轮换的操作人",
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"),
                server_default=sa.func.now(),
                nullable=False,
                comment="创建/最近轮换时间",
            ),
            sa.Column(
                "revoked_at",
                sa.DateTime(timezone=True),
                nullable=True,
                comment="撤销时间（非空即失效；行保留供审计）",
            ),
            sa.UniqueConstraint("token_hash", name="uq_kb_publication_token_hash"),
            comment="公开知识库发布（显式白名单的只读检索入口）",
        )

    if DOCUMENT_PUBLICATION not in _tables():
        op.create_table(
            DOCUMENT_PUBLICATION,
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column(
                "organization_id",
                sa.Integer(),
                sa.ForeignKey("organizations.id"),
                nullable=False,
                comment="所属组织（冗余保存，供组织删除保护与跨组织防护）",
            ),
            sa.Column(
                "publication_id",
                sa.String(length=36),
                sa.ForeignKey(f"{PUBLICATION}.id"),
                nullable=False,
                comment="所属发布 id",
            ),
            sa.Column(
                "document_id",
                sa.String(length=36),
                sa.ForeignKey("documents.id"),
                nullable=False,
                comment="白名单文档 id（文档删除时随 _DOCUMENT_CHILD_MODELS 清理）",
            ),
            sa.Column(
                "created_by",
                sa.Integer(),
                sa.ForeignKey("users.id"),
                nullable=False,
                comment="加入白名单的操作人",
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True).with_variant(mysql.DATETIME(fsp=6), "mysql"),
                server_default=sa.func.now(),
                nullable=False,
                comment="加入白名单时间",
            ),
            sa.UniqueConstraint(
                "publication_id", "document_id", name="uq_document_publication_pub_doc"
            ),
            comment="公开知识库发布白名单（显式写入的文档才可匿名检索）",
        )

    # ORM 的 index=True 单列索引：create_all 会生成，迁移必须一并对齐。
    publication_indexes = _indexes(PUBLICATION)
    for column in ("organization_id", "knowledge_base_id"):
        name = f"ix_kb_publication_{column}"
        if name not in publication_indexes:
            op.create_index(name, PUBLICATION, [column])

    document_indexes = _indexes(DOCUMENT_PUBLICATION)
    for column in ("organization_id", "publication_id", "document_id"):
        name = f"ix_document_publication_{column}"
        if name not in document_indexes:
            op.create_index(name, DOCUMENT_PUBLICATION, [column])


def downgrade() -> None:
    # 逆序删表：白名单 → 发布头。索引随 drop_table 一并删除
    # （MySQL 1553：先删 FK 依赖的二级索引再删表会失败）。
    if DOCUMENT_PUBLICATION in _tables():
        op.drop_table(DOCUMENT_PUBLICATION)
    if PUBLICATION in _tables():
        op.drop_table(PUBLICATION)
