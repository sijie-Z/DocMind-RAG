<p align="center">
  <a href="README_EN.md">English</a> · <strong>中文</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-blue?logo=python" alt="Python">
  <img src="https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi" alt="FastAPI">
  <img src="https://img.shields.io/badge/Vue_3-3.4-4FC08D?logo=vuedotjs" alt="Vue 3">
  <img src="https://img.shields.io/badge/License-MIT-green" alt="License">
</p>

<div align="center">
  <h1>🤖 DocMind</h1>
  <p><strong>企业级 AI Agent 知识库系统 — PER 架构 · 混合检索 · 可观测 · 自我进化</strong></p>
</div>

---

## 简介

DocMind 是一个全栈企业级 AI 知识库系统，围绕 **PER（Plan-Execute-Reflect）Agent 架构**构建。支持多格式文档解析、混合检索增强生成（RAG）、可视化工作流编辑、实时 SSE 流式问答，以及完整的可观测性体系。

核心场景：
- 📄 **文档知识库** — 上传 PDF/Word/Excel，自动解析、分块、向量化，支持语义搜索
- 💬 **智能问答** — 基于 RAG 管线（关键词 + 向量混合检索 + 重排序 + LLM 生成），带 SSE 流式输出
- 🤖 **自主 Agent** — PER 三阶段循环（规划 → 执行 → 反思），可调用 25+ 工具完成复杂任务
- 🧩 **工作流编辑器** — 可视化 DAG 设计与调试（Vue Flow），可配置 LLM 模型、提示词、执行引擎
- 📊 **可观测性** — Prometheus 指标、Langfuse 全链路追踪、12 种 SSE 事件、RunReport 运行摘要
- 🔐 **多租户 + RBAC** — 组织隔离、角色权限管理、JWT 认证

> 关于架构决策和实验数据，请参见研究仓库：[DocMind-Agent-Causal-Study](https://github.com/sijie-Z/DocMind-Agent-Causal-Study)

---

## 系统架构

```
┌── 前端 ──────────────────────────────────────┐
│  Vue 3 · TypeScript · Naive UI · ECharts     │
│  Vue Flow 工作流编辑器 · Pinia · UnoCSS      │
├── API 网关 ──────────────────────────────────┤
│  FastAPI · JWT + RBAC · CORS · SSE · GZip    │
│  WebSocket 实时推送 · Request ID 追踪        │
├── AI Agent 核心 ─────────────────────────────┤
│  PER Loop  (Planner → Executor → Reflector)  │
│  Tool Registry (25+) · RunReport · Reviewer  │
│  经验记忆 · 执行回放 · 模式挖掘              │
├── RAG 管线 ──────────────────────────────────┤
│  HybridRetriever (BM25 + 向量 + RRF 融合)    │
│  Query Processor · Reranker · Context Comp.   │
│  二级缓存 (精确 + 语义) · PII 掩码           │
├── LLM ───────────────────────────────────────┤
│  DeepSeek · OpenAI 兼容 API · Langfuse 追踪  │
├── 数据层 ────────────────────────────────────┤
│  MySQL 8 · Elasticsearch 8 · Redis 7         │
│  Kafka · MinIO (S3 兼容对象存储)             │
└──────────────────────────────────────────────┘
```

---

## 核心模块详解

### 🤖 PER Agent 系统 (`backend/app/agent/`)

自主 AI Agent，支持多步骤任务规划与执行。工作流：

```
用户指令
  → Planner   — 任务分解为 DAG/Serial 步骤，推荐工具，评估风险等级
  → Executor  — 依赖感知调度，并行执行独立步骤，内置超时/重试/回退
  → Reflector — 验证输出质量，检测幻觉/遗漏/矛盾，触发修正或结束
  → RunReport — 汇总整次运行：做了什么、为何这么做、结果如何
```

| 模块 | 文件 | 功能 |
|------|------|------|
| 主循环 | `loop.py` | PER 三阶段编排，SSE 事件流，RunReport 生成 |
| 规划器 | `planner.py` | LLM 规划 + 结构化规则模板（coarse/normal/fine） |
| 执行器 | `executor.py` | DAG 拓扑排序 + 并行组调度，超时/重试/fallback |
| 反思器 | `reflector.py` | 输出质量评估，自动修正建议 |
| Reviewer | `reviewer.py` | 对抗式审查，发现问题提出改进 |
| 工具注册 | `registry.py` | 集中式工具命名空间 + 标签筛选 |
| 上下文引擎 | `context.py` | 令牌预算管理，消息窗口构建 |
| 配置 | `config.py` | AgentConfig（模型/温度/最大步骤/阶段开关） |

**25+ 内置工具** (`tools/`)：

| 类别 | 工具 | 说明 |
|------|------|------|
| 知识检索 | `search_knowledge_base`, `vector_search` | 混合搜索、语义搜索 |
| 网络 | `web_search` | DuckDuckGo 实时搜索 |
| 代码 | `code_execution` | 沙箱化 Python 执行 |
| 数据 | `data_analysis` | Pandas/NumPy 数据分析 |
| 翻译 | `translation` | 多语言翻译 |
| 文档 | `summarize_document`, `extract_keywords` | 文档摘要、关键词提取 |
| 分析 | `extract_insights`, `cross_document_analysis`, `generate_report` | 深度分析、跨文档、报告 |
| 管理 | `list_documents`, `get_document_info` | 文档管理 |
| 对话 | `list_conversations`, `get_conversation_history` | 会话历史 |
| 外部 | `mcp_call`, `feishu/*` | MCP 协议桥接、飞书多维表格 |

**自我进化系统** (实验性)：

| 子系统 | 目录 | 功能 |
|--------|------|------|
| 经验记忆 | `experience/` | 从失败运行中自动提取经验教训，注入 Planner |
| 执行回放 | `replay/` | 完整执行快照保存与回放，支持 diff 对比 |
| 模式挖掘 | `mining/` | 扫描回放发现高频工具序列，生成技能建议 |

### 📚 RAG 管线 (`backend/app/rag/`)

多阶段检索增强生成管道，从用户提问到流式回答：

```
查询进入
  → QueryIntentClassifier    — 意图分类（事实/程序/定义/因果）
  → QueryComplexityClassifier — 复杂度分级，决定检索策略
  → 改写/HyDE                 — 查询重写 + 假设文档嵌入（可选）
  → HybridRetriever           — 关键词（模糊 + 通配符 + multi_match）
                                + 向量（script_score cosineSimilarity）
                                + RRF 倒数排序融合（k=60）
  → MMR 选择                  — 最大边际相关性去重
  → Reranker                  — 交叉编码器重排序（本地 BGE 或 API）
  → ContextCompressor         — LLM 上下文压缩
  → 缓存检查                  — 精确匹配缓存（TTL 600s）+ 语义缓存
  → LLM 生成                  — 流式 SSE 输出，严格模式可选，PII 掩码
```

| 文件 | 功能 |
|------|------|
| `pipeline.py` | 管道编排器，串联所有阶段 |
| `retriever.py` | HybridRetriever — 3 策略（关键词/混合/HyDE）+ RRF + MMR |
| `reranker.py` | 交叉编码器重排序（本地 BAAI/bge-reranker-base / API） |
| `query_processor.py` | HyDE 生成、LLM 查询改写、意图/复杂度分类、查询分解 |
| `cache.py` | RetrievalCache（精确 TTL）+ SemanticCache（向量余弦相似度） |
| `context_compressor.py` | 基于 LLM 的检索结果压缩 |
| `context_window.py` | RAG 聊天的令牌预算消息构建器 |
| `evaluator.py` / `metrics.py` | RAG 质量指标（基础性、相关性） |

### 🔧 基础设施 (`backend/app/core/`)

| 模块 | 文件 | 功能 |
|------|------|------|
| 配置 | `config/` | Pydantic Settings（Base/Database/AI/Security），环境变量驱动 |
| 数据库 | `database.py` | SQLAlchemy 异步引擎，Alembic 迁移 |
| 缓存 | `redis.py` | 延迟连接代理模式，RedisTools 缓存封装 |
| 搜索 | `elasticsearch.py` | ES 客户端代理，dense_vector 索引，IK 分析器 |
| 存储 | `minio_client.py` | MinIO S3 兼容客户端 |
| 消息 | `kafka_client.py` | aiokafka 生产者 |
| 认证 | `security.py` | JWT + bcrypt，get_current_user 依赖，RBAC 辅助 |
| 中间件 | `middleware.py` | 指标收集（请求数/延迟/错误率） |
| 熔断 | `circuit_breaker.py` | 外部服务调用的熔断器模式 |
| 步骤执行 | `step_runner.py` | 带重试/回退的安全步骤执行 |
| 链路追踪 | `tracing.py` | OpenTelemetry OTLP gRPC 导出 |
| 追踪日志 | `trace_logger.py` | Agent 执行追踪 JSONL 记录 |
| 指标 | `prometheus.py` | 自定义计数器/直方图（检索延迟、LLM token、缓存命中、RAG 质量） |
| WebSocket | `notification_ws.py` | 通知实时推送连接管理器 |
| 请求 ID | `request_id.py` | X-Request-ID 注入 |

### 🌐 API 层 (`backend/app/api/v1/`)

19 个端点模块，统一 `/api/v1` 前缀：

| 模块 | 路由前缀 | 说明 |
|------|----------|------|
| 认证 | `/auth` | 注册、登录、刷新 Token、密码重置、登出 |
| 用户管理 | `/users` | 用户 CRUD、个人资料、头像、会话管理、审计日志 |
| 文件管理 | `/files` | 上传/下载/删除，MinIO 存储，分块上传 |
| 文档管理 | `/documents` | 文档解析、向量化、知识库索引、状态追踪 |
| 聊天 | `/chat` | 会话 CRUD、SSE 流式问答、严格模式、调试端点 |
| 知识库 | `/knowledge` | 知识库 CRUD、检索、统计、设置 |
| 组织管理 | `/organizations` | 组织 CRUD、成员管理、角色分配 |
| 系统监控 | `/monitoring` | 性能指标、健康检查、告警、RAG 质量评估 |
| 通知 | `/notifications` | 通知 CRUD、已读标记、WebSocket 推送 |
| 提示词 | `/prompts` | 提示词模板 CRUD + 版本控制 |
| Token 用量 | `/token-usage` | 使用追踪 + 报告 |
| 操作手册 | `/manuals` | 系统手册管理 |
| 工作流 | `/workflows` | 工作流 CRUD、执行、调试、节点定义 |
| LLM 配置 | `/llm-config` | 用户级模型配置 |
| 技能库 | `/curated-skills` | 技能发现与管理 |
| Agent 记忆 | `/memory` | 短期/长期/工作记忆 CRUD、语义搜索 |
| 智能 Agent | `/agent` | PER Agent 对话（SSE 流式）、工具调用、配置 |
| 用户设置 | `/user` | 个人 UI 偏好 |
| 示例数据 | `/demo` | 演示文档与数据填充 |

Swagger 文档：`http://localhost:8000/docs`

### 🖥 前端 (`frontend/src/`)

Vue 3 + TypeScript + Naive UI 单页应用，21 条路由：

| 页面 | 路由 | 功能 |
|------|------|------|
| 仪表盘 | `/dashboard` | KPI 卡片、统计图表、快速入口 |
| AI 对话 | `/chat` | RAG + Agent 双模式，SSE 实时流式 |
| 会话历史 | `/conversations` | 历史会话列表与管理 |
| 知识库 | `/knowledge` | 文档上传、解析、搜索、知识图谱 |
| 搜索 | `/search` | 全局独立搜索 |
| 提示词 | `/prompts` | 提示词模板管理 |
| 工作流 | `/workflow` | 可视化 DAG 编辑器（Vue Flow） |
| Agent 配置 | `/agent` | 模型/温度/工具/记忆开关配置 |
| 组织管理 | `/organizations` | 组织 CRUD、成员管理（管理员） |
| 用户管理 | `/users` | 用户 CRUD、角色分配（管理员） |
| 审计日志 | `/audit-logs` | 用户活动审计（管理员） |
| 系统监控 | `/monitoring` | 性能图表、健康状态（管理员） |
| 个人设置 | `/profile` | 个人信息、密码修改 |
| 通知中心 | `/notifications` | 实时通知列表 |
| 系统帮助 | `/system-help` | 使用帮助文档 |
| 关于 | `/system-about` | 系统信息 |

### 🧰 服务层 (`backend/app/services/`)

| 服务 | 文件 | 功能 |
|------|------|------|
| 认证服务 | `auth_service.py` | JWT 创建/验证、bcrypt 哈希、Token 黑名单（jti） |
| 文档解析 | `document_parser.py` | 解析 PDF/Word/Excel/TXT/MD，文本提取与分块 |
| RAG 服务 | `rag_service.py` | RAG 管线 Facade |
| 文件服务 | `file_service.py` | 文件存储与检索 |
| 知识库服务 | `knowledge_service.py` | 知识库业务逻辑 |
| 权限服务 | `permission_service.py` | RBAC 权限初始化与管理 |
| 审计服务 | `audit_service.py` | 审计日志写入与查询 |
| 工作流引擎 | `workflow_engine.py` | 工作流执行引擎 |
| 组织服务 | `organization_service.py` | 多租户组织管理 |
| 记忆服务 | `memory_service.py` | Agent 记忆 CRUD |
| PII 掩码 | `masking_service.py` | 敏感信息脱敏 |
| 语义分块 | `semantic_chunker.py` | 语义感知的文本分块 |
| 图 RAG | `graph_rag_service.py` | 知识图谱增强检索 |

### 🔄 异步处理 (`backend/app/worker/`)

Kafka 消费者独立进程（`start_worker.py`），监听 `file-processing` 主题，处理文档解析 → 分块 → 嵌入 → ES 索引入库。

---

## 快速开始

### 前置要求

Docker Desktop（推荐），或手动安装：
- Python 3.11+ · Node.js 18+ · MySQL 8 · Redis 7 · Elasticsearch 8 · Kafka · MinIO

### 1. 克隆

```bash
git clone https://github.com/sijie-Z/DocMind-RAG.git
cd DocMind-RAG
```

### 2. 启动基础设施

```bash
cd backend && docker compose up -d    # MySQL / Redis / ES / Kafka / MinIO
```

### 3. 配置

```bash
cp .env.docker.example .env.docker
# 编辑 .env.docker，至少填入 LLM API Key：
#   DEEPSEEK_API_KEY=sk-xxx
#   DEEPSEEK_API_URL=https://api.deepseek.com/v1
#   EMBEDDING_API_KEY=sk-xxx
#   EMBEDDING_API_URL=https://api.deepseek.com/v1
```

### 4. 启动后端

```bash
cd backend
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 5. 启动前端

```bash
cd frontend && npm install && npm run dev          # http://localhost:5173
```

### 访问

| 地址 | 说明 |
|------|------|
| http://localhost:5173 | 前端界面 |
| http://localhost:8000/docs | Swagger API 文档 |
| http://localhost:8000/health | 健康检查 |
| http://localhost:8000/metrics | Prometheus 指标 |

### 演示账号

| 用户名 | 密码 | 角色 |
|--------|------|------|
| `guest` | `123456` | 普通用户 |
| `admin` | `admin123` | 管理员 |

---

## 项目结构

```
DocMind/
├── backend/
│   ├── app/
│   │   ├── main.py                  # FastAPI 入口，生命周期管理
│   │   ├── agent_api.py             # 独立 Agent 测试 API（端口 8010）
│   │   ├── dependencies.py          # 依赖注入容器
│   │   ├── exceptions.py            # 自定义异常层次结构
│   │   ├── api/v1/
│   │   │   ├── router.py            # 主路由（注册 19 个模块）
│   │   │   └── endpoints/           # 端点实现（19 个文件）
│   │   ├── agent/                   # PER Agent 核心
│   │   │   ├── loop.py              #   PER 主循环
│   │   │   ├── planner.py           #   规划器（LLM + 结构化规则）
│   │   │   ├── executor.py          #   执行器（DAG/Serial + 重试）
│   │   │   ├── reflector.py         #   反思器
│   │   │   ├── reviewer.py          #   对抗式 Reviewer
│   │   │   ├── run_report.py        #   RunReport（运行摘要）
│   │   │   ├── orchestrator.py      #   确定性编排器
│   │   │   ├── service.py           #   AgentService（Facade）
│   │   │   ├── config.py            #   AgentConfig
│   │   │   ├── exec_context.py      #   ExecutionContext
│   │   │   ├── registry.py          #   工具注册表
│   │   │   ├── memory_bridge.py     #   记忆桥接
│   │   │   ├── events.py            #   SSE 事件类型（12 种）
│   │   │   ├── message.py           #   Agent 间消息总线
│   │   │   ├── tracing.py           #   执行追踪记录
│   │   │   ├── tools/               #   25+ 工具实现
│   │   │   │   ├── web_search.py    #   网络搜索
│   │   │   │   ├── code_execution.py #  Python/SQL 沙箱
│   │   │   │   ├── data_analysis.py #   数据分析
│   │   │   │   ├── translation.py   #   翻译
│   │   │   │   ├── skills.py        #   技能学习
│   │   │   │   ├── utility.py       #   通用工具
│   │   │   │   ├── mcp_bridge.py    #   MCP 协议桥接
│   │   │   │   └── feishu/          #   飞书集成
│   │   │   ├── experience/          #   经验记忆
│   │   │   │   ├── extractor.py     #   经验提取器
│   │   │   │   ├── store.py         #   经验存储
│   │   │   │   ├── models.py        #   数据模型
│   │   │   │   └── run_extract.py   #   运行级提取
│   │   │   ├── replay/              #   执行回放
│   │   │   │   └── engine.py        #   回放引擎
│   │   │   └── mining/              #   模式挖掘
│   │   │       ├── miner.py         #   挖掘器
│   │   │       ├── analyzer.py      #   分析器
│   │   │       ├── patterns.py      #   模式定义
│   │   │       └── report.py        #   挖掘报告
│   │   ├── core/                    # 基础设施
│   │   │   ├── config/              #   配置（Base/DB/AI/Security）
│   │   │   ├── database.py          #   SQLAlchemy 引擎
│   │   │   ├── redis.py             #   Redis 代理
│   │   │   ├── elasticsearch.py     #   ES 代理
│   │   │   ├── minio_client.py      #   MinIO 客户端
│   │   │   ├── kafka_client.py      #   Kafka 生产者
│   │   │   ├── security.py          #   JWT + RBAC
│   │   │   ├── middleware.py         #   指标收集
│   │   │   ├── circuit_breaker.py   #   熔断器
│   │   │   ├── step_runner.py       #   步骤执行器
│   │   │   ├── tracing.py           #   OpenTelemetry
│   │   │   ├── trace_logger.py      #   JSONL 追踪日志
│   │   │   ├── prometheus.py        #   Prometheus 指标
│   │   │   ├── notification_ws.py   #   WebSocket 管理
│   │   │   ├── request_id.py        #   Request ID
│   │   │   ├── response.py          #   统一响应
│   │   │   └── ensure_demo_user.py  #   演示账号初始化
│   │   ├── rag/                     # RAG 管道
│   │   │   ├── pipeline.py          #   管道编排器
│   │   │   ├── retriever.py         #   混合检索器
│   │   │   ├── reranker.py          #   重排序器
│   │   │   ├── query_processor.py   #   查询处理（意图/复杂度/HyDE）
│   │   │   ├── cache.py             #   二级缓存
│   │   │   ├── context_compressor.py #  上下文压缩
│   │   │   ├── context_window.py    #   令牌预算管理
│   │   │   └── evaluator.py         #   质量评估
│   │   ├── services/                # 业务服务层（14 个服务）
│   │   ├── models/                  # ORM 模型（14 个实体）
│   │   ├── schemas/                 # Pydantic 请求/响应模式
│   │   └── worker/                  # Kafka 消费者
│   ├── tests/                       # 422+ 测试用例
│   ├── benchmark/                   # 评测框架
│   ├── alembic/                     # 数据库迁移
│   ├── scripts/                     # 工具脚本
│   ├── docker/                      # Docker 初始化 SQL
│   ├── config/                      # Prometheus/Grafana 配置
│   └── docker-compose.yml           # 10 服务编排
├── frontend/
│   └── src/
│       ├── views/                   # 页面（19 个模块，21 条路由）
│       ├── components/              # 共享组件
│       ├── api/                     # Axios API 客户端
│       ├── stores/                  # Pinia 状态管理
│       ├── router/                  # Vue Router 配置
│       ├── composables/             # 组合式函数
│       ├── locales/                 # i18n 国际化
│       └── utils/                   # 工具函数
└── docs/                            # 设计文档
```

---

## 配置说明

环境变量通过 `.env` 文件配置，核心配置项：

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `DEEPSEEK_API_KEY` | LLM API Key | 必填 |
| `DEEPSEEK_API_URL` | LLM API 地址 | `https://api.deepseek.com/v1` |
| `EMBEDDING_API_KEY` | Embedding API Key | 必填 |
| `EMBEDDING_API_URL` | Embedding API 地址 | 必填 |
| `DATABASE_URL` | MySQL 连接串 | `mysql+aiomysql://root:...` |
| `VECTOR_DIMENSION` | 向量维度 | `1536` |
| `ENABLE_TRACING` | OpenTelemetry 开关 | `false` |
| `ENABLE_DEMO_ACCOUNT` | 启用演示账号 | `true` |
| `APP_VERSION` | 应用版本 | `1.0.0` |

完整配置见 `backend/.env.example` 和 `backend/.env.docker.example`。

---

## 测试

```bash
cd backend

# 运行全部测试
python -m pytest tests/ -v --tb=short

# 生成覆盖率报告
python -m pytest tests/ --cov=app --cov-report=html

# 前端测试
cd ../frontend && npm run test
```

---

## 部署

### Docker Compose

```bash
cd backend && docker compose up -d
# 启动 10 个服务：backend, worker, mysql, redis, elasticsearch, minio, zookeeper, kafka, prometheus, grafana
```

### 基础设施端口

| 服务 | 端口 |
|------|------|
| FastAPI 后端 | 8000 |
| Vite 前端开发 | 5173 |
| MySQL | 3308 (Docker) / 3306 (本地) |
| Redis | 6390 (Docker) / 6379 (本地) |
| Elasticsearch | 9200 |
| MinIO API / Console | 9000 / 9001 |
| Kafka | 9092 / 29092 (内部) |
| Prometheus | 9090 |
| Grafana | 3001 |

---

## 链接

- **研究仓库**：https://github.com/sijie-Z/DocMind-Agent-Causal-Study
- **GitHub**：https://github.com/sijie-Z/DocMind-RAG
- **API 文档**：http://localhost:8000/docs
- **Issues**：https://github.com/sijie-Z/DocMind-RAG/issues

---

<p align="center">
  <sub>Built with ❤️ by the DocMind Team · MIT License</sub>
</p>
