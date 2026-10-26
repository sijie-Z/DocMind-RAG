"""RAG 原文溯源：document_chunks 增加可空 original_text。

Revision ID: 018_rag_source_provenance
Revises: 017_agent_message_task_id

- 现有 chunk_text 继续承载检索文本（可能含 LLM 背景增强）。
- original_text 只保存 parser 真实原文；旧行保持 NULL，绝不回填 chunk_text。
- 本迁移不重建文档、不改变向量或索引数据。

downgrade 会删除 original_text 列，只能由显式迁移操作执行；代码回滚不需要、
也不应自动执行 downgrade。
"""

import sqlalchemy as sa

from alembic import op

revision = "018_rag_source_provenance"
down_revision = "017_agent_message_task_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "document_chunks",
        sa.Column(
            "original_text",
            sa.Text(),
            nullable=True,
            comment="未加检索背景摘要的原文；legacy 分块为 NULL",
        ),
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("document_chunks") as batch:
            batch.drop_column("original_text")
    else:
        op.drop_column("document_chunks", "original_text")
