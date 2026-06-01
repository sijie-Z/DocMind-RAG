<p align="center">
  <h1 align="center">🤖 DocMind</h1>
  <p align="center"><strong>企业级 AI Agent 智能体平台</strong></p>
  <p align="center">
    PER Agent 自主推理 · 混合检索 RAG · 知识图谱 · 工作流引擎
  </p>
  <p align="center">
    <a href="https://sijie-z.github.io/DocMind-RAG/architecture.html"><img src="https://img.shields.io/badge/在线演示-架构图-22d3ee?logo=githubpages" alt="Demo"></a>
    <a href="https://github.com/sijie-Z/DocMind-RAG/blob/main/LICENSE"><img src="https://img.shields.io/badge/开源协议-MIT-green" alt="License"></a>
    <a href="https://github.com/sijie-Z/DocMind-RAG"><img src="https://img.shields.io/badge/版本-v1.2.0-blue" alt="Version"></a>
    <br>
    <img src="https://img.shields.io/badge/Python-3.11+-blue?logo=python" alt="Python">
    <img src="https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi" alt="FastAPI">
    <img src="https://img.shields.io/badge/Vue-3.4-4FC08D?logo=vuedotjs" alt="Vue 3">
    <img src="https://img.shields.io/badge/TypeScript-5.3-3178C6?logo=typescript" alt="TS">
    <img src="https://img.shields.io/badge/Elasticsearch-8.11-005571?logo=elasticsearch" alt="ES">
    <img src="https://img.shields.io/badge/测试-400%20通过-brightgreen" alt="Tests">
    <img src="https://img.shields.io/badge/覆盖率-84%25-brightgreen" alt="Coverage">
    <img src="https://img.shields.io/badge/工具-11%20个-orange" alt="Tools">
  </p>
</p>

---

## 📋 项目概述

**DocMind** 是一个面向企业的 AI Agent 智能体平台，以自研 **PER Agent（Plan-Execute-Reflect）** 为核心，融合混合检索 RAG、知识图谱、工作流引擎。Agent 能够自主理解用户意图、拆解复杂任务、动态调用工具、逐步推理并生成最终答案。

### 核心能力

```
用户提问 → Agent 意图分析 → 拆解为子任务 → 动态调用工具 → 观察结果 → 继续推理 → 最终回答
                                     ↑                        ↓
                                🔎 知识库检索  🌐 联网搜索  📄 文档解析  ⌨️ 代码执行 ...
```

### 适用场景

| 场景 | 说明 |
|------|------|
| **企业智能知识助手** | Agent 自主检索规章制度、操作手册，分析对比后给出带引用的精准回答 |
| **深度文档分析** | 跨文档对比、趋势分析、实体提取、结构化报告自动生成 |
| **研发辅助** | 技术方案分析、代码审查辅助、API 文档智能问答 |
| **自动化工作流** | Agent 编排多步骤任务，串联检索、分析、生成全流程 |
| **合规审计** | 合同、政策文件集中管理，关键条款自动提取与风险识别 |

### 为什么选择 DocMind？

与市面上简单的"套壳 LLM"产品不同，DocMind 是一个**完整的企业级生产系统**：

- **Agent 优先架构**：自研 PER Agent 三阶段架构，11 个内置工具可动态编排，支持复杂任务自动拆解
- **不是 RAG 包装器**：RAG 只是 Agent 的一个工具，Agent 自主决定何时检索、如何分析
- **全栈可部署**：前后端 + AI 管线 + 基础设施全部在一个仓库，Docker 一键启动
- **生产就绪**：JWT 鉴权、RBAC 权限、审计日志、Prometheus 监控、Grafana 看板、K8s 部署文件
- **可扩展**：插件式工具系统、可视化工作流编排、国际化（中/英/日/法）
- **充分测试**：400+ 单元测试 + 集成测试，CI/CD 持续集成

---

## 🏗 系统架构

### 五层架构设计

```
┌─────────────────────────────────────────────────────────────┐
│                     表现层 (Presentation)                    │
│         Vue 3 + Naive UI + ECharts + Vue Flow              │
├─────────────────────────────────────────────────────────────┤
│                   API 网关层 (API Gateway)                   │
│          FastAPI + JWT + CORS + Rate Limit + SSE            │
├─────────────────────────────────────────────────────────────┤
│                   AI Agent 核心层                            │
│   PER Loop │ Tool Registry │ Context Engine │ Skill       │
│       │              ↑                        │             │
│       ↓              │                        ↓             │
│   RAG 管道 │ 知识图谱 │ 工作流引擎 │ 文档管理                │
├─────────────────────────────────────────────────────────────┤
│                    AI / LLM 层 (Intelligence)               │
│   DeepSeek V4 │ Embedding │ Reranker │ Tool Registry       │
├─────────────────────────────────────────────────────────────┤
│                   数据存储层 (Data Storage)                   │
│  MySQL 8 │ Elasticsearch 8 │ Redis 7 │ Kafka │ MinIO       │
└─────────────────────────────────────────────────────────────┘
```

> 打开 `docs/architecture.html` 查看完整交互式架构图。

### PER Agent 三阶段架构

DocMind 的 Agent 采用自研 **PER（Plan-Execute-Reflect）** 三阶段架构，比传统 ReAct 模式更智能：

```
用户提问
   ↓
┌──────────────────────────────────────────────┐
│  Phase 1: 规划 (Planner)                     │
│  • 分析任务意图                              │
│  • 制定分步执行计划（含依赖关系）              │
│  • 为每步推荐最佳工具                         │
├──────────────────────────────────────────────┤
│  Phase 2: 执行 (Executor)                    │
│  • 按计划顺序调用工具（25+ 内置工具）          │
│  • 每步结果经 LLM 自然语言合成                │
│  • 支持失败重试和工具降级                      │
├──────────────────────────────────────────────┤
│  Phase 3: 反思 (Reflector)                   │
│  • 审查执行结果是否满足原始需求               │
│  • 检测错误或不一致（幻觉/缺漏/矛盾）          │
│  • 必要时触发重新规划或局部修复                │
└──────────────────────────────────────────────┘
   ↓
SSE 流式返回最终答案（含规划推理 + 执行过程 + 引用溯源）
```

**关键参数**：最大步数 15 | LLM 温度 0.1 | SSE 事件流推送 | 指数退避重试

核心优势：Plan 阶段一次性生成完整 DAG，避免 ReAct 每步串行决策的开销；Reflect 阶段对执行结果做质量检查，发现错误自动纠偏。

---

## ✨ 功能特性

### 🤖 AI Agent 智能体（核心差异化能力）

| 功能 | 说明 |
|------|------|
| **PER 三阶段架构** | 规划→执行→反思，DAG 任务分解 + 并行工具调用 + 结果纠错 |
| **11 个内置工具** | 知识检索、网页搜索、文档解析、摘要、深度分析、代码执行、翻译等 |
| **工具注册中心** | 所有工具统一注册、鉴权、沙箱隔离、审计追踪 |
| **上下文引擎** | 多轮记忆管理，Token 预算自动分配（系统 2K / 对话 8K / 工具 4K） |
| **思考流可视化** | 前端实时展示 Agent 的每一步推理、工具调用和结果 |
| **子任务分解** | 复杂任务自动拆解为多步执行，逐步逼近最终答案 |
| **技能学习** | 从成功的工具使用模式中自我改进，持续优化推理路径 |

#### 内置工具

| 工具名称 | 功能描述 |
|---------|---------|
| `🔎 知识库检索` | 混合搜索企业知识文档，返回带相关度评分的结果片段 |
| `🌐 Web Search` | 实时联网搜索，获取最新信息补充知识库盲区 |
| `📄 文档解析` | 读取 PDF、Word、TXT、Markdown，提取结构化内容 |
| `📝 智能摘要` | 长文档自动摘要、多文档对比、分块逐层归纳 |
| `📊 深度分析` | 观点提炼、趋势分析、情感分析、多文档观点对比 |
| `🗂️ 文件管理` | 文件组织、批量标注、归档、重命名与目录管理 |
| `⌨️ 代码执行` | 沙箱化 Python 执行，数据分析与计算自动化 |
| `🔗 内容爬取` | 获取网页内容，自动清洗转换为可分析文本 |
| `🔄 批量处理` | 大数据量分批处理，进度跟踪与结果合并 |
| `🌍 翻译` | 中英日法多语言翻译，文档级与片段级 |
| `🧭 知识图谱` | 实体关系探索、图谱查询、可视化交互式浏览 |

### 📚 RAG 检索管线（Agent 的核心工具之一）

| 功能 | 说明 |
|------|------|
| **文档解析** | 支持 PDF、Word、TXT、Markdown，基于 LangChain 智能分块 |
| **混合检索** | BM25 关键词 + KNN 向量双通道检索，RRF 融合排序 |
| **Cross-Encoder 重排序** | 二阶段精排，检索精度提升 30%+ |
| **语义缓存** | 余弦相似度 ≥0.92 的重复查询直接返回缓存，节约 LLM 成本 |
| **上下文压缩** | 检索结果智能裁剪，控制 Token 消耗 |
| **引用溯源** | 每个答案标注 `[n]` 引用链接，点击跳转源文档 |

### 💬 智能对话

- **SSE 流式响应**：Token 级别实时显示，打字机效果
- **多轮对话**：会话历史上下文感知，支持会话管理
- **Agent 模式**：Agent 自主决定是否调用 RAG 或其他工具
- **答案溯源**：`[1]` `[2]` 引用标注，点击查看原文
- **Markdown 渲染**：代码高亮、LaTeX 数学公式、表格、流程图
- **对话导出**：支持导出为 Markdown

### 🔗 知识图谱

- Canvas 画布力导向图可视化
- 7 类实体自动提取（人物、组织、地点、技术、概念、事件、产品）
- 交互操作：拖拽、缩放、点击查看详情、关键词过滤

### ⚙️ 可视化工作流编辑器

- 拖拽式 DAG 构建器（基于 Vue Flow）
- **节点类型**：LLM、API 调用、代码执行、条件分支、智能路由、记忆节点、数据转换
- **实时调试**：执行轨迹抽屉面板，节点状态颜色标识
- **DAG 引擎**：Kahn 拓扑排序 + DFS 环检测，自动优化执行顺序

### 🏢 企业级能力

| 功能 | 说明 |
|------|------|
| **RBAC 权限** | 用户 → 角色 → 组织三层多租户体系 |
| **JWT 认证** | Token 鉴权 + 24h/7d 双 Token 机制 |
| **审计日志** | 全操作审计追踪，满足合规要求 |
| **Prometheus 监控** | 请求量、延迟、错误率、Agent 工具调用统计 |
| **Grafana 看板** | 预置仪表盘（API 性能、Agent 统计、系统资源） |
| **OpenTelemetry** | 分布式链路追踪 |
| **多语言** | 中文 / English / 日本語 / Français，即时切换 |

---

## 🛠 技术栈

| 层级 | 技术选型 | 说明 |
|------|---------|------|
| **后端框架** | FastAPI + Uvicorn | 全异步，自动生成 Swagger 文档 |
| **数据库** | MySQL 8 + SQLAlchemy 2.0 | 异步 ORM + Alembic 迁移 |
| **缓存** | Redis 7 | 语义缓存 + Token 黑名单 + 会话存储 |
| **搜索引擎** | Elasticsearch 8 | KNN 向量检索 + BM25 关键词检索 |
| **消息队列** | Kafka (aiokafka) | 异步文档处理管线 |
| **对象存储** | MinIO | S3 兼容，文档文件存储 |
| **AI 模型** | DeepSeek V4 (Flash/Pro) | LLM 推理 + 深度分析 |
| **Embedding** | OpenAI-compatible API | 向量嵌入（2048 维）|
| **Agent 架构** | PER 三阶段架构 | 规划→执行→反思，DAG 并行调度 |
| **文档解析** | LangChain + PyPDF + python-docx | 多格式文档智能分块 |
| **前端框架** | Vue 3.4 + TypeScript 5.3 + Vite 5 | 组合式 API + 类型安全 |
| **UI 组件** | Naive UI + ECharts + Vue Flow | 企业级组件 + 图表 + 流程图 |
| **状态管理** | Pinia | Vue 3 官方推荐 |
| **国际化** | Vue I18n | 中/英/日/法 四语言 |
| **监控** | Prometheus + Grafana + OpenTelemetry | 指标采集 + 可视化 + 链路追踪 |
| **安全** | JWT + RBAC + 多租户 + 审计日志 | 企业级安全体系 |
| **容器化** | Docker + Docker Compose + K8s | 开发/测试/生产 全环境覆盖 |
| **CI/CD** | GitHub Actions | 测试 + 代码检查 + 构建 + 安全扫描 |

---

## 🚀 快速开始

### 环境要求

- **Docker Desktop**（推荐）— 一键启动所有基础设施
- 或手动安装：Python 3.11+、Node.js 18+、MySQL 8、Redis 7、Elasticsearch 8、Kafka、MinIO

### 1. 克隆项目

```bash
git clone https://github.com/sijie-Z/DocMind-RAG.git
cd DocMind-RAG
```

### 2. 启动基础设施

```bash
cd backend
docker compose up -d
```

> 启动 MySQL、Redis、Elasticsearch、Kafka、MinIO 五个服务。预计 30 秒完成。

### 3. 配置环境变量

```bash
cp .env.docker.example .env.docker
```

编辑 `.env.docker`，填入你的 AI API Key：

```env
# LLM（必填，支持 DeepSeek / OpenAI 兼容 API）
DEEPSEEK_API_KEY=sk-your-api-key-here

# Embedding 模型（必填）
EMBEDDING_API_KEY=your-embedding-api-key

# Rerank 模型（可选，不填则跳过重排序）
RERANK_API_KEY=your-rerank-api-key
```

### 4. 启动后端

```bash
cd backend
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

# 启动后端服务（端口 8000）
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 5. 启动前端

```bash
cd frontend
npm install
npm run dev                      # 端口 5173
```

### 6. 打开应用

| 地址 | 说明 |
|------|------|
| http://localhost:5173 | 前端界面 |
| http://localhost:8000/docs | API 文档（Swagger） |
| http://localhost:8000/health | 健康检查 |

### 演示账号

| 用户名 | 密码 | 角色 |
|--------|------|------|
| `guest` | `123456` | 普通用户 |
| `admin` | `admin123` | 管理员 |

> **快速启动**：项目根目录执行 `make dev` 可同时启动前后端。

### 7. 导入示例数据（可选）

```bash
cd backend
python seed_docs/seed.py
```

将导入 2 篇示例文档（`architecture.md` 系统架构设计、`python_tutorial.md` Python 教程），可直接体验 Agent 分析功能。

---

## 📁 项目结构

```
DocMind/
├── backend/                          # 后端服务
│   ├── app/
│   │   ├── api/v1/endpoints/         # REST API（17 个模块）
│   │   │   ├── agent.py              #   Agent 聊天 + 会话管理
│   │   │   ├── chat.py               #   主聊天 SSE 端点 + Agent 事件转发
│   │   │   ├── documents.py          #   文档上传/解析/下载
│   │   │   ├── knowledge.py          #   知识库检索
│   │   │   ├── workflow.py           #   工作流 CRUD + 执行
│   │   │   ├── auth.py               #   认证（登录/注册/刷新）
│   │   │   ├── memory.py             #   Agent 记忆管理
│   │   │   └── ...
│   │   ├── agent/                    # PER Agent 核心
│   │   │   ├── loop.py               #   主循环（规划→执行→反思）
│   │   │   ├── registry.py           #   工具注册中心
│   │   │   ├── context.py            #   上下文引擎
│   │   │   ├── events.py             #   SSE 事件模型
│   │   │   ├── skills.py             #   技能学习模块
│   │   │   └── tools/               #   工具实现
│   │   │       ├── knowledge_retrieval.py
│   │   │       ├── web_search.py
│   │   │       ├── code_execution.py
│   │   │       ├── data_analysis.py
│   │   │       └── ...
│   │   ├── core/                    # 核心基础设施
│   │   │   ├── config/              #   配置管理
│   │   │   ├── database.py          #   异步数据库
│   │   │   ├── elasticsearch.py     #   ES 客户端
│   │   │   ├── redis.py             #   Redis 客户端
│   │   │   ├── minio_client.py      #   MinIO 对象存储
│   │   │   └── prometheus.py        #   指标采集
│   │   ├── models/                  # SQLAlchemy ORM 模型
│   │   ├── rag/                     # RAG 管线（Agent 的核心工具）
│   │   │   ├── pipeline.py          #   检索主流程
│   │   │   ├── retriever.py         #   混合检索器
│   │   │   ├── reranker.py          #   重排序
│   │   │   └── cache.py             #   语义缓存
│   │   ├── schemas/                 # Pydantic 数据校验
│   │   ├── services/                # 业务逻辑层
│   │   └── worker/                  # Kafka 异步文档处理器
│   ├── tests/                       # 400+ 测试用例
│   │   ├── unit/                    #   单元测试
│   │   └── integration/             #   集成测试
│   ├── alembic/                     # 数据库迁移脚本
│   ├── seed_docs/                   # 示例文档
│   ├── pyproject.toml               # ruff + pytest 配置
│   ├── Dockerfile                   # 多阶段构建
│   └── docker-compose.yml           # 基础设施编排
├── frontend/                        # 前端应用
│   ├── src/
│   │   ├── api/                     # API 客户端（17 个模块）
│   │   ├── components/
│   │   │   └── agent/               #   Agent 组件
│   │   │       ├── PlanTree.vue     #     执行计划树
│   │   │       ├── ThinkingStream.vue#    思考流展示
│   │   │       ├── ToolCallCard.vue #     工具调用卡片
│   │   │       ├── AgentConfigPanel.vue#  配置面板
│   │   │       └── ...
│   │   ├── stores/                  # Pinia 状态管理
│   │   ├── utils/                   # 工具函数 + SSE 客户端
│   │   └── views/                   # 页面组件（20+ 个视图）
│   │       ├── chat/                #   智能对话
│   │       ├── agent/               #   Agent 面板
│   │       ├── knowledge/           #   知识库管理
│   │       ├── workflow/            #   工作流编辑器
│   │       ├── dashboard/           #   仪表盘
│   │       └── ...
│   ├── Dockerfile                   # Nginx 生产构建
│   └── nginx.conf                   # 生产 Nginx 配置
├── deploy/
│   ├── k8s/                         # K8s 部署清单（8 个 YAML）
│   └── README.md                    # 部署指南
├── docs/
│   └── architecture.html            # 交互式架构图
├── .github/workflows/
│   └── ci.yml                       # CI/CD
├── .pre-commit-config.yaml          # Pre-commit hooks
├── Makefile                         # 常用开发命令
├── CHANGELOG.md
├── CONTRIBUTING.md
├── CODE_OF_CONDUCT.md
└── README.md
```

---

## 📊 数据流

### 文档入库流程

```
用户上传 (PDF/Word/TXT/MD)
    │
    ├──→ 原始文件存入 MinIO
    ├──→ 元数据写入 MySQL
    └──→ 消息入 Kafka
              │
              ↓
         Worker 异步消费
              │
              ├── LangChain 文档解析
              ├── 智能分块（段落 + 语义）
              ├── Embedding 向量化（2048 维）
              └── 写入 Elasticsearch
```

### Agent 查询流程

```
用户提问
    │
    ├──→ SSE 连接建立
    ├──→ [PER 三阶段]
    │      ├── Planner：意图分析 → 输出执行计划 DAG
    │      ├── Executor：按序/并行调度工具（检索/搜索/分析/代码）
    │      └── Reflector：检查结果质量，决定完成或修复
    │
    ├──→ DeepSeek V4 流式生成最终答案
    └──→ SSE 逐 Token 返回前端（含规划和执行过程）
```

---

## 🧪 测试

```bash
# 后端测试（400+ 用例）
cd backend
python -m pytest tests/ -v --tb=short

# 前端测试
cd frontend
npx vitest run

# 测试覆盖率
cd backend
python -m pytest tests/ --cov=app --cov-report=html

# 一键全检
make test
make lint
```

---

## 🚢 部署

| 方式 | 说明 | 命令 |
|------|------|------|
| **Docker Compose** | 单机部署 | `cd backend && docker compose up -d` |
| **Kubernetes** | 集群部署 | `kubectl apply -f deploy/k8s/` |
| **手动部署** | 自定义环境 | 参见 `deploy/README.md` |

### 生产环境注意

- 修改 `.env.docker` 中的默认密码
- 配置 HTTPS（Nginx + Let's Encrypt）
- 开启 Prometheus 监控 + Grafana 告警
- 配置 Elasticsearch 集群（多节点 + 副本）

---

## 📝 版本历史

详见 [CHANGELOG.md](CHANGELOG.md)

| 版本 | 日期 | 核心变更 |
|------|------|---------|
| **v1.2.1** | 2026-05-24 | Agent 并行执行、页面过渡动画、ErrorBoundary 错误详情 |
| **v1.2.0** | 2026-05-24 | PER Agent 架构、25+ 工具、深度分析、SSE 事件管线完整转发 |
| **v1.1.0** | 2026-05-17 | Agent 模式开关、示例文档、CJK 分词修复 |
| **v1.0.0** | 2026-05-17 | 首个完整版本：RAG 管线、PER Agent、工作流编辑器、知识图谱 |

---

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！详见 [CONTRIBUTING.md](CONTRIBUTING.md)

开发规范：
- 后端：Python 3.11+，遵循 ruff 代码风格
- 前端：TypeScript strict mode，ESLint + Prettier
- 提交：遵循 Conventional Commits 规范

---

## 📄 开源协议

MIT License — 详见 [LICENSE](LICENSE)

---

## 🔗 相关链接

- **在线架构图**：[DocMind Architecture](https://sijie-z.github.io/DocMind-RAG/architecture.html)
- **GitHub 仓库**：[sijie-Z/DocMind-RAG](https://github.com/sijie-Z/DocMind-RAG)
- **API 文档**：http://localhost:8000/docs（启动后端后访问）
- **问题反馈**：[GitHub Issues](https://github.com/sijie-Z/DocMind-RAG/issues)

---

<p align="center">
  <strong>DocMind</strong> — 企业级 AI Agent 智能体平台
  <br>
  <sub>Built with ❤️ by the DocMind Team</sub>
</p>
