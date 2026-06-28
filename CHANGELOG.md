# Changelog

All notable changes to DocMind will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.3.0] - 2026-06-28

### 重大功能: PaiAgent 工作流编辑器特性全量吸收

> **背景**: PaiAgent 是一个 Java/React 技术栈的开源 AI 工作流可视化编排平台（已作为参考项目研究）。
> 经过全面的功能对比分析，将其所有有价值的特性吸收到 DocMind 的 Vue 3 工作流编辑器中，
> 同时完全保留了 DocMind 原有的差异化优势。吸收完成后 PaiAgent 参考项目已删除。

### Added — 新增功能 (13 项)

#### 工作流编辑器 UI/UX 升级 (`frontend/src/views/workflow/editor.vue`)

1. **自动保存（500ms 防抖）**: 修改节点属性后自动保存到 Pinia store + 后端 API，
   用户再也不会因忘记点"保存"丢失工作。参考 PaiAgent `EditorPage.tsx` 的 `useEffect` + `setTimeout` 模式。

2. **动态节点面板**: 节点列表从 `GET /api/v1/workflows/nodes/definitions` 动态加载，
   API 失败时回退到硬编码列表。不再需要修改前端代码来新增节点类型。

3. **Skill 选择器**: LLM 节点属性面板中可以通过下拉框选择已学习的 Skill，
   将 Skill 指南注入到 LLM 的 system prompt。前端接入 `GET /api/v1/agent/skills`。

4. **工作流加载列表**: 工具栏"加载"按钮弹出 Modal 列表，浏览所有已保存工作流，
   点击加载到画布。参考 PaiAgent 的 `Modal + List` 模式。

5. **参数引用系统**: 参数支持"输入"（静态值）和"引用"（上游节点输出）两种模式。
   引用模式自动列出所有上游节点的可用输出字段。

6. **模板变量校验**: 保存前自动检查 prompt 中的 `{{paramName}}` 是否在已定义的参数列表中，
   未定义参数弹出警告。参考 PaiAgent 的 `validateTemplateParams` 模式。

7. **调试面板 emoji 升级**: 执行日志使用 🚀/✅/❌/📊 emoji 标记事件类型，
   Timeline 时间线自动着色。

#### LLM 全局配置系统 (`backend/app/api/v1/endpoints/llm_config.py`)

8. **多配置 per provider 的表格式管理**: 每个 LLM 供应商支持创建**多套**配置
   （如 OpenAI 的"国内代理"和"官方直连"），配置拥有唯一 ID、支持 CRUD、
   设默认（每供应商仅一个默认）。存储在 Redis Hash。

   API 端点:
   - `GET /api/v1/llm-config` — 列出所有配置
   - `POST /api/v1/llm-config` — 创建新配置
   - `PUT/PATCH/DELETE /api/v1/llm-config/{id}` — 更新/删除配置
   - `POST /api/v1/llm-config/{id}/default` — 设为默认
   - `GET /api/v1/llm-config/providers` — 列出支持的供应商
   - `GET /api/v1/llm-config/default/{provider}` — 获取供应商默认配置

9. **LLM 全局配置前端 UI**: `n-data-table` 表格式展示所有配置（供应商/配置名/API URL/
   模型/温度/默认标记），表格行内编辑/删除/设默认按钮，表单支持新建和编辑。

10. **LLM 节点全局配置引用**: 节点属性面板中"使用全局配置"开关 + provider 配置下拉选择器。
    参考 PaiAgent 的 `configId` 全局配置引用模式。

#### TTS 节点配置面板

11. **TTS 节点可配置**: 语音合成节点从"该节点暂无可配置项"升级为完整配置面板，
    包含: 15 种音色选择（Cherry/Serena/Ethan/Momo...）、语言类型、API Key、模型名称、
    输入输出参数列表。参考 PaiAgent 的 TTS 节点配置 UI。

#### 引擎类型选择器

12. **DAG / LangGraph 引擎切换**: 工具栏添加引擎类型下拉框（DAG 引擎 / LangGraph 引擎），
    保存/加载/自动保存时携带 `engine_type` 字段。后端已有双引擎支持，前端此前未暴露。

#### 通用 LLM 节点

13. **通用 `llm` 节点 + provider 下拉**: 新增 `llm` 节点类型，拖出后通过下拉框
    选择供应商（OpenAI/DeepSeek/Qwen/Step/Zhipu/AI平），替代原本每种供应商一种节点类型
    的硬编码限制。LLMNode 支持 `purple` 颜色。LLM 节点新增输出参数配置
    （name/type/description 列表）。

#### Curated Skill 系统 (`backend/app/agent/curated_skills.py`)

14. **SKILL.md 文件系统**: 支持人工编写 YAML frontmatter + Markdown 格式的 curated skill，
    放在 `skills/` 目录下，启动时自动加载。支持 `reference/` 子目录下的参考文档。

    API 端点:
    - `GET /api/v1/curated-skills` — 列出所有 curated skills
    - `GET /api/v1/curated-skills/{name}` — 获取详情
    - `GET /api/v1/curated-skills/{name}/references/{ref}` — 获取参考文档
    - `POST /api/v1/curated-skills/reload` — 强制重新扫描

15. **预置 ai-podcast skill**: 附带完整的播客脚本生成 skill，包含 SKILL.md、
    `reference/script-template.md`、`reference/voice-guide.md`，
    作为 curated skill 的参考实现。

### Changed — 已有功能改进

- **LLMNode.vue**: `color` prop 新增 `purple` 选项，支持通用 llm 节点
- **Workflow TypeScript 接口**: 新增 `engine_type?: string` 字段
- **CLAUDE.md**: 移除 PaiAgent 相关文档，更新为单项目结构
- **Router (`backend/app/api/v1/router.py`)**: 注册 `llm_config` 和 `curated_skills` 路由

### Stats — 数据统计

| 指标 | 变化 |
|------|------|
| `editor.vue` | 1115 → 1839 行 (+724, +65%) |
| 新增 Python 文件 | 3 个 (`llm_config.py` 313行, `curated_skills.py` 199行, `curated_skills.py` API 69行) |
| 新增 TypeScript 文件 | 2 个 (`api/llmConfig.ts` 49行, `stores/llmConfigStore.ts` 110行) |
| 新增 Markdown 文件 | 3 个 (`skills/ai-podcast/` 目录下的 SKILL.md + 2 refs) |
| PaiAgent 吸收完成度 | **15/15 项（100%）** |
| 后端单测 | **265 passed**, 0 failures |
| TypeScript | 6 个预存 error (v-for idx 类型)，0 个新引入 |

## [1.2.1] - 2026-05-24

### Fixed
- **Agent page crash**: Fixed `tools.value.filter is not a function` — API response nested `data` field was not properly extracted
- **Auth bypass**: Route guard now checks token expiry via `isTokenExpired()`, expired tokens are cleared and user is redirected to login
- **Error propagation**: Agent `onMounted` wrapped in double try/catch to prevent errors from bubbling through Suspense to ErrorBoundary
- **CSS `@apply` broken**: Converted all UnoCSS `@apply` directives in `Markdown.vue` to standard CSS (transformerDirectives was not configured)
- **API response parsing**: Fixed `response.data` vs `response.data.data` mismatch in agent view and config panel

### Changed
- **Agent parallel execution**: Independent plan steps now execute concurrently via `asyncio.gather` instead of sequential `for` loop
- **Page transitions**: Layout router-view wrapped in `<Transition name="page-slide">` with slide-up + fade animation (0.35s)
- **Login page animation**: Brand panel fades in from bottom (0.6s), form panel slides in with 0.15s delay, submit button has bounce hover effect
- **ErrorBoundary**: Now shows actual error details in collapsible `<details>` section; captures `window.onerror` and `unhandledrejection` events
- **Route guard**: Added `isTokenExpired()` check, `removeToken()` on validation failure, `sessionValidated` reset on logout

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
