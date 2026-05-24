# Changelog

All notable changes to DocMind will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.0] - 2026-05-24

### Added
- **PER Agent Architecture**: Upgraded from ReAct to Plan-Execute-Reflect (PER) with streaming event pipeline
  - 12 SSE event types: thinking, plan_start, plan_step, plan_complete, tool_call, tool_result, reflection, chunk, done, error, message, heartbeat
  - Lazy-initialized Planner/Executor/Reflector components
  - Quick-pass reflection for simple tasks, LLM-based reflection with fast-pass fallback
- **Deep Analysis Tools**: 3 new tools for document analysis
  - `extract_insights`: Entity, metrics, claims, structure extraction from documents
  - `cross_document_analysis`: Multi-document pattern analysis (common themes, differences, trends)
  - `generate_report`: Polished markdown report generation from analysis data
- **Total 25 registered tools** (up from 11): 7 analysis tools with tag-based categorization
- **Agent SSE in Main Chat**: Chat stream endpoint (`/api/v1/chat/stream`) now forwards all 12 agent event types when `useAgent: true`
- **Agent sources extraction**: `_extract_agent_sources()` parses search tool output for citation metadata

### Fixed
- **ES field mapping**: All tools now correctly use `chunk_text` instead of empty `content` field (5 files)
- **ES health check**: Single-node ES cluster (yellow status) now correctly reports "healthy"
- **Model name consistency**: All 9 instances of `deepseek-chat` corrected to `deepseek-v4-flash`
- **MinIO blocking**: Bucket check removed from synchronous `.client` property — no longer blocks event loop on startup
- **list_documents ID truncation**: Now returns full UUIDs instead of 8-char truncated IDs
- **cross_document_analysis**: Added filename-fallback lookup when document_id search returns empty
- **Frontend SSE service**: Expanded from 4 to 13 event types with typed event dispatch
- **Tool result synthesis**: Executor now synthesizes tool output into natural language via LLM

### Changed
- Agent SSE events use `data:` format (not named events) for consistent frontend parsing
- Frontend chat composable handles 8 agent event types with inline progress display
- Agent model dropdown now shows `deepseek-v4-flash` and `deepseek-v4-pro` (not deprecated `deepseek-chat`)

## [1.1.0] - 2026-05-17

### Added
- SQLite dev mode for no-Docker development
- Vite WebSocket proxy with SSE as default transport
- Agent mode toggle in main chat input
- Seed knowledge base with demo documents (architecture.md, python_tutorial.md)
- DuckDuckGo web search integration
- CJK analyzer fallback for Chinese text search

### Fixed
- Duplicate SSE event emission in Agent mode
- Agent response quality (raw tool output → natural language synthesis)
- ES CJK search with `cjk` analyzer (IK analyzer optional)
- Missing agent sources in SSE response
- Frontend HTTP error logging

## [1.0.0] - 2026-05-17

### Added
- **RAG Pipeline**: Full hybrid search (BM25 + KNN vector + RRF fusion) with Cross-Encoder reranking
- **Document Processing**: PDF, Word, Excel, TXT, Markdown parsing via LangChain + async Kafka pipeline
- **AI Chat**: WebSocket streaming with multi-turn context, citation-backed answers, response regeneration
- **ReAct Agent**: 11 built-in tools (search, analysis, code execution, translation, etc.) with SSE streaming
- **Knowledge Graph**: Force-directed graph visualization with entity extraction (7 categories)
- **Workflow Editor**: Visual DAG builder with LLM/API/Code/Condition nodes and real-time debugging
- **Agent Memory**: Short-term, long-term, and workspace memory with embedding-based recall
- **Authentication**: JWT-based auth with RBAC (User → Role → Organization) multi-tenancy
- **Monitoring**: Prometheus + Grafana dashboards, OpenTelemetry tracing, audit log
- **Internationalization**: Chinese, English, Japanese, French
- **PWA**: Offline support via service worker
- **Onboarding**: Empty state guides, demo data loading, 8 prompt templates
- **Infrastructure**: Docker Compose (MySQL, Redis, ES, Kafka, MinIO), multi-stage builds

### Architecture
- `backend/`: FastAPI async backend with SQLAlchemy 2.0, Alembic migrations
- `frontend/`: Vue 3 + TypeScript + Naive UI + Pinia
- `agent/`: ReAct loop with tool registry, context engine, skill learning
