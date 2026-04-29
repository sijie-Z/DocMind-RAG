"""
数据库迁移脚本 - 添加用户api_key字段
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '002_add_user_api_key'
down_revision = '001_initial'
branch_labels = None
depends_on = None


def upgrade():
    # 添加 api_key 字段到 users 表
    op.add_column('users', sa.Column('api_key', sa.String(length=100), nullable=True))


def downgrade():
    # 删除 api_key 字段
    op.drop_column('users', 'api_key')
