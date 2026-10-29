<p align="center">
  <strong>English</strong> · <a href="README.md">中文</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-blue?logo=python" alt="Python">
  <img src="https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi" alt="FastAPI">
  <img src="https://img.shields.io/badge/Vue_3-3.4-4FC08D?logo=vuedotjs" alt="Vue 3">
  <img src="https://github.com/sijie-Z/DocMind-RAG/actions/workflows/ci-fast.yml/badge.svg?branch=develop" alt="CI">
  <img src="https://img.shields.io/badge/License-MIT-green" alt="License">
</p>

<div align="center">
  <h1>DocMind</h1>
  <p><strong>From documents to trustworthy answers — and to deliverable review reports.</strong></p>
  <p>An enterprise RAG × Agent platform: hybrid retrieval, a PER autonomous agent, and a contract-review vertical.<br/>
  Every answer traces back to parser-original text; every review conclusion carries version, hash and evidence snapshots; uncertainty is reported as unidentified — never silently guessed.</p>
  <p>
    <a href="#quick-start">Quick Start</a> ·
    <a href="#architecture-in-three-minutes">Architecture</a> ·
    <a href="#engineering-quality-five-verifiable-commitments">Quality Gates</a> ·
    <a href="#contract-review-a-concrete-business-slice">Contract Review</a> ·
    <a href="docs/USER_GUIDE_EN.md">User Guide</a>
  </p>
</div>

---

## What this is

DocMind is not a chat box wrapped around an LLM. It treats an enterprise knowledge base as a system that can be **accepted by business owners** — retrieval, answer provenance, agent execution, and review deliverables each come with verifiable semantics.

Three typical uses:

- **Knowledge Q&A with provenance** — upload PDF/Word/Excel/TXT/Markdown or scanned images (OCR); the system parses, chunks, retrieves with hybrid search (BM25 + vector + RRF) and reranking, then streams an answer. Every citation comes from **parser-original text** with clause/page/character anchors, not from retrieval-augmented text.
- **An agent that plans, executes and self-checks** — a PER (Plan → Execute → Reflect) loop with 31 tools for complex multi-step tasks; every run produces a RunReport (what was done, why, with what result), with replay and experience memory.
- **Contract review (vertical slice)** — bind “policy/template + contract V1 + V2” into a review case: document identity and content hashes, readiness gates, evidence snapshots, deterministic clause extraction and V1↔V2 alignment, and finally a review report with coverage and disclaimers. This is the most product-oriented part of the repo.

> **Project status**: public project, completeness ahead of stability — suitable for learning, secondary development and local demos; harden security and operations before any production use. Engineering discipline comes before feature count: **numbers have a stated method, fixes come with evidence, regressions are blocked before merge** (see “Engineering quality”).

---

## Architecture in three minutes

```
┌── Frontend ─────────────────────────────────────────┐
│  Vue 3 · TypeScript · Naive UI · Vue Flow editor    │
├── API Gateway ──────────────────────────────────────┤
│  FastAPI · JWT + RBAC · SSE · WebSocket · Request ID│
├── AI Agent Core ────────────────────────────────────┤
│  PER Loop (Planner → Executor → Reflector)          │
│  Tool Registry (31) · RunReport · Hooks · Replay    │
├── RAG Pipeline ─────────────────────────────────────┤
│  HybridRetriever (BM25 + vector + RRF) · Reranker   │
│  two-level cache · PII masking · original O/E split │
├── Data layer ───────────────────────────────────────┤
│  MySQL 8 · Elasticsearch 8 · Redis 7 · Kafka · MinIO│
└─────────────────────────────────────────────────────┘
```

Lifecycle of a RAG request:

```
upload → parse (original O vs retrieval text E stored separately) → chunk → embed → index
ask → intent/complexity routing → hybrid retrieval → rerank → context compression → streaming LLM answer
    → citations only expose parser-original text (O); legacy docs return an explicit 409, never fall back to enhanced text
```

Contract review end-to-end:

```
register document identity → readiness gate → create case (blocked/ready + reason codes)
→ evidence snapshots (quote + hash + locator) → rule-based extraction/alignment
→ per-item human decisions → export review report (versions/hashes/coverage/human-review items)
```

## Capabilities

| Area | What it does | Key facts |
|------|--------------|-----------|
| Knowledge base / RAG | multi-format parsing (OCR included), semantic chunking, hybrid retrieval, reranking, query rewriting/HyDE, semantic cache, strict mode | scoring happens on enhanced text; **the evidence exit only exposes parser-original text**; unusable documents return `SOURCE_ORIGINAL_UNAVAILABLE` |
| Agent | PER loop, DAG-based parallel scheduling, timeout/retry/fallback, adversarial reviewer, RunReport, replay, experience memory | **31 Built-In Tools** (10 tools are injected by default; terminal/Feishu/delegation are gated) |
| Contract review | document role/family/version/content hash, readiness, review cases, evidence snapshots, deterministic extraction and alignment | text facts and change types only (modified/renumbered/…); no legal conclusions; ambiguous facts are never guessed |
| Platform | multi-tenant org isolation, RBAC + API-Key scopes, audit logs, Prometheus/Grafana, OpenTelemetry, 24 SSE event types | org isolation spans retrieval/chat/documents/cache/WS; failure semantics are decidable (4xx + error_code) |

<details>
<summary><b>31 Built-In Tools</b> (click to expand)</summary>

11 in `core_tools.py` + 20 under `tools/` (31 built-in tools total), with 10 tools are injected by default; see `docs/METRICS.md` for the derivation. Legacy names in `disabled_tools` act at capability granularity (e.g. `calculate` disables only the calculate operation of `utility`); canonical names disable the whole tool.

| Category | Tools | Notes |
|----------|-------|-------|
| Knowledge | `search_knowledge_base` | hybrid search; `mode=vector` for pure semantic search |
| Web | `web_search` | live search; `action=fetch` extracts page text |
| Code | `execute_python`, `execute_sql` | sandboxed (disabled by default, opt-in) |
| Data | `utility` | calculation, conversion, language detection, numeric analysis, time |
| Translation | `translate_text` | multi-language translation |
| Documents | `extract_insights`, `extract_keywords` | summaries/structured insights, keywords |
| Analysis | `cross_document_analysis`, `generate_report` | cross-document analysis, report generation |
| Management | `list_documents`, `get_document_info` | document management |
| Conversations | `list_conversations`, `get_conversation_history` | history (extended surface) |
| External | `mcp_call`, `feishu_bitable_query` | MCP bridge, Feishu Bitable (gated) |
| Skills | `load_skill_reference`, `deep_research`, `document_comparison`, `knowledge_analysis` | skill loading, composite analysis |

</details>

---

## Engineering quality: five verifiable commitments

“No silent regressions” is not a slogan; it is a set of mechanisms enforced in CI:

1. **Evaluation gate in CI** — retrieval quality is guarded per PR with zero tolerance: Eval v1.5 ships **80 golden cases (60 positive + 20 negative)**; ClauseHit@1/@3/@5, MRR, EmptyAcc, NearMiss@5 and errors are all zero-tolerance, fail-closed. `make eval-ci` (`mode=fake`, 2026-10-24, corpus commit `d867a2b3`) measured ClauseHit@1 **70.0%** / @3 **76.7%** / @5 **80.0%**, NearMiss@5 **25.0%**, MRR **0.74**, EmptyAcc **100%**, errors **0** (PR #525, squash 552efaad; not comparable with the 28-case v1 baseline). Baseline updates are explicit PRs (`backend/docs/eval-gate-phase1.md`; method in `docs/METRICS.md`).
2. **Silent false-success cleanup (#301)** — ~551 exception handlers audited, 32 silent false-success paths closed (logout fail-closed, three-state retrieval, honest document state machine, degraded stats identity…), each with a regression test.
3. **Red first, then green + mutation checks** — every fix starts with a failing test; after the fix, injecting the defect back must fail *that* test — proof the test has discriminating power.
4. **Documented-number invariants** — SSE event count, endpoint-module count, frontend-route count and tool count are derived from source; expected values live in `docs/facts.json`, the public method for every metric (value / method / command / date / note) lives in `docs/METRICS.md`, and `backend/scripts/check_doc_facts.py` verifies code ↔ facts JSON ↔ METRICS.md ↔ docs on every PR. Frontend routes are recorded in two ways: 34 route records (29 named routes, including top-level and redirect records).
5. **Ratchet only tightens** — backend coverage floor `--cov-fail-under=42` plus four frontend baseline ratchets (hardcoded Chinese / i18n completeness / naming consistency / key usage): they block drops, never chase scores.

Hardening closed (completed): user-scoped agent memory, `/files` proxy authorization (auth + org ownership for non-public objects), pure-ASGI metrics and rate-limit middleware, legacy `ci.yml` removed; plus workflow ownership isolation, API-key masking, SSRF protection for web fetch, upload path traversal and MinIO delete fixes, agent sandbox escape closure, `execute_sql` tenant filtering, MCP subprocess env allow-list, login-failure lockout + enumeration timing defense, and end-to-end PII masking.

Historical snapshots are kept separate from current quality: `docs/legacy/` archives old evaluation artifacts (e.g. `eval_first_report_2026-06-15.json`), for reference only — **not representative of current quality** and not part of the gate.

---

## Contract review: a concrete business slice

Generic “chat + knowledge base” is hard to accept. Contract review is narrow enough to define “done”:

**Input**: one policy (or template) plus V1 and V2 of the same contract (text-layer PDF/DOCX).
**Fixed checklist**: payment term, start condition, acceptance/invoice prerequisites, exceptions/deferrals/other agreements — only these four; no “full-contract review” claim.
**What the system does**:

- `PUT /documents/{id}/review-meta`: register role, version family/sequence, effective dates; SHA-256 computed server-side from the MinIO object;
- `GET /documents/{id}/readiness`: `reviewable/blocked` with reason codes (legacy / not indexed / parse failed / missing version meta / no original text);
- `POST /reviews`: the three documents must share an org, have correct roles and increasing version sequence to become `ready`; otherwise the case is stored as `blocked` with `side:reason` codes;
- evidence snapshots: at case creation / selection, parser-original quotes are frozen with hashes, clause/page/character anchors — snapshots survive document rebuilds or deletion, while chunk and document fingerprints flag `live_source_stale`;
- `POST /reviews/{id}/analyze`: rule-based extraction (term value/unit, start events, conditions, exceptions/cross-refs) and V1↔V2 alignment (unchanged/modified/added/removed/renumbered/unidentified); date fragments and warranty periods are never mistaken for payment terms, unclear units stay `ambiguous` instead of guessing;
- next batches: per-item human decisions and export with versions/hashes/coverage (in progress).

**Explicitly out of scope**: no legal conclusions (lawful/unlawful, compliant/non-compliant, risk-free, safe-to-sign), `needs_human`/`insufficient` never count as done, and no conclusion without evidence.

---

## Quick start

### Prerequisites

Docker Desktop (recommended), or: Python 3.11+ · Node.js 18+ · MySQL 8 · Redis 7 · Elasticsearch 8 · Kafka · MinIO.

### 1. Clone

```bash
git clone https://github.com/sijie-Z/DocMind-RAG.git
cd DocMind-RAG/backend
```

### 2. Configure

```bash
# host app
cp .env.example .env               # Windows: copy .env.example .env
# required for any Docker usage:
cp .env.docker.example .env.docker
```

- Host app requires `SECRET_KEY`, `JWT_SECRET_KEY` (generate with `python -c "import secrets; print(secrets.token_hex(32))"`).
- `.env.docker` requires self-generated values for `MYSQL_ROOT_PASSWORD`, `MYSQL_PASSWORD`, `MINIO_ACCESS_KEY`, `MINIO_SECRET_KEY`, `GRAFANA_PASSWORD` (no default passwords).
- Backend startup requires `SECRET_KEY`, `JWT_SECRET_KEY`; chat requires `DEEPSEEK_API_KEY`. Embedding defaults to local Ollama (nomic-embed-text / 768 dims, reusing the chat key); configure `EMBEDDING_API_KEY` / `EMBEDDING_API_URL` for a cloud provider.
- Compose does whole-file interpolation before service selection: `GRAFANA_PASSWORD` is required even without selecting Grafana.

### 3. Start Infrastructure

```bash
docker compose --env-file .env.docker up -d mysql redis elasticsearch minio kafka zookeeper
```

### 4. Start the backend (with migrations)

```bash
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
alembic upgrade head                              # schema guard refuses to start on a stale DB (issue #495)
python -m app --profile web                       # profiles: web | worker | headless | sdk | acp
```

`python -m app --profile web` compares `alembic_version` with the migration head at startup; when stale it prints `current=... head=...` and the fix command, then exits with code 2 instead of serving 500s (`SCHEMA_GUARD`; `AUTO_MIGRATE` is off by default). `uvicorn app.main:app` / `python run.py` remain compatible entrypoints.

### 5. Start the frontend

```bash
cd ../frontend && npm install && npm run dev      # http://localhost:5173
```

### Endpoints

| URL | Purpose |
|-----|---------|
| http://localhost:5173 | frontend |
| http://localhost:8000/docs | Swagger UI |
| http://localhost:8000/health | diagnostic health view |
| http://localhost:8000/readyz | readiness probe (DB/Redis/ES) |
| http://localhost:8000/metrics | Prometheus metrics |

Demo accounts: `guest` (random password printed once at creation, or `DEMO_PASSWORD`; enabled by `ENABLE_DEMO_ACCOUNT=true`); `admin` via `reset_admin.py` (`ADMIN_PASSWORD`).

---

## Project layout

```
DocMind/
├── backend/
│   ├── app/
│   │   ├── main.py                  # FastAPI compatibility entry (assembly lives in bootstrap.py)
│   │   ├── agent/                   # PER agent: loop / planner / executor / reflector
│   │   │   ├── events.py            #   SSE event types (24 types)
│   │   │   ├── core_tools.py        #   base tools (11)
│   │   │   └── tools/               #   extended tools (20; 31 built-in tools total)
│   │   ├── rag/                     # retriever / reranker / pipeline / cache
│   │   ├── api/v1/
│   │   │   ├── router.py            # main router (29 modules registered)
│   │   │   └── endpoints/           # Endpoint implementations (29 files)
│   │   ├── services/                # business services (29 services)
│   │   ├── models/                  # ORM models (21 model files)
│   │   ├── core/                    # config / DB / Redis / ES / MinIO / Kafka / JWT
│   │   └── worker/                  # Kafka consumer (parse → chunk → embed → index)
│   ├── tests/                       # backend tests (unit + API + integration)
│   ├── alembic/                     # migrations
│   ├── benchmark/                   # evaluation framework (eval gate)
│   └── docker-compose.yml
├── frontend/
│   └── src/
│       ├── views/                   # pages (23 page modules, 34 route records / 29 named routes)
│       ├── components/ · api/ · stores/ · router/ · composables/ · locales/
└── docs/                            # architecture page / user guide / ADRs / metrics
```

### 🌐 API Layer (`backend/app/api/v1/`)

29 endpoint modules under `/api/v1`:

| Module | Prefix | Description |
|--------|--------|-------------|
| Auth | `/auth` | Register, login, refresh token, password reset, logout |
| Users | `/users` | User CRUD, profiles, avatars, session management, audit logs (personal API-key endpoints deprecated) |
| API Keys | `/api-keys` | Create/list/revoke `dm_` API keys (bcrypt hash, scopes; issue #480) |
| Files | `/files` | Upload/download/delete, MinIO storage, chunked upload |
| Documents | `/documents` | Document parsing, vectorization, knowledge base indexing, status tracking |
| Chat | `/chat` | Session CRUD, SSE streaming Q&A, strict mode, debug endpoints |
| Knowledge | `/knowledge` | Knowledge base CRUD, retrieval, statistics, settings |
| Contract Review | `/reviews` | Review cases / items / evidence snapshots (parser-original quotes + version hashes) |
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

Swagger docs: `http://localhost:8000/docs` (OpenAPI: `/api/v1/openapi.json`).

---

## Design trade-offs (the five questions interviewers ask)

1. **Why PER instead of single-turn ReAct?** Enterprise tasks need planning, replay and explanations. The planner produces an auditable step DAG, the executor runs independent steps in parallel with retries, and the reflector self-checks before finishing; every run leaves a RunReport and replay snapshots — debuggable, not just a chat transcript.
2. **Why separate `original_text` from retrieval-augmented text?** Retrieval optimizes recall; citations must be verifiable. Long documents prepend generated summaries for scoring (E), while the evidence exit only exposes parser-original text (O) marked via `text_provenance`; legacy documents return an explicit 409 `SOURCE_ORIGINAL_UNAVAILABLE` instead of disguising enhanced text as original.
3. **Why is “cannot review” a state, not an exception?** Missing version identity, legacy text or cross-family versions are business facts; users must see reason codes (`side:reason`) and the flow must stop. Exceptions are reserved for genuine program errors. A `blocked` case cannot select evidence, cannot complete, and will never be exported as “reviewed”.
4. **Why put the evaluation gate in CI?** Retrieval/generation quality degrades silently. A zero-tolerance gate over 80 golden cases (seven metrics) turns “quality must not silently regress” into a process constraint, not a matter of discipline.
5. **Why rule-based extraction + human decisions before LLM extraction?** Build a decidable gold set and evidence chain first: rules only output text facts that can be compared field by field (term value/unit, start events, conditions, exceptions); anything unclear is `ambiguous`/`not_found`. Humans decide per item with an audit trail. Once the gold set is stable, model assistance can be added — without letting the model emit unverifiable conclusions.

**Hooks extension points** (off by default): an in-process event bus with `waterfall` (rewrite/short-circuit) and `serial` (observe) semantics; `ask` on `tools/pre_execute` reuses the HITL approval gate and cannot be bypassed. See [`docs/decisions/ADR-004-hooks.md`](docs/decisions/ADR-004-hooks.md) and more records in [`docs/decisions/`](docs/decisions/).

---

## Configuration

| Variable | Purpose | Default |
|----------|---------|---------|
| `DEEPSEEK_API_KEY` | LLM API key | required |
| `DEEPSEEK_API_URL` | LLM API URL | `https://api.deepseek.com` |
| `EMBEDDING_API_KEY` | embedding key | falls back to `DEEPSEEK_API_KEY` |
| `EMBEDDING_API_URL` | embedding URL | local Ollama |
| `DATABASE_URL` | MySQL DSN | required in deployment |
| `VECTOR_DIMENSION` | vector dimension | `768` |
| `SCHEMA_GUARD` / `AUTO_MIGRATE` | startup schema check / fallback migration | `true` / `false` |
| `ENABLE_TRACING` | OpenTelemetry | `false` |
| `ENABLE_DEMO_ACCOUNT` | demo account | `false` |
| `ENABLE_WORKFLOW_CODE_NODES` | allow workflow code nodes (security risk, off by default) | `false` |

See `backend/.env.example` and `backend/.env.docker.example`; model/vector defaults are sourced from `backend/app/core/config/ai.py` (CPU/8G/16G auto tiers; explicit config wins).

---

## Tests and evaluation

```bash
cd backend
python -m pytest tests/ -v --tb=short          # all backend tests
python -m pytest tests/ --cov=app --cov-report=html
make eval-ci                                    # evaluation gate (same as CI)
cd ../frontend && npm run test                  # frontend tests
```

Frozen test baseline (2026-10-15): backend `pytest tests/` **2223 passed / 51 skipped** (0 failed); frontend **861 passed** (128 test files) + `vue-tsc --noEmit` clean. Numbers evolve with CI — CI is the single source of truth; this page keeps only the frozen snapshot (method in `docs/METRICS.md`).

---

## Deployment

```bash
cd backend && docker compose --env-file .env.docker up -d
# starts migrate + backend + worker + mysql + redis + elasticsearch + minio
# + zookeeper + kafka + prometheus + grafana; apps wait for the one-shot migrate to exit successfully
```

| Service | Port |
|---------|------|
| FastAPI backend | 8000 |
| Vite dev server | 5173 |
| MySQL | 3308 (Docker) / 3306 (local) |
| Redis | 6390 (Docker) / 6379 (local) |
| Elasticsearch | 9200 |
| MinIO API / Console | 9000 / 9001 |
| Kafka | 9092 / 29092 (internal) |
| Prometheus / Grafana | 9090 / 3001 |

K8s manifests and deployment notes live in [`deploy/`](deploy/). When Kafka is unavailable, `/documents/upload` and rebuild endpoints automatically fall back to in-process processing, so local development never gets stuck in `pending`.

---

## Known boundaries

- Workflow code nodes are off by default and must run in the Docker sandbox with `ENABLE_WORKFLOW_CODE_NODES=true`.
- GraphRAG is not part of the RAG retrieval path; context compression is rule-based, no LLM call; short-term/working memory is in-process while long-term/reflective memory uses Redis.
- Contract review currently covers four fixed checklist items for Chinese contracts; evidence is chunk-level quotes with clause/page/character anchors, not PDF highlighting.
- Evaluation `mode=fake` metrics guard regressions; they are not an online-quality claim for a specific model.

---

## Documentation map

| Document | Content |
|----------|---------|
| [`docs/USER_GUIDE_EN.md`](docs/USER_GUIDE_EN.md) | 5-minute setup → first document → first grounded answer → agent workbench |
| [`docs/architecture.html`](docs/architecture.html) | interactive architecture page (GitHub Pages: [sijie-z.github.io/DocMind-RAG](https://sijie-z.github.io/DocMind-RAG/)) |
| [`docs/METRICS.md`](docs/METRICS.md) | method behind every public number (value / command / date / note) |
| [`docs/decisions/`](docs/decisions/) | ADRs |
| [`backend/docs/eval-gate-phase1.md`](backend/docs/eval-gate-phase1.md) | eval-gate rules and baseline-update process |
| [Research repo](https://github.com/sijie-Z/DocMind-Agent-Causal-Study) | architecture decisions and experiment data |

---

<p align="center">
  <sub>Built with ❤️ by the DocMind Team · MIT License</sub>
</p>
