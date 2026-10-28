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
  <p><strong>Enterprise RAG×Agent platform — eval gates in CI, fake-successes cleared, ratchets that only shrink. Quality can't silently regress.</strong></p>
</div>

---

## Overview

DocMind is a full-stack enterprise AI knowledge base system built around the **PER (Plan-Execute-Reflect) Agent architecture**. It provides multi-format document parsing, hybrid retrieval-augmented generation (RAG), visual workflow editing, real-time SSE streaming Q&A, and a comprehensive observability stack.

Core use cases:
- 📄 **Document KB** — Upload PDF, Word, Excel, TXT, Markdown, CSV, and PNG/JPG/JPEG/WEBP/BMP images (OCR). Auto-parse, chunk, embed, and semantic search.
- 💬 **Intelligent Q&A** — RAG pipeline: keyword + vector hybrid search, reranking, LLM generation with SSE streaming.
- 🤖 **Autonomous Agent** — PER three-phase loop (Plan → Execute → Reflect), 31 tools for complex multi-step tasks.
- 🧩 **Workflow Editor** — Visual DAG design & debugging (Vue Flow). Configurable LLM models, prompts, and execution engines.
- 📊 **Observability** — Prometheus metrics, Langfuse full-trace, 24 SSE event types, RunReport execution summaries.
- 🔐 **Multi-Tenant + RBAC** — Organization isolation, role-based permissions, JWT authentication.

> Design decisions and experimental data belong in the research repo: [DocMind-Agent-Causal-Study](https://github.com/sijie-Z/DocMind-Agent-Causal-Study)
>
> 📖 **User guide**: [docs/USER_GUIDE_EN.md](docs/USER_GUIDE_EN.md) — quick start, configuration, core concepts, API, operations, and troubleshooting.

---

## Project Status

> Positioning: **public project / immature product** — feature completeness runs ahead of stability; suitable for learning, secondary development, and local demos; harden security and operations before deploying publicly. The product can still be maturing while engineering discipline leads: **numbers carry their method, fixes carry evidence, regressions are stopped before merge** (see Quality & Gates).

Current baseline:
- Test baseline (frozen snapshot, 2026-10-15): backend `pytest tests/` **2223 passed / 51 skipped** (0 failed); frontend **861 passed** (128 test files) + `vue-tsc --noEmit` 0 errors. Numbers evolve with CI in real time — CI is the single source of truth; this page keeps a frozen snapshot. Method and commands: `docs/METRICS.md`.
- Recent fixes: org-scoped semantic cache, conversation ownership checks, org isolation for knowledge search/delete/rebuild, upload path traversal and MinIO deletion fixes, API key masking, web-fetch SSRF protection, workflow ownership isolation, in-process document processing when Kafka is unavailable, local startup scripts, and removal of sensitive artifacts from git tracking.
- Architecture: document parsing/embedding/ES indexing is unified in a single processor, the chat pipeline moved into `services/chat_service.py`, and a shared document authorization helper was added.
- Hardening: agent memory endpoints are user-scoped, the `/files` proxy requires auth for non-public objects, pure-ASGI metrics and rate-limit middleware are enabled, and the legacy `ci.yml` was removed.

Known limitations:
- Workflow code nodes are disabled by default. Set `ENABLE_WORKFLOW_CODE_NODES=true` to opt in; execution now requires the Docker sandbox.
- GraphRAG graphs, long-term/reflective agent memory, metrics snapshots, and live counters now persist to Redis (short-term/working memory stays in-process and does not survive a restart); live counters are written per instance and aggregated on read for multi-instance deployments.
- The knowledge, profile, and workflow editor pages have all been split into components/composables; no 1000-line SFC pages remain.

---

## Quality & Gates

"Never silently regress" is not a slogan — it is a set of checkable mechanisms:

- **Eval gate in CI (`Eval Gate`)** — retrieval quality is guarded per PR by a zero-tolerance gate: Eval v1.5 uses **80 golden-set items (60 positive / 20 negative)** and seven metrics (ClauseHit@1/@3/@5, MRR, EmptyAcc, NearMiss@5, errors) with zero tolerance — any drop turns the build red (fail-closed). `make eval-ci` (`mode=fake`, 2026-10-24, corpus commit `d867a2b3`) measures ClauseHit@1 **70.0%** / @3 **76.7%** / @5 **80.0%**, NearMiss@5 **25.0%**, MRR **0.74**, EmptyAcc **100%**, errors **0** (source PR #525, merged in 552efaad; not comparable with the v1 28-item baseline). Updating the baseline is an explicit PR action (rules in `backend/docs/eval-gate-phase1.md`; method and commands in `docs/METRICS.md`).
- **Ratchets only shrink** — a backend coverage floor (`--cov-fail-under=42`) plus four frontend baseline ratchets (hardcoded CJK / i18n completeness / naming consistency / translation-key usage). Growth turns them red; shrinkage is allowed.
- **Silent false-successes cleared (#301)** — a full audit of ~551 exception handlers; all 32 silent false-success paths closed (fail-closed logout, three-state retrieval, honest document status, degraded-identity stats…), each backed by regression tests.
- **Red-first + mutation verification** — every fix starts with a failing test; after the fix, injected defects must turn exactly the right tests red, proving the tests can actually detect the bug.
- **Documented-number invariants** — the SSE event count, endpoint-module count, frontend-route count, and tool count are derived from source; expected values live in `docs/facts.json`, and the public method for every metric (value / method / command / date / note) lives in `docs/METRICS.md`. `backend/scripts/check_doc_facts.py` verifies code ↔ facts JSON ↔ METRICS.md ↔ docs on every PR. Frontend routes are recorded in two ways: 34 route records (including top-level and redirect records) / 29 named routes (routes with a name).

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
│  Tool Registry (31) · RunReport · Reviewer  │
│  Experience Memory · Replay                  │
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

**31 Built-In Tools** (`core_tools.py` 11 + `tools/` 20; 10 tools are injected by default, with terminal/Feishu/delegation/extended tools gated by settings or explicit `tool_tags`; method: `docs/METRICS.md`):

| Category | Tool | Description |
|----------|------|-------------|
| Knowledge | `search_knowledge_base` | Hybrid search; `mode=vector` for pure semantic |
| Web | `web_search` | Real-time search; `action=fetch` for webpage text |
| Code | `execute_python`, `execute_sql` | Sandboxed Python / SQL (disabled by default, opt-in) |
| Data | `utility` | Math, format conversion, language detection, statistics, current time |
| Translation | `translate_text` | Multi-language translation |
| Document | `extract_insights`, `extract_keywords` | Summary/structured insights, keyword extraction |
| Analysis | `cross_document_analysis`, `generate_report` | Cross-doc analysis, report generation |
| Management | `list_documents`, `get_document_info` | Document management |
| Conversation | `list_conversations`, `get_conversation_history` | Session history (extended surface) |
| External | `mcp_call`, `feishu_bitable_query` | MCP bridge, Feishu Bitable (gated by default) |
| Skills | `load_skill_reference`, `deep_research`, `document_comparison`, `knowledge_analysis` | Skill loading, composite analysis (last three extended) |

**Self-Improving System** (experimental):

| Subsystem | Directory | Purpose |
|-----------|-----------|---------|
| Experience Memory | `experience/` | Auto-extract lessons from failed runs, inject into Planner |
| Execution Replay | `replay/` | Full execution snapshots, save & replay with diff comparison |

**Hooks extension points** (issue #474, off by default):

In-process event bus covering agent turns and tool execution, with `waterfall`
(can rewrite/short-circuit) and `serial` (ordered observation) semantics.
Configuration lives in `backend/config/hooks.yaml`; enable it with
`HOOKS_ENABLED=true` (disabled means the file is never read and behavior is
unchanged):

```yaml
hooks:
  - name: audit_tool_calls
    event: tools/pre_execute        # agent/turn_start | agent/pre_step | agent/post_step
    module: app.agent.hooks.builtin.audit_tool_calls   # must start with app.agent.hooks.
    enabled: true
    priority: 100                   # lower runs first
    timeout_seconds: 2.0            # 0.1-30
    failure_policy: fail_open       # fail_open | fail_closed
```

A handler is `async def handler(ctx, next)`: `await next(payload)` continues the
chain; skipping it short-circuits; return
`HookDecision(action="deny"|"modify"|"ask", ...)`. `ask` on `tools/pre_execute`
reuses the existing HITL approval gate — hooks cannot bypass approval. Failures
emit `hook_error` SSE events and `docmind_hook_*` metrics. See
`docs/decisions/ADR-004-hooks.md`.

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
  → ContextCompressor         — query-term sentence extraction (rule-based, no LLM call)
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
| `context_compressor.py` | Query-term sentence extraction compression (rule-based, no LLM call) |
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
| Circuit Breaker | `circuit_breaker.py` | Circuit breaker pattern — **state-machine API is wired into the LLM step path (cross-request breaking + half-open recovery, issue #242); covers the executor's per-step main LLM call only — planner (stream) / synthesis / reflector / loop direct-answer paths are not covered**; the unused decorator degraded-value path has been removed (issue #467; the state-machine API is the only contract) |
| LLM Fast-Fail | `llm_failfast.py` | Connection-level unreachable classification (refused / DNS / connect-phase timeout) + separate short connect timeout + bounded transient retries + per-endpoint negative cache; slow reads/writes and post-connect transport interruptions are not treated as unreachable (issue #467) |
| Step Runner | `step_runner.py` | Safe step execution with retry/fallback |
| Tracing | `tracing.py` | OpenTelemetry OTLP gRPC export |
| Trace Logger | `trace_logger.py` | Agent execution tracing to JSONL (dev-only: used solely by the unmounted standalone test app `agent_api.py`:8010; no in-repo readers) |
| Metrics | `prometheus.py` | Custom counters/histograms (retrieval latency, LLM tokens, cache hits, RAG quality) |
| WebSocket | `notification_ws.py` | Real-time push notification connection manager |
| Request ID | `request_id.py` | X-Request-ID injection |

### 🌐 API Layer (`backend/app/api/v1/`)

28 endpoint modules under `/api/v1`:

| Module | Prefix | Description |
|--------|--------|-------------|
| Auth | `/auth` | Register, login, refresh token, password reset, logout |
| Users | `/users` | User CRUD, profiles, avatars, session management, audit logs (personal API-key endpoints deprecated) |
| API Keys | `/api-keys` | Create/list/revoke `dm_` API keys (bcrypt hash, scopes; issue #480) |
| Files | `/files` | Upload/download/delete, MinIO storage, chunked upload |
| Documents | `/documents` | Document parsing, vectorization, knowledge base indexing, status tracking |
| Chat | `/chat` | Session CRUD, SSE streaming Q&A, strict mode, debug endpoints |
| Knowledge | `/knowledge` | Knowledge base CRUD, retrieval, statistics, settings |
| Organizations | `/organizations` | Org CRUD, member management, role assignment |
| Organization Settings | `/organizations` | Org-level settings and audit (shared prefix with Organizations) |
| Monitoring | `/monitoring` | Performance metrics, health checks, alerts, RAG quality scoring |
| Notifications | `/notifications` | Notification CRUD, read marking, WebSocket push |
| Prompts | `/prompts` | Prompt template CRUD + versioning |
| Token Usage | `/token-usage` | Usage tracking + reports |
| Manuals | `/manuals` | System user guide management |
| Workflows | `/workflows` | Workflow CRUD, execution, debugging, node definitions |
| Scheduled Tasks | `/scheduled-tasks` | Scheduled task CRUD, pause/resume, and manual trigger |
| LLM Config (deprecated) | `/llm-config` | Global config entry removed from the workflow editor; endpoints marked deprecated pending the unified Provider layer |
| Skills | `/curated-skills` | Skill discovery & management |
| Agent Memory | `/memory` | Short/long/working/reflective memory CRUD, semantic search |
| Agent | `/agent` | PER Agent conversations (SSE streaming), tool calls, configuration |
| Session Fork | `/sessions` | Session fork / seed / lineage (issue #476) |
| Persistent Terminal | `/terminal` | Container-backed persistent PTY: session CRUD, one-time WS tickets, WebSocket bridge (issue #477) |
| Agent Commands | `/commands` | Server-side command catalog and model-free execution (/help, /status, /export, /tools) |
| MCP | `/mcp` | MCP server status (admin, read-only) |
| JSON-RPC | `/rpc` | HTTP JSON-RPC 2.0 (API key + `rpc` scope; gated by `RPC_HTTP_ENABLED`) |
| User Settings | `/user` | Personal UI preferences |
| Webhooks | `/webhooks` | Inbound webhook rule management, HMAC-verified delivery, outbox processing (issue #478) |
| Demo Data | `/demo` | Seed demo documents & data |

Swagger docs: `http://localhost:8000/docs`

### 🖥 Frontend (`frontend/src/`)

Vue 3 + TypeScript + Naive UI SPA, 34 route records (29 named routes; the table below lists key pages):

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
| Document Parser | `document_parser.py` | Parse PDF/Word/Excel/CSV/TXT/MD and images (OCR), text extraction & chunking |
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
| Graph Service | `graph_rag_service.py` | Knowledge-graph build, query, and visualization (not part of the RAG retrieval path) |

### 🔄 Async Processing (`backend/app/worker/`)

A Kafka consumer worker process (`python -m app --profile worker`; the legacy `start_worker.py` still works) listens on the `file-processing` topic, handling: document parsing → chunking → embedding → ES indexing.

When Kafka is unavailable, `/documents/upload` and knowledge-base rebuild endpoints automatically fall back to in-process processing, so local development does not stay stuck in `pending`.

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

### 2. Configure

Create the environment files **before starting anything** — without `backend/.env.docker`, every compose command fails outright.

```bash
cd backend

# Local development (apps run on the host — steps 4/5 below)
cp .env.example .env               # Windows: copy .env.example .env
# Required: SECRET_KEY / JWT_SECRET_KEY (generate: python -c "import secrets; print(secrets.token_hex(32))")
# Fill in DEEPSEEK_API_KEY / EMBEDDING_API_KEY as needed (LLM / Embedding credentials)

# Any Docker use (including infrastructure-only in step 3) requires this file — compose fails without it:
cp .env.docker.example .env.docker
# Fill in the required values below; never commit real credentials.
```

- **Required for Compose interpolation**: generate and set `MYSQL_ROOT_PASSWORD`,
  `MYSQL_PASSWORD`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, and `GRAFANA_PASSWORD`;
  none has a default password.
- **Required for backend startup**: `SECRET_KEY` and `JWT_SECRET_KEY`; chat also needs
  `DEEPSEEK_API_KEY`. Embedding defaults to local Ollama (nomic-embed-text / 768 dims)
  and reuses the chat key. Configure `EMBEDDING_API_KEY` / `EMBEDDING_API_URL` for a
  cloud embedding provider.
- Compose performs **whole-file interpolation before service selection**: even without
  selecting Grafana, `GRAFANA_PASSWORD` remains required. This documents existing
  behavior; it neither adds a default password nor weakens Grafana's validation.
- `.env.example` targets host apps with Docker infrastructure and defaults to
  `localhost:3308/docmind` plus Redis `localhost:6390`. Full Docker still overrides
  these with container-internal `docmind-mysql:3306/docmind` and
  `docmind-redis:6379`; do not substitute the host-published ports.
- If MySQL and Redis also run directly on the host without Docker, update `.env`
  with their actual hosts, ports, and database name (commonly 3306/6379 locally,
  but always follow your service configuration).

### 3. Start Infrastructure

```bash
cd backend && docker compose --env-file .env.docker up -d mysql redis elasticsearch minio kafka zookeeper
# Infrastructure containers only (apps keep running on the host — see below); skip this step without Docker and use local services per SETUP.md
# Full Docker deployment (apps in containers): see the Deployment section
```

### 4. Start Backend

On Windows you can use `start_windows.bat` at the repo root (it detects `.venv` or `venv`). Manual start:

```bash
cd backend
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

The unified entrypoint is recommended (issue #479):

```bash
# profile = web | worker | headless | sdk | acp (default: web)
python -m app --profile web
# apply patch files in order
python -m app --profile web --patch config/patches/local.yaml
# print the merged config (sorted keys, masked secrets)
python -m app --profile web --dump-config
```

Layering: `backend/config/base.yaml` → bundles declared by the profile → `profiles/<profile>.yaml` → `--patch` → environment (`.env` belongs to the environment layer). `uvicorn app.main:app` and `python run.py` remain supported compatibility entrypoints; keep using `python run.py` for reload.

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
| `guest` | Random at first creation (printed once, or via `DEMO_PASSWORD`) | User (when `ENABLE_DEMO_ACCOUNT=true`) |
| `admin` | Created via `reset_admin.py` (`ADMIN_PASSWORD` env) | Admin |

---

## Configuration

Environment variables via `.env` file. Key settings:

| Variable | Description | Default |
|----------|-------------|---------|
| `DEEPSEEK_API_KEY` | LLM API Key | Required |
| `DEEPSEEK_API_URL` | LLM API URL | `https://api.deepseek.com` |
| `DEEPSEEK_MODEL` | Chat model | `deepseek-v4-flash` |
| `EMBEDDING_API_KEY` | Embedding API Key | Falls back to `DEEPSEEK_API_KEY` |
| `EMBEDDING_API_URL` | Embedding API URL | `http://localhost:11434/v1` (local Ollama) |
| `EMBEDDING_MODEL` | Embedding model | `nomic-embed-text` |
| `DATABASE_URL` | MySQL connection string | `mysql+aiomysql://...` (required in deployments) |
| `VECTOR_DIMENSION` | Vector dimension | `768` |
| `ENABLE_TRACING` | OpenTelemetry toggle | `false` |
| `ENABLE_DEMO_ACCOUNT` | Enable demo accounts | `false` |
| `ENABLE_WORKFLOW_CODE_NODES` | Allow workflow code nodes (security risk; disabled by default) | `false` |
| `APP_VERSION` | App version | `1.22.1-rc.1` |

> Model / vector-dimension defaults are defined once in
> `backend/app/core/config/ai.py`. When `EMBEDDING_MODEL` / `VECTOR_DIMENSION` are
> not set explicitly, hardware tiers auto-select: CPU `bge-m3` / 1024, 8G-GPU
> `Qwen3-Embedding-4B` / 2048, 16G-GPU `Qwen3-Embedding-8B` / 4096. Explicit
> values (as in the templates above) take precedence over auto-tiering.

Full configuration reference: `backend/.env.example` and `backend/.env.docker.example`.

---

## Project Structure

```
DocMind/
├── backend/
│   ├── app/
│   │   ├── main.py                  # FastAPI entry point, lifecycle management
│   │   ├── agent_api.py             # Standalone agent test API (dev-only, port 8010, not in production deploy)
│   │   ├── dependencies.py          # Dependency injection container
│   │   ├── exceptions.py            # Custom exception hierarchy
│   │   ├── api/v1/
│   │   │   ├── router.py            # Main router (28 modules registered)
│   │   │   └── endpoints/           # Endpoint implementations (28 files)
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
│   │   │   ├── events.py            #   SSE event types (24 types)
│   │   │   ├── message.py           #   Agent message bus
│   │   │   ├── tracing.py           #   Execution trace records (dev-only: eval/offline; not written in production)
│   │   │   ├── core_tools.py        #   11 base tools
│   │   │   ├── tools/               #   20 extension tools (31 built-in tools total)
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
│   │   │   │   └── run_extract.py   #   Experience bootstrap (dev-only; manual run after benchmarks)
│   │   │   └── replay/              #   Execution replay
│   │   │       └── engine.py        #   Replay engine
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
│   │   ├── services/                # Business service layer (27 services)
│   │   ├── models/                  # ORM models (20 model files)
│   │   ├── schemas/                 # Pydantic request/response schemas
│   │   └── worker/                  # Kafka consumer
│   ├── tests/                       # 2223 backend tests (plus 861 frontend)
│   ├── benchmark/                   # Benchmark framework
│   ├── alembic/                     # DB migrations
│   ├── scripts/                     # Utility scripts
│   ├── docker/                      # Docker init SQL
│   ├── config/                      # Prometheus/Grafana configs
│   └── docker-compose.yml           # 10-service orchestration
├── frontend/
│   └── src/
│       ├── views/                   # Pages (23 page modules, 34 route records / 29 named routes)
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

Prerequisite: create and fill `backend/.env.docker` first (see Quick Start · 2. Configure) — without it, every compose command fails outright.

```bash
cd backend && docker compose --env-file .env.docker up -d
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
