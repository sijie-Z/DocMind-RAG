知识库页面负责文档的上传、状态跟踪、查看与删除，也是组织内所有可检索资料的入口。

## 上传与状态

上传格式、大小限制、状态含义与失败处理，详见「快速开始 / 上传第一份文档」。核心口径：

- 支持 PDF、Word（.docx）、Excel（.xlsx）、TXT、Markdown、CSV 与常见图片；图片只做 OCR。
- 不支持 .doc、.ppt / .pptx。
- 单文件上限 100MB，上传是异步处理，状态变为「已索引」后才可检索。

## 列表与查看

- 支持网格/列表两种视图，分页大小 12/24/48。
- 网格卡片上的状态只用图标表示；列表视图会显示中文状态文字。
- 「查看」打开只读详情弹窗，没有编辑、重命名或下载操作。
- 支持单个删除与批量删除，删除不可恢复。

## 搜索

页面顶部的搜索框只按文档标题过滤（后端只对标题做包含匹配），不搜索正文。要按内容语义搜索，请使用左侧菜单的「语义搜索」页面。

## 重建索引

- 单个失败文档：状态为「失败」时出现「重新解析」。
- 批量失败：工具栏「最近失败文档重建」。
- 接口按文档重建：删除该文档的索引分块，重置状态并重新入队；重建与删除都会清理该组织的 RAG 缓存。
- 重建权限为 `knowledge_base:rebuild_index`，普通成员默认没有；界面上按钮可能可见，但请求会返回 403。

## 其他入口

- 「知识图谱」：跳转到 `/knowledge/graph` 查看文档关系图。
- 顶部 RAG 指标卡：检索命中率、有据回答率、平均延迟、缓存命中 / 重试，数据来自 `/chat/metrics`。

## 依据（代码事实）

- `frontend/src/views/knowledge/index.vue`
- `frontend/src/views/knowledge/components/KnowledgeUploadPanel.vue`
- `backend/app/api/v1/endpoints/knowledge.py`（标题搜索、重建接口与权限）
- `backend/app/services/permission_service.py`（重建权限归属）
- `backend/app/worker/doc_processor.py`（重建后的后台处理）
