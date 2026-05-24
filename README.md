<p align="center">
  <h1 align="center">DocMind</h1>
  <p align="center"><strong>Enterprise RAG Knowledge Base System</strong></p>
  <p align="center">
    Upload documents, search with natural language, get AI-powered answers with citations.
  </p>
  <p align="center">
    <a href="https://sijie-z.github.io/DocMind-RAG/architecture.html"><img src="https://img.shields.io/badge/Live%20Demo-Architecture-22d3ee?logo=githubpages" alt="Demo"></a>
    <a href="https://github.com/sijie-Z/DocMind-RAG/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="License"></a>
    <br>
    <img src="https://img.shields.io/badge/Python-3.11+-blue?logo=python" alt="Python">
    <img src="https://img.shields.io/badge/FastAPI-0.104+-009688?logo=fastapi" alt="FastAPI">
    <img src="https://img.shields.io/badge/Vue-3.4-4FC08D?logo=vuedotjs" alt="Vue 3">
    <img src="https://img.shields.io/badge/TypeScript-5.3-3178C6?logo=typescript" alt="TS">
    <img src="https://img.shields.io/badge/Elasticsearch-8.11-005571?logo=elasticsearch" alt="ES">
    <img src="https://img.shields.io/badge/tests-172-passing" alt="Tests">
    <img src="https://img.shields.io/badge/coverage-84%25-brightgreen" alt="Coverage">
  </p>
</p>

---

## 📋 Overview

**DocMind** is a full-stack enterprise RAG (Retrieval-Augmented Generation) knowledge base system. It transforms documents into a searchable, AI-powered knowledge repository — enabling natural language querying with citation-backed answers.

Built for real-world use cases: enterprise knowledge management, customer support, internal documentation, research repositories.

### Architecture

![Architecture Diagram](docs/architecture.html)

> Open `docs/architecture.html` in a browser for the full interactive architecture diagram.

---

## ✨ Features

### 🧠 RAG Pipeline

| Feature | Description |
|---------|------------|
| **Document Processing** | PDF, Word, Excel, TXT, Markdown — LangChain-based parsing with smart chunking |
| **Hybrid Search** | BM25 keyword + KNN vector dual-channel retrieval with RRF fusion |
| **Cross-Encoder Reranking** | Precision-boosting second-pass reranking |
| **Semantic Cache** | Cosine-similarity cache (≥0.92) for repeated queries |
| **Context Compression** | Intelligent trimming of retrieved context before LLM inference |
| **RAG Evaluation** | Automated quality metrics: faithfulness, answer relevancy, context precision |

### 🤖 PER Agent (Plan-Execute-Reflect)

| Feature | Description |
|---------|------------|
| **PER Architecture** | Plan → Execute → Reflect autonomous loop with 12 SSE event types |
| **25+ Built-in Tools** | Search, analysis (deep doc analysis, cross-doc comparison, report generation), code execution, web search, translation, and more |
| **Deep Analysis** | Entity extraction, multi-document cross-analysis, polished markdown report generation |
| **Skill Learning** | Self-improvement from successful tool-use patterns |
| **SSE Streaming** | Real-time thinking, planning, tool call, and reflection visualization |
| **Subagent Delegation** | Complex tasks broken down into sub-tasks |
| **Memory System** | Short-term, long-term, and workspace memory with embedding recall |

### 💬 AI Chat

- **WebSocket streaming** with real-time token display
- **Multi-turn context** with session history
- **Citation-backed answers** — `[n]` reference links to source documents
- Response regeneration, conversation export
- Markdown rendering with syntax highlighting and LaTeX

### 📊 Knowledge Graph

- Canvas-based force-directed graph visualization
- Entity extraction with 7 type categories
- Interactive: drag, zoom, click for details, keyword filter

### 🔧 Workflow Editor

- Visual DAG builder with drag-and-drop nodes
- **Node types**: LLM, API, Code, Condition, Memory, Input, Output, Transform
- Real-time debug drawer with execution traces
- Kahn topological sort + DFS cycle detection

### 🏢 Enterprise

| Feature | Description |
|---------|------------|
| **RBAC** | User → Role → Organization multi-tenancy hierarchy |
| **JWT Auth** | Token-based authentication with Redis blacklist |
| **Audit Log** | Full operation audit trail |
| **Monitoring** | Prometheus metrics + Grafana dashboards |
| **Tracing** | OpenTelemetry distributed tracing (optional) |

### 🌐 Internationalization

zh | en | ja | fr — switch instantly, extensible locale system.

---

## 🏗 Tech Stack

| Layer | Technology |
|-------|-----------|
| **Backend** | FastAPI + Uvicorn (async) |
| **Database** | MySQL 8 + SQLAlchemy 2.0 (async) + Alembic |
| **Cache** | Redis 7 |
| **Search** | Elasticsearch 8 (KNN + BM25) |
| **Queue** | Kafka (aiokafka) |
| **Storage** | MinIO (S3-compatible) |
| **AI** | DeepSeek V4 (Flash / Pro) / OpenAI-compatible API |
| **Agent** | PER loop (Plan-Execute-Reflect) + Tool Registry + Context Engine + Skill Learning |
| **Parser** | LangChain + PyPDF + python-docx + unstructured |
| **Frontend** | Vue 3.4 + TypeScript 5.3 + Vite 5 |
| **UI** | Naive UI + ECharts + Vue Flow |
| **State** | Pinia |
| **i18n** | Vue I18n (zh / en / ja / fr) |
| **PWA** | Service worker + offline cache |
| **Monitoring** | Prometheus + Grafana + OpenTelemetry |
| **Security** | JWT + RBAC + multi-tenancy + audit |
| **Container** | Docker + Docker Compose + Kubernetes |
| **CI/CD** | GitHub Actions (test, lint, build, security scan) |

---

## 🚀 Quick Start

### Prerequisites

- **Docker Desktop** — or install Python 3.11+, Node.js 18+, MySQL 8, Redis 7, ES 8, Kafka, MinIO manually

### 1. Start Infrastructure

```bash
git clone https://github.com/sijie-Z/DocMind-RAG.git
cd DocMind-RAG

# Start all services (MySQL, Redis, ES, Kafka, MinIO)
cd backend
docker compose up -d
```

### 2. Configure

```bash
cp .env.docker.example .env.docker
# Edit .env.docker with your AI API keys:
#   DEEPSEEK_API_KEY  — LLM (ZhipuAI / OpenAI-compatible)
#   EMBEDDING_API_KEY — Embedding model
#   RERANK_API_KEY    — Rerank model
```

### 3. Start Backend

```bash
cd backend
python -m venv venv
source venv/bin/activate   # or venv\Scripts\activate on Windows
pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 4. Start Frontend

```bash
cd frontend
npm install
npm run dev
```

### 5. Open

- **Frontend**: http://localhost:5173
- **API Docs**: http://localhost:8000/docs
- **Demo Account**: `guest` / `123456`

> **Pro tip**: Run `make dev` to start both backend and frontend with one command.

---

## 📁 Project Structure

```
├── backend/
│   ├── app/
│   │   ├── api/v1/endpoints/    # REST API routes
│   │   ├── agent/               # ReAct Agent (loop, tools, registry, memory, skills)
│   │   ├── core/                # Infrastructure (DB, ES, Redis, MinIO, Kafka, config)
│   │   ├── models/              # SQLAlchemy ORM models
│   │   ├── rag/                 # RAG pipeline (retriever, reranker, cache, metrics)
│   │   ├── schemas/             # Pydantic request/response schemas
│   │   ├── services/            # Business logic layer
│   │   └── worker/              # Kafka document processor
│   ├── alembic/                 # Database migrations
│   ├── tests/                   # 21 test files / 216 test cases
│   ├── config/                  # Prometheus, Grafana, alerts
│   ├── docker/                  # MySQL init scripts
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── api/                 # API client modules (17 modules)
│   │   ├── components/          # Reusable Vue components
│   │   ├── composables/         # Vue composables
│   │   ├── stores/              # Pinia state management
│   │   ├── utils/               # Utility functions
│   │   └── views/               # Page-level components (20+ views)
│   ├── Dockerfile               # Nginx-based production build
│   └── nginx.conf               # Production nginx config
├── deploy/
│   ├── k8s/                     # Kubernetes manifests (namespace, config, secrets, deployments, services, ingress)
│   └── README.md                # Deployment guide
├── docs/
│   └── architecture.html        # Interactive architecture diagram
├── .github/workflows/
│   └── ci.yml                   # CI: test + lint + type-check + build + security scan
├── .pre-commit-config.yaml      # Pre-commit hooks (ruff, eslint)
├── Makefile                     # Common development commands
├── CHANGELOG.md
├── CONTRIBUTING.md
├── CODE_OF_CONDUCT.md
└── README.md
```

---

## 📊 Data Flow

```
Upload → MinIO + DB → Kafka → Worker → Parse & Chunk → Embedding → Elasticsearch
                                                                              ↓
User Query → WebSocket/SSE → Hybrid Search (BM25 + KNN + RRF) → Rerank → DeepSeek LLM → Stream Response
```

---

## 🧪 Testing

```bash
# Backend (216 tests)
cd backend && python -m pytest tests/ -v --tb=short

# Frontend
cd frontend && npx vitest run

# Coverage
cd backend && python -m pytest tests/ --cov=app --cov-report=html
cd frontend && npx vitest run --coverage

# All tests + lint
make test
make lint
```

---

## 📦 Deployment

| Method | Guide |
|--------|-------|
| **Docker Compose** | `cd backend && docker compose up -d` |
| **Kubernetes** | `kubectl apply -f deploy/k8s/` |
| **Manual** | See [deploy/README.md](deploy/README.md) |

---

## 🤝 Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, coding standards, and PR workflow.

---

## 📄 License

MIT License — see [LICENSE](LICENSE) for details.

---

## 🏆 Why DocMind?

DocMind was built to solve a real problem: **enterprise knowledge is scattered across documents, and finding answers is slow**. Instead of a thin wrapper around an LLM, DocMind is a complete production system:

- **Full-stack**: Frontend, backend, AI pipeline, infrastructure — all in one repo
- **Production-ready**: Docker, K8s, monitoring, auth, RBAC, audit
- **Extensible**: Plugin agent system, workflow editor, i18n
- **Tested**: 216 unit + integration tests, CI/CD pipeline
- **Documented**: Architecture diagram, deployment guide, contributing guide, API docs (Swagger)
