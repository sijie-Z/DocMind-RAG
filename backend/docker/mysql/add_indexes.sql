-- 添加缺失的数据库索引 - 优化查询性能
-- 执行时间: 约 10-30 秒

-- ChatSession 表索引
ALTER TABLE chat_sessions ADD INDEX IF NOT EXISTS ix_chat_sessions_user_id (user_id);
ALTER TABLE chat_sessions ADD INDEX IF NOT EXISTS ix_chat_sessions_organization_id (organization_id);

-- ChatMessage 表索引
ALTER TABLE chat_messages ADD INDEX IF NOT EXISTS ix_chat_messages_session_id (session_id);

-- Document 表索引
ALTER TABLE documents ADD INDEX IF NOT EXISTS ix_documents_organization_id (organization_id);
ALTER TABLE documents ADD INDEX IF NOT EXISTS ix_documents_uploaded_by (uploaded_by);
ALTER TABLE documents ADD INDEX IF NOT EXISTS ix_documents_status (status);

-- DocumentChunk 表索引
ALTER TABLE document_chunks ADD INDEX IF NOT EXISTS ix_document_chunks_document_id (document_id);

-- DocumentTag 表索引
ALTER TABLE document_tags ADD INDEX IF NOT EXISTS ix_document_tags_document_id (document_id);
ALTER TABLE document_tags ADD INDEX IF NOT EXISTS ix_document_tags_tag_id (tag_id);
