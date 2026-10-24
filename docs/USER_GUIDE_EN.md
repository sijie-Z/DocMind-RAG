# DocMind User Guide

> For readers who have just cloned the repo and want to **run it, upload a document, get a grounded answer, and use the Agent workbench**.
>
> This guide only *references* the sources of truth:
> - ports, credentials, and service orchestration: `backend/docker-compose.yml` and `backend/.env.docker.example`;
> - headline numbers (39 built-in tools / 26 endpoint modules / 34 frontend routes / 24 SSE event types): `docs/facts.json`; existing anchors in README/architecture pages are checked on every PR by `backend/scripts/check_doc_facts.py`, and this guide mirrors them (the guide itself is not yet in the anchor list);
> - architecture and design rationale: `README.md`, `AGENT_ARCHITECTURE.md`, `docs/decisions/`.
>
> 中文版：[USER_GUIDE.md](USER_GUIDE.md)

---

## Contents

1. [Five-minute quick start](#1-five-minute-quick-start)
2. [Configuration](#2-configuration)
3. [Core concepts](#3-core-concepts)
4. [Hands-on features](#4-hands-on-features)
5. [API and integration](#5-api-and-integration)
6. [Operations](#6-operations)
7. [Troubleshooting FAQ](#7-troubleshooting-faq)
8. [Development and contribution](#8-development-and-contribution)
9. [Fact source index](#9-fact-source-index)

---

## 1. Five-minute quick start

### 1.1 Prerequisites

| Dependency | Requirement | Evidence |
|------------|-------------|----------|
| Python | 3.11+ | `backend/pyproject.toml` sets `target-version = "py311"`; README badge says Python 3.11+ |
| Node.js | **22.12+** (CI uses Node 22) | `frontend/package-lock.json`: Vite 8 requires `^20.19.0 \|\| >=22.12.0`, Vitest 5 requires `^22.12.0 \|\| ^24.0.0 \|\| >=26.0.0`; `.github/workflows/ci-fast.yml` uses Node 22. The root README's "Node.js 18+" is stale — trust the lockfile engines |
| Docker Desktop | with Compose v2 (WSL2 backend recommended on Windows) | `backend/docker-compose.yml`; `SETUP.md` |
| Ollama | default embeddings use local `nomic-embed-text` (768 dims) | defaults in `backend/app/core/config/ai.py` |
| Ports | 8000, 5173; infrastructure 3308, 6390, 9200/9300, 9000/9001, 9092/29092, 2181, 9090, 3001 | `backend/docker-compose.yml` |

Prepare Ollama (needed for the default embedding path; skip if you switch to a cloud embedding provider):

```bash
ollama pull nomic-embed-text
```

Ollama is **optional infrastructure** (it is not a compose service), but the default embedding configuration points at it. Without Ollama and without changing the embedding trio, document embedding fails and the vector retrieval arm degrades or fails (keyword retrieval may still return results). The startup probe only warns when the service is unreachable, so "it started" does not mean "retrieval works" — verify with `/readyz` and one real search.

> You can replace the defaults with environment variables, but **URL / model / dimension must change together**. For example, to use OpenAI `text-embedding-3-small`, set `EMBEDDING_API_URL`, `EMBEDDING_MODEL` **and** `VECTOR_DIMENSION=1536`. Changing only the URL will be rejected by the startup probe or at first index write (see 2.3).

### 1.2 Choose a run posture

| Posture | Good for | What runs |
|---------|----------|-----------|
| **A. Host development** (recommended first time) | editing code, reading logs, debugging | Docker runs infrastructure only; backend / worker / frontend run on the host |
| **B. Full Docker** | quick evaluation, deployment-like setup | Compose runs backend + worker + all infrastructure (10 services); frontend is built/served separately |

Both postures prepare config files first; posture A additionally needs the host-side `.env` (see 1.3).

### 1.3 Prepare configuration files

Create the env files **before starting anything** — without `backend/.env.docker`, every compose command fails outright.

```bash
cd backend

# Docker side (compose interpolation + container env_file)
cp .env.docker.example .env.docker      # Windows: copy .env.docker.example .env.docker

# Host side (used when backend / worker run on the host)
cp .env.example .env                    # Windows: copy .env.example .env
```

Generate secrets:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
python -c "import secrets; print(secrets.token_hex(24))"   # database / middleware passwords
```

The "required" label on `.env.docker` has three distinct meanings:

- **Compose interpolation fail-closed** (`:?`; any compose command fails when missing): `MYSQL_ROOT_PASSWORD`, `MYSQL_PASSWORD`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `GRAFANA_PASSWORD`.
- **Backend startup fail-closed**: empty `SECRET_KEY` / `JWT_SECRET_KEY` refuses startup.
- **Required for features, not startup-enforced**: `DEEPSEEK_API_KEY` — the service still starts; chat calls fail.

For `.env` (host side), at minimum set `SECRET_KEY`, `JWT_SECRET_KEY`, and `DEEPSEEK_API_KEY`. Note that `.env.example` points `DATABASE_URL` at `localhost:3306`, while **Docker infrastructure maps MySQL to 3308**; with posture A, change it:

```dotenv
DATABASE_URL=mysql+aiomysql://docmind:<MYSQL_PASSWORD>@localhost:3308/docmind
REDIS_HOST=localhost
REDIS_PORT=6390
```

> `SETUP.md` documents the same host-side ports. Change ports in compose and `.env`, not in a second configuration source.

### 1.4 Start infrastructure

```bash
cd backend
docker compose --env-file .env.docker up -d mysql redis elasticsearch minio kafka zookeeper
docker compose --env-file .env.docker ps        # wait until services are ready (~1-2 min; zookeeper/kafka have no healthcheck and show running, not healthy)
```

Notes:

- `--env-file .env.docker` supplies **compose interpolation** variables such as `MYSQL_PASSWORD`; the same file is also injected into the backend/worker containers via `env_file`. These are different mechanisms — configure both sides (Redis passwords especially; see compose comments).
- When running apps on the host, do **not** start backend/worker services; for full Docker, drop the service list: `docker compose --env-file .env.docker up -d`.
- Full Docker does not include a frontend container; build/serve the frontend separately (see `deploy/README.md`).

### 1.5 Start the backend

```bash
cd backend
python -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install -r requirements.txt

# First-time schema (explicit migration recommended; an empty DB also gets create_all at startup)
alembic upgrade head

python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

The unified entrypoint is also available (issue #479, recommended; profile = web | worker | headless | sdk | acp):

```bash
python -m app --profile web
python -m app --profile web --patch config/patches/local.yaml
python -m app --profile web --dump-config   # sorted JSON, secrets masked
```

`uvicorn app.main:app` and `python run.py` remain compatibility entrypoints (use the latter for reload). Layering: `config/base.yaml` → bundles → `profiles/<profile>.yaml` → `--patch` → environment.

On Windows you can also use `start_windows.bat` at the repo root (it detects `.venv` or `venv`), but it does not start the worker.

Three startup behaviors worth knowing:

1. **Secret validation**: empty `SECRET_KEY` / `JWT_SECRET_KEY` fails startup (`_check_secrets` in `backend/app/core/config/security.py`).
2. **Embedding consistency probe**: `EMBEDDING_STARTUP_PROBE=true` by default. A reachable embedding service returning a different dimension than `VECTOR_DIMENSION` **refuses startup**; an unreachable service only logs a warning (offline development is expected). See `backend/app/core/config/ai.py` and issue #196.
3. **Schema strategy**: `init_db()` runs `create_all` **only on an empty database**; existing databases must use `alembic upgrade head` (`backend/app/core/database.py`). Production/upgrades always follow migrations.

### 1.6 Start the worker (document processing)

```bash
# new terminal
cd backend
source venv/bin/activate            # Windows: venv\Scripts\activate
python start_worker.py          # equivalent to: python -m app --profile worker
```

The unified entrypoint works too: `python -m app --profile worker`.

The worker is a Kafka consumer: parse → chunk → embed → index into ES. **If Kafka is available, the worker must run**, otherwise documents stay pending. When Kafka is unavailable, upload and rebuild endpoints automatically fall back to in-process processing (`README.md`, "Async Processing").

### 1.7 Start the frontend

```bash
cd frontend
npm ci            # npm ci to install only; to change dependencies use npx npm@12 install (see CONTRIBUTING)
npm run dev       # http://localhost:5173
```

> The root `README.md` says `npm install`, which conflicts with `CONTRIBUTING.md`: npm 10 hits an Arborist bug on this dependency tree. Install with `npm ci`; change dependencies with `npx npm@12 install`.

The dev server proxies `/api`, `/static`, `/health`, and WebSockets to the local backend (`frontend/vite.config.mts`). The backend writes `backend/.backend-port` at startup and Vite follows that file; only when it is missing does Vite fall back to 8010. **Starting the backend first is the safest order.**

### 1.8 Reading health checks

| Endpoint | Semantics | When to use |
|----------|-----------|-------------|
| `GET /livez` | **Liveness**: always 200 while the process is alive, `{"status":"alive"}` | liveness probe |
| `GET /readyz` | **Readiness**: DB `SELECT 1` + ES cluster health + knowledge-index reconciliation; any failure → **503** | container healthcheck, traffic admission |
| `GET /health` | **Diagnostic view, always 200**, with `status` / `ready` / `version` / `services` / `uptime_seconds` | human troubleshooting; not an orchestration probe |
| `GET /metrics` | Prometheus text metrics | scraping; requires `Authorization: Bearer <token>` when `METRICS_TOKEN` is set |

Exact readiness rules (`_is_ready` in `backend/app/main.py`):

- **Included**: `database == healthy`, `elasticsearch == healthy`, `knowledge_base != unhealthy`.
- **Excluded**: Redis, MinIO, Kafka — their failures only make `/health` report `degraded`; `/readyz` stays 200.
- ES `disabled` or `degraded` both mean not ready; knowledge base `degraded` (coverage 95%-100% or a transient timeout) does not drain traffic, `unhealthy` does.
- `/health` overall `status`: all healthy → `healthy`; any `unhealthy` → `unhealthy`; otherwise `degraded`.

The backend healthcheck in `backend/docker-compose.yml` uses `/readyz` precisely because `/health` always returns 200 (issue #223).

```bash
curl -s http://localhost:8000/livez
curl -s http://localhost:8000/readyz
curl -s http://localhost:8000/health | python -m json.tool
```

**Reading a `/readyz` 503**: the body names the failing checks. Common causes are MySQL not running, ES still starting (~30 s on first boot), or documents marked `INDEXED` with no chunks in ES (reconciliation failure). Use `reconcile_knowledge_index.py` (section 4.1) for the third case.

### 1.9 First login

**Demo account (off by default)**: `ENABLE_DEMO_ACCOUNT` defaults to `false`. When explicitly set to `true`, startup creates a regular `guest` user (`guest@example.com`, not a superuser). The password is generated randomly **on first creation** and printed once at `WARNING` level; it is not reset on later startups (`backend/app/core/ensure_demo_user.py`, `deploy/README.md`). First creation also creates/reuses an organization named `Default` and backfills `guest`'s organization.

> `DEMO_PASSWORD` is read directly from `os.environ`; when running on the host, a `.env` file does **not** inject it. Export it (`export DEMO_PASSWORD=...`) or inject it via `.env.docker` / Compose. Empty values and `123456` are ignored in favor of a random password. The same `os.getenv` behavior applies to `ENABLE_TRACING` / `OTLP_ENDPOINT`, Feishu credentials, and `agent_api` settings (see `backend/.env.example`).

**Admin account**: from the backend directory:

```bash
cd backend
ADMIN_PASSWORD='<at least 8 chars>' python reset_admin.py     # PowerShell: $env:ADMIN_PASSWORD='...'; python reset_admin.py
```

`reset_admin.py` creates `admin` (`admin@example.com`) when absent, or resets the password when present; in both cases it sets `is_active=True`, `role="user"`, `is_superuser=True`. Platform administration is granted only by explicit `is_superuser` (ADR-001 D3).

> Do not use `backend/seed_user.py` to bootstrap any real environment — it contains a hardcoded weak password and is an early-development artifact.

**Self-service registration**: `POST /api/v1/auth/register` (JSON: `username` / `email` / `password`, optional `full_name`). Registration cannot choose an organization; the server **automatically creates a personal organization** named `{username} 的空间` (with duplicate-name fallbacks) and makes it the current organization. The user is never merged into an existing organization (`backend/app/api/v1/endpoints/auth.py`).

---

## 2. Configuration

### 2.1 Two env files, two jobs

| File | Read by | Purpose |
|------|---------|---------|
| `backend/.env` | backend / worker / ops scripts on the host | local development |
| `backend/.env.docker` | `docker compose --env-file .env.docker` (interpolation) + container `env_file` | Docker deployment |
| `backend/.env.example` / `.env.docker.example` | humans | templates; no real secrets in the repo |

Two read paths coexist:

- **Pydantic Settings** (`app/core/config/`) reads `.env` / environment variables, case-sensitive, `extra="ignore"`;
- keys read via **`os.getenv`** (listed in the previous section) are not populated from a `.env` file for host runs — export them.

### 2.2 Required changes and secure defaults

| Variable | Default | Notes |
|----------|---------|-------|
| `SECRET_KEY` / `JWT_SECRET_KEY` | empty (startup fails) | generate your own; `SECRET_KEY` currently only enforces the non-empty startup contract, JWT uses `JWT_SECRET_KEY` (`backend/app/core/config/security.py`) |
| `DEEPSEEK_API_KEY` | empty | chat model credential (required) |
| `EMBEDDING_API_KEY` | empty | falls back to `DEEPSEEK_API_KEY` (local Ollama does not check keys) |
| `MYSQL_ROOT_PASSWORD` / `MYSQL_PASSWORD` | empty (compose fails) | database passwords |
| `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` | empty (compose fails) | object storage credentials; secret at least 8 chars |
| `GRAFANA_PASSWORD` | empty (compose fails) | monitoring admin password |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `1440` (24 h) | access token lifetime |
| `REFRESH_TOKEN_EXPIRE_MINUTES` | `10080` (7 d) | refresh token lifetime |
| `ENABLE_DEMO_ACCOUNT` | `false` | demo account |
| `ENABLE_WORKFLOW_CODE_NODES` | `false` | workflow code nodes (security risk) |
| `SANDBOX_MODE` | `auto` | `auto` / `docker` refuse execution when Docker is unavailable (fail-closed); `ast` explicitly selects the in-process AST sandbox |
| `ENABLE_PII_MASKING` | `true` | PII masking (context + streaming) |
| `EXPOSE_EXCEPTION_DETAIL` | `false` | do not expose exception details to clients |
| `ENABLE_RATE_LIMIT` | `true` | rate limiting; host template 10000/min, compose overrides to 300/min (Redis uses a sliding window; the in-memory fallback uses a fixed window) |
| `ALLOWED_ORIGINS` | localhost:3000/5173/5174/5175 etc. | add your frontend origin in production or browser CORS fails |

### 2.3 Models and embeddings: the consistency contract

Defaults (source of truth: `backend/app/core/config/ai.py`):

| Setting | Default | Notes |
|---------|---------|-------|
| `DEEPSEEK_API_URL` | `https://api.deepseek.com` | OpenAI-compatible |
| `DEEPSEEK_MODEL` | `deepseek-v4-flash` | chat model |
| `EMBEDDING_API_URL` | `http://localhost:11434/v1` | local Ollama |
| `EMBEDDING_MODEL` | `nomic-embed-text` | 768 dims |
| `VECTOR_DIMENSION` | `768` | must match the model output |
| `EMBEDDING_STARTUP_PROBE` | `true` | one real embedding call at startup |
| `EMBEDDING_SEND_DIMENSIONS` | `false` | do not send `dimensions`: server-side truncation would mask a stale dimension setting |

**Hardware-tier auto-selection**: if `EMBEDDING_MODEL` / `VECTOR_DIMENSION` are set **nowhere**, hardware tiers apply: CPU `bge-m3` / 1024, 8 GB GPU `Qwen3-Embedding-4B` / 2048, 16 GB GPU `Qwen3-Embedding-8B` / 4096 (`README.md`; implementation `ai.py::_auto_tier_defaults`). Explicit configuration wins. **The templates pin `nomic-embed-text` / 768, so copying them avoids auto-tiering.**

To change embedding providers:

1. change `EMBEDDING_API_URL`, `EMBEDDING_MODEL`, and `VECTOR_DIMENSION` together;
2. rebuild the knowledge index (old vectors are unusable once the dimension changes);
3. restart the backend and confirm there is no dimension warning in the startup log.

### 2.4 Reranking

| Variable | Default | Notes |
|----------|---------|-------|
| `RAG_ENABLE_RERANKER` | `true` | master switch |
| `RERANK_USE_LOCAL` | `true` | local cross-encoder (`BAAI/bge-reranker-base`), no API key |
| `RERANK_MODEL` / `RERANK_API_KEY` / `RERANK_API_URL` | empty | cloud rerank; also set `RERANK_USE_LOCAL=false` |
| `RAG_RERANK_TOP_N` | `20` | candidates to rerank |
| `RAG_RERANK_TIMEOUT_SECONDS` | `8.0` | timeout triggers degradation (see 3.2) |
| `RAG_QUERY_REWRITE_TIMEOUT_SECONDS` | `1.2` | query-rewrite read budget |
| `LLM_CONNECT_TIMEOUT_SECONDS` | `1.0` | connection-level fast fail: separate short connect-phase timeout (read phase unaffected; raise it on high-latency links, which proportionally raises unreachable exposure time) |
| `LLM_TRANSIENT_MAX_RETRIES` | `1` | retries for transient faults (408/409/429/5xx, post-connect transport interruptions); connection-level unreachable and read timeouts get none |
| `LLM_UNREACHABLE_COOLDOWN_SECONDS` | `5.0` | unreachable negative-cache window (per LLM endpoint, cleared on any success); `0` disables |

### 2.5 Storage and infrastructure

| Variable | Default (template/code) | Notes |
|----------|-------------------------|-------|
| `DATABASE_URL` | `mysql+aiomysql://...@localhost:3306/docmind_db` (host template; use 3308 with Docker infrastructure) | MySQL |
| `DATABASE_POOL_SIZE` / `DATABASE_MAX_OVERFLOW` | `20` / `10` (compose: pool 20 / max_overflow 30) | connection pool |
| `REDIS_HOST` / `REDIS_PORT` | template `localhost` / `6379`; code default port `6390` (matches the Docker mapping) | cache, rate limit, token blacklist |
| `REDIS_PASSWORD` | empty | optional; when enabled, set it on both the server and app side (see compose comments) |
| `ELASTICSEARCH_HOSTS` | `["http://localhost:9200"]` | ES 8 |
| `ELASTICSEARCH_INDEX_NAME` | `docmind_knowledge` (template value; the old code default is kept only for legacy-data compatibility) | knowledge index |
| `MINIO_ENDPOINT` / `MINIO_BUCKET_NAME` | `localhost:9000` / `docmind` | object storage; the bucket is created lazily on **first read/write** |
| `KAFKA_BOOTSTRAP_SERVERS` | host `localhost:9092` | inside containers: backend uses `docmind-kafka:9092`, worker uses `docmind-kafka:29092` |
| `KAFKA_FILE_PROCESSING_TOPIC` | `file-processing` | document processing topic |
| `MAX_FILE_SIZE` | `104857600` (100 MB) | per-file limit |
| `USER_STORAGE_LIMIT_BYTES` | `10737418240` (10 GiB) | per-user storage quota (**display/statistics only; not enforced on upload**) |

### 2.6 Observability and logging

| Variable | Default | Notes |
|----------|---------|-------|
| `PROMETHEUS_ENABLED` | `false` (code) / `true` (compose) | enables RAG/Agent business metrics; single-process safe, configure multiprocess before multi-worker |
| `METRICS_TOKEN` | empty | when non-empty, `/metrics` requires Bearer |
| `ENABLE_MONITORING` | `true` | only controls the background snapshot task, not the `/metrics` endpoint |
| `LOG_LEVEL` / `LOG_JSON` | `INFO` / `true` (code; template says `false`) | log level and JSON format |
| `LOG_FILE` | `logs/docmind.log` | log file |
| `LLM_CALL_LOG_ENABLED` / `LLM_CALL_LOG_FILE` / `LLM_CALL_LOG_PER_RUN` | `true` / `logs/llm_calls.jsonl` / `false` | per-LLM-call JSONL (issue #510): one metadata line per call (`task_id`/`request_id`/model/tokens/latency/prompt_hash), **never prompt/completion bodies**; `PER_RUN=true` splits by run |
| `ENABLE_TRACING` / `OTLP_ENDPOINT` | `false` / empty | OpenTelemetry (read via `os.getenv`) |
| `LANGFUSE_ENABLED` / `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` | `true` / empty / empty / `https://cloud.langfuse.com` | LLM tracing (optional; inactive without keys. Marked as a deletion candidate, kept until approved) |
| `ENABLE_SCHEDULER` | `true` | in-process scheduler; **single-instance semantics**, use external scheduling for multi-worker |

---

## 3. Core concepts

### 3.1 PER Agent: plan → execute → reflect

DocMind's agent is a staged loop (`backend/app/agent/`), not a free-running LLM loop:

```
User instruction
  → Planner    decompose into DAG / Serial steps, annotate dependencies and tool hints
  → Executor   topological execution; independent steps may run in parallel;
               up to 3 retries per step (exponential backoff 0.5s × 2^n)
  → Reflector  score goal achievement (0-100%), decide pass / retry / replan
  → RunReport  summarize what happened, why, and the outcome
```

- Invalid planner JSON falls back to a single-step plan instead of failing the whole run.
- Reflection outcomes include `pass` / `retry` / `replan` plus `unavailable` / `unparsed` — the latter two mean "evaluation absent", neither a pass nor a failure (`backend/app/agent/events.py`).
- Memory has four layers: short-term (session), long-term (cross-session), working (step intermediates), reflective (lessons); long-term/reflective can persist to Redis, short-term/working stay in-process (`README.md`, "Known limitations").
- Actual defaults come from `backend/app/agent/config.py`: `max_plan_steps=10`, `max_iterations=20`, `max_tool_calls_per_turn=5`, `max_retries_per_step=3`, step timeout 120 s, `max_context_tokens=8000`; `approval_timeout_seconds=0` (wait forever), `plan_approval_mode="never"`.
- `execute_python` / `execute_sql` / `mcp_call` are in `DEFAULT_DISABLED_TOOLS`; the server always unions them into `disabled_tools` even if the user config omits them.
- Reflection `retry` re-runs the step in place; `replan` re-enters planning at most once per run (`MAX_REPLANS_PER_RUN=1`). Reviewer `revise` / `reject` is recorded and displayed as advice only — it does not block the final answer.
- The agent stream has **24 SSE event types** (`docs/facts.json` → `sse_event_types`, derived from the `EventType` literal in `backend/app/agent/events.py`); the frontend renders plan trees, thinking streams, tool cards, and approval cards from them.

> The "Configuration" block in `AGENT_ARCHITECTURE.md` shows older defaults (e.g. `max_iterations=10`, `max_plan_steps=8`). When it conflicts with code, `backend/app/agent/config.py` wins.

### 3.2 RAG pipeline and degradation semantics

An actual RAG request (`backend/app/rag/`):

```
Query
  → intent / complexity classification
  → query rewrite (1.2 s timeout) / HyDE (1.5 s timeout, optional)
  → HybridRetriever: keyword (BM25-style) + vector (cosineSimilarity) + RRF fusion (k=60)
  → MMR dedup (on by default; skipped for clause-lookup queries)
  → Reranker: local cross-encoder → cloud API → LLM rerank → no rerank
  → context compression: keyword sentence extraction (rule-based, no LLM call)
  → exact cache (on by default, TTL 600 s); semantic cache off by default (3.6)
  → streaming LLM generation (PII masking)
```

**Three-state retrieval semantics** (`backend/app/rag/errors.py`):

| State | Behavior |
|-------|----------|
| Empty but healthy | returns empty results; `/knowledge/search` adds `index_status` and `index_doc_count` so you can tell whether the index has content |
| Service failure | `RetrievalUnavailableError(kind="failed")` → REST **502** `error_code=RETRIEVAL_UNAVAILABLE`, `detail.kind` explains why |
| Timeout | `RetrievalUnavailableError(kind="timeout")` → also 502; never disguised as "no relevant documents" |

- In strict chat mode, retrieval failure emits an SSE `error`; non-strict mode injects a "retrieval unavailable" notice so the model answers honestly instead of inventing.
- **Rerank fails open**: unavailable local model, API failure, or LLM rerank failure all degrade to the fused order; the request does not fail. Degraded results are not cached (`reranker.py`, `pipeline.py`).
- **A keyword-arm failure aborts that hybrid attempt** (even if the vector arm succeeded); the pipeline retries per sub-query (`RAG_RETRIEVAL_MAX_RETRIES` default 2, i.e. up to 3 attempts).
- **GraphRAG is not in the RAG retrieval path**: graph extraction runs non-blocking after indexing and failures only warn. Retrieval is still HybridRetriever (`README.md`, `backend/app/worker/doc_processor.py`). Graphs are visible in the knowledge page or via `/api/v1/knowledge/graph*`.

### 3.3 Organizations and multi-tenancy: organization = knowledge base

ADR-001 froze this semantic (`docs/decisions/ADR-001-路线冻结.md`, D5):

- **There is no `knowledge_bases` table.** An organization is the knowledge-base singleton: documents, chunks, indices, and retrieval are all scoped by `organization_id`.
- A user belongs to an organization (`users.organization_id`); endpoints resolve it through `require_organization` and non-superusers with no organization fail closed (403). A superuser without an organization falls back to a real organization from the database. Clients cannot choose an organization in the request body.
- **Switching organizations** uses `POST /api/v1/users/me/organizations/{organization_id}/switch`: it writes `users.organization_id`, returns 403 for non-members, bypasses for superusers, and audits `switch_organization`. Later list/search calls resolve context from this field.
- **Admin semantics (D3)**: the implicit "`role=admin` means global admin" mapping is gone. Platform operations require explicit `is_superuser`; organization admins are determined by the org membership role (`OrgAdmin` in `user_organization_role_association`) and can only manage their own organization.
- `POST /api/v1/knowledge/search` requires `organization_id`; for non-superusers the server overrides the supplied value with the effective organization. Cross-organization data is invisible.
- Agent tasks are stricter: `AgentTask.organization_id` must exactly match the effective organization (including NULL), otherwise 404.
- Semantic caches, RAG caches, and graph partitions are all org-scoped (`README.md`, `backend/app/services/graph_rag_service.py`).
- Boundary: D5's naming cleanup is not fully complete — `/knowledge` list endpoints and the frontend `KnowledgeBase` type still use the old names. Treat "organization = knowledge base" as the real semantic and do not assume multiple knowledge bases per organization.

> Organization switching and conversation visibility depend on the conversation's `organization_id`; see 7.4.

### 3.4 Tools and HITL approval

**Tools**: every tool self-registers via `@register_tool(name=...)`. There are **39 built-in tools** (`docs/facts.json` → `registered_tools`; 14 in `core_tools.py`, 25 in `tools/`), plus dynamic MCP tools. Parameters use JSON Schema and are converted to OpenAI function-calling format; `tags` drive configuration filtering, and sensitive tools are disabled by default in `DEFAULT_DISABLED_TOOLS`.

**HITL (human-in-the-loop)** lets the agent pause before a risky next action:

| Setting | Values | Meaning |
|---------|--------|---------|
| `approval_mode` | `never` / `risky` / `always`, default `risky` | when to pause for **tool call** approval. With risky tools disabled by default, the default approval volume is zero; enabling risky tools activates it |
| `plan_approval_mode` | `never` (default) / `always` | whether to approve the **generated plan** before execution (API-only; not exposed in the UI) |
| `approval_timeout_seconds` | integer, default `0` | `0` waits forever; `>0` marks the task `FAILED(approval_timeout)` on expiry, never disguised as "denied" (API-only) |

- On pause, the stream emits `approval_required` (or `plan_approval_required`), then `done` carries `task_state=waiting_approval` (or `waiting_plan_approval`).
- Approval: `POST /api/v1/agent/tasks/{task_id}/approval` (`decision=approve|deny`, optional `modified_instruction` ≤2000 chars and `note` ≤500 chars). Plan approval: `POST /api/v1/agent/tasks/{task_id}/plan-approval` (`decision=approve|edit|reject`, with `edited_steps` for `edit`; the first `reject` triggers one replan, the second ends in terminal `rejected`).
- `approve` is sticky per **(tool_name, args) fingerprint** within the run; `deny` skips the call and feeds a structured `approval_denied` failure to the reflector.
- `approve + modified_instruction` resumes with human guidance injected into later step prompts.
- Resuming may stop at the next approval point; concurrent decisions return 409 (`TASK_NOT_WAITING_APPROVAL` / `TASK_NOT_RESUMABLE` / `TASK_BUSY`).

**Hooks (extension points, off by default)**: an in-process event bus covers agent turns and tool execution (`agent/pre_step`, `tools/pre_execute`, ...). See `backend/config/hooks.yaml`, the "Hooks extension points" section in README_EN, and `docs/decisions/ADR-004-hooks.md` for semantics and boundaries. `ask` on `tools/pre_execute` reuses the same HITL approval gate — hooks cannot bypass approval; `HOOKS_ENABLED=false` means no file read and no behavior change.

### 3.5 Three prompt layers: system / organization / personal

Prompt templates (`prompt_templates`) use `scope` for three visibility layers (`backend/app/api/v1/endpoints/prompts.py`, migration `011_organization_settings.py`):

| Layer | Storage predicate | Visible to | Maintained by |
|-------|-------------------|------------|---------------|
| **system** | `is_system=true AND organization_id IS NULL` | all organizations, read-only | platform |
| **organization** | `organization_id = current org AND scope='organization'` | org members | org admins |
| **personal** | `organization_id = current org AND scope='personal' AND creator_id = current user` | creator only | the user |

- ADR-001 states one active template per `(org, category, scope)` maintained by admins, but **the code does not enforce uniqueness**: multiple active templates can coexist, and RAG takes the one with the latest `updated_at` (`backend/app/services/chat_service.py`). Deactivate old versions before enabling a new one. Templates keep version history (`PromptTemplateVersion`), and `/api/v1/prompts` offers CRUD and version rollback.
- Write rules: system templates are read-only; personal templates are writable only by the creator; **activating** an organization template requires an org admin (non-admin org templates are created inactive); clients cannot forge the `system` layer; activation is audited.
- Pre-011 non-system templates without an organization are visible only to superusers — they are never treated as system templates by every tenant.
- Rollback caveat: dropping the `scope` column makes personal templates organization-visible; disable personal templates before rolling back (migration header).
- These three scopes are **template-management** concepts. The agent does not stack three prompt layers at runtime — it uses a built-in system prompt plus optional `system_prompt_override`; templates reach the agent only through the `list_prompt_templates` / `get_prompt_template` tools.

### 3.6 Why semantic caching is off by default

The semantic cache namespaces (`SEMANTIC_CACHE_RETRIEVAL_ENABLED` / `SEMANTIC_CACHE_ANSWER_ENABLED`) default to `false` (issue #195):

- A 0.92 cosine threshold is only a **coarse filter**. Adjacent clauses ("article 7" vs "article 13") or the same sentence pattern with a different entity ("Beijing" vs "Shanghai") can exceed it, causing sources or full answers to leak **across questions**.
- Therefore both retrieval-sources and answer semantic caches default off. Even when explicitly enabled, a hit must also pass clause/number/entity equivalence (`rag/cache.py::queries_equivalent`); the threshold is not the only criterion.
- **The exact cache is unaffected**: `RAG_ENABLE_CACHE=true` by default, keyed by `query + org + top_k`, TTL 600 s — enough for repeated questions.
- Evidence: comments in `backend/app/core/config/ai.py`, `backend/app/rag/cache.py`, `backend/docs/retrieval_routing.md`.

---

## 4. Hands-on features

### 4.1 Upload a document and make it retrievable

**Supported formats** (source of truth: `supported_extensions` in `backend/app/services/document_parser.py`):

| Type | Extensions | Parsing |
|------|------------|---------|
| PDF | `.pdf` | `pdfplumber`; pages with <50 extracted chars attempt `pytesseract` + `pdf2image` OCR |
| Word | `.docx` | `python-docx` primary, `docx2txt` fallback (legacy `.doc` unsupported) |
| Excel | `.xlsx` | `openpyxl`. `.xls` is in the extension map, but `openpyxl` cannot read legacy `.xls` — it fails in practice |
| Text | `.txt` / `.md` / `.csv` | direct read |
| Images | `.png` / `.jpg` / `.jpeg` / `.webp` / `.bmp` / `.gif` | RapidOCR (ONNX); GIF uses the first frame only |

Notes:

- `.doc` / `.ppt` / `.pptx` are **not supported**; uploads fail at parse time with "unsupported file format". The upload picker's `accept` also omits `.gif` (the backend parser supports it; upload via API if needed).
- Image OCR depends on `rapidocr-onnxruntime` (in `requirements.txt`). **Scanned PDFs** need `pytesseract` / `pdf2image` plus system Tesseract/Poppler, which are not default dependencies — scanned PDFs fail without them.
- Limits: `MAX_FILE_SIZE=100 MB` per file; `USER_STORAGE_LIMIT_BYTES=10 GiB` is currently used for statistics only and is not enforced on upload.
- Chunking is **semantic** (`semantic_chunker.py`): breakpoints at the 90th percentile of embedding distance, `chunk_max_chars=1200`; short texts (<500 chars) become one chunk; when the text is long enough and summary generation succeeds, each chunk gets a 50-100 char document-summary prefix (contextual retrieval).
- The knowledge page polls every 5 s while documents are pending; failed rows offer "re-parse" and the toolbar offers "rebuild latest failed document", both calling `POST /knowledge/rebuild/{document_id}`.

**What happens after upload**:

```
POST /api/v1/documents/upload (multipart: file / title / description / tags)
  → file stored in MinIO ({organization_id}/{md5}.{ext})
  → documents row created: status=PENDING
  → knowledge_processing_jobs row: QUEUED / trigger_type=upload
  → Kafka file-processing topic → worker parses → chunks → embeds → indexes into ES
  → status: PARSING → INDEXED (FAILED + parse_error on failure)
```

Status enum: `PENDING` / `UPLOADED` / `PARSING` / `PARSED` / `INDEXED` / `FAILED` (`backend/app/models/document.py`). The main path uses `PARSING → INDEXED/FAILED`; `PARSED` exists only on the legacy parser path.

**When Kafka is unavailable**: the upload endpoint detects an uninitialized producer or a send failure and runs the same `DocumentProcessor` in-process via `asyncio.create_task` (`endpoints/documents.py`), so local development does not stay pending. If even in-process scheduling fails, the document and job are marked `FAILED` and the response is `success:false` (HTTP stays 201 because the file was accepted).

**Verify retrievability**:

1. search in the knowledge page, or call `POST /api/v1/knowledge/search`;
2. check the document is `INDEXED`;
3. check the `knowledge_base` item in `/readyz` is `healthy`;
4. if "INDEXED but not searchable", reconcile:

```bash
cd backend
python scripts/reconcile_knowledge_index.py               # read-only reconciliation
python scripts/reconcile_knowledge_index.py --reindex     # fix missing / partial
python scripts/reconcile_knowledge_index.py --org 3       # limit to one organization
python scripts/reconcile_knowledge_index.py --reindex --limit 10
```

Reconciliation compares `documents.status=INDEXED` + `chunk_count` against ES chunk counts aggregated per `document_id`, classifying `missing` / `partial` / `extra` / `ok`. `extra` chunks and orphan ES documents are reported only, **never auto-deleted**. `--reindex` creates `reprocess` jobs for `missing`/`partial` and runs the official processing chain — it never bypasses parsing/validation to write ES directly. Exit codes: `0` consistent, `1` unresolved items remain, `2` fatal.

**Single-document rebuild**: `POST /api/v1/knowledge/rebuild/{document_id}` (deletes that document's ES chunks, then reprocesses via Kafka / in-process).

**Graph**: after successful indexing, a knowledge graph is extracted non-blocking and stored per organization in Redis (30-day TTL). It does not participate in retrieval ranking, and failures do not mark the document FAILED. Backfill historical documents with `backend/scripts/backfill_graph.py`; the graph page truncates to `max_nodes` (backend 5-200, default 50).

### 4.2 Chat and deep analysis

The chat page (`/chat`) has two modes, selected by the "Quick Q&A / Deep analysis" radio in the input area (`frontend/src/views/chat/components/ChatInput.vue`):

| Mode | Actual path | Good for |
|------|-------------|----------|
| **Quick Q&A** | RAG pipeline (`POST /api/v1/chat/stream`) | grounded knowledge-base answers with citations, low latency |
| **Deep analysis (Agent)** | same entry with `useAgent=true`, reusing the `/agent` PER capability | multi-step planning, tool calls, reflection; **SSE only** (the frontend forces `useSSE=true`) |

The request fields are `content` / `conversationId` / `fileIds` / `useAgent` / `debug` plus `payload` (`strictMode` / `privacyMode`) — there is no `mode` or `deep_analysis` field; "deep analysis" is simply `useAgent=true` at the protocol level.

Practical notes:

- **Ask with files**: `fileIds` references uploaded files. `POST /api/v1/chat/parse-upload` parses without indexing — useful for "read this one document" rather than adding it to the knowledge base.
- **Strict / privacy mode**: passed in the request `payload` (`strictMode` / `privacyMode`). In strict mode, retrieval failure emits an SSE `error` instead of letting the model improvise; privacy mode works with end-to-end PII masking.
- **Stop generation**: the frontend aborts the SSE stream with an AbortController; the server finalizes on disconnect.
- **History**: `GET /api/v1/chat/conversations` supports `page` / `page_size` / `search` (title LIKE), newest first; conversations are visible by "owner + current organization" (see 7.4).
- **Deep analysis shows its work**: plan tree, thinking stream, tool-call cards, and reflection are driven by the 24 agent SSE event types; quick Q&A only emits `chunk` / `message` / `error` / `debug`.
- **When approval is needed**: deep analysis inherits HITL. `approval_mode` defaults to `risky`, but risky tools are disabled by default, so the default configuration triggers no approvals. Once you enable `execute_python` / `execute_sql` / `mcp_call`, risky calls pause for approval. For complex tasks prefer the Agent workbench (4.3), which has the full approval and replay UI.
- **Boundary**: the standalone search page's file-type filter is **display-only** — `file_types` is not part of the backend `KnowledgeSearchRequest`, so the filter has no effect. Control retrieval with the query and `search_mode`.

### 4.3 Agent workbench: approvals, resume, replay

The Agent workbench (`/agent`) is the "intervenable agent" UI: plan tree/session on the left, chat and tool cards in the middle, configuration on the right (`AGENT_ARCHITECTURE.md`; components in `frontend/src/components/agent/`).

**Configuration**: model and temperature, Planning / Reflection / Memory / Thinking toggles, personality, approval mode, tool toggles, system-prompt override (`AgentConfigPanel.vue`); `GET/PUT /api/v1/agent/config`, persisted per user. Agent sessions: `GET/POST /api/v1/agent/sessions`, `GET/DELETE /api/v1/agent/sessions/{id}`; tools: `GET /api/v1/agent/tools` (parameter schemas, tags, `disabled_by_default`, total).

**Server-side commands** (`GET /api/v1/commands`, `POST /api/v1/commands/execute`): type `/help` (command catalog), `/status` (session and recent tasks), `/export [markdown|json]` (export the current session), `/tools` (tools and default-disabled state). Commands run before the model, write no user message, and create no model turn; an unknown command returns an explicit error instead of being sent to the model as a prompt. Set `COMMANDS_ENABLED=false` to roll back.

**Approval and resume** (HITL; semantics in 3.4):

1. On a risky point, the stream emits `approval_required` / `plan_approval_required`; the task enters `waiting_approval` / `waiting_plan_approval`.
2. Approve / deny / modify in the cards (`ApprovalCard.vue` / `PlanApprovalCard.vue`), calling `POST /api/v1/agent/tasks/{task_id}/approval` and `.../plan-approval`.
3. The server resumes from the checkpoint with the same SSE contract (there is no separate "resumed" event; `done` carries `decision` / `task_state`).
4. After refresh/reconnect: `GET /api/v1/agent/tasks/{task_id}` returns pending approval details and expiry status; `GET /api/v1/agent/tasks` lists tasks with `has_replay`.
5. Cooperative cancel: `POST /api/v1/agent/tasks/{task_id}/cancel` — returns 200 with terminal `cancelled` while waiting, 202 `cancel_requested` while active (converges at a checkpoint), 409 for terminal tasks, 503 when Redis is unavailable. Cancel is not a hard kill; the current tool/LLM call finishes first.
6. Steering: `POST /api/v1/agent/tasks/{task_id}/steer` (message 1-2000 chars, queue cap 5). Messages are injected into later step prompts at step boundaries (SSE `steer_applied`); queue full 429, wrong state 409, channel unavailable 503.

**Replay**: `GET /api/v1/agent/tasks/{task_id}/replay?format=json|text` returns the execution snapshot (steps, tool calls, results, decisions, failures, token/duration timeline); `format=text` is CLI-style. Snapshots are normally written to Redis (7-day TTL); the local file `benchmark/replay/{task_id}.json` is a fallback **only when the Redis write fails**, and reads try Redis first, then the local file — with a healthy Redis, nothing usually survives TTL expiry locally. `has_replay` on `GET /agent/tasks/{id}` and the task list indicates availability. Run offline commands from `backend/`: `python -m benchmark.replay <task_id>`, `--diff <a> <b>`, `--list`. In the UI, use the replay button in the run history and `ReplayTimeline.vue`; deep link `?id=<task_id>`.

**Boundaries**:

- Pause/cancel is not "completion" — no partial answer is persisted; only truly finished runs write conversation messages.
- A denied call becomes a structured failure for the reflector; the run may continue with other steps instead of failing entirely.
- Approval timeout sets `FAILED(approval_timeout)`, never "denied".

### 4.4 Workflows

The workflow page (`/workflow`) is a visual DAG editor (Vue Flow). Common node types (`frontend/src/views/workflow/composables/useWorkflowEditor.ts`):

| Category | Nodes |
|----------|-------|
| LLM | OpenAI GPT, DeepSeek, Qwen |
| Tool | knowledge retrieval (`tool_search`), TTS (`tool_tts`) |
| I/O | input, output |
| Logic | condition, smart router |
| Data | memory, code execution, API call, transform |

> The backend executor supports exactly these types (`backend/app/services/workflow_engine.py`). When node definitions fail to load, the frontend shows a generic `llm` fallback node, but the backend has no `llm` executor — running it raises `Unknown node type: llm`. Use the three named LLM nodes.

API prefix `/api/v1/workflows`: workflow CRUD, execution, debugging, node definitions. Workflows are org-scoped; ownership is enforced server-side.

**Code nodes** (security boundary):

- Disabled by default: `ENABLE_WORKFLOW_CODE_NODES=false`. If not enabled, execution fails with `代码执行节点未启用，请设置 ENABLE_WORKFLOW_CODE_NODES=true` (`backend/app/services/workflow_engine.py`).
- Even when enabled, workflow code nodes are **Docker-only**: `SANDBOX_MODE=ast` does not apply; when Docker is unavailable they raise `代码执行节点需要 Docker 沙箱，当前未启用` (fail-closed).
- Full Docker does not mount `/var/run/docker.sock` into the backend container and the image has no Docker CLI, so **containerized code nodes hit the "Docker unavailable" path**. Mount the socket explicitly or run the backend on the host (see 7.6).

**Scheduled tasks**: `/api/v1/scheduled-tasks` provides CRUD, pause/resume, and manual trigger. `ENABLE_SCHEDULER` controls an **in-process asyncio loop (single-instance semantics)**; multi-worker deployments need external scheduling.

### 4.5 Organization settings and switching

**Organization management** (`/organizations`, admins): org CRUD, membership, roles. Platform operations require `is_superuser`; org admins manage only their own organization (ADR-001 D3).

**Organization settings** (same `/api/v1/organizations` prefix; `backend/app/api/v1/endpoints/organization_settings.py`):

| Setting | Meaning |
|---------|---------|
| `default_provider` / `default_model` | organization default provider and model |
| `retrieval_top_k` | retrieval count (1-50) |
| `allow_privacy_mode` | whether members may enable privacy mode |
| `monthly_token_quota` | monthly token quota (null = unlimited) |
| `tool_whitelist` | tool allowlist |

- `GET` (effective values) is readable by org members and returns each item's `value` / `source` (`organization` override or platform `default`) / `requires_restart`, plus `usage.month_tokens` and quota.
- `PUT` and audit reads require an **org admin** or platform superuser; every write is audited in `user_activity_logs` (`action=update_organization_settings`).
- Updates are partial: omitted fields stay unchanged, explicit `null` clears the override and falls back to the platform default.
- **Honest boundary**: `default_model`, `retrieval_top_k`, and `allow_privacy_mode` are wired into runtime; `default_provider`, `monthly_token_quota`, and `tool_whitelist` are stored and displayed but **not enforced yet** (`backend/app/services/organization_settings_service.py`).

**Switching organizations**: desktop uses the header `OrgSwitcher` (static text when the user has a single organization); mobile uses the user menu. Knowledge base (org = KB) and conversation lists follow the new organization, and the current page remounts and refetches. Not seeing old conversations is expected — see 7.4. Upload and search under the organization that owns the data.

### 4.6 Monitoring

The monitoring page (`/monitoring`) is **platform-admin only (explicit `is_superuser`)** — the route requires `requiresPlatformAdmin`; backend endpoint auth is not uniform (some only require login), so check Swagger for specifics. It shows notification-service status, CPU/memory, application metrics (active connections, requests, errors, average response time), knowledge-base metrics (docs/chunks/index size/searches), alert counts and recent alerts, trend charts, and audit logs; time ranges 1h/6h/12h/24h/7d, auto-refresh every 30 s. It maps to `/api/v1/monitoring` endpoints: dashboard, trends, system stats, alerts and alert rules, external Alertmanager webhook, route performance, notification status, system logs, performance metrics, LLM resource consumption, organization summary, storage analysis, `health`, `llm-status`, and RAG quality evaluation (single/batch). The page itself does **not** embed Prometheus/Grafana links.

Metrics stack:

- **Prometheus** scrapes `/metrics` (HTTP requests/errors/latency/status codes, WebSocket connections, RAG/Agent metrics). `PROMETHEUS_ENABLED=true` enables business metrics; `METRICS_TOKEN` adds Bearer auth.
- **Grafana** at `http://localhost:3001` in full Docker; dashboards come from `backend/config/grafana/`; the admin password is `GRAFANA_PASSWORD`.
- **Alerting**: thresholds in `.env` (`ALERT_ERROR_RATE_PERCENT`, `ALERT_P95_MS`, `ALERT_ACTIVE_CONNECTIONS`, etc.); `ALERT_WEBHOOK_TOKEN` must match the `deploy/monitoring` side.
- **Probes**: `/livez` and `/readyz` semantics are in 1.8; the worker uses a heartbeat file rather than an HTTP probe.
- **Tracing (optional)**: Langfuse records LLM calls; OpenTelemetry needs `ENABLE_TRACING=true` and `OTLP_ENDPOINT` (read via `os.getenv`).

---

## 5. API and integration

### 5.1 Authentication

All business endpoints share the `/api/v1` prefix (mounted in `backend/app/main.py`) and use **JWT Bearer** auth:

| Action | Endpoint | Request |
|--------|----------|---------|
| Form login | `POST /api/v1/auth/login` | `application/x-www-form-urlencoded`: `username`, `password` |
| JSON login | `POST /api/v1/auth/login/json` | JSON `{ "username": "...", "password": "..." }` |
| Register | `POST /api/v1/auth/register` | JSON `username`, `email`, `password`, optional `full_name` |
| Refresh | `POST /api/v1/auth/refresh` | JSON `{ "refresh_token": "..." }` |
| Logout | `POST /api/v1/auth/logout` | `Authorization: Bearer <access>`, optional `{ "refresh_token": "..." }` |

Successful login returns an envelope:

```json
{
  "success": true,
  "message": "...",
  "data": {
    "access_token": "...",
    "token_type": "bearer",
    "expires_in": 86400,
    "refresh_token": "...",
    "user_info": { "...": "..." }
  }
}
```

`expires_in` is in **seconds** (`ACCESS_TOKEN_EXPIRE_MINUTES * 60`), not minutes.

- Send `Authorization: Bearer <access_token>` on HTTP requests.
- **Refresh rotation**: refresh blacklists the old refresh token and issues a new access + refresh pair; the old access token is not revoked immediately (it expires naturally). If Redis is unavailable, logout/blacklist fails closed with 503.
- WebSockets prefer `Sec-WebSocket-Protocol: auth.<token>`, with legacy `?token=<token>` supported. WS endpoints: `/api/v1/chat/ws`, `/api/v1/notifications/ws`.
- The main API has **no** API-key auth; `X-API-Key` exists only in the dev-only standalone agent test app `backend/app/agent_api.py` (port 8010, not production).

### 5.2 Endpoint modules

There are **28 endpoint modules** (`docs/facts.json` → `endpoint_modules`, derived from the `ROUTER_SPECS` table in `backend/app/api/v1/router.py`):

| Module | Prefix | Purpose |
|--------|--------|---------|
| auth | `/api/v1/auth` | login, register, refresh, logout, password change |
| users | `/api/v1/users` | user CRUD, profile, sessions, audit logs |
| api_keys | `/api/v1/api-keys` | `dm_` API key create/list/revoke (bcrypt, scopes; issue #480) |
| files | `/api/v1/files` | upload/avatar/list/delete (MinIO); document download is `/api/v1/documents/{id}/download` |
| documents | `/api/v1/documents` | upload, parse, embed, status |
| chat | `/api/v1/chat` | session CRUD, SSE Q&A |
| knowledge | `/api/v1/knowledge` | retrieval, stats, rebuild, graph |
| organizations | `/api/v1/organizations` | org CRUD, members, roles |
| organization_settings | `/api/v1/organizations` | org-level settings (shared prefix) |
| monitoring | `/api/v1/monitoring` | metrics, health, alerts, RAG quality |
| notifications | `/api/v1/notifications` | notifications and WebSocket |
| prompts | `/api/v1/prompts` | templates and versions; three scopes |
| token_usage | `/api/v1/token-usage` | token usage and reports |
| manuals | `/api/v1/manuals` | operations manuals |
| workflow | `/api/v1/workflows` | workflow CRUD, execution, debugging |
| llm_config | `/api/v1/llm-config` | deprecated entry (endpoints marked deprecated) |
| curated_skills | `/api/v1/curated-skills` | skill library |
| memory | `/api/v1/memory` | agent memory (short/long/working/reflective) |
| agent | `/api/v1/agent` | PER agent SSE, tools, sessions, config |
| commands | `/api/v1/commands` | server-side command catalog and model-free execution (`/help`, `/status`, `/export`, `/tools`) |
| mcp | `/api/v1/mcp` | MCP server status (admin read-only) |
| rpc | `/api/v1/rpc` | HTTP JSON-RPC 2.0 (API key + `rpc` scope; issue #480) |
| scheduled_tasks | `/api/v1/scheduled-tasks` | scheduled task CRUD / pause / manual run |
| sessions | `/api/v1/sessions` | session fork / seed / lineage (issue #476) |
| terminal | `/api/v1/terminal` | container-backed persistent PTY, one-time WS tickets (issue #477) |
| webhooks | `/api/v1/webhooks` | inbound webhook rules, HMAC-verified delivery, outbox processing (issue #478) |
| demo | `/api/v1/demo` | demo data |
| user_settings | `/api/v1/user` | UI preferences |

Interactive docs: `http://localhost:8000/docs` (OpenAPI: `/api/v1/openapi.json`).

### 5.2.1 Session Fork API (issue #476)

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/v1/sessions/{id}/fork` | Fork a child session from a settled boundary |
| GET | `/api/v1/sessions/{id}/lineage` | Ancestor chain (root → parent) + direct children + lineage metadata |

`fork` body (all optional): `message_id` (boundary message; ASSISTANT = copy through
it, USER = fork before it; defaults to the last settled message), `title`,
`include_checkpoint` (deep-copy the latest settled checkpoint into a new PAUSED task
that can be resumed), `seed` (seed content written into the child's isolated memory
namespace), `reason`. Idempotency: `Idempotency-Key` header; sequential retries
return the same child session (concurrent duplicates are not guaranteed).

Errors: `404` (session/boundary message missing, foreign user or cross-org — no
ownership leaks), `409` (`FORK_BOUNDARY_INVALID` / `FORK_QUOTA_EXCEEDED` /
`FORK_CHECKPOINT_REQUIRED` / `FORK_CHECKPOINT_UNAVAILABLE`), `400` (seed too long).

Lineage fields: `parent_session_id` / `root_session_id` / `fork_point_message_id` /
`fork_reason` / `is_fork` / `is_seeded` (returned by session list/detail and
`lineage`). Forks may only land on settled turns (copied prefix ends with an
assistant message or is empty; intermediate assistant messages and open turns
return 409); copied messages get renumbered `seq` in the child; parent memory is
not copied by default.

### 5.3 Request examples

**Log in and extract a token**

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login/json \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"<your password>"}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['data']['access_token'])")
```

**Upload a document** (organization comes from the caller context; the client cannot set `organization_id`)

```bash
curl -X POST http://localhost:8000/api/v1/documents/upload \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@./manual.pdf" \
  -F "title=Product manual" \
  -F "description=Internal material" \
  -F 'tags=["manual","rag"]'
# 201 → {"success": true, "data": {"id": ..., "filename": ..., "status": ...}}
```

**Knowledge search** (`organization_id` is required)

```bash
curl -X POST http://localhost:8000/api/v1/knowledge/search \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"How do I configure backups?","organization_id":1,"top_k":5,"search_mode":"hybrid"}'
```

The response contains `results[]`, `total_count`, `search_mode`, `search_time`, `index_status`, `index_doc_count` (`backend/app/schemas/knowledge.py`).

**RAG chat (SSE)**

```bash
curl -N -X POST http://localhost:8000/api/v1/chat/stream \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"content":"What does this manual cover?","conversationId":null}'
```

- The RAG SSE wire format is `event: <type>` plus `data: <json>`; events include `chunk`, `message`, `error`, `debug`.
- With `useAgent=true`, the agent path emits `thinking`, `plan_start`, `plan_step`, `plan_complete`, `tool_call`, `tool_result`, `tool_error`, `reflection`, `execution_context`, `warning`, `message`, `error`.
- The **agent endpoint** (`POST /api/v1/agent/chat`) uses a different frame format: only `data: {"type": ..., "content": ...}`, with no `event:` line.

**Agent chat**

```bash
curl -N -X POST http://localhost:8000/api/v1/agent/chat \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"What documents are in the knowledge base?","enable_planning":true}'
```

The body also supports `session_id`, `enable_tools`, `enable_reflection`, `enable_memory`, `enable_thinking`, `model`, `temperature`, `disabled_tools`, `system_prompt_override`, `approval_mode`, `plan_approval_mode` (`endpoints/agent.py`).

**Health and metrics**

```bash
curl -s http://localhost:8000/readyz
curl -s -H "Authorization: Bearer $METRICS_TOKEN" http://localhost:8000/metrics
```

---

## 6. Operations

### 6.1 Database migrations (Alembic)

```bash
cd backend
source venv/bin/activate
alembic upgrade head                        # apply to latest
alembic revision --autogenerate -m "desc"   # create a migration (dev)
```

- Config: `backend/alembic.ini`; the runtime URL is overridden from `settings.DATABASE_URL` in `backend/alembic/env.py`.
- Current head: `011_organization_settings` (`backend/alembic/versions/011_organization_settings.py`).
- `init_db()` runs `create_all` **only on an empty database**; existing databases are not auto-upgraded. `USE_ALEMBIC=true` skips `create_all` entirely (`backend/app/core/database.py`).
- **Release order**: new code may read new tables/columns (e.g. `organization_settings` and `prompt_templates.scope` from 011). Upgrade existing deployments by running migrations **before** routing traffic to new instances; do not create personal prompt templates during the rolling window (old instances treat them as organization-visible).
- 011's `downgrade` is **lossy**: dropping `organization_settings` loses overrides; dropping `scope` makes personal templates organization-visible. Export/disable related data first (migration header).

### 6.2 Backup and rollback notes

The repository **has no built-in backup/restore scripts**; `deploy/README.md` lists "Set up database backups" as a production checklist item. Establish backups yourself:

| Data | Location | Suggestion |
|------|----------|------------|
| MySQL | Docker volume `mysql-data` | periodic `mysqldump --single-transaction` |
| MinIO objects | volume `minio-data` | MinIO versioning/replication or storage-side backup |
| Elasticsearch | volume `elasticsearch-data` | snapshot API; after an incident, rebuild from MySQL+MinIO with `reconcile_knowledge_index.py --reindex` |
| Prometheus / Grafana | `prometheus-data` / `grafana-data` | back up dashboards/history as needed |

Rollback notes:

- Code rollback is not data rollback. Migration 011's `downgrade` is lossy (see 6.1) — back up first.
- The per-org RBAC data migration has a SQL rollback script: `backend/scripts/migrations/w0_1_rbac_admin_scope_down.sql`, restoring precisely from the `w0_1_rbac_admin_backup` table (`backend/README.md`).
- For index problems, prefer `python scripts/reconcile_knowledge_index.py --reindex` over dropping the database (see 4.1).

### 6.3 Logs and request_id

- Logs go to `backend/logs/docmind.log` (`LOG_FILE`); `LOG_JSON` controls JSON formatting (code default `true`, `.env.example` template says `false`). JSON logs automatically carry `request_id` / `trace_id` / `user_id`.
- **`X-Request-ID` contract**: `RequestIDMiddleware` accepts an inbound `X-Request-ID` only if it fully matches `[A-Za-z0-9._-]{1,64}`, otherwise it generates a UUID; the response header echoes the value, and error bodies include `request_id`. The frontend axios interceptor generates/propagates the header.
- Troubleshooting order: take `X-Request-ID` from the response header → search JSON logs for the same value → follow the request across services. CORS preflight and rate-limit rejections happen outside this middleware and may lack a request_id (`backend/app/core/request_id.py` comments).
- Agent execution tracing is **dev-only**: production does not write JSONL traces; for offline evaluation use the standalone `backend/app/agent_api.py` (port 8010).

### 6.4 Metrics, alerting, and probes

- `/metrics`: Prometheus text; requires Bearer when `METRICS_TOKEN` is non-empty, otherwise 401. `PROMETHEUS_ENABLED` defaults to `false` in code and `true` in compose. Confirm `prometheus_client` multiprocess configuration before multi-worker deployment (current deployments are single-process).
- Scrape/alert config: `backend/config/prometheus.yml`, `backend/config/alerts.yml`; Grafana provisioning in `backend/config/grafana/` (mapped to 9090 / 3001 in full Docker). **If you enable `METRICS_TOKEN`, add an `authorization` header to the Prometheus scrape config**, otherwise scrapes return 401 (the bundled config has none).
- Alert webhook: `ALERT_WEBHOOK_TOKEN` must match the `deploy/monitoring` side, otherwise the backend rejects alerts.
- Worker liveness: the consume loop refreshes a heartbeat file and `worker_healthcheck.py` checks its freshness; a frozen/stuck process becomes unhealthy (compose comments, issue #156).
- Scheduler: with `ENABLE_SCHEDULER=true`, an in-process asyncio loop drives it — **single-instance semantics**; multi-worker deployments need an external scheduler.

---

## 7. Troubleshooting FAQ

### 7.1 Login expired / refresh fails

- Access tokens last 24 hours (`ACCESS_TOKEN_EXPIRE_MINUTES=1440`); refresh tokens last 7 days (`REFRESH_TOKEN_EXPIRE_MINUTES=10080`).
- On 401, call `POST /api/v1/auth/refresh` with the refresh token. **Refresh rotates**: the old refresh token is blacklisted immediately, so reusing it fails.
- The old access token is not revoked by refresh; it stays valid until natural expiry. Logout blacklists the access token and (if provided) the refresh token.
- If refresh also fails, or logout/refresh returns **503**, Redis is probably unavailable — the token blacklist is fail-closed. Check Redis (`services.redis` in `/health`).
- WebSocket auth failures close with `4001/4002/4003`; confirm `Sec-WebSocket-Protocol: auth.<token>` (or legacy `?token=`).

### 7.2 Uploaded but not retrievable

Check in order:

1. **Status**: the document must be `INDEXED`. `PENDING` / `PARSING` means the worker did not consume it; `FAILED` — read `parse_error` (unsupported format, scanned PDF without OCR dependencies, embedding dimension mismatch, etc.).
2. **Organization**: organization = knowledge base. Confirm the document and the search request use the same organization; `/knowledge/search` requires `organization_id`, and for non-superusers the server overrides it with the effective organization.
3. **Index reconciliation**: the `/readyz` `knowledge_base` item compares "documents/chunks claimed INDEXED in DB" with "chunks actually in ES". Chunk coverage **<95% is `unhealthy`** (drained with 503); **95%-100% is `degraded`** (not drained); zero chunks or missing documents are also `unhealthy`.
4. **Run the reconcile script** (read-only first):
   ```bash
   cd backend
   python scripts/reconcile_knowledge_index.py
   python scripts/reconcile_knowledge_index.py --reindex --limit 10
   ```
   `missing` / `partial` are fixable; `extra` and orphans are report-only.
5. **Empty-result metadata**: on empty results `/knowledge/search` returns `index_status` and `index_doc_count` — first confirm whether the index has content at all.
6. **Embedding drift**: if search returns 502 with `error_code=RETRIEVAL_UNAVAILABLE` and `detail.kind` points to `vector_arm_config_error`, see 7.5. Drift is fail-closed: results from the keyword arm are not returned either.
7. **Kafka and worker**: Kafka running without a worker keeps documents `PENDING` — the most common "fake success". Start `python start_worker.py`, or avoid half a Kafka setup when only running infrastructure.
8. **Rerank is not the cause**: rerank failures fail open and keep the original order; they never zero out results.

### 7.3 Agent stuck at "waiting for approval"

This is normal HITL, not a hang:

- The stream emits `approval_required` (or `plan_approval_required`), and `done` carries `task_state=waiting_approval` (or `waiting_plan_approval`).
- Act:
  - Tool approval: `POST /api/v1/agent/tasks/{task_id}/approval` with `{"decision":"approve"}` or `{"decision":"deny","note":"..."}`; add `modified_instruction` to steer the resume.
  - Plan approval: `POST /api/v1/agent/tasks/{task_id}/plan-approval` (approve / edit / reject).
  - Cancel: `POST /api/v1/agent/tasks/{task_id}/cancel`.
- After refresh/reconnect: `GET /api/v1/agent/tasks/{task_id}` returns pending approval details and `approval_expired`; `GET /api/v1/agent/tasks/{task_id}/replay` returns the full replay.
- Approval timeout: the task becomes `FAILED(approval_timeout)`; the API never disguises it as "denied". Start a new task.
- 409 on decision: `TASK_NOT_WAITING_APPROVAL` (wrong state / already expired), `TASK_NOT_RESUMABLE` (terminal), `TASK_BUSY` (concurrent decision). Handle the code; do not retry in a loop.
- A resumed run may stop at the **next** approval point. Approvals are sticky per `(tool, args)` fingerprint, not a blanket approval for all tools.

### 7.4 Old conversations disappear after switching organizations

Conversations are **personal data with an organization scope**: the list condition is "owner + not ended + (current organization OR legacy `organization_id IS NULL`)". After switching to organization B, conversations created under A do not appear; switch back to A to see them. Superusers bypass the organization filter but still only see their own conversations.

The system also forbids writing messages into a conversation owned by another organization (`save_session_interaction` raises `AuthorizationError`), so messages never silently cross organizations. NULL-organization conversations are pre-migration leftovers, visible only to their owner.

### 7.5 Embedding dimension drift (fail-closed)

| Stage | Behavior |
|-------|----------|
| Startup | the probe calls embedding; dimension ≠ `VECTOR_DIMENSION` → **refuses to start** (log `[EMBEDDING] 维度漂移，拒绝启动`); unreachable → warning only |
| Indexing | the worker validates every vector; mismatch raises `EmbeddingDimensionMismatchError`, the document becomes `FAILED` with `parse_error` (the upload already returned 201; check document status) |
| Retrieval | the vector arm returns `vector_arm_config_error`; the pipeline **fails closed** → `RetrievalUnavailableError`; REST 502 `RETRIEVAL_UNAVAILABLE`; strict chat emits SSE `error` |
| ES index | a mapping `dims` mismatch raises `IndexConfigError` and is **never auto-recreated**; the `/health` knowledge item becomes `unhealthy` |

Fix:

1. make `EMBEDDING_API_URL` / `EMBEDDING_MODEL` / `VECTOR_DIMENSION` consistent (e.g. `nomic-embed-text`=768, `bge-m3`=1024, OpenAI `text-embedding-3-small`=1536);
2. after a dimension change, **rebuild the ES index and reprocess all documents** (old vectors are unusable); `reconcile_knowledge_index.py` only fixes missing/partial, not mapping;
3. restart the backend and confirm no dimension warning;
4. do not mask drift with `EMBEDDING_SEND_DIMENSIONS=true` — server-side truncation makes "the model changed" look normal.

### 7.6 Code execution refused without Docker

- **Agent code tools** (`execute_python` / `execute_sql`): with `SANDBOX_MODE=auto` (default) or `docker`, an unavailable Docker daemon means **execution is refused** with a structured failure such as `Docker 沙箱不可用（SANDBOX_MODE=auto），已拒绝执行代码。` This is the issue #206 fail-closed design; it never silently falls back to in-process execution.
- **Workflow code nodes**: a separate switch `ENABLE_WORKFLOW_CODE_NODES=false`; when enabled they are **Docker-only**, `SANDBOX_MODE=ast` does not apply, and unavailable Docker raises `代码执行节点需要 Docker 沙箱，当前未启用`.
- Full Docker does **not** mount `/var/run/docker.sock` into the backend container and the image has no Docker CLI, so containerized code execution/code nodes take the "Docker unavailable" path. Mount the socket explicitly (accepting container-escape risk) or run the backend on the host.
- `SANDBOX_MODE=ast` explicitly selects the in-process AST-allowlist sandbox (weaker isolation than a container). It applies to agent code tools only, not workflow code nodes. Prefer fixing Docker in production.

### 7.7 Common 5xx and rate limiting

| Symptom | Most likely cause | Action |
|---------|-------------------|--------|
| **502** `RETRIEVAL_UNAVAILABLE` | retrieval failure/timeout, or embedding drift fail-closed | inspect `detail.kind`; check ES and embedding; see 7.5 |
| **503** | `/readyz` not ready (DB / ES / knowledge reconciliation); or Redis unavailable makes the token blacklist fail closed | check `/readyz` and `/health` `services`; then Redis |
| **500** | unexpected exception; details only in server logs | record the `X-Request-ID` response header and search JSON logs; with `EXPOSE_EXCEPTION_DETAIL=false` clients only see a generic message |
| **409** | agent task state conflict (`TASK_BUSY` / `TASK_NOT_RESUMABLE`, etc.) | handle the error code; do not retry concurrently |
| **429** | rate limit (host template 10000/min, compose 300/min) | slow down or adjust `RATE_LIMIT_REQUESTS_PER_MINUTE`; `/health`, `/livez`, `/readyz`, `/metrics` are exempt |

General order: `/readyz` → `/health` `services` → document/task status → server logs by `X-Request-ID`.

---

## 8. Development and contribution

### 8.1 Running tests

**Backend**

```bash
cd backend
python -m pytest tests/ -v --tb=short                     # full suite
python -m pytest tests/unit/ tests/api/ tests/behavior/ -v  # fast subset (CI PR gate)
python -m pytest tests/ -v --cov=app --cov-report=html    # coverage
```

Markers: `unit` / `api` / `integration` / `slow` (`backend/pyproject.toml`). `pytest.ini` was removed; `pyproject.toml` is the only config source.

**Frontend**

```bash
cd frontend
npm test                  # = vitest run (there is no test:run script)
npm run test:watch
npm run test:coverage
npm run type-check        # vue-tsc --noEmit
npm run lint              # eslint . --fix
npm run build             # vue-tsc && vite build
npm run test:e2e          # playwright test (browsers must be installed)
```

**Everything**: `make test` (backend + frontend), or run the commands above separately.

### 8.2 CI gates

Per `.github/workflows/ci-fast.yml` and `ci-hygiene.yml`, a PR must pass at least:

| Gate | Content |
|------|---------|
| **Ruff** | `ruff check app/ tests/ scripts/ evals/` + `ruff format --check` (`backend/pyproject.toml`) |
| **Bandit** | `bandit -r app/ -ll` (security scan) |
| **Migration smoke** | `alembic upgrade head` → `check_schema.py`; the MySQL job also downgrades and upgrades again |
| **Backend tests** | `pytest tests/unit/ tests/api/ tests/behavior/`; the Python 3.12 leg adds `--cov-fail-under=42` |
| **Integration** | `pytest tests/` on PR/push to `develop` (MySQL + Redis + ES) |
| **Eval gate** | `make eval-ci`: rebuild the eval index with fake embeddings → run 28 golden-set items → zero-tolerance compare against `backend/evals/baselines/ci_baseline.json`; any drop fails |
| **Frontend** | `eslint .`, `vue-tsc --noEmit`, `vitest run --coverage`, `vite build` |
| **Frontend ratchets** | hardcoded-CJK baseline, i18n completeness, naming consistency, translation-key usage (shrink-only) |
| **E2E** | `playwright test` |
| **Doc facts** | `python scripts/check_doc_facts.py` plus its discriminating-power test: source ↔ `docs/facts.json` ↔ documented numbers |
| **Commit hygiene** | commitlint; PRs also check conflict markers, no internal reports, release version sync, etc. |

Local pre-check: backend `ruff check` + `ruff format --check` + relevant pytest; frontend `npm run type-check` + `npm test`. If you changed documented numbers (tool count, endpoint modules, routes, SSE events), run:

```bash
cd backend && python scripts/check_doc_facts.py --print-derived   # derived values
python scripts/check_doc_facts.py                                  # three-way check
```

### 8.3 ADR (architecture decision record) process

- Directory: `docs/decisions/`; it answers "why it is this way", while git commits answer "what changed" (`docs/decisions/README.md`).
- A good decision record contains: **conclusion first**; **rejected alternatives and why**; **evidence** (spec text / measurements / code references with sources); **explicit separation of measured vs inferred**; **known uncertainties**.
- Formal ADRs follow `ADR-001-路线冻结.md`; land significant routing decisions as an ADR before splitting execution waves.
- Branching (`CONTRIBUTING.md`): `main` production / `develop` integration / `feat/*`, `fix/*`, `docs/*`; commits use `<type>(<scope>): <description>`.

---

## 9. Fact source index

| Topic | Source of truth |
|-------|-----------------|
| Ports, services, healthchecks, volumes | `backend/docker-compose.yml` |
| Environment templates and required values | `backend/.env.example`, `backend/.env.docker.example` |
| Configuration defaults | `backend/app/core/config/{base,ai,database,security}.py` |
| Health probe semantics | `backend/app/main.py` (`/livez`, `/readyz`, `/health`) |
| Endpoint modules | `backend/app/api/v1/router.py`; numbers in `docs/facts.json` |
| Supported formats and parsing | `backend/app/services/document_parser.py` |
| Index reconciliation / reindex | `backend/scripts/reconcile_knowledge_index.py` |
| Migrations | `backend/alembic/versions/` (head: `011_organization_settings`) |
| Agent architecture and events | `AGENT_ARCHITECTURE.md`, `backend/app/agent/events.py` |
| Org = knowledge base, admin semantics | `docs/decisions/ADR-001-路线冻结.md` (D3 / D5) |
| Deployment and production checklist | `deploy/README.md`, `SETUP.md` |
| CI gates | `.github/workflows/ci-fast.yml`, `ci-hygiene.yml`, `docs/ci-charter.md` |
| Documented-number checks | `backend/scripts/check_doc_facts.py`, `docs/facts.json` |
