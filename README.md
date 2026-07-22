<p align="center">
  <a href="README_EN.md">English</a> · <strong>中文</strong>
</p>

<div align="center">
  <h1>🤖 DocMind</h1>
  <p><strong>企业级 AI Agent 系统 — 基于 PER 架构的智能知识库平台</strong></p>
  <p>
    <img src="https://img.shields.io/badge/Python-3.11+-blue?logo=python" alt="Python">
    <img src="https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi" alt="FastAPI">
    <img src="https://img.shields.io/badge/Vue_3-3.4-4FC08D?logo=vuedotjs" alt="Vue 3">
    <img src="https://img.shields.io/badge/License-MIT-green" alt="License">
  </p>
</div>

---

## 简介

DocMind 是一个企业级 AI Agent 系统，提供智能文档管理、知识库构建、RAG 问答和可观测的 Agent 工作流。

核心能力：
- **PER Agent**：Plan-Execute-Reflect 三阶段自主推理，支持 25+ 内置工具
- **RAG 管线**：文档解析 → 向量化 → 混合检索（BM25 + 向量）→ LLM 生成
- **知识库管理**：多知识库 CRUD、权限控制、统计分析
- **工作流编辑器**：可视化 DAG 工作流设计与调试（Vue Flow）
- **可观测性**：SSE 流式事件（12 种）、Langfuse 全链路追踪、Prometheus 指标、RunReport
- **自我进化**：经验记忆 → 执行回放 → 模式挖掘（实验性）

> 设计决策与架构研究请参见研究仓库：[DocMind-Agent-Causal-Study](https://github.com/sijie-Z/DocMind-Agent-Causal-Study)

---

## 技术栈

| 层 | 技术 |
|---|---|
| **后端** | Python 3.11+ · FastAPI · SQLAlchemy 2.0 · Alembic |
| **前端** | Vue 3 · TypeScript · Naive UI · ECharts · Vue Flow |
| **数据库** | MySQL 8 · Redis 7 · Elasticsearch 8 |
| **消息/存储** | Kafka · MinIO |
| **AI/LLM** | DeepSeek · OpenAI 兼容 API · LangChain · Langfuse |
| **可观测** | OpenTelemetry · Prometheus · SSE |
| **测试** | pytest (422+ cases) · pytest-asyncio · pytest-cov |

---

## 快速开始

### 前置要求

Docker Desktop（推荐），或手动安装 Python 3.11+ / Node.js 18+ / MySQL 8 / Redis 7 / ES 8 / Kafka / MinIO。

### 1. 克隆项目

```bash
git clone https://github.com/sijie-Z/DocMind-RAG.git
cd DocMind-RAG
```

### 2. 启动基础设施

```bash
cd backend && docker compose up -d    # MySQL / Redis / ES / Kafka / MinIO
```

### 3. 配置环境变量

```bash
cp .env.docker.example .env.docker
# 编辑 .env.docker，填入 API Key
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

### 演示账号

| 用户名 | 密码 | 角色 |
|--------|------|------|
| `guest` | `123456` | 普通用户 |
| `admin` | `admin123` | 管理员 |

---

## API 概览

| 模块 | 前缀 | 说明 |
|------|------|------|
| 认证 | `/api/v1/auth` | 注册、登录、Token 管理 |
| 用户管理 | `/api/v1/users` | 用户 CRUD、审计日志 |
| 文件管理 | `/api/v1/files` | 文件上传/下载（MinIO） |
| 文档管理 | `/api/v1/documents` | 文档解析、向量化（RAG 管线） |
| 聊天 | `/api/v1/chat` | 会话、消息、SSE 流式问答 |
| 知识库 | `/api/v1/knowledge` | 知识库 CRUD、检索 |
| 组织管理 | `/api/v1/organizations` | 组织、成员、角色权限 |
| 系统监控 | `/api/v1/monitoring` | 指标、健康检查、RAG 质量 |
| 通知 | `/api/v1/notifications` | 通知 CRUD、WebSocket 推送 |
| 提示词 | `/api/v1/prompts` | 提示词模板管理 |
| Token 用量 | `/api/v1/token-usage` | Token 统计 |
| 操作手册 | `/api/v1/manuals` | 系统手册管理 |
| Agent 工作流 | `/api/v1/workflows` | 工作流 CRUD、执行、调试 |
| LLM 配置 | `/api/v1/llm-config` | 模型配置管理 |
| 精选技能 | `/api/v1/curated-skills` | Agent 技能库 |
| Agent 记忆 | `/api/v1/memory` | 短期/长期/工作记忆 |
| 智能 Agent | `/api/v1/agent` | PER Agent 对话、工具调用 |
| 用户设置 | `/api/v1/user` | 个人偏好设置 |

Swagger 文档：http://localhost:8000/docs

---

## 项目结构

```
DocMind/
├── backend/
│   ├── app/
│   │   ├── api/v1/endpoints/     # REST API（19 模块）
│   │   ├── agent/                # PER Agent 核心
│   │   │   ├── loop.py           #   PER 主循环
│   │   │   ├── planner.py        #   规划器
│   │   │   ├── executor.py       #   执行器（DAG/Serial + 重试）
│   │   │   ├── reflector.py      #   反思器
│   │   │   ├── reviewer.py       #   对抗式 Reviewer
│   │   │   ├── run_report.py     #   RunReport（运行摘要）
│   │   │   ├── experience/       #   经验记忆
│   │   │   ├── replay/           #   执行回放
│   │   │   ├── mining/           #   模式挖掘
│   │   │   └── tools/            #   25+ 工具实现
│   │   ├── core/                 # 基础设施（DB/Redis/ES/Kafka/MinIO）
│   │   ├── rag/                  # RAG 管道
│   │   ├── services/             # 业务服务层
│   │   └── worker/               # Kafka 异步处理
│   ├── tests/                    # 422+ 测试用例
│   └── benchmark/                # 评测框架
├── frontend/src/                 # Vue 3 前端
│   └── views/                    # 页面（19 个模块）
└── docs/                         # 文档
```

---

## 测试

```bash
cd backend
python -m pytest tests/ -v --tb=short
python -m pytest tests/ --cov=app --cov-report=html
```

---

## 部署

```bash
# Docker Compose
cd backend && docker compose up -d

# 生产部署
# 参考 deploy/ 目录
```

---

## 链接

- **研究仓库**：https://github.com/sijie-Z/DocMind-Agent-Causal-Study
- **API 文档**：http://localhost:8000/docs
- **Issues**：https://github.com/sijie-Z/DocMind-RAG/issues

---

<p align="center">
  <sub>Built with ❤️ by the DocMind Team · MIT License</sub>
</p>
