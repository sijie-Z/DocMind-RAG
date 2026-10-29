<p align="center">
  <a href="README_EN.md">English</a> · <strong>中文</strong>
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
  <p><strong>从文档到可信答案，再到可交付的核验单。</strong></p>
  <p>企业级 RAG × Agent 平台：混合检索 + PER 自主 Agent + 合同核验垂直场景。<br/>
  回答必须能回到原文；核验结论必须带版本、哈希与证据快照；不确定就标「未识别」，绝不假装成功。</p>
  <p>
    <a href="#快速开始">快速开始</a> ·
    <a href="#三分钟看懂">架构与请求生命周期</a> ·
    <a href="#工程质量五个可验证的承诺">质量门禁</a> ·
    <a href="#合同核验从一个真实业务场景切入">合同核验</a> ·
    <a href="docs/USER_GUIDE.md">使用手册</a>
  </p>
</div>

---

## 这是什么

DocMind 不是一个「聊天框套壳」：它把企业知识库当成一套**可验收的系统**来做——检索链路、答案出处、Agent 执行过程、核验交付物，每一层都有可核对的口径。

三个典型用法：

- **带出处的知识问答** — 上传 PDF/Word/Excel/TXT/Markdown、扫描件（OCR），系统解析、分块、混合检索（BM25 + 向量 + RRF）、重排后流式回答；每个引用都来自**未加工的解析原文**，可溯源到条款/页码/字符区间，而不是检索用的增强文本。
- **能规划、能执行、能自检的 Agent** — PER（Plan → Execute → Reflect）循环，可调用 31 个工具完成多步任务；每轮运行产出 RunReport（做了什么、为何这么做、结果如何），支持执行回放与经验记忆。
- **合同核验（垂直场景）** — 把「制度/模板 + 合同 V1 + V2」绑成核验案件：文档版本身份与内容哈希、readiness 门禁、原文证据快照、确定性条款抽取与差异对齐，最后交付带覆盖范围与免责声明的核验单。这是本仓库最「产品化」的一块，见下文。

> **项目状态**：公开项目，功能完整度高于稳定度，适合学习、二次开发与本地演示；生产部署前请按自己的合规要求加固。工程纪律先于功能数量：**数字带口径、修复带证据、退化拦在合并前**（见「工程质量」）。

---

## 三分钟看懂

```
┌── 前端 ─────────────────────────────────────────────┐
│  Vue 3 · TypeScript · Naive UI · Vue Flow 工作流     │
├── API 网关 ─────────────────────────────────────────┤
│  FastAPI · JWT + RBAC · SSE · WebSocket · 请求 ID    │
├── AI Agent 核心 ────────────────────────────────────┤
│  PER Loop (Planner → Executor → Reflector)          │
│  Tool Registry (31) · RunReport · Hooks · 回放        │
├── RAG 管线 ─────────────────────────────────────────┤
│  HybridRetriever (BM25 + 向量 + RRF) · Reranker      │
│  二级缓存 · PII 掩码 · 原文/检索文本分离(O/E)          │
├── 数据层 ───────────────────────────────────────────┤
│  MySQL 8 · Elasticsearch 8 · Redis 7 · Kafka · MinIO │
└─────────────────────────────────────────────────────┘
```

一次 RAG 请求的生命周期：

```
上传 → 解析(原文 O 与检索文本 E 分离落库) → 分块 → 嵌入 → ES 索引
提问 → 意图/复杂度分类 → 混合检索 → 重排 → 上下文压缩 → LLM 流式生成
     → 引用只展示 parser 原文(O)；legacy 文档显式 409，绝不回退增强文本
```

合同核验的端到端路径：

```
注册版本身份 → readiness 门禁 → 建案(blocked/ready + 原因码) → 证据快照(quote+哈希+定位)
→ 规则抽取/对齐 → 人工逐项决策 → 生成核验单(版本/哈希/覆盖范围/需人工项)
```

## 核心能力

| 方向 | 能力 | 关键事实 |
|------|------|----------|
| 知识库 / RAG | 多格式解析（含 OCR）、语义分块、混合检索、重排、查询改写/HyDE、语义缓存、严格模式 | 检索评分按增强文本、**证据出口只放 parser 原文**；不可用文档显式 `SOURCE_ORIGINAL_UNAVAILABLE` |
| Agent | PER 三阶段循环、DAG 并行调度、超时/重试/回退、对抗式 Reviewer、RunReport、执行回放、经验记忆 | **31 个内置工具**（默认注入 10 个工具，终端/飞书/委派按门控开放） |
| 合同核验 | 文档角色/版本族/版本序/内容哈希、readiness、核验案件、原文证据快照、确定性抽取与对齐 | 只输出文本事实与差异类型（modified/renumbered/…），不输出任何法律结论；未识别不猜值 |
| 平台化 | 多租户组织隔离、RBAC + API Key scope、审计日志、Prometheus/Grafana、OpenTelemetry、24 种 SSE 事件 | 组织隔离贯穿检索/会话/文档/缓存/WS；失败语义可判定（4xx + error_code） |

<details>
<summary><b>31 个内置工具一览</b>（点击展开）</summary>

`core_tools.py` 11 个 + `tools/` 20 个，默认注入 10 个；口径见 `docs/METRICS.md`。兼容口径：`disabled_tools` 中的旧工具名按**能力粒度**生效（如 `calculate` 只禁用 `utility` 的 calculate operation），写规范名则禁用整个工具。

| 类别 | 工具 | 说明 |
|------|------|------|
| 知识检索 | `search_knowledge_base` | 混合搜索；`mode=vector` 纯语义搜索 |
| 网络 | `web_search` | 实时搜索；`action=fetch` 网页正文提取 |
| 代码 | `execute_python`, `execute_sql` | 沙箱化 Python / SQL（默认禁用、opt-in） |
| 数据 | `utility` | 计算、格式转换、语言检测、数值分析、当前时间 |
| 翻译 | `translate_text` | 多语言翻译 |
| 文档 | `extract_insights`, `extract_keywords` | 摘要/结构化洞察、关键词提取 |
| 分析 | `cross_document_analysis`, `generate_report` | 跨文档分析、报告生成 |
| 管理 | `list_documents`, `get_document_info` | 文档管理 |
| 对话 | `list_conversations`, `get_conversation_history` | 会话历史（扩展面） |
| 外部 | `mcp_call`, `feishu_bitable_query` | MCP 协议桥接、飞书多维表格（默认门控） |
| 技能 | `load_skill_reference`, `deep_research`, `document_comparison`, `knowledge_analysis` | 技能加载、复合分析（后三者扩展面） |

</details>

---

## 工程质量：五个可验证的承诺

「不静默回退」不是口号，而是一组可以在 CI 里核对的机制：

1. **评测门禁进 CI（Eval Gate）** — 检索质量由零容差门禁逐 PR 守护：Eval v1.5 共 **80 条 golden set（60 正例 + 20 负例）**，ClauseHit@1/@3/@5、MRR、EmptyAcc、NearMiss@5、errors 七项指标容差为 0，任何下跌即红、fail-closed。`make eval-ci`（`mode=fake`，2026-10-24，语料 commit `d867a2b3`）实测 ClauseHit@1 **70.0%** / @3 **76.7%** / @5 **80.0%**、NearMiss@5 **25.0%**、MRR **0.74**、EmptyAcc **100%**、errors **0**（来源 PR #525，已合并，squash 552efaad；与 v1 的 28 条基线不可混比）。基线更新是显式 PR 行为（规则见 `backend/docs/eval-gate-phase1.md`，数字口径见 `docs/METRICS.md`）。
2. **静默假成功清零（#301）** — 全仓 ~551 处异常捕获全量审计，32 条静默假成功全部闭环（登出 fail-closed、检索三态、文档状态机诚实化、统计端点降级身份……），每条修复都带回归测试。
3. **先红后绿 + 变异验证** — 每笔修复先写失败测试；修复后注入缺陷必须「精确变红」，证明测试真的有判别力，而不是顺手写个能过的断言。
4. **文档数字不变量** — SSE 事件数、端点模块数、前端路由数、工具数由源码派生，期望值统一放在 `docs/facts.json`，对外口径（值/口径/命令/日期/备注）登记在 `docs/METRICS.md`；`backend/scripts/check_doc_facts.py` 在 CI 逐 PR 校验「代码 ↔ facts JSON ↔ METRICS ↔ 文档」一致。前端路由按两种口径记录：34 条路由记录（29 条具名路由，含顶层与 redirect 记录）。
5. **棘轮只缩不涨** — 后端覆盖率下限 `--cov-fail-under=42` + 前端四道基线棘轮（硬编码中文 / i18n 完备性 / 命名一致 / 用键定义）：只拦下滑、不追高分，新增长即红。

加固收口（已完成）：Agent 记忆接口按用户隔离、`/files` 代理鉴权（非公开对象需登录 + 组织归属校验）、纯 ASGI 监控与限流中间件、移除 legacy `ci.yml`；另有工作流归属隔离、API Key 掩码、Web 抓取 SSRF 防护、上传路径穿越与 MinIO 删除、Agent 沙箱逃逸封堵、`execute_sql` 租户过滤、MCP 子进程环境变量白名单、登录失败锁定 + 用户枚举时序防护、PII 全链路掩码等安全修复。

历史快照与当前口径分离：`docs/legacy/` 归档旧评测产物（如 2026-06-15 的 `eval_first_report_2026-06-15.json`），仅用于对照，**不代表当前质量**，不参与门禁；沿革见 [`docs/legacy/README.md`](docs/legacy/README.md)。

---

## 合同核验：从一个真实业务场景切入

通用「聊天 + 知识库」很难验收；合同核验是一个能定义完成标准的窄场景：

**输入**：一份制度（或模板）+ 同一份合同的 V1 与 V2（有文本层的 PDF/DOCX）。
**固定核验项**：付款期限、起算条件、验收/发票前提、例外/暂缓/另有约定——只做这四类，不宣称「全合同审查」。
**系统做的事**：

- `PUT /documents/{id}/review-meta`：注册文档角色、版本族/版本序、生效期，服务端从 MinIO 对象流式计算 SHA-256；
- `GET /documents/{id}/readiness`：`reviewable/blocked` + 原因码（legacy/未索引/解析失败/缺版本身份/无原文）；
- `POST /reviews`：三份文档同组织、角色正确、版本序递增才能 `ready`；否则 `blocked` 落库并带 `side:reason`；
- 证据快照：建案/选择时把 parser 原文的 quote、哈希、条款/页码/字符区间固化成行——文档 later 被重建或删除，快照仍可读，并用 chunk 指纹 + 文档处理指纹标注 `live_source_stale`；
- `POST /reviews/{id}/analyze`：纯规则抽取（期限值/单位、起算事件、条件、例外/交叉引用）+ V1↔V2 对齐（unchanged/modified/added/removed/renumbered/unidentified）；日期片段与保修/质保期不会被误认为账期，单位存疑标 `ambiguous` 不猜值；
- 后续批次：逐项人工决策、导出带版本/哈希/覆盖范围的核验单（进行中）。

**明确不做**：不输出「合法/违法/合规/无风险/可签」等法律结论；`needs_human`/`insufficient` 不算完成；没有证据就不给结论。设计动机与已知缺口以模块注释与批次 PR 描述为准。

---

## 快速开始

### 前置要求

Docker Desktop（推荐），或手动安装：Python 3.11+ · Node.js 18+ · MySQL 8 · Redis 7 · Elasticsearch 8 · Kafka · MinIO。

### 1. 克隆

```bash
git clone https://github.com/sijie-Z/DocMind-RAG.git
cd DocMind-RAG/backend
```

### 2. 配置

```bash
# 本地开发（应用在宿主机跑）
cp .env.example .env               # Windows: copy .env.example .env
# 只要用到 Docker（含仅启动基础设施），都需要 .env.docker：
cp .env.docker.example .env.docker
```

- 本地开发必填：`SECRET_KEY`、`JWT_SECRET_KEY`（生成：`python -c "import secrets; print(secrets.token_hex(32))"`）。
- `.env.docker` 的 compose 必填项（不提供默认密码，全部自行生成）：`MYSQL_ROOT_PASSWORD`、`MYSQL_PASSWORD`、`MINIO_ACCESS_KEY`、`MINIO_SECRET_KEY`、`GRAFANA_PASSWORD`。
- 后端启动必填 `SECRET_KEY`、`JWT_SECRET_KEY`，Chat 需要 `DEEPSEEK_API_KEY`；Embedding 默认走本机 Ollama（nomic-embed-text / 768 维，复用 Chat Key），云端 Embedding 再配置 `EMBEDDING_API_KEY` / `EMBEDDING_API_URL`。
- Compose 的**全文件插值**先于服务选择：即使不选择 Grafana，`GRAFANA_PASSWORD` 仍为必填。

### 3. 启动基础设施

```bash
docker compose --env-file .env.docker up -d mysql redis elasticsearch minio kafka zookeeper
```

### 4. 启动后端（含迁移）

```bash
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
alembic upgrade head                              # schema guard 会在版本落后时拒绝启动（issue #495）
python -m app --profile web                       # 统一入口：web | worker | headless | sdk | acp
```

`python -m app --profile web` 会在启动期比对 `alembic_version` 与迁移 head，落后时打印 `current=... head=...` 与修复命令并以退出码 2 结束，而不是带病提供 500（`SCHEMA_GUARD`，`AUTO_MIGRATE` 默认关闭）。`uvicorn app.main:app` / `python run.py` 保留为兼容入口。

### 5. 启动前端

```bash
cd ../frontend && npm install && npm run dev      # http://localhost:5173
```

### 访问

| 地址 | 说明 |
|------|------|
| http://localhost:5173 | 前端界面 |
| http://localhost:8000/docs | Swagger API 文档 |
| http://localhost:8000/health | 健康检查（诊断视图） |
| http://localhost:8000/readyz | 就绪探针（DB/Redis/ES 依赖健康） |
| http://localhost:8000/metrics | Prometheus 指标 |

演示账号：`guest`（随机生成，首次创建时打印一次，或 `DEMO_PASSWORD` 指定，`ENABLE_DEMO_ACCOUNT=true` 时启用）；`admin` 通过 `reset_admin.py`（`ADMIN_PASSWORD`）创建。

---

## 目录结构

```
DocMind/
├── backend/
│   ├── app/
│   │   ├── main.py                  # FastAPI 兼容入口（组装在 bootstrap.py）
│   │   ├── agent/                   # PER Agent：loop / planner / executor / reflector
│   │   │   ├── events.py            #   SSE 事件类型（24 种）
│   │   │   ├── core_tools.py        #   基础工具（11 个）
│   │   │   └── tools/               #   扩展工具（20 个；合计 31 个内置工具）
│   │   ├── rag/                     # 检索管线：retriever / reranker / pipeline / cache
│   │   ├── api/v1/
│   │   │   ├── router.py            # 主路由（注册 29 个模块）
│   │   │   └── endpoints/           # 端点实现（29 个文件）
│   │   ├── services/                # 业务服务层（29 个服务）
│   │   ├── models/                  # ORM 模型（21 个模型文件）
│   │   ├── core/                    # 配置 / DB / Redis / ES / MinIO / Kafka / JWT
│   │   └── worker/                  # Kafka 消费者（解析 → 分块 → 嵌入 → 索引）
│   ├── tests/                       # 后端测试（单测 + API + 集成）
│   ├── alembic/                     # 数据库迁移
│   ├── benchmark/                   # 评测框架（eval gate）
│   └── docker-compose.yml           # 基础设施与全量编排
├── frontend/
│   └── src/
│       ├── views/                   # 页面（23 个页面模块，34 条路由记录 / 29 条具名路由）
│       ├── components/ · api/ · stores/ · router/ · composables/ · locales/
└── docs/                            # 架构页 / 使用手册 / 决策记录 / 指标口径
```

### 🌐 API 层 (`backend/app/api/v1/`)

29 个端点模块，统一 `/api/v1` 前缀：

| 模块 | 路由前缀 | 说明 |
|------|----------|------|
| 认证 | `/auth` | 注册、登录、刷新 Token、密码重置、登出 |
| 用户管理 | `/users` | 用户 CRUD、个人资料、头像、会话管理、审计日志（个人 API Key 端点已弃用） |
| API Key | `/api-keys` | `dm_` API Key 创建/列表/吊销（bcrypt 哈希、scope；issue #480） |
| 文件管理 | `/files` | 上传/下载/删除，MinIO 存储，分块上传 |
| 文档管理 | `/documents` | 文档解析、向量化、知识库索引、状态追踪 |
| 聊天 | `/chat` | 会话 CRUD、SSE 流式问答、严格模式、调试端点 |
| 知识库 | `/knowledge` | 知识库 CRUD、检索、统计、设置 |
| 合同核验 | `/reviews` | 核验案件/核验项/证据快照（原文证据 + 版本哈希；Batch 2） |
| 组织管理 | `/organizations` | 组织 CRUD、成员管理、角色分配 |
| 组织设置 | `/organizations` | 组织级设置读写与审计（与组织管理共用前缀） |
| 系统监控 | `/monitoring` | 性能指标、健康检查、告警、RAG 质量评估 |
| 通知 | `/notifications` | 通知 CRUD、已读标记、WebSocket 推送 |
| 提示词 | `/prompts` | 提示词模板 CRUD + 版本控制 |
| Token 用量 | `/token-usage` | 使用追踪 + 报告 |
| 操作手册 | `/manuals` | 系统手册管理 |
| 工作流 | `/workflows` | 工作流 CRUD、执行、调试、节点定义 |
| 定时任务 | `/scheduled-tasks` | 定时任务 CRUD、暂停/恢复与手动触发 |
| LLM 配置（已弃用） | `/llm-config` | 工作流全局配置入口已移除；端点标 deprecated，待统一 Provider 层落地后删除 |
| 技能库 | `/curated-skills` | 技能发现与管理 |
| Agent 记忆 | `/memory` | 短期/长期/工作/反思记忆 CRUD、语义搜索 |
| 智能 Agent | `/agent` | PER Agent 对话（SSE 流式）、工具调用、配置 |
| 会话分叉 | `/sessions` | 会话 fork / seed / 谱系（issue #476） |
| 持久终端 | `/terminal` | 容器持久 PTY：会话 CRUD、一次性 WS 票据、WebSocket 桥（issue #477） |
| Agent 命令 | `/commands` | 服务端命令清单与无模型轮次执行（/help、/status、/export、/tools） |
| MCP 工具 | `/mcp` | MCP 服务器状态（管理员只读） |
| JSON-RPC | `/rpc` | HTTP JSON-RPC 2.0（API Key + `rpc` scope；`RPC_HTTP_ENABLED` 门控） |
| 用户设置 | `/user` | 个人 UI 偏好 |
| Webhook 规则 | `/webhooks` | 入站 Webhook 规则管理、HMAC 验签投递与 outbox 处理（issue #478） |
| 示例数据 | `/demo` | 演示文档与数据填充 |

Swagger 文档：`http://localhost:8000/docs`（OpenAPI：`/api/v1/openapi.json`）。

---

## 设计取舍（面试最常问的五个问题）

1. **为什么是 PER，而不是单轮 ReAct？** 企业任务需要「先规划、可回放、能解释」。Planner 产出可审阅的步骤 DAG，Executor 依赖感知并行执行，Reflector 在结束前自检；每次运行都有 RunReport 与回放快照——出问题能复盘，而不是只剩一段聊天记录。
2. **为什么把 `original_text` 和检索增强文本分开？** 检索要的是召回率，引用要的是可核验。长文档会拼接摘要增强文本（E）参与评分，但证据出口只放解析原文（O），并用 `text_provenance` 标记来源；legacy 文档显式 409 `SOURCE_ORIGINAL_UNAVAILABLE`，绝不把增强文本伪装成原文。
3. **为什么「不能核验」是一个状态，而不是一个异常？** 合同核验里「文档缺版本身份/是 legacy/跨版本族」都是业务事实，必须让用户看到原因码（`side:reason`）并阻止流程继续；异常只留给真正的程序错误。`blocked` 案件不能选择证据、不能完成，更不会被导出成「已核验」。
4. **为什么评测门禁要进 CI？** 检索/生成质量的退化是静默的。把 80 条 golden set 的七项指标做成零容差门禁，任何下跌直接红；基线更新必须走显式 PR——把「质量不可静默回退」变成流程约束，而不是人的自觉。
5. **为什么合同核验先做规则 + 人工，而不是 LLM 抽取？** 先建立可判定的 gold 与证据链：规则只输出「期限值/单位、起算事件、条件、例外」这类可逐字段比对的文本事实，无法确定就标 `ambiguous`/`not_found`；人工逐项决策并留痕。等 golden 稳定后，再考虑用模型增强，而不是让模型直接产出不可复核的结论。

**Hooks 扩展点**（默认关闭）：进程内事件总线，`waterfall`（可改写/短路）与 `serial`（顺序观察）两种语义，`tools/pre_execute` 的 `ask` 复用 HITL 审批门且不可绕过；语义与边界见 [`docs/decisions/ADR-004-hooks.md`](docs/decisions/ADR-004-hooks.md)。更多决策记录见 [`docs/decisions/`](docs/decisions/)。

---

## 配置说明

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `DEEPSEEK_API_KEY` | LLM API Key | 必填 |
| `DEEPSEEK_API_URL` | LLM API 地址 | `https://api.deepseek.com` |
| `EMBEDDING_API_KEY` | Embedding API Key | 留空时复用 `DEEPSEEK_API_KEY` |
| `EMBEDDING_API_URL` | Embedding API 地址 | 本机 Ollama |
| `DATABASE_URL` | MySQL 连接串 | 部署时必填 |
| `VECTOR_DIMENSION` | 向量维度 | `768` |
| `SCHEMA_GUARD` / `AUTO_MIGRATE` | 启动期 schema 校验 / 兜底迁移 | `true` / `false` |
| `ENABLE_TRACING` | OpenTelemetry 开关 | `false` |
| `ENABLE_DEMO_ACCOUNT` | 启用演示账号 | `false` |
| `ENABLE_WORKFLOW_CODE_NODES` | 允许工作流代码节点（安全风险，默认关闭） | `false` |

完整配置见 `backend/.env.example` 与 `backend/.env.docker.example`；模型/向量维度默认值以 `backend/app/core/config/ai.py` 为真源（CPU/8G/16G 显存自动档位，显式配置优先）。

---

## 测试与评测

```bash
cd backend
python -m pytest tests/ -v --tb=short          # 全部后端测试
python -m pytest tests/ --cov=app --cov-report=html
make eval-ci                                    # 评测门禁（CI 同口径）
cd ../frontend && npm run test                  # 前端测试
```

测试基线（冻结快照 2026-10-15）：后端 `pytest tests/` **2223 passed / 51 skipped**（0 failed）；前端 **861 passed**（128 个测试文件）+ `vue-tsc --noEmit` 0 错误。数字随 CI 实时演进——CI 是唯一真源，本页只记录冻结快照；口径见 `docs/METRICS.md`。

---

## 部署

```bash
cd backend && docker compose --env-file .env.docker up -d
# 启动 migrate + backend + worker + mysql + redis + elasticsearch + minio
# + zookeeper + kafka + prometheus + grafana；migrate 成功退出后应用才启动
```

| 服务 | 端口 |
|------|------|
| FastAPI 后端 | 8000 |
| Vite 前端开发 | 5173 |
| MySQL | 3308 (Docker) / 3306 (本地) |
| Redis | 6390 (Docker) / 6379 (本地) |
| Elasticsearch | 9200 |
| MinIO API / Console | 9000 / 9001 |
| Kafka | 9092 / 29092 (内部) |
| Prometheus / Grafana | 9090 / 3001 |

K8s manifests 与部署说明见 [`deploy/`](deploy/)；Kafka 不可用时 `/documents/upload` 与重建接口自动降级为进程内处理，本地开发不会卡在 pending。

---

## 已知边界

- 工作流代码节点默认关闭，必须 `ENABLE_WORKFLOW_CODE_NODES=true` 且经 Docker 沙箱执行。
- GraphRAG 图谱不参与 RAG 检索链路；上下文压缩为规则式，无 LLM 调用；Agent 的短期/工作记忆为进程内，长期/反思记忆走 Redis。
- 合同核验当前覆盖 4 类固定核验项的中文合同；证据引用是 chunk 级 quote + 条款/页码/字符区间，不是 PDF 高亮。
- 评测 `mode=fake` 的指标用于门禁回归，不代表真实模型在线效果。

---

## 文档地图

| 文档 | 内容 |
|------|------|
| [`docs/USER_GUIDE.md`](docs/USER_GUIDE.md) | 5 分钟启动 → 上传第一份文档 → 带引用的回答 → Agent 工作台 |
| [`docs/architecture.html`](docs/architecture.html) | 交互式架构图（GitHub Pages：[sijie-z.github.io/DocMind-RAG](https://sijie-z.github.io/DocMind-RAG/)） |
| [`docs/METRICS.md`](docs/METRICS.md) | 所有对外数字的口径（值/命令/日期/备注） |
| [`docs/decisions/`](docs/decisions/) | ADR 决策记录 |
| [`backend/docs/eval-gate-phase1.md`](backend/docs/eval-gate-phase1.md) | 评测门禁规则与基线更新流程 |
| [研究仓库](https://github.com/sijie-Z/DocMind-Agent-Causal-Study) | 架构决策与实验数据 |

---

<p align="center">
  <sub>Built with ❤️ by the DocMind Team · MIT License</sub>
</p>
