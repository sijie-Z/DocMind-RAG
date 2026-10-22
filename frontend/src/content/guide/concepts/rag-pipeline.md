RAG（检索增强生成）负责在回答问题前，先从知识库里找到相关片段。DocMind 的检索链路完全建立在 Elasticsearch 上，没有额外的向量数据库。

## 检索链路

1. **查询向量化**：把用户问题转成 embedding。
2. **关键词臂**：在 Elasticsearch 中对多个字段做 `multi_match`（带字段权重），并叠加通配子句。
3. **向量臂**：用 `script_score` + `cosineSimilarity` 计算相似度，只召回带 embedding 的片段。
4. **融合**：两路结果用倒数排名融合（RRF，k=60）合并。
5. **后处理**：可选的 MMR 多样性选择、重排序（rerank）、单文档多样性上限，最后截取 `top_k`。

关键词臂和向量臂都会带上组织过滤条件，只在本组织的文档范围内检索。

## top_k 的优先级

- 请求显式传入的 `top_k` 优先；
- 否则使用组织设置中的「检索 top_k」；
- 否则使用平台默认值 10。

聊天场景另有默认：带附件文档时为 8，普通检索为 5（仍会被组织设置覆盖）。组织设置允许 1–50。

## 来源与页码

- 对话来源会返回文件名、正文片段、相关度、排名等字段。
- `pageNumber` 只对 PDF 等能记录页范围的格式存在；非 PDF 格式没有页码。
- Agent 的 `search_knowledge_base` 工具返回文件名、文档 ID 与分数，不包含页码。

## 依据（代码事实）

- `backend/app/rag/retriever.py`（关键词臂、向量臂、RRF、组织过滤）
- `backend/app/rag/pipeline.py`（后处理与 top_k）
- `backend/app/services/chat_service.py`（聊天默认 top_k 与来源结构）
- `backend/app/api/v1/endpoints/knowledge.py`（搜索接口与 top_k 优先级）
- `backend/app/core/config/ai.py`（平台默认值 10）
- `backend/app/core/elasticsearch.py`（单一索引与向量字段）
