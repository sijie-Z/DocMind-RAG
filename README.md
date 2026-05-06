<p align="center">
  <h1 align="center">DocMind</h1>
  <p align="center"><strong>企业级 RAG 智能知识库系统</strong></p>
  <p align="center">
    <img src="https://img.shields.io/badge/Python-3.10+-blue?logo=python" alt="Python">
    <img src="https://img.shields.io/badge/FastAPI-0.104+-009688?logo=fastapi" alt="FastAPI">
    <img src="https://img.shields.io/badge/Vue-3.4-4FC08D?logo=vuedotjs" alt="Vue 3">
    <img src="https://img.shields.io/badge/TypeScript-5.3-3178C6?logo=typescript" alt="TS">
    <img src="https://img.shields.io/badge/Elasticsearch-8.11-005571?logo=elasticsearch" alt="ES">
    <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
  </p>
</p>

---

## 简介

DocMind 是一个基于 **RAG（Retrieval-Augmented Generation）** 架构的全栈 AI 知识库系统。上传你的文档后，系统自动完成解析、分块、向量化与索引，用户可通过自然语言对话，获得基于文档内容的精准、可溯源的 AI 回答。

适用于企业知识管理、技术文档问答、客户支持知识库等场景，也可作为大模型落地的全栈参考实现。

---

## 系统架构

```
┌──────────────┐     ┌──────────────────────────────────────────────────┐
│   Browser    │     │                 Backend (FastAPI)                 │
│              │     │                                                  │
│  Vue 3 SPA  ◄┼─────┼►  REST API / WebSocket / SSE                    │
│  (Naive UI) │     │                                                  │
│              │     │  ┌──────────┐  ┌──────────┐  ┌──────────────┐  │
└──────────────┘     │  │  Auth    │  │  Chat    │  │  Knowledge   │  │
                     │  │  Service │  │  Service │  │  Service     │  │
                     │  └──────────┘  └────┬─────┘  └──────┬───────┘  │
                     │                    │                 │           │
                     │           ┌────────▼─────────┐       │           │
                     │           │   RAG Pipeline    │       │           │
                     │           │ ┌───────────────┐ │       │           │
                     │           │ │ Hybrid Search  │ │       │           │
                     │           │ │ BM25 + Vector  │ │       │           │
                     │           │ └───────────────┘ │       │           │
                     │           └────────┬──────────┘       │           │
                     │                    │                   │           │
                     └────────────────────┼───────────────────┘           │
                                          │                               │
            ┌─────────────────────────────┼───────────────────────────┐   │
            │           Infrastructure    │                           │   │
            │                             │                           │   │
            │  ┌──────────┐  ┌──────────┐ │ ┌──────────┐ ┌────────┐  │   │
            │  │  MySQL   │  │  Redis   │   │   MinIO  │ │ Kafka  │  │   │
            │  │  元数据  │  │  缓存    │   │  文件存储│ │ 消息队列│  │   │
            │  └──────────┘  └──────────┘ │ └──────────┘ └───┬────┘  │   │
            │                             │                   │       │   │
            │  ┌──────────────────────────┴──┐        ┌──────▼────┐  │   │
            │  │  Elasticsearch              │        │  Worker    │  │   │
            │  │  向量 + 全文索引            │◄───────│  文档处理  │  │   │
            │  └─────────────────────────────┘        └───────────┘  │   │
            │                                                       │   │
            └───────────────────────────────────────────────────────┘   │
                                                                        │
                     ┌──────────────────────────────┐                   │
                     │        AI Services           │                   │
                     │ ┌────────────┐ ┌───────────┐ │                   │
                     │ │  DeepSeek  │ │ Embedding │ │                   │
                     │ │  LLM 生成  │ │  向量化   │ │                   │
                     │ └────────────┘ └───────────┘ │                   │
                     └──────────────────────────────┘                   │
```

---

## 核心特性

### 文档智能处理
- 支持 **PDF / Word / Excel / TXT / Markdown** 等多种格式
- 基于 **LangChain** 的智能分块策略（滑动窗口 + 语义分块）
- **Kafka 异步消息队列**解耦上传与处理，支持高并发

### 混合检索
- **BM25 关键词匹配** + **向量语义检索**双路召回
- **Reranker 重排序**提升检索精度
- **组织级多租户隔离**，确保数据安全

### 流式智能问答
- **WebSocket 实时通信**，逐字流式输出
- 支持 **多轮对话**上下文记忆
- 答案附带 **来源引用**，可追溯原始文档

### 企业级基础设施
- **RBAC 权限体系**：用户 → 角色 → 组织三级管控
- **JWT 认证** + API Key 双模式
- **Prometheus + Grafana** 全链路监控告警
- **审计日志**全量记录操作轨迹

### 可视化工作流编排
- 拖拽式 **DAG 工作流编辑器**
- 内置 LLM / API / Code / Condition / Memory 等节点
- 实时调试与执行追踪

---

## 技术栈

| 层级 | 技术选型 |
|------|----------|
| **后端框架** | FastAPI + Uvicorn（async） |
| **数据库** | MySQL 8 + SQLAlchemy 2.0（async）+ Alembic 迁移 |
| **缓存** | Redis 5 |
| **搜索引擎** | Elasticsearch 8（KNN 向量 + BM25 全文） |
| **消息队列** | Kafka（aiokafka） |
| **对象存储** | MinIO（S3 兼容） |
| **AI 模型** | DeepSeek / OpenAI 兼容 API（Chat + Embedding + Rerank） |
| **文档解析** | LangChain + PyPDF + Unstructured + python-docx |
| **前端框架** | Vue 3 + TypeScript + Vite |
| **UI 组件** | Naive UI + ECharts + Vue Flow |
| **状态管理** | Pinia |
| **国际化** | Vue I18n（中 / 英 / 日 / 法） |
| **监控** | Prometheus + Grafana + AlertManager |
| **安全** | JWT + RBAC + 多租户 + 审计日志 |

---

## 快速开始

### 前置条件

- **Docker Desktop**（推荐，一键启动所有中间件）
- 或手动安装：Python 3.10+ / Node.js 18+ / MySQL 8 / Redis / Elasticsearch 8 / Kafka / MinIO

### 方式一：一键启动（Docker Compose）

```bash
# 1. 克隆仓库
git clone https://github.com/sijie-Z/DocMind-RAG.git
cd DocMind-RAG

# 2. 启动基础设施
cd backend
docker-compose up -d

# 3. 配置环境变量
cp .env.example .env
# 编辑 .env，填入你的 AI API Key（DeepSeek / OpenAI 等）

# 4. 启动后端 + Worker
pip install -r requirements.txt
alembic upgrade head
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload &
python -m worker.doc_consumer &

# 5. 启动前端
cd ../frontend
npm install
npm run dev
```

### 方式二：Windows 一键启动

```bash
# 双击运行
start_windows.bat
```

### 访问地址

| 服务 | 地址 |
|------|------|
| 前端界面 | http://localhost:5173 |
| API 文档（Swagger） | http://localhost:8000/docs |
| Grafana 监控 | http://localhost:3000 |

> **测试账号**：`guest` / `123456`

### 环境变量配置

关键配置项（完整列表见 `backend/.env.example`）：

```bash
# --- AI 模型 ---
DEEPSEEK_API_KEY=sk-xxx           # LLM API Key
DEEPSEEK_API_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat

EMBEDDING_API_KEY=sk-xxx          # Embedding API Key
EMBEDDING_API_URL=https://api.openai.com/v1
EMBEDDING_MODEL=text-embedding-3-small

# --- 基础设施 ---
DATABASE_URL=mysql+aiomysql://root:root@localhost:3306/paicongming_db
REDIS_HOST=localhost
ELASTICSEARCH_HOSTS=["http://localhost:9200"]
KAFKA_BOOTSTRAP_SERVERS=localhost:9092
MINIO_ENDPOINT=localhost:9000

# --- 安全 ---
SECRET_KEY=your-secret-key
JWT_SECRET_KEY=your-jwt-secret-key
```

---

## 数据流程

### 文档入库

```
用户上传 ──► MinIO 存储 ──► MySQL 记录 ──► Kafka 消息 ──► Worker 消费
                                                              │
                                    文档解析（LangChain）◄────┘
                                           │
                                    智能分块（滑动窗口）
                                           │
                                    Embedding 向量化
                                           │
                                    写入 Elasticsearch ✅
```

### 智能问答

```
用户提问 ──► WebSocket ──► 问题向量化 ──► ES 混合检索（BM25 + KNN）
                                                 │
                                    带权限过滤的结果集
                                                 │
                              上下文 + 历史消息 ──► LLM 流式生成
                                                 │
                                    答案 + 引用来源 ──► 前端渲染
```

---

## 项目结构

```
DocMind/
├── backend/                         # FastAPI 后端
│   ├── app/
│   │   ├── api/v1/endpoints/        # 15 个 API 模块（auth, chat, knowledge, workflow...）
│   │   ├── core/                    # 基础设施层（DB, ES, Kafka, MinIO, Redis, 安全）
│   │   ├── models/                  # SQLAlchemy 数据模型（11 张表）
│   │   ├── schemas/                 # Pydantic 请求/响应模型
│   │   └── services/                # 业务服务层（RAG, Chat, Auth, Workflow...）
│   ├── lib/rag/                     # RAG 工具库（chunk, vectorizer, retriever, hybrid, BM25）
│   ├── worker/                      # 独立 Kafka 消费者（文档处理 Worker）
│   ├── config/                      # Prometheus + Grafana 监控配置
│   ├── docker-compose.yml           # 基础设施容器编排
│   ├── requirements.txt
│   └── .env.example
├── frontend/                        # Vue 3 前端
│   ├── src/
│   │   ├── views/                   # 25+ 页面（chat, knowledge, workflow, admin...）
│   │   ├── api/                     # 15 个 API 模块封装
│   │   ├── stores/                  # Pinia 状态管理
│   │   ├── composables/             # 组合式函数（debounce, prefetch, errorHandler）
│   │   ├── utils/                   # 工具函数（WebSocket, SSE, auth）
│   │   └── locales/                 # 国际化（zh / en / ja / fr）
│   └── package.json
├── deploy/monitoring/               # 生产监控部署（Prometheus + Grafana + AlertManager）
├── docs/                            # 项目文档
├── start_windows.bat                # Windows 一键启动
└── start.sh                         # Linux/Mac 一键启动
```

---

## API 概览

| 模块 | 路径 | 功能 |
|------|------|------|
| 认证 | `/api/v1/auth/*` | 登录、注册、Token 刷新 |
| 用户 | `/api/v1/users/*` | 用户 CRUD、角色分配 |
| 知识库 | `/api/v1/knowledge/*` | 文档上传、检索、索引管理 |
| 聊天 | `/api/v1/chat/*` | WebSocket 流式对话、会话管理 |
| 工作流 | `/api/v1/workflow/*` | DAG 可视化编排 |
| 组织 | `/api/v1/organizations/*` | 多租户管理 |
| 监控 | `/api/v1/monitoring/*` | 系统指标、健康检查 |
| 审计 | `/api/v1/audit/*` | 操作日志查询 |

完整 API 文档启动后端后访问：http://localhost:8000/docs

---

## 许可证

## 维护者

- **sijieZ** — [GitHub](https://github.com/sijie-Z/DocMind-RAG) · [Email](mailto:1683039482@qq.com)

---

本项目基于 [MIT License](LICENSE) 开源。如果对你有帮助，欢迎 ⭐ Star！
