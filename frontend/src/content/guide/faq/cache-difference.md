DocMind 有两类检索缓存，目的都是减少重复计算，但匹配方式、默认状态与寿命不同。

## 精确缓存（Exact）

- 匹配方式：把「组织 + top_k + 模式 + 规范化后的问题」拼成缓存键，完全一致才命中。
- 存储：Redis，键前缀 `rag:cache:`，TTL 600 秒；Redis 不可用时退回进程内 LRU（最多 1000 条）。
- 默认状态：开启（`RAG_ENABLE_CACHE=True`）。

## 语义缓存（Semantic）

- 匹配方式：对问题做 embedding，按量化后的向量相似度检索；余弦相似度阈值 0.92，并且还要通过 `queries_equivalent` 的文本等价校验（数字、否定、条款编号、实体词等）。
- 存储：Redis 有序集合，TTL 3600 秒。
- 命名空间：`rag:sem:` 存完整答案，`rag:sem:retrieval:` 存检索来源（答案为空时）。
- 默认状态：关闭。`SEMANTIC_CACHE_RETRIEVAL_ENABLED` 与 `SEMANTIC_CACHE_ANSWER_ENABLED` 默认都是 false。

## 共同点与使用提示

- 两类缓存都按组织命名空间隔离；RAG 调用链在缺少组织上下文时会提前拒绝请求。
- 文档删除或索引重建会清理该组织的两类缓存。
- 对话页的调试面板会分别显示「缓存命中(exact)」与「缓存命中(semantic)」；知识库指标卡有「缓存命中 / 重试」。
- 前端没有缓存设置界面；语义缓存需要部署方修改配置并重启服务后才会生效。

## 依据（代码事实）

- `backend/app/rag/cache.py`（两类缓存实现）
- `backend/app/core/config/ai.py`（默认开关与 TTL）
- `backend/app/rag/pipeline.py`（语义缓存的启用判断）
- `frontend/src/views/chat/components/DebugPanel.vue`（命中标记）
- `frontend/src/views/knowledge/index.vue`（缓存命中指标）
