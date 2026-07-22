<p align="center">
  <strong>English</strong> · <a href="README.md">中文</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-blue?logo=python" alt="Python">
  <img src="https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi" alt="FastAPI">
  <img src="https://img.shields.io/badge/Vue_3-3.4-4FC08D?logo=vuedotjs" alt="Vue 3">
  <img src="https://img.shields.io/badge/License-MIT-green" alt="License">
</p>

<div align="center">
  <h1>🤖 DocMind</h1>
  <p><strong>Enterprise AI Agent Knowledge Base — PER Architecture · Hybrid RAG · Observable · Self-Improving</strong></p>
</div>

---

## Overview

DocMind is a full-stack enterprise AI knowledge base system built around the **PER (Plan-Execute-Reflect) Agent architecture**. It provides multi-format document parsing, hybrid retrieval-augmented generation (RAG), visual workflow editing, real-time SSE streaming Q&A, and a comprehensive observability stack.

Core use cases:
- 📄 **Document KB** — Upload PDFs, Word docs, Excel sheets. Auto-parse, chunk, embed, and semantic search.
- 💬 **Intelligent Q&A** — RAG pipeline: keyword + vector hybrid search, reranking, LLM generation with SSE streaming.
- 🤖 **Autonomous Agent** — PER three-phase loop (Plan → Execute → Reflect), 25+ tools for complex multi-step tasks.
- 🧩 **Workflow Editor** — Visual DAG design & debugging (Vue Flow). Configurable LLM models, prompts, and execution engines.
- 📊 **Observability** — Prometheus metrics, Langfuse full-trace, 12 SSE event types, RunReport execution summaries.
- 🔐 **Multi-Tenant + RBAC** — Organization isolation, role-based permissions, JWT authentication.

> Design decisions and experimental data belong in the research repo: [DocMind-Agent-Causal-Study](https://github.com/sijie-Z/DocMind-Agent-Causal-Study)

---

## System Architecture

```
┌── Frontend ──────────────────────────────────┐
│  Vue 3 · TypeScript · Naive UI · ECharts     │
│  Vue Flow Editor · Pinia · UnoCSS            │
├── API Gateway ───────────────────────────────┤
│  FastAPI · JWT + RBAC · CORS · SSE · GZip    │
│  WebSocket Push · Request ID Tracking        │
├── AI Agent Core ─────────────────────────────┤
│  PER Loop (Planner → Executor → Reflector)   │
│  Tool Registry (25+) · RunReport · Reviewer  │
│  Experience Memory · Replay · Pattern Mining │
├── RAG Pipeline ──────────────────────────────┤
│  HybridRetriever (BM25 + Vector + RRF fusion)│
│  Query Processor · Reranker · Context Comp.   │
│  Two-Level Cache (Exact + Semantic) · PII    │
├── LLM ───────────────────────────────────────┤
│  DeepSeek · OpenAI-compatible API · Langfuse │
├── Data Layer ────────────────────────────────┤
│  MySQL 8 · Elasticsearch 8 · Redis 7         │
│  Kafka · MinIO (S3-compatible Object Storage) │
└──────────────────────────────────────────────┘
```

---

## Core Modules

### 🤖 PER Agent System (`backend/app/agent/`)

An autonomous AI agent that decomposes and executes multi-step tasks:

```
User instruction
  → Planner   — Decompose into DAG/Serial steps, recommend tools, assess risk
  → Executor  — Dependency-aware scheduling, parallel independent steps,
                timeout/retry/fallback per step
  → Reflector — Verify output, detect hallucinations/gaps/contradictions, trigger fixes
  → RunReport — Summarize the entire run: what happened, why, and outcome
```

| Module | File | Purpose |
|--------|------|---------|
| Main Loop | `loop.py` | PER three-phase orchestration, SSE events, RunReport generation |
| Planner | `planner.py` | LLM planner + structured rule templates (coarse/normal/fine) |
| Executor | `executor.py` | DAG topological sort + parallel group scheduling, timeout/retry/fallback |
| Reflector | `reflector.py` | Output quality evaluation, auto-correction suggestions |
| Reviewer | `reviewer.py` | Adversarial review — finds issues, proposes improvements |
| Tool Registry | `registry.py` | Centralized tool namespace + tag-based filtering |
| Context Engine | `context.py` | Token budget management, message window builder |
| Config | `config.py` | AgentConfig (model/temperature/max steps/phase toggles) |

**25+ Built-In Tools** (`tools/`):

| Category | Tool | Description |
|----------|------|-------------|
| Knowledge | `search_knowledge_base`, `vector_search` | Hybrid + semantic search |
| Web | `web_search` | DuckDuckGo real-time search |
| Code | `code_execution` | Sandboxed Python execution |
| Data | `data_analysis` | Pandas/NumPy data analysis |
| Translation | `translation` | Multi-language translation |
| Document | `summarize_document`, `extract_keywords` | Summaries, keyword extraction |
| Analysis | `extract_insights`, `cross_document_analysis`, `generate_report` | Deep analysis, cross-doc, reports |
| Management | `list_documents`, `get_document_info` | Document management |
| Conversation | `list_conversations`, `get_conversation_history` | Session history |
| External | `mcp_call`, `feishu/*` | MCP bridge, Feishu Bitable |

**Self-Improving System** (experimental):

| Subsystem | Directory | Purpose |
|-----------|-----------|---------|
| Experience Memory | `experience/` | Auto-extract lessons from failed runs, inject into Planner |
| Execution Replay | `replay/` | Full execution snapshots, save & replay with diff comparison |
| Pattern Mining | `mining/` | Scan replays for high-frequency tool sequences → skill suggestions |

### 📚 RAG Pipeline (`backend/app/rag/`)

Multi-stage retrieval-augmented generation — from user query to streaming answer:

```
Query enters
  → QueryIntentClassifier     — Intent classification (fact/procedure/definition/causal)
  → QueryComplexityClassifier — Complexity grading → strategy selection
  → Rewrite/HyDE              — Query rewriting + hypothetical document embeddings (optional)
  → HybridRetriever           — Keyword (fuzzy + wildcard + multi_match)
                                + Vector (script_score cosineSimilarity)
                                + RRF reciprocal rank fusion (k=60)
  → MMR Selection             — Maximal Marginal Relevance deduplication
  → Reranker                  — Cross-encoder rerank (local BGE or API)
  → ContextCompressor         — LLM context compression
  → Cache Check               — Exact match cache (TTL 600s) + semantic cache
  → LLM Generation            — Streaming SSE output, strict mode, PII masking
```

| File | Purpose |
|------|---------|
| `pipeline.py` | Pipeline orchestrator — chains all stages |
| `retriever.py` | HybridRetriever — 3 modes (keyword/hybrid/HyDE) + RRF + MMR |
| `reranker.py` | Cross-encoder rerank (local BAAI/bge-reranker-base or API) |
| `query_processor.py` | HyDE generation, LLM query rewrite, intent/complexity classification, decomposition |
| `cache.py` | RetrievalCache (exact TTL) + SemanticCache (cosine similarity) |
| `context_compressor.py` | LLM-based context pruning |
| `context_window.py` | Token budget message builder for RAG chat |
| `evaluator.py` / `metrics.py` | RAG quality scoring (groundedness, relevance) |

### 🔧 Infrastructure (`backend/app/core/`)

| Module | File(s) | Purpose |
|--------|---------|---------|
| Config | `config/` | Pydantic Settings (Base/DB/AI/Security), env-var driven |
| Database | `database.py` | SQLAlchemy async engine, Alembic migrations |
| Cache | `redis.py` | Lazy-connect proxy pattern, RedisTools cache wrapper |
| Search | `elasticsearch.py` | ES client proxy, dense_vector index, IK analyzer support |
| Storage | `minio_client.py` | MinIO S3-compatible client |
| Messaging | `kafka_client.py` | aiokafka producer |
| Auth | `security.py` | JWT + bcrypt, get_current_user dependency, RBAC helpers |
| Middleware | `middleware.py` | Metrics collection (request count/latency/errors) |
| Circuit Breaker | `circuit_breaker.py` | External service call circuit breaker |
| Step Runner | `step_runner.py` | Safe step execution with retry/fallback |
| Tracing | `tracing.py` | OpenTelemetry OTLP gRPC export |
| Trace Logger | `trace_logger.py` | Agent execution tracing to JSONL |
| Metrics | `prometheus.py` | Custom counters/histograms (retrieval latency, LLM tokens, cache hits, RAG quality) |
| WebSocket | `notification_ws.py` | Real-time push notification connection manager |
| Request ID | `request_id.py` | X-Request-ID injection |

### 🌐 API Layer (`backend/app/api/v1/`)

19 endpoint modules under `/api/v1`:

| Module | Prefix | Description |
|--------|--------|-------------|
| Auth | `/auth` | Register, login, refresh token, password reset, logout |
| Users | `/users` | User CRUD, profiles, avatars, session management, audit logs |
| Files | `/files` | Upload/download/delete, MinIO storage, chunked upload |
| Documents | `/documents` | Document parsing, vectorization, knowledge base indexing, status tracking |
| Chat | `/chat` | Session CRUD, SSE streaming Q&A, strict mode, debug endpoints |
| Knowledge | `/knowledge` | Knowledge base CRUD, retrieval, statistics, settings |
| Organizations | `/organizations` | Org CRUD, member management, role assignment |
| Monitoring | `/monitoring` | Performance metrics, health checks, alerts, RAG quality scoring |
| Notifications | `/notifications` | Notification CRUD, read marking, WebSocket push |
| Prompts | `/prompts` | Prompt template CRUD + versioning |
| Token Usage | `/token-usage` | Usage tracking + reports |
| Manuals | `/manuals` | System user guide management |
| Workflows | `/workflows` | Workflow CRUD, execution, debugging, node definitions |
| LLM Config | `/llm-config` | Per-user model configuration |
| Skills | `/curated-skills` | Skill discovery & management |
| Agent Memory | `/memory` | Short/long/working memory CRUD, semantic search |
| Agent | `/agent` | PER Agent conversations (SSE streaming), tool calls, configuration |
| User Settings | `/user` | Personal UI preferences |
| Demo Data | `/demo` | Seed demo documents & data |

Swagger docs: `http://localhost:8000/docs`

### 🖥 Frontend (`frontend/src/`)

Vue 3 + TypeScript + Naive UI SPA, 21 routes:

| Page | Route | Description |
|------|-------|-------------|
| Dashboard | `/dashboard` | KPI cards, charts, quick access |
| AI Chat | `/chat` | Dual mode RAG + Agent, SSE streaming |
| Conversations | `/conversations` | Chat history list & management |
| Knowledge Base | `/knowledge` | Document upload, parsing, search, knowledge graph |
| Search | `/search` | Global standalone search |
| Prompts | `/prompts` | Prompt template management |
| Workflow | `/workflow` | Visual DAG editor (Vue Flow) |
| Agent Config | `/agent` | Model/temperature/tools/memory toggle config |
| Organizations | `/organizations` | Org CRUD, member management (admin) |
| Users | `/users` | User CRUD, role assignment (admin) |
| Audit Logs | `/audit-logs` | User activity audit (admin) |
| Monitoring | `/monitoring` | Performance charts, health status (admin) |
| Profile | `/profile` | Personal info, password change |
| Notifications | `/notifications` | Real-time notification list |
| System Help | `/system-help` | Usage help docs |
| About | `/system-about` | System info |

### 🧰 Service Layer (`backend/app/services/`)

| Service | File | Purpose |
|---------|------|---------|
| Auth Service | `auth_service.py` | JWT creation/validation, bcrypt hashing, token blacklist (jti) |
| Document Parser | `document_parser.py` | Parse PDF/Word/Excel/TXT/MD, text extraction & chunking |
| RAG Service | `rag_service.py` | RAG pipeline facade |
| File Service | `file_service.py` | File storage & retrieval |
| Knowledge Service | `knowledge_service.py` | Knowledge base business logic |
| Permission Service | `permission_service.py` | RBAC permission initialization & management |
| Audit Service | `audit_service.py` | Audit log write & query |
| Workflow Engine | `workflow_engine.py` | Workflow execution engine |
| Organization Service | `organization_service.py` | Multi-tenant org management |
| Memory Service | `memory_service.py` | Agent memory CRUD |
| PII Masking | `masking_service.py` | Sensitive info redaction |
| Semantic Chunker | `semantic_chunker.py` | Semantic-aware text chunking |
| Graph RAG | `graph_rag_service.py` | Knowledge graph enhanced retrieval |

### 🔄 Async Processing (`backend/app/worker/`)

A Kafka consumer worker process (`start_worker.py`) listens on the `file-processing` topic, handling: document parsing → chunking → embedding → ES indexing.

---

## Quick Start

### Prerequisites

Docker Desktop (recommended), or manual install:
- Python 3.11+ · Node.js 18+ · MySQL 8 · Redis 7 · Elasticsearch 8 · Kafka · MinIO

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
# Edit .env.docker — at minimum fill in LLM API Keys:
#   DEEPSEEK_API_KEY=sk-xxx
#   DEEPSEEK_API_URL=https://api.deepseek.com/v1
#   EMBEDDING_API_KEY=sk-xxx
#   EMBEDDING_API_URL=https://api.deepseek.com/v1
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

### Access

| URL | Description |
|-----|-------------|
| http://localhost:5173 | Frontend UI |
| http://localhost:8000/docs | Swagger API Docs |
| http://localhost:8000/health | Health Check |
| http://localhost:8000/metrics | Prometheus Metrics |

### Demo Accounts

| Username | Password | Role |
|----------|----------|------|
| `guest` | `123456` | User |
| `admin` | `admin123` | Admin |

---

## Configuration

Environment variables via `.env` file. Key settings:

| Variable | Description | Default |
|----------|-------------|---------|
| `DEEPSEEK_API_KEY` | LLM API Key | Required |
| `DEEPSEEK_API_URL` | LLM API URL | `https://api.deepseek.com/v1` |
| `EMBEDDING_API_KEY` | Embedding API Key | Required |
| `EMBEDDING_API_URL` | Embedding API URL | Required |
| `DATABASE_URL` | MySQL connection string | `mysql+aiomysql://root:...` |
| `VECTOR_DIMENSION` | Vector dimension | `1536` |
| `ENABLE_TRACING` | OpenTelemetry toggle | `false` |
| `ENABLE_DEMO_ACCOUNT` | Enable demo accounts | `true` |
| `APP_VERSION` | App version | `1.0.0` |

Full configuration reference: `backend/.env.example` and `backend/.env.docker.example`.

---

## Project Structure

```
DocMind/
├── backend/
│   ├── app/
│   │   ├── main.py                  # FastAPI entry point, lifecycle management
│   │   ├── agent_api.py             # Standalone agent test API (port 8010)
│   │   ├── dependencies.py          # Dependency injection container
│   │   ├── exceptions.py            # Custom exception hierarchy
│   │   ├── api/v1/
│   │   │   ├── router.py            # Main router (19 modules registered)
│   │   │   └── endpoints/           # Endpoint implementations (19 files)
│   │   ├── agent/                   # PER Agent core
│   │   │   ├── loop.py              #   PER main loop
│   │   │   ├── planner.py           #   Planner (LLM + structured rules)
│   │   │   ├── executor.py          #   Executor (DAG/Serial + retry)
│   │   │   ├── reflector.py         #   Reflector
│   │   │   ├── reviewer.py          #   Adversarial Reviewer
│   │   │   ├── run_report.py        #   RunReport (execution summary)
│   │   │   ├── orchestrator.py      #   Deterministic orchestrator
│   │   │   ├── service.py           #   AgentService (Facade)
│   │   │   ├── config.py            #   AgentConfig
│   │   │   ├── exec_context.py      #   ExecutionContext
│   │   │   ├── registry.py          #   Tool registry
│   │   │   ├── memory_bridge.py     #   Memory bridge
│   │   │   ├── events.py            #   SSE event types (12 types)
│   │   │   ├── message.py           #   Agent message bus
│   │   │   ├── tracing.py           #   Execution trace records
│   │   │   ├── tools/               #   25+ tool implementations
│   │   │   │   ├── web_search.py    #   Web search
│   │   │   │   ├── code_execution.py #  Python/SQL sandbox
│   │   │   │   ├── data_analysis.py #   Data analysis
│   │   │   │   ├── translation.py   #   Translation
│   │   │   │   ├── skills.py        #   Skill learning
│   │   │   │   ├── utility.py       #   General utilities
│   │   │   │   ├── mcp_bridge.py    #   MCP protocol bridge
│   │   │   │   └── feishu/          #   Feishu integration
│   │   │   ├── experience/          #   Experience memory
│   │   │   │   ├── extractor.py     #   Experience extractor
│   │   │   │   ├── store.py         #   Experience store
│   │   │   │   ├── models.py        #   Data models
│   │   │   │   └── run_extract.py   #   Run-level extraction
│   │   │   ├── replay/              #   Execution replay
│   │   │   │   └── engine.py        #   Replay engine
│   │   │   └── mining/              #   Pattern mining
│   │   │       ├── miner.py         #   Miner
│   │   │       ├── analyzer.py      #   Analyzer
│   │   │       ├── patterns.py      #   Pattern definitions
│   │   │       └── report.py        #   Mining reports
│   │   ├── core/                    # Infrastructure
│   │   │   ├── config/              #   Settings (Base/DB/AI/Security)
│   │   │   ├── database.py          #   SQLAlchemy engine
│   │   │   ├── redis.py             #   Redis proxy
│   │   │   ├── elasticsearch.py     #   ES proxy
│   │   │   ├── minio_client.py      #   MinIO client
│   │   │   ├── kafka_client.py      #   Kafka producer
│   │   │   ├── security.py          #   JWT + RBAC
│   │   │   ├── middleware.py         #   Metrics collection
│   │   │   ├── circuit_breaker.py   #   Circuit breaker
│   │   │   ├── step_runner.py       #   Step runner
│   │   │   ├── tracing.py           #   OpenTelemetry
│   │   │   ├── trace_logger.py      #   JSONL trace logging
│   │   │   ├── prometheus.py        #   Prometheus metrics
│   │   │   ├── notification_ws.py   #   WebSocket manager
│   │   │   ├── request_id.py        #   Request ID
│   │   │   ├── response.py          #   Unified response
│   │   │   └── ensure_demo_user.py  #   Demo account seeding
│   │   ├── rag/                     # RAG pipeline
│   │   │   ├── pipeline.py          #   Pipeline orchestrator
│   │   │   ├── retriever.py         #   Hybrid retriever
│   │   │   ├── reranker.py          #   Reranker
│   │   │   ├── query_processor.py   #   Query processing (intent/complexity/HyDE)
│   │   │   ├── cache.py             #   Two-level cache
│   │   │   ├── context_compressor.py #  Context compression
│   │   │   ├── context_window.py    #   Token budget management
│   │   │   └── evaluator.py         #   Quality evaluation
│   │   ├── services/                # Business service layer (14 services)
│   │   ├── models/                  # ORM models (14 entities)
│   │   ├── schemas/                 # Pydantic request/response schemas
│   │   └── worker/                  # Kafka consumer
│   ├── tests/                       # 422+ test cases
│   ├── benchmark/                   # Benchmark framework
│   ├── alembic/                     # DB migrations
│   ├── scripts/                     # Utility scripts
│   ├── docker/                      # Docker init SQL
│   ├── config/                      # Prometheus/Grafana configs
│   └── docker-compose.yml           # 10-service orchestration
├── frontend/
│   └── src/
│       ├── views/                   # Pages (19 modules, 21 routes)
│       ├── components/              # Shared components
│       ├── api/                     # Axios API clients
│       ├── stores/                  # Pinia state stores
│       ├── router/                  # Vue Router config
│       ├── composables/             # Vue composables
│       ├── locales/                 # i18n internationalization
│       └── utils/                   # Utility functions
└── docs/                            # Design documents
```

---

## Testing

```bash
cd backend

# Run all tests
python -m pytest tests/ -v --tb=short

# Coverage report
python -m pytest tests/ --cov=app --cov-report=html

# Frontend tests
cd ../frontend && npm run test
```

---

## Deployment

### Docker Compose

```bash
cd backend && docker compose up -d
# Starts 10 services: backend, worker, mysql, redis, elasticsearch, minio, zookeeper, kafka, prometheus, grafana
```

### Infrastructure Ports

| Service | Port |
|---------|------|
| FastAPI Backend | 8000 |
| Vite Frontend Dev | 5173 |
| MySQL | 3308 (Docker) / 3306 (local) |
| Redis | 6390 (Docker) / 6379 (local) |
| Elasticsearch | 9200 |
| MinIO API / Console | 9000 / 9001 |
| Kafka | 9092 / 29092 (internal) |
| Prometheus | 9090 |
| Grafana | 3001 |

---

## Links

- **Research Repo**: https://github.com/sijie-Z/DocMind-Agent-Causal-Study
- **GitHub**: https://github.com/sijie-Z/DocMind-RAG
- **API Docs**: http://localhost:8000/docs
- **Issues**: https://github.com/sijie-Z/DocMind-RAG/issues

---

<p align="center">
  <sub>Built with ❤️ by the DocMind Team · MIT License</sub>
</p>
