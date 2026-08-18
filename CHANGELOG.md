# Changelog

All notable changes to DocMind will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.20.0] - 2026-08-06

### Security hardening
- **Agent 沙箱**：`execute_python` 的 AST 检查拦截一切下划线前缀属性访问（封堵
  random._os.system 逃逸链，已实证），增加代码长度/输出上限；`execute_sql`
  拒绝 UNION/子查询/INTO OUTFILE，移除 users 敏感列，执行时自动注入
  organization_id 租户过滤（无组织上下文 fail-closed）。
- **工具授权**：/agent/chat 与 /agent/config 的 disabled_tools 由服务端
  强制合并黑名单（客户端无法重新启用 execute_python/execute_sql/mcp_call）；
  mcp_call 默认禁用且子进程环境变量白名单化（不再透传 os.environ）。
- **认证**：注册禁止自选 organization_id；update_user_role 限同组织；
  登录失败锁定（5 次/15 分钟）+ dummy bcrypt 消除用户枚举时序侧信道；
  refresh token 轮换 + 登出双吊销 + 禁用用户拒绝续期；WS 端点仅接受
  access token 且校验黑名单。
- **缓存一致性**：get_current_user 双路径校验 is_active；改密/角色/状态
  变更后主动失效用户 Redis 缓存（修复改密 24h 内必失败）；RAG 精确/语义缓存
  在文档删除/重建时按组织清理。
- **数据边界**：/files 代理按对象名前缀校验组织归属 + nosniff/attachment
  头；GraphRAG 图谱按组织分区（rag:graph:{org}）；workflow executions 增加
  归属校验；llm_config 写操作仅限管理员；知识库任务列表对无组织用户恒空。
- **PII 掩码链路重构**：检索上下文与 query 进 prompt 前统一掩码（占位符编号
  全局唯一），流式 chunk 出服务器前掩码，最终消息仅还原引用型占位符；
  身份证/银行卡正则修正（按真实身份证结构，避免互相吞并）。
- **前端凭证面**：WS token 移出 URL（改 subprotocol）；PWA 不再缓存 /api/*；
  KaTeX 嵌套依赖强制升至 0.16.28（消除 2015 版 XSS 向量）；手册页复用
  DOMPurify 净化组件；登出清理 user_info 等 PII。
- **API Key**：不再进入公共响应 schema 与 Redis 缓存，/me 仅返回掩码。
- /metrics 支持 METRICS_TOKEN Bearer 鉴权。

### Correctness & reliability
- organizations 四个写端点补 commit（修复静默数据丢失）。
- SQLAlchemy 恒假条件修复：通知未读 is_(False)、会话列表 is_(None)。
- 聊天历史取最近 10 条（原取最旧）；SSE agent 模式加载会话历史。
- SSE/WS 断连时取消孤儿 pipeline 任务（停止烧 LLM 费用）。
- 限流器 zadd 成员唯一化（修复同秒漏计）；token 计量改 ContextVar 隔离。
- 分块上传限制（单块 5MB、总块 ≤21）；/rag-eval-batch 上限 20 条。
- Kafka 消费者手动提交 + 消息类型防护；文档处理加 Redis 处理锁；
  create_task 全部模块级引用持有。
- Docker Compose：Redis 端口修正、默认凭据占位化、中间件端口仅本机绑定。

### Migrations
- 修复 001 外键列类型（String(36) → Integer，MySQL FK 3780 错误）。
- 新增 005 迁移补齐 15 张缺失表（RBAC/通知/工作流/审计等），
  alembic upgrade head 冒烟通过（SQLite 全链验证 26 张表齐备）。

### Testing
- 后端 tests/unit + tests/behavior：446 passed / 1 skipped。
- 新增 24 个安全回归测试（沙箱逃逸/SQL 租户过滤/PII 掩码/工具禁用合并等）。

## [1.19.0] - 2026-08-05

### Agent SDK learnings
- 新增轻量 JSON Schema 校验器 `schema_validation.py`。
- `ToolRegistry` 在工具成功返回后按 `output_schema` 校验输出，不匹配时返回 `validation_error`。

### Testing
- 后端 `tests/unit + tests/behavior`：422 passed / 1 skipped。

## [1.18.0] - 2026-08-05

### Agent SDK learnings
- 新增类型化 `RunContext`，Executor 在工具调用时构造并传入，工具可显式声明
  `run_context: RunContext`，对齐 OpenAI Agents SDK / Claude Agent SDK 的上下文传递方式。
- 新增 `docs/sdk_learnings.md`，记录从两个 SDK 学到的架构点与落地清单。

### Testing
- 后端 `tests/unit + tests/behavior`：416 passed / 1 skipped。

## [1.17.0] - 2026-08-04

### Frontend performance
- ECharts 改为 `echarts/core` 按需注册，charts chunk 从约 1MB 降到 528KB。
- highlight.js 使用 `lib/common`，markdown/highlight/katex 拆分为独立 chunk。
- 构建不再出现循环 chunk 和超过 800KB 的大 chunk 警告。

### Testing
- 前端 `vue-tsc --noEmit` 与 `vite build` 通过。

## [1.16.0] - 2026-08-04

### Ops and observability
- 指标实时计数改为按实例写入 Redis 并在读取时聚合，支持多实例部署下的请求数/错误数恢复。
- pre-commit 新增前端 `vue-tsc` 类型检查钩子。

### Testing
- 后端 `tests/unit + tests/behavior`：414 passed / 1 skipped。

## [1.15.0] - 2026-08-04

### Build and deployment
- 前端 `manualChunks` 改为按包名精确分组，消除 `vendor -> vue -> vendor` 循环 chunk 警告。
- `.env.example` / `.env.docker.example` 补充 `METRICS_LIVE_PERSIST_SECONDS`、`ENABLE_WORKFLOW_CODE_NODES`、`SANDBOX_MODE`。
- `docker-compose.yml` 为后端增加 Python 健康检查。

### Testing
- 前端 `vue-tsc --noEmit` 与 `vite build` 通过。

## [1.14.0] - 2026-08-04

### Metrics persistence
- 指标实时计数支持 Redis 持久化：请求计数、错误数、状态码分布会按 `METRICS_LIVE_PERSIST_SECONDS` 间隔写回 Redis，服务重启后自动恢复。
- 新增实时计数恢复单元测试。

### Testing
- 后端 `tests/unit + tests/behavior`：412 passed / 1 skipped。

## [1.13.0] - 2026-08-04

### Memory persistence
- Agent 记忆 API 在读取前从 Redis 加载，避免服务重启后读不到历史记忆。
- 清空和导入记忆后立即写回 Redis，保持 API 与 Redis 状态一致。

### Testing
- 后端 `tests/unit + tests/behavior`：412 passed / 1 skipped。

## [1.12.0] - 2026-08-04

### Observability and state
- GraphRAG 图谱支持 Redis 持久化：`build_graph_from_entities`、`clear` 后自动保存，查询前自动加载。
- `MetricsCollector` 快照支持 Redis 持久化，服务重启后仍可恢复历史趋势。
- 新增 GraphRAG 与 Metrics Redis 持久化单元测试。

### Testing
- 后端 `tests/unit + tests/behavior`：410 passed / 1 skipped。

## [1.11.0] - 2026-08-04

### Frontend architecture
- 将工作流编辑器的业务逻辑抽取到 `composables/useWorkflowEditor.ts`。
- `editor.vue` 从 1820 行降到 971 行，三个超大前端页面全部完成拆分。

### Testing
- 前端 `vue-tsc --noEmit` 与 `vite build` 通过。

## [1.10.0] - 2026-08-04

### Frontend architecture
- 将个人中心页面的业务逻辑抽取到 `composables/useProfilePage.ts`。
- `profile/index.vue` 从 1432 行降到 514 行，模板与脚本职责分离。

### Testing
- 前端 `vue-tsc --noEmit` 与 `vite build` 通过。

## [1.9.0] - 2026-08-04

### Frontend architecture
- 拆分知识库页面：上传面板、上传任务抽屉、文档详情/错误弹窗独立为组件。
- `knowledge/index.vue` 从 1119 行降到 904 行，新增 `KnowledgeUploadPanel.vue` 和 `KnowledgeDetailModals.vue`。
- 修复前端上传格式 accept 与后端支持的格式对齐（新增 `.csv`，移除 `.doc/.ppt`）。

### Testing
- 前端 `vue-tsc --noEmit` 与 `vite build` 通过。

## [1.8.0] - 2026-08-04

### Features and fixes
- 补齐用户管理后端接口：管理员创建用户、更新用户信息、重置用户密码。
- 前端用户管理新增初始密码字段，创建用户不再依赖不存在的 `/users/create`。
- 移除前端无效/死代码 API：`/chat/completions`、`PUT /knowledge/{id}`、GET `/knowledge/search`、批量删除会话。

### Testing
- 后端 `tests/unit + tests/behavior`：410 passed / 1 skipped。
- 前端 `vue-tsc --noEmit` 通过。

## [1.7.0] - 2026-08-04

### Security
- 工作流代码节点不再在进程内执行 Python；开启后必须通过 Docker 沙箱运行。
- 移除了进程内 `exec` 与受限 builtins 实现，代码节点现在只在容器内执行。

### Testing
- 后端 `tests/unit + tests/behavior`：409 passed / 1 skipped。
- 前端 `vue-tsc --noEmit` 通过。

## [1.6.0] - 2026-08-04

### Security and operations
- Agent 记忆 API 按用户命名空间隔离，`agent_id` 不再全局可读写。
- `/files/{path}` 资源代理对非公开对象要求登录；`avatars/` 和 `demo/` 保持公开。
- 移除 legacy `ci.yml`，CI 只保留 `ci-fast.yml` 与 `ci-nightly.yml`。
- 监控与限流改为纯 ASGI 中间件：`/metrics` 现在真正采集请求指标，限流在 `ENABLE_RATE_LIMIT=true` 时生效。

### Testing
- 后端 `tests/unit + tests/behavior`：409 passed / 1 skipped。
- 前端 `vue-tsc --noEmit` 通过。

## [1.5.0] - 2026-08-04

### Architecture
- 统一文档处理管线：`worker/doc_processor.py` 成为解析、分块、向量化、ES 索引的唯一实现，任务状态同步到 `knowledge_processing_jobs`。
- 知识库构建接口改为委托统一 Processor，移除三套并行索引逻辑。
- 聊天核心管线从 `api/v1/endpoints/chat.py` 下沉到 `services/chat_service.py`，路由层只保留协议编排。
- 新增统一文档授权辅助 `get_document_for_user`，文档查看/内容/下载/删除/重建共用同一组织校验。
- 增加文档处理端到端集成测试（默认跳过，`RUN_INTEGRATION=1` 时运行）。

### Testing
- 后端 `tests/unit + tests/behavior`：409 passed / 1 skipped。
- 前端 `vue-tsc --noEmit` 通过。

## [1.4.0] - 2026-08-04

### Security hardening and local usability

### Fixed
- 语义缓存改为按组织隔离，避免跨租户命中检索结果和回答。
- 会话写入增加归属校验，禁止向他人会话追加消息。
- 知识库搜索、建议、统计、重建、删除、任务事件补上组织归属校验。
- 文件分块上传增加 `file_hash/file_name` 合法性校验，修复路径穿越与 MinIO 对象残留。
- LLM API Key 在接口响应中掩码；Web 抓取增加 SSRF 防护。
- Agent 文档摘要/详情工具按组织过滤；`agent_api` 未配置 `AGENT_API_KEY` 时拒绝访问。
- 工作流接口按创建者隔离；工作流代码节点默认关闭，需设置 `ENABLE_WORKFLOW_CODE_NODES=true`。
- 上传格式与解析器能力对齐，补充 `.csv` 解析支持。
- Kafka 不可用时，文档上传与知识库重建自动切换为进程内处理。
- 修复 Windows 启动脚本对 `.venv` 和 worker 文件名的识别。
- 将开发数据库、向量存储、benchmark 产物等敏感/生成文件移出 git 跟踪。

### Testing
- 后端 `tests/unit + tests/behavior`：408 passed / 1 skipped。
- 前端 `vue-tsc --noEmit` 通过。

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
