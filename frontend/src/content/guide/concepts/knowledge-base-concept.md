DocMind 没有「创建多个知识库」的概念。每个组织拥有一个逻辑上的知识库：组织内的所有文档放在一起，检索时只在本组织范围内进行。

## 实现方式

- 数据模型里没有 `KnowledgeBase` 表。知识库页面列出的是当前组织下的文档（`Document.organization_id`）。
- 所有组织共用一个 Elasticsearch 索引；每个分块都带 `organization_id` 字段，检索时用 term 过滤强制隔离。
- 组织级检索设置（如 top_k）保存在 `organization_settings`，每个组织一条。

## 这意味着什么

- 同一组织内的成员共享文档与检索结果；换到另一个组织，看到的是另一套文档。
- 你无法把文档「移动」到另一个知识库，因为不存在多知识库实体；只能切换组织或删除后重新上传。
- 跨组织访问文档会被拒绝或返回 404；文件代理也会校验对象路径中的组织前缀。

## 依据（代码事实）

- `backend/app/models/document.py`（`organization_id`）
- `backend/app/core/elasticsearch.py`（单一索引与组织字段）
- `backend/app/rag/retriever.py`（组织 term 过滤）
- `backend/app/models/organization_settings.py`（组织级设置）
- `backend/app/api/v1/endpoints/knowledge.py`（按组织列出文档）
- `backend/app/core/security.py`（文档访问的组织校验）
