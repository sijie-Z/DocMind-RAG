<p align="center">
  <h1 align="center">DocMind</h1>
  <p align="center"><strong>Enterprise RAG Knowledge Base System</strong></p>
  <p align="center">Upload documents, search with natural language, get AI-powered answers with citations.</p>
  <p align="center">
    <img src="https://img.shields.io/badge/Python-3.10+-blue?logo=python" alt="Python">
    <img src="https://img.shields.io/badge/FastAPI-0.104+-009688?logo=fastapi" alt="FastAPI">
    <img src="https://img.shields.io/badge/Vue-3.4-4FC08D?logo=vuedotjs" alt="Vue 3">
    <img src="https://img.shields.io/badge/TypeScript-5.3-3178C6?logo=typescript" alt="TS">
    <img src="https://img.shields.io/badge/Elasticsearch-8.11-005571?logo=elasticsearch" alt="ES">
    <img src="https://img.shields.io/badge/tests-311-passing" alt="Tests">
    <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
  </p>
</p>

---

## What is DocMind?

DocMind is a full-stack AI knowledge base system built on **RAG (Retrieval-Augmented Generation)**. Upload your documents, and the system automatically parses, chunks, embeds, and indexes them. Ask questions in natural language and get accurate, citation-backed answers.

**Live demo**: [Architecture Page](https://sijie-z.github.io/DocMind-RAG/architecture.html)

---

## Features

**Document Processing**
- PDF / Word / Excel / TXT / Markdown support
- LangChain-based smart chunking (sliding window + semantic)
- Kafka async pipeline for high-concurrency upload processing

**Hybrid Search**
- BM25 keyword + KNN vector dual-channel retrieval
- RRF (Reciprocal Rank Fusion) result merging
- Cross-Encoder reranking for precision
- Semantic cache (cosine similarity >= 0.92)

**AI Chat**
- WebSocket real-time streaming
- Multi-turn conversation with context memory
- Answer citations with `[n]` source references
- Regenerate response, export conversation

**Agent (ReAct)**
- 11 built-in tools (search, analysis, conversation, prompts)
- Autonomous task decomposition for complex queries
- Smart tool result truncation per tool type
- Skill learning from successful tool usage patterns
- SSE streaming with real-time tool call visualization

**Knowledge Graph**
- Canvas force-directed graph visualization
- Entity extraction with 7 type categories
- Drag, zoom, click for details, keyword filter

**Enterprise**
- RBAC (User -> Role -> Organization) multi-tenancy
- JWT auth + Token blacklist (Redis)
- Prometheus + Grafana monitoring
- Audit log for all operations

**Workflow Editor**
- Visual DAG workflow builder
- LLM / API / Code / Condition / Memory nodes
- Real-time debug and execution tracking

**Onboarding**
- Empty state guide for new users
- One-click demo data loading (3 sample documents + conversation)
- 8 prompt templates for common tasks

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | FastAPI + Uvicorn (async) |
| Database | MySQL 8 + SQLAlchemy 2.0 (async) + Alembic |
| Cache | Redis 5 |
| Search | Elasticsearch 8 (KNN + BM25) |
| Queue | Kafka (aiokafka) |
| Storage | MinIO (S3-compatible) |
| AI | ZhipuAI / OpenAI-compatible API (Chat + Embedding + Rerank) |
| Agent | ReAct loop + tool registry + context engine + skill learning |
| Parser | LangChain + PyPDF + python-docx |
| Frontend | Vue 3 + TypeScript + Vite |
| UI | Naive UI + ECharts + Vue Flow |
| State | Pinia |
| i18n | Vue I18n (zh / en / ja / fr) |
| Monitoring | Prometheus + Grafana |
| Security | JWT + RBAC + multi-tenancy + audit |

---

## Quick Start

### Prerequisites

- **Docker Desktop** (recommended) — or install Python 3.10+, Node.js 18+, MySQL 8, Redis, ES 8, Kafka, MinIO manually

### 1. Start Infrastructure

```bash
git clone https://github.com/sijie-Z/DocMind-RAG.git
cd DocMind-RAG/backend
docker-compose up -d
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env with your AI API key
```

Key settings:

```bash
DEEPSEEK_API_KEY=sk-xxx        # LLM API Key
EMBEDDING_API_KEY=sk-xxx       # Embedding API Key (same key for ZhipuAI)
RERANK_API_KEY=sk-xxx          # Rerank API Key (same key for ZhipuAI)
```

### 3. Start Backend

```bash
pip install -r requirements.txt
alembic upgrade head
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
python start_worker.py  # Kafka document processor (separate terminal)
```

### 4. Start Frontend

```bash
cd ../frontend
npm install
npm run dev
```

### Access

| Service | URL |
|---------|-----|
| Frontend | http://localhost:5173 |
| API Docs (Swagger) | http://localhost:8000/docs |
| Grafana | http://localhost:3000 |

**Demo account**: `guest` / `123456`

---

## Data Flow

```
Upload ──► MinIO + MySQL ──► Kafka ──► Worker ──► Parse & Chunk ──► Embed ──► Elasticsearch
                                                                              │
User Query ──► WebSocket ──► Hybrid Search (BM25 + Vector + RRF) ◄──────────┘
                                      │
                              Rerank ──► Context Assembly ──► LLM ──► Stream Response
```

---

## Project Structure

```
DocMind/
├── backend/
│   ├── app/
│   │   ├── agent/              # ReAct Agent (registry, tools, loop, context, skills)
│   │   ├── api/v1/endpoints/   # 15 API modules (auth, chat, knowledge, agent, demo...)
│   │   ├── core/               # Infrastructure (DB, ES, Kafka, MinIO, Redis, security)
│   │   ├── rag/                # RAG pipeline (retriever, reranker, context compressor, cache)
│   │   ├── models/             # SQLAlchemy models (12 tables)
│   │   ├── schemas/            # Pydantic request/response models
│   │   └── services/           # Business logic (17 service modules)
│   ├── tests/                  # 216 pytest cases (unit + integration)
│   ├── worker/                 # Kafka document processing worker
│   ├── docker-compose.yml      # Infrastructure containers
│   └── alembic/                # Database migrations
├── frontend/
│   ├── src/
│   │   ├── views/              # Page components (chat, knowledge, agent, dashboard...)
│   │   ├── api/                # API client modules (15 modules)
│   │   ├── stores/             # Pinia state management
│   │   ├── composables/        # Vue composables (useErrorHandler, useDebounce...)
│   │   └── locales/            # i18n (zh/en/ja/fr)
│   ├── tests/                  # 95 Vitest cases
│   └── package.json
├── docs/
│   ├── architecture.html       # Self-contained architecture showcase (82KB)
│   └── index.html              # GitHub Pages landing
└── monitoring/
    ├── prometheus.yml
    └── grafana-dashboard.json
```

---

## Testing

```bash
# Backend (216 tests)
cd backend && python -m pytest -v

# Frontend (95 tests)
cd frontend && npm test

# TypeScript check
cd frontend && npx vue-tsc --noEmit

# API type generation (requires backend running)
cd frontend && npm run api:generate
```

---

## API Overview

| Module | Path | Description |
|--------|------|-------------|
| Auth | `/api/v1/auth/*` | Login, register, token refresh |
| Users | `/api/v1/users/*` | User CRUD, role assignment |
| Documents | `/api/v1/documents/*` | Upload, status, download |
| Knowledge | `/api/v1/knowledge/*` | Search, stats, graph, rebuild |
| Chat | `/api/v1/chat/*` | WebSocket/SSE streaming, sessions |
| Agent | `/api/v1/agent/*` | ReAct agent (SSE), tools, skills |
| Prompts | `/api/v1/prompts/*` | Prompt template library |
| Workflow | `/api/v1/workflows/*` | DAG workflow editor |
| Demo | `/api/v1/demo/*` | Demo data seeding |
| Monitoring | `/api/v1/monitoring/*` | System metrics, health check |

Full API docs: http://localhost:8000/docs

---

## Security

| Level | Issue | Status |
|-------|-------|--------|
| CRITICAL | Plaintext password in logs | Fixed |
| CRITICAL | Real API key in .env.example | Fixed |
| CRITICAL | Weak default secret key | Fixed (startup validation) |
| CRITICAL | Token valid after logout | Fixed (Redis blacklist) |
| HIGH | Bare `except:` swallowing errors | Fixed (6 locations) |
| HIGH | CORS allow all methods | Fixed (restricted) |
| HIGH | Auth paths excluded from rate limiting | Fixed |
| MEDIUM | `datetime.utcnow()` deprecated | Fixed |

---

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for detailed release history.

**Latest (Round 12)**: Demo data system, onboarding flow, architecture showcase, GitHub Pages, API type generation.

---

## License

[MIT](LICENSE) | [GitHub](https://github.com/sijie-Z/DocMind-RAG)
