# DocMind · 企业级 RAG 知识库系统

基于 **RAG（检索增强生成）** 架构的企业级 AI 知识库系统，全栈 Python + Vue 3 实现，支持文档上传、混合检索与流式智能问答，适合作为简历项目展示大模型落地与工程化能力。

---

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | FastAPI、SQLAlchemy（async）、MySQL 8、Redis、Elasticsearch 8、Kafka、MinIO |
| AI/RAG | DeepSeek API、OpenAI 兼容 Embedding、LangChain 文档解析与分块 |
| 前端 | Vue 3、TypeScript、Vite、Naive UI、Pinia、Vue Router |
| 安全 | JWT、RBAC、组织级权限 |

---

## 核心功能

- **文档上传与处理**：多格式（PDF/Word/TXT 等）上传至 MinIO，经 Kafka 异步任务由 Worker 解析、分块、向量化后写入 Elasticsearch。
- **混合检索**：关键词（multi_match）+ 向量（knn/script_score）混合检索，并按组织 ID 做权限过滤。
- **流式对话**：WebSocket 实时通信，RAG 检索上下文 + DeepSeek 流式生成，支持多轮对话。
- **知识库管理**：列表、搜索、删除、重建索引；统计卡片（文档总数 / 已索引 / 处理中 / 失败）。
- **权限与多租户**：基于组织与用户的访问控制，文档归属与检索隔离。

---

## 项目结构

```
1_demo/
├── backend/                 # FastAPI 后端
│   ├── app/
│   │   ├── api/v1/endpoints/ # 接口：auth, documents, chat, knowledge, ...
│   │   ├── core/             # 配置、数据库、ES、Kafka、MinIO、安全
│   │   ├── models/           # 数据模型
│   │   ├── services/         # RAG、Embedding、文档解析、知识库服务
│   │   └── worker/           # 应用内任务（可选）
│   ├── worker/               # 独立 RAG Worker（消费 Kafka，写 ES）
│   │   └── doc_consumer.py
│   ├── requirements.txt
│   └── .env.example
├── frontend/                 # Vue 3 前端
│   ├── src/
│   │   ├── views/            # 聊天、知识库、登录、首页等
│   │   ├── api/              # 接口封装
│   │   ├── stores/           # Pinia
│   │   └── layouts/          # 布局与侧栏
│   └── package.json
└── README.md                 # 本文件
```

---

## 本地运行

### 环境准备

- Python 3.10+
- Node.js 18+
- MySQL 8、Redis、Elasticsearch 8、Kafka、MinIO（可用 Docker 一键起）

### 1. 后端

```bash
cd backend
cp .env.example .env
# 编辑 .env：DATABASE_URL、REDIS_HOST、ELASTICSEARCH_HOSTS、KAFKA_BOOTSTRAP_SERVERS、MINIO_*、DEEPSEEK_API_KEY、EMBEDDING_*
pip install -r requirements.txt
alembic upgrade head   # 若有迁移
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

API 文档：http://localhost:8000/docs

### 2. RAG Worker（消费 Kafka，解析文档并写 ES）

```bash
cd backend
python -m worker.doc_consumer
```

确保 Kafka topic `file-processing` 存在；上传文档后由后端发消息，Worker 消费并完成解析→向量化→写入 ES。

### 3. 前端

```bash
cd frontend
npm install
npm run dev
```

访问：http://localhost:5173

---

## 数据流简述

1. **上传**：用户在前端上传文件 → 后端写入 MinIO 与 DB（Document），并向 Kafka 发送 `file-processing` 消息。
2. **处理**：Worker 消费消息 → 从 MinIO 下载文件 → 解析分块（LangChain）→ 调用 Embedding API → 批量写入 Elasticsearch（字段：document_id、content、embedding、organization_id、filename 等）。
3. **问答**：用户发送问题 → WebSocket 到后端 → RAG 服务：查询向量化 + ES 混合检索（带 organization_id 过滤）→ 上下文 + 问题交给 DeepSeek 流式生成 → 结果与来源回传前端。

---

## 简历 / 面试亮点

- **RAG 全链路**：文档解析 → 分块 → 向量化 → 存储 → 混合检索 → Prompt 构建 → 流式生成，可清晰讲清每一环。
- **企业级组件**：Kafka 异步解耦、Redis 缓存、MinIO 对象存储、Elasticsearch 向量+关键词检索、JWT+RBAC。
- **可落地**：前后端分离、接口规范、错误处理与状态管理（如文档处理中/已完成/失败），便于演示与扩展。
- **可扩展**：可在此基础上增加分片上传与断点续传、RRF 融合与 Reranker、多租户与审计等。

---

## 配置说明

关键环境变量见 `backend/.env.example`，包括：

- `DATABASE_URL`：MySQL 连接串（建议 `mysql+aiomysql://...`）
- `KAFKA_FILE_PROCESSING_TOPIC`：文档处理 topic（默认 `file-processing`）
- `ELASTICSEARCH_INDEX_NAME`：索引名（默认 `paicongming_knowledge`）
- `DEEPSEEK_API_KEY` / `DEEPSEEK_API_URL` / `DEEPSEEK_MODEL`
- `EMBEDDING_API_KEY` / `EMBEDDING_API_URL` / `EMBEDDING_MODEL`（可选，可与 DeepSeek 共用或单独配置）

---

## 许可证

MIT
