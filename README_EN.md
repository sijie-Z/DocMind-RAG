<p align="center">
  <strong>English</strong> · <a href="README.md">中文</a>
</p>

<div align="center">
  <h1>🤖 DocMind</h1>
  <p><strong>Enterprise AI Agent System — PER-Architecture Intelligent Knowledge Base</strong></p>
  <p>
    <img src="https://img.shields.io/badge/Python-3.11+-blue?logo=python" alt="Python">
    <img src="https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi" alt="FastAPI">
    <img src="https://img.shields.io/badge/Vue_3-3.4-4FC08D?logo=vuedotjs" alt="Vue 3">
    <img src="https://img.shields.io/badge/License-MIT-green" alt="License">
  </p>
</div>

---

## Overview

DocMind is an enterprise AI Agent system providing intelligent document management, knowledge base construction, RAG-based Q&A, and observable Agent workflows.

Core capabilities:
- **PER Agent**: Plan-Execute-Reflect autonomous reasoning with 25+ built-in tools
- **RAG Pipeline**: Document parsing → vectorization → hybrid search (BM25 + vector) → LLM generation
- **Knowledge Base**: Multi-KB CRUD, access control, analytics
- **Workflow Editor**: Visual DAG workflow design & debugging (Vue Flow)
- **Observability**: 12 SSE event types, Langfuse tracing, Prometheus metrics, RunReport
- **Self-Improving**: Experience memory → execution replay → pattern mining (experimental)

> For design decisions and architecture research, see the research repo: [DocMind-Agent-Causal-Study](https://github.com/sijie-Z/DocMind-Agent-Causal-Study)

---

## Tech Stack

| Layer | Technology |
|---|---|
| **Backend** | Python 3.11+ · FastAPI · SQLAlchemy 2.0 · Alembic |
| **Frontend** | Vue 3 · TypeScript · Naive UI · ECharts · Vue Flow |
| **Database** | MySQL 8 · Redis 7 · Elasticsearch 8 |
| **MQ/Storage** | Kafka · MinIO |
| **AI/LLM** | DeepSeek · OpenAI-compatible API · LangChain · Langfuse |
| **Observability** | OpenTelemetry · Prometheus · SSE |
| **Testing** | pytest (422+ cases) · pytest-asyncio · pytest-cov |

---

## Quick Start

### Prerequisites

Docker Desktop (recommended), or Python 3.11+ / Node.js 18+ / MySQL 8 / Redis 7 / ES 8 / Kafka / MinIO installed manually.

### 1. Clone

```bash
git clone https://github.com/sijie-Z/DocMind-RAG.git
cd DocMind-RAG
```

### 2. Start Infrastructure

```bash
cd backend && docker compose up -d    # MySQL / Redis / ES / Kafka / MinIO
```

### 3. Configure

```bash
cp .env.docker.example .env.docker
# Edit .env.docker with your API keys
```

### 4. Start Backend

```bash
cd backend
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 5. Start Frontend

```bash
cd frontend && npm install && npm run dev          # http://localhost:5173
```

### Demo Accounts

| Username | Password | Role |
|----------|----------|------|
| `guest` | `123456` | User |
| `admin` | `admin123` | Admin |

---

## API Overview

| Module | Prefix | Description |
|--------|--------|-------------|
| Auth | `/api/v1/auth` | Register, login, token management |
| Users | `/api/v1/users` | User CRUD, audit logs |
| Files | `/api/v1/files` | File upload/download (MinIO) |
| Documents | `/api/v1/documents` | Doc parsing, vectorization (RAG pipeline) |
| Chat | `/api/v1/chat` | Sessions, messages, SSE streaming Q&A |
| Knowledge | `/api/v1/knowledge` | Knowledge base CRUD, search |
| Organizations | `/api/v1/organizations` | Orgs, members, roles & permissions |
| Monitoring | `/api/v1/monitoring` | Metrics, health checks, RAG quality |
| Notifications | `/api/v1/notifications` | Notification CRUD, WebSocket push |
| Prompts | `/api/v1/prompts` | Prompt template management |
| Token Usage | `/api/v1/token-usage` | Token statistics |
| Manuals | `/api/v1/manuals` | System user guides |
| Workflows | `/api/v1/workflows` | Agent workflow CRUD, execution, debugging |
| LLM Config | `/api/v1/llm-config` | Model configuration |
| Curated Skills | `/api/v1/curated-skills` | Agent skill library |
| Memory | `/api/v1/memory` | Short/long/working memory |
| Agent | `/api/v1/agent` | PER Agent conversations, tool calls |
| User Settings | `/api/v1/user` | Personal preferences |

Swagger docs: http://localhost:8000/docs

---

## Project Structure

```
DocMind/
├── backend/
│   ├── app/
│   │   ├── api/v1/endpoints/     # REST API (19 modules)
│   │   ├── agent/                # PER Agent core
│   │   │   ├── loop.py           #   PER main loop
│   │   │   ├── planner.py        #   Planner
│   │   │   ├── executor.py       #   Executor (DAG/Serial + retry)
│   │   │   ├── reflector.py      #   Reflector
│   │   │   ├── reviewer.py       #   Adversarial reviewer
│   │   │   ├── run_report.py     #   RunReport (run summary)
│   │   │   ├── experience/       #   Experience memory
│   │   │   ├── replay/           #   Execution replay
│   │   │   ├── mining/           #   Pattern mining
│   │   │   └── tools/            #   25+ tool implementations
│   │   ├── core/                 # Infrastructure (DB/Redis/ES/Kafka/MinIO)
│   │   ├── rag/                  # RAG pipeline
│   │   ├── services/             # Business service layer
│   │   └── worker/               # Kafka async processing
│   ├── tests/                    # 422+ test cases
│   └── benchmark/                # Benchmark framework
├── frontend/src/                 # Vue 3 frontend
│   └── views/                    # Pages (19 modules)
└── docs/                         # Documentation
```

---

## Testing

```bash
cd backend
python -m pytest tests/ -v --tb=short
python -m pytest tests/ --cov=app --cov-report=html
```

---

## Deployment

```bash
# Docker Compose
cd backend && docker compose up -d

# Production
# See deploy/ directory
```

---

## Links

- **Research Repo**: https://github.com/sijie-Z/DocMind-Agent-Causal-Study
- **API Docs**: http://localhost:8000/docs
- **Issues**: https://github.com/sijie-Z/DocMind-RAG/issues

---

<p align="center">
  <sub>Built with ❤️ by the DocMind Team · MIT License</sub>
</p>
