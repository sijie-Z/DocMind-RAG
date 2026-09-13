"""
Add prompt_template_versions and token_usage tables
"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
# revision id 必须 <= 32 字符：alembic 的 alembic_version.version_num 在 MySQL 上是
# VARCHAR(32)，超长会在写入版本号时报 1406 Data too long。
revision = '003_prompt_versions_token_usage'
down_revision = '002_add_user_api_key'
branch_labels = None
depends_on = None


def upgrade():
    # Prompt templates table
    # 必须先于 prompt_template_versions：后者 prompt_id 的外键指向本表。
    # 该表原先只在 005 创建，导致 003 在 MySQL 上引用不存在的表（错误 1824）。
    op.create_table(
        'prompt_templates',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('description', sa.String(length=255), nullable=True),
        sa.Column('category', sa.String(length=50), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        # 与模型一致：模型有 is_system 且运行时读写（agent/core_tools.py、api/v1/endpoints/prompts.py）
        sa.Column('is_system', sa.Boolean(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('creator_id', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['creator_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        mysql_engine='InnoDB',
    )
    op.create_index('ix_prompt_templates_id', 'prompt_templates', ['id'])

    # Prompt template versions table
    op.create_table(
        'prompt_template_versions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('prompt_id', sa.Integer(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('description', sa.String(length=255), nullable=True),
        sa.Column('change_note', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column('creator_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['prompt_id'], ['prompt_templates.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['creator_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_prompt_template_versions_prompt_id', 'prompt_template_versions', ['prompt_id'])
    op.create_index('ix_prompt_template_versions_id', 'prompt_template_versions', ['id'])

    # Token usage tracking table
    op.create_table(
        'token_usage_records',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=True),
        sa.Column('model', sa.String(length=100), nullable=False),
        sa.Column('source', sa.String(length=50), nullable=False, comment='rag_chat, agent, tool_call'),
        sa.Column('input_tokens', sa.Integer(), nullable=False),
        sa.Column('output_tokens', sa.Integer(), nullable=False),
        sa.Column('cost_usd', sa.Float(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_token_usage_records_user_id', 'token_usage_records', ['user_id'])
    op.create_index('ix_token_usage_records_organization_id', 'token_usage_records', ['organization_id'])
    op.create_index('ix_token_usage_records_created_at', 'token_usage_records', ['created_at'])
    op.create_index('ix_token_usage_records_id', 'token_usage_records', ['id'])


def downgrade():
    op.drop_table('token_usage_records')
    op.drop_table('prompt_template_versions')
    op.drop_table('prompt_templates')
