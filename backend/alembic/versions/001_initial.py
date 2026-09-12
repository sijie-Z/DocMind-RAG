"""
数据库迁移脚本 - 创建所有表
"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = '001_initial'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # 创建组织表
    # 必须先于 users：users.organization_id 的外键指向本表，
    # 而 MySQL 在 CREATE TABLE 时就会校验被引用表是否存在（错误 1824）。
    op.create_table(
        'organizations',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('parent_id', sa.Integer(), nullable=True),
        sa.Column('level', sa.Integer(), nullable=False),
        sa.Column('path', sa.String(length=500), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['parent_id'], ['organizations.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name')
    )

    # 创建用户表
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('username', sa.String(length=50), nullable=False),
        sa.Column('email', sa.String(length=100), nullable=False),
        sa.Column('hashed_password', sa.String(length=255), nullable=False),
        sa.Column('full_name', sa.String(length=100), nullable=True),
        sa.Column('organization_id', sa.Integer(), nullable=True),
        sa.Column('role', sa.String(length=20), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('is_superuser', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('last_login_at', sa.DateTime(), nullable=True),
        sa.Column('avatar_url', sa.String(length=500), nullable=True),
        sa.Column('phone', sa.String(length=20), nullable=True),
        sa.Column('department', sa.String(length=100), nullable=True),
        sa.Column('position', sa.String(length=100), nullable=True),
        sa.Column('preferences', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email'),
        sa.UniqueConstraint('username')
    )
    
    # 创建文档表
    op.create_table(
        'documents',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('filename', sa.String(length=255), nullable=False),
        sa.Column('file_path', sa.String(length=500), nullable=False),
        sa.Column('file_size', sa.Integer(), nullable=False),
        sa.Column('file_type', sa.Enum('PDF', 'WORD', 'EXCEL', 'PPT', 'TXT', 'OTHER', name='documenttype'), nullable=False),
        sa.Column('mime_type', sa.String(length=100), nullable=True),
        sa.Column('title', sa.String(length=500), nullable=True),
        sa.Column('author', sa.String(length=255), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('keywords', sa.JSON(), nullable=True),
        sa.Column('status', sa.Enum('PENDING', 'UPLOADED', 'PARSING', 'PARSED', 'INDEXED', 'FAILED', name='documentstatus'), nullable=False),
        sa.Column('parse_error', sa.Text(), nullable=True),
        sa.Column('content_length', sa.Integer(), nullable=True),
        sa.Column('chunk_count', sa.Integer(), nullable=False),
        # 修复：与模型一致使用 Integer（String(36) 导致 MySQL FK 类型不匹配 3780 错误）
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('uploaded_by', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('parsed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('indexed_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id']),
        sa.ForeignKeyConstraint(['uploaded_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id')
    )
    
    # 创建文档分块表
    op.create_table(
        'document_chunks',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('document_id', sa.String(length=36), nullable=False),
        sa.Column('chunk_index', sa.Integer(), nullable=False),
        sa.Column('chunk_text', sa.Text(), nullable=False),
        sa.Column('chunk_length', sa.Integer(), nullable=False),
        sa.Column('start_pos', sa.Integer(), nullable=True),
        sa.Column('end_pos', sa.Integer(), nullable=True),
        sa.Column('page_number', sa.Integer(), nullable=True),
        sa.Column('section_title', sa.String(length=500), nullable=True),
        sa.Column('embedding_id', sa.String(length=36), nullable=True),
        sa.Column('metadata', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['document_id'], ['documents.id']),
        sa.PrimaryKeyConstraint('id')
    )
    
    # 创建标签表
    op.create_table(
        'tags',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('color', sa.String(length=7), nullable=True),
        sa.Column('description', sa.String(length=500), nullable=True),
        sa.Column('document_count', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name')
    )
    
    # 创建文档标签关联表
    op.create_table(
        'document_tags',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('document_id', sa.String(length=36), nullable=False),
        sa.Column('tag_id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['document_id'], ['documents.id']),
        sa.ForeignKeyConstraint(['tag_id'], ['tags.id']),
        sa.PrimaryKeyConstraint('id')
    )
    
    # 创建聊天会话表
    op.create_table(
        'chat_sessions',
        sa.Column('id', sa.String(length=36), nullable=False),
        # 修复：与模型一致使用 Integer（String(36) 导致 MySQL FK 类型不匹配 3780 错误）
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=True),
        sa.Column('title', sa.String(length=200), nullable=True),
        sa.Column('status', sa.Enum('ACTIVE', 'PAUSED', 'ENDED', name='chatsessionstatus'), nullable=False),
        sa.Column('message_count', sa.Integer(), nullable=False),
        sa.Column('last_message_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('settings', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['organization_id'], ['organizations.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id')
    )
    
    # 创建聊天消息表
    op.create_table(
        'chat_messages',
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('session_id', sa.String(length=36), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('message_type', sa.Enum('USER', 'ASSISTANT', 'SYSTEM', name='messagetype'), nullable=False),
        sa.Column('metadata', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['session_id'], ['chat_sessions.id']),
        sa.PrimaryKeyConstraint('id')
    )
    
    # 创建索引
    op.create_index('ix_users_email', 'users', ['email'], unique=True)
    op.create_index('ix_users_username', 'users', ['username'], unique=True)
    op.create_index('ix_documents_id', 'documents', ['id'], unique=True)
    op.create_index('ix_documents_organization_id', 'documents', ['organization_id'])
    op.create_index('ix_documents_uploaded_by', 'documents', ['uploaded_by'])
    op.create_index('ix_documents_status', 'documents', ['status'])
    op.create_index('ix_document_chunks_document_id', 'document_chunks', ['document_id'])
    op.create_index('ix_document_chunks_embedding_id', 'document_chunks', ['embedding_id'])
    op.create_index('ix_chat_sessions_user_id', 'chat_sessions', ['user_id'])
    op.create_index('ix_chat_sessions_organization_id', 'chat_sessions', ['organization_id'])
    op.create_index('ix_chat_messages_session_id', 'chat_messages', ['session_id'])


def downgrade():
    # 只删表即可，索引随表一并消失。
    # 不要显式 DROP INDEX：MySQL 会拒绝删除外键仍在引用的索引（错误 1553）。
    # 顺序与 upgrade 相反——先删引用 organizations 的 users。
    op.drop_table('chat_messages')
    op.drop_table('chat_sessions')
    op.drop_table('document_tags')
    op.drop_table('tags')
    op.drop_table('document_chunks')
    op.drop_table('documents')
    op.drop_table('users')
    op.drop_table('organizations')

    # PostgreSQL 下 sa.Enum 是独立 TYPE，需要显式删除；
    # MySQL/SQLite 的枚举内联在表定义里，随表一起消失。
    if op.get_bind().dialect.name == 'postgresql':
        for enum_name in ('messagetype', 'chatsessionstatus', 'documentstatus', 'documenttype'):
            op.execute(f'DROP TYPE IF EXISTS {enum_name}')
