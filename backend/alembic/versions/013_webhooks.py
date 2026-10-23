"""Webhook runtime（issue #478）：webhook_rules / webhook_deliveries + chat_sessions 来源列。

Revision ID: 013_webhooks
Revises: 012_session_fork

与 ``app/models/webhook.py`` / ``app/models/chat.py`` 严格同形：
``tests/integration/test_schema_path_parity.py`` 会在真实 MySQL 上逐列比对
迁移路径与 ``create_all`` 路径（COLUMN_TYPE / IS_NULLABLE / COLUMN_DEFAULT / EXTRA）。

与 issue §5.1 的字段差异：``webhook_deliveries.payload`` 为新增列（issue 清单
只有 payload_hash）——异步 outbox 必须在请求结束后还原 payload，否则 poller
无法渲染 prompt，见 ADR-005「与 issue 的偏离」。

另：``webhook_deliveries.payload`` / ``chat_messages.content`` /
``agent_tasks.query`` 在 MySQL 上为 MEDIUMTEXT（16 MiB）。webhook body 上限
默认 1 MiB，而 MySQL TEXT 只有 64 KiB——否则合法签名请求会在落库时 DataError
（对抗评审 blocker B1/B2）。SQLite 无长度限制，保持 TEXT。

downgrade 有损：两表与其行全部删除（投递历史不可恢复）；chat_sessions 只删
来源列，会话与消息本身保留（webhook 会话退化为普通会话）；MEDIUMTEXT 列回退
为 TEXT 前，超 65535 字节的行按 16000 字符截断（utf8mb4 下最坏 64000 字节），
超长 webhook prompt 丢失尾部。
"""

import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from alembic import op

revision = "013_webhooks"
down_revision = "012_session_fork"
branch_labels = None
depends_on = None

# 与模型枚举成员顺序一致（MySQL ENUM 的成员顺序参与 COLUMN_TYPE 比对）。
_PROVIDER_ENUM = sa.Enum("GENERIC", "GITHUB", name="webhookprovider")
_SESSION_MODE_ENUM = sa.Enum("NEW", "REUSE", name="webhooksessionmode")
_DELIVERY_STATUS_ENUM = sa.Enum(
    "RECEIVED", "PROCESSING", "SUCCEEDED", "FAILED", name="webhookdeliverystatus"
)

# webhook body 上限默认 1 MiB，MySQL TEXT 只有 64 KiB（评审 blocker B1/B2）。
# 三处会承接 1 MiB payload/渲染 prompt 的列在 MySQL 上用 MEDIUMTEXT（16 MiB）；
# 模型侧同形（Text().with_variant(MEDIUMTEXT, "mysql")），SQLite 保持 TEXT。
_PAYLOAD_TEXT = sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql")


def _dialect_name() -> str:
    return op.get_bind().dialect.name


def upgrade() -> None:
    op.create_table(
        "webhook_rules",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "org_id",
            sa.Integer(),
            sa.ForeignKey("organizations.id"),
            nullable=False,
            comment="组织ID",
        ),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=False,
            comment="服务账号（规则创建者，会话以此身份运行）",
        ),
        sa.Column("name", sa.String(length=100), nullable=False, comment="规则名"),
        sa.Column("provider", _PROVIDER_ENUM, nullable=False, comment="提供方"),
        sa.Column(
            "secret_encrypted",
            sa.Text(),
            nullable=False,
            comment="Fernet 密文（当前 secret）",
        ),
        sa.Column(
            "secret_prev_encrypted",
            sa.Text(),
            nullable=True,
            comment="轮换期旧 secret 密文（24h 内验签接受）",
        ),
        sa.Column("secret_rotated_at", sa.DateTime(), nullable=True, comment="最近一次轮换时间"),
        sa.Column("enabled", sa.Boolean(), nullable=False, comment="是否启用"),
        sa.Column(
            "prompt_template",
            sa.Text(),
            nullable=False,
            comment="提示词模板；仅允许 {payload}/{rule_id}/{delivery_id}",
        ),
        sa.Column("session_mode", _SESSION_MODE_ENUM, nullable=False, comment="会话模式"),
        sa.Column(
            "rate_limit_per_minute", sa.Integer(), nullable=False, comment="每规则+IP 每分钟限流"
        ),
        sa.Column(
            "max_body_bytes", sa.Integer(), nullable=False, comment="该规则允许的最大请求体字节数"
        ),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_webhook_rules_org_id", "webhook_rules", ["org_id"])

    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "rule_id",
            sa.String(length=36),
            sa.ForeignKey("webhook_rules.id", ondelete="CASCADE"),
            nullable=False,
            comment="规则ID",
        ),
        sa.Column("org_id", sa.Integer(), nullable=False, comment="组织ID（冗余）"),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("status", _DELIVERY_STATUS_ENUM, nullable=False, comment="投递状态"),
        sa.Column("payload_hash", sa.String(length=64), nullable=False, comment="原始 body sha256"),
        sa.Column(
            "payload", _PAYLOAD_TEXT, nullable=False, comment="原始 JSON 文本（poller 渲染用）"
        ),
        sa.Column("session_id", sa.String(length=36), nullable=True, comment="处理出的会话ID"),
        sa.Column("error", sa.Text(), nullable=True, comment="失败摘要（脱敏、限长）"),
        sa.Column("attempts", sa.Integer(), nullable=False, comment="已开始的尝试次数"),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("processing_started_at", sa.DateTime(), nullable=True),
        sa.Column("received_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("rule_id", "idempotency_key", name="uq_webhook_deliveries_rule_idem"),
    )
    op.create_index("ix_webhook_deliveries_rule_id", "webhook_deliveries", ["rule_id"])
    op.create_index("ix_webhook_deliveries_org_id", "webhook_deliveries", ["org_id"])
    op.create_index(
        "ix_webhook_deliveries_status_next_attempt",
        "webhook_deliveries",
        ["status", "next_attempt_at"],
    )
    op.create_index(
        "ix_webhook_deliveries_status_processing_started",
        "webhook_deliveries",
        ["status", "processing_started_at"],
    )

    # chat_sessions：来源列 + reuse 查找索引。source 带 server_default，存量行
    # 自动为 "user"（NOT NULL 可直接加）。
    op.add_column(
        "chat_sessions",
        sa.Column(
            "source",
            sa.String(length=32),
            nullable=False,
            server_default="user",
            comment="会话来源（user/webhook）",
        ),
    )
    op.add_column(
        "chat_sessions", sa.Column("webhook_rule_id", sa.String(length=36), nullable=True)
    )
    op.add_column(
        "chat_sessions", sa.Column("webhook_delivery_id", sa.String(length=36), nullable=True)
    )
    op.add_column(
        "chat_sessions", sa.Column("webhook_session_key", sa.String(length=128), nullable=True)
    )
    op.create_index(
        "ix_chat_sessions_webhook_rule_session_key",
        "chat_sessions",
        ["webhook_rule_id", "webhook_session_key"],
    )

    # 评审 blocker B1/B2：1 MiB 级渲染 prompt 会写进 chat_messages.content 与
    # agent_tasks.query（checkpoint 快照载体），两列在 MySQL 上从 TEXT(64 KiB)
    # 扩到 MEDIUMTEXT(16 MiB)。SQLite 无长度限制，保持 TEXT（与模型 create_all 同形）。
    if _dialect_name() == "mysql":
        op.alter_column(
            "chat_messages",
            "content",
            existing_type=sa.Text(),
            type_=mysql.MEDIUMTEXT(),
            existing_nullable=False,
        )
        op.alter_column(
            "agent_tasks",
            "query",
            existing_type=sa.Text(),
            type_=mysql.MEDIUMTEXT(),
            existing_nullable=False,
        )


def downgrade() -> None:
    op.drop_index("ix_chat_sessions_webhook_rule_session_key", table_name="chat_sessions")
    if _dialect_name() == "sqlite":
        with op.batch_alter_table("chat_sessions") as batch:
            for column in (
                "webhook_session_key",
                "webhook_delivery_id",
                "webhook_rule_id",
                "source",
            ):
                batch.drop_column(column)
    else:
        for column in (
            "webhook_session_key",
            "webhook_delivery_id",
            "webhook_rule_id",
            "source",
        ):
            op.drop_column("chat_sessions", column)

    # 丢表即可连带删除其上索引；MySQL 不允许单独 DROP 被外键约束依赖的索引
    # （ix_webhook_rules_org_id 服务于 organizations 外键），先删依赖方 deliveries。
    op.drop_table("webhook_deliveries")
    op.drop_table("webhook_rules")

    # 有损回退：MEDIUMTEXT → TEXT 前先截断超限行（16000 字符；utf8mb4 最坏
    # 4 字节/字符 = 64000 字节 < 65535）。webhook 生成的超长 prompt 会因此
    # 丢失尾部；历史普通消息通常远小于该阈值。
    if _dialect_name() == "mysql":
        bind = op.get_bind()
        bind.execute(
            sa.text(
                "UPDATE chat_messages SET content = LEFT(content, 16000) "
                "WHERE OCTET_LENGTH(content) > 65535"
            )
        )
        bind.execute(
            sa.text(
                "UPDATE agent_tasks SET query = LEFT(query, 16000) "
                "WHERE OCTET_LENGTH(query) > 65535"
            )
        )
        op.alter_column(
            "chat_messages",
            "content",
            existing_type=mysql.MEDIUMTEXT(),
            type_=sa.Text(),
            existing_nullable=False,
        )
        op.alter_column(
            "agent_tasks",
            "query",
            existing_type=mysql.MEDIUMTEXT(),
            type_=sa.Text(),
            existing_nullable=False,
        )
