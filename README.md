<p align="center">
  <img src="https://img.shields.io/badge/版本-v1.2.1-blue?logo=semver" alt="Version">
  <img src="https://img.shields.io/badge/Python-3.11+-blue?logo=python" alt="Python">
  <img src="https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi" alt="FastAPI">
  <img src="https://img.shields.io/badge/Vue_3-3.4-4FC08D?logo=vuedotjs" alt="Vue 3">
  <img src="https://img.shields.io/badge/DeepSeek-V4-8A2BE2" alt="DeepSeek">
  <img src="https://img.shields.io/badge/开源协议-MIT-green" alt="License">
  <img src="https://img.shields.io/badge/PRs-welcome-brightgreen" alt="PRs Welcome">
</p>

<div align="center">
  <h1>🤖 DocMind</h1>
  <p><strong>PER-based Agentic RAG System</strong></p>
  <p>基于 PER（Plan-Execute-Reflect）架构的企业级 AI Agent 系统 · 25+ 工具 · 自我进化</p>
  <p>
    <a href="https://sijie-z.github.io/DocMind-RAG/architecture.html">📊 交互式架构图</a> ·
    <a href="#-benchmark">📈 评测数据</a> ·
    <a href="#-quick-start">🚀 快速开始</a> ·
    <a href="https://github.com/sijie-Z/DocMind-RAG">GitHub</a>
  </p>
  <br>
</div>

---

## 📖 Overview · 概览

**DocMind** is an enterprise AI Agent system built on the **PER (Plan-Execute-Reflect)** architecture — an autonomous Agent with 25+ tools including RAG-based knowledge retrieval, web search, code execution, data analysis, multi-language translation, and more. The PER loop enables complex multi-step reasoning with self-correction and memory.

**DocMind** 是一个基于 **PER（规划-执行-反思）** 架构的企业级 AI Agent 系统。拥有 25+ 个内置工具，涵盖 RAG 知识检索、联网搜索、代码执行、数据分析、多语言翻译等能力。PER 循环使其具备复杂多步推理、自我纠错和经验积累的能力。

### Key Metrics · 核心指标

| Metric | Value |
|--------|-------|
| 🧠 **Agent Architecture** | PER (Plan → Execute → Reflect → Learn) |
| 🔧 **Built-in Tools** | 25+ (knowledge, web, code, analysis, translation, MCP...) |
| 📚 **Test Cases** | 422+ tests across 25 files |
| 🌐 **Languages** | 中文 / English / 日本語 / Français |
| 📊 **Benchmark** | 30 questions, 69% keyword coverage, 60% success rate |
| 🚀 **Deploy** | Docker Compose + Kubernetes |
| 📈 **Observability** | Langfuse full-trace |
| 🧬 **Self-Improving** | Experience Memory, Execution Replay, Pattern Mining |

### Product Positioning · 产品定位

> **"文档太多，找不到、看不完、分析不过来。"**
>
> DocMind 解决的核心问题：上传文档，既能**问答找答案**，也能**自动分析出结论**。

| 能力 | 一句话 | 解决谁的什么问题 |
|------|--------|----------------|
| **A - 知识问答** | 问什么答什么，带来源 | 普通员工：文档太多找不到信息 |
| **B - 智能分析** | 说需求，自动读文档出结论 | 分析师：多份报告看不完、对比不过来 |

---

## 🧠 Why PER Agent instead of RAG? · 为什么用 Agent 而非纯 RAG？

Most "RAG systems" stop at retrieval. DocMind's PER Agent goes further — RAG is **one tool** in a 25+ tool arsenal, invoked only when the Agent decides it's needed.

大多数 "RAG 系统"止步于检索。DocMind 的 PER Agent 走得更远——RAG 只是 **一个工具**，Agent 在需要时才调用它。

| 任务场景 | RAG Only | PER Agent |
|----------|:--------:|:---------:|
| "Find the revenue in this annual report" | ✅ 直接检索 | ✅ Agent 使用知识工具 |
| "Compare gross margins across 3 competitors" | ❌ 无法跨文档推理 | ✅ Agent 多步检索 + 合成 |
| "SWOT analysis of Company A" | ❌ 无法应用框架 | ✅ Agent 选择 SWOT → 提取 → 结构化 |
| "What changed in 2024 data regulation vs 2023?" | ❌ 无法对比差异 | ✅ Agent 分别检索 → 对比 → 总结 |
| "Search the web for latest AI funding, then assess" | ❌ 无网络访问 | ✅ Agent 联网搜索 → 阅读 → 分析 |
| "Document ID not found — what else do you have?" | ❌ 无法恢复 | ✅ Agent 列出可用文档，推荐替代 |
| "Analyze apples." (ambiguous) | ❌ 无法澄清 | ⚠️ 两者均遇歧义极限 |

**RAG finds information. The Agent plans, selects tools, cross-references, and verifies results.**

**RAG 负责"找得准"，Agent 负责"想得透"。**

---

## 📊 Benchmark · 评测结果

30-question evaluation comparing **PER Agent** against a **RAG-only Baseline** on enterprise knowledge tasks. [Benchmark v1] — frozen, reproducible.

30 道企业知识任务的评测，对比 **PER Agent** 和 **纯 RAG 基线**。[Benchmark v1] — 结果固定，可复现。

| Metric | Baseline (RAG only) | PER Agent | Change |
|--------|:-------------------:|:---------:|:------:|
| **Keyword Coverage** | 63% | **69%** | +6% |
| **Success Rate** | 15/30 (50%) | **18/30 (60%)** | +10% |
| **Avg Duration** | 20s | 36s | +16s (more tools) |
| **Tool Failures** | 0.0 | **0.0** | ✅ Reliable |

### Per-Scenario Breakdown · 分场景分析

| Scenario | Baseline | PER Agent | Gain | Why Agent Wins |
|----------|:--------:|:---------:|:----:|----------------|
| Single Document Retrieval | 94% | **100%** | +6% | 更精准的文档定位 |
| **Cross-Document Analysis** | 65% | **77%** | **+12%** | 多步检索覆盖更多文档 |
| **Framework Analysis** (SWOT/PEST/DuPont) | 56% | **80%** | **+24%** | 正确选择工具 + 框架 |
| Multi-Step Reasoning | 85% | **90%** | +5% | 基线已强；Agent 更稳定 |
| Web Search Integration | 75% | **88%** | **+12%** | 真实 DuckDuckGo 调用 |
| Tool Recovery | 72% | 67% | -6% | Agent 可能在重试时过度复杂化 |
| Edge Cases | 50% | 38% | -12% | Agent 对边界查询过度处理 |
| Ambiguity (L2) | 0% | 0% | — | 系统极限 |

> **Key insight**: Agent's biggest gains are in **cross-document analysis** (+12%), **framework reasoning** (+24%), and **web search** (+12%) — precisely the tasks where RAG alone falls short. The 7 failures are all L2 ambiguity/boundary questions (0% infrastructure noise).

### Optimization Journey · 优化历程

```
  Agent v1                     Agent v2
  ─────────                    ─────────
  46% coverage   ──→   69% coverage   (+23pp ✅)
  8/30 success   ──→  18/30 success   (+10 ✅)
  89s avg        ──→  36s avg         (-60% ✅)
  1.0 tool fail  ──→  0.0 tool fail   (zeroed ✅)
```

**How It Happened · 过程：**

```
① Agent v1 Benchmark (46%)
    ↓
② Failure Collection — 分类每个失败原因
    ├─ APIConnectionError
    ├─ Timeout (无退避)
    ├─ Redis 冷启动未初始化
    └─ 工具调用失败
    ↓
③ Langfuse Trace — 追踪每个失败到根因
    ↓
④ Runtime Fixes
    ├─ 指数退避重试
    ├─ Redis/ES 客户端懒初始化
    ├─ 工具错误传播 → 优雅降级
    └─ 按工具类型配置超时
    ↓
⑤ Re-benchmark → Agent v2 (69%)
```

This is not a model improvement — it's an **engineering improvement**. The 23pp gain came entirely from reliability fixes, not from changing the LLM or prompt.

这不是模型改进——这是**工程改进**。23 个百分点的提升完全来自可靠性修复，而非更换模型或修改提示词。

---

## 🧬 Self-Improving Agent · 自我进化

DocMind's most advanced capability: the Agent learns from its own execution history, remembers mistakes, replays past runs for analysis, and discovers recurring patterns that become new skills.

DocMind 最先进的能力：Agent 从自身执行历史中学习，记住错误，回放过去的运行进行分析，并发现可成为新技能的重复模式。

### Three-Stage Learning Pipeline · 三阶段学习流水线

```
Execution History
    ↓
① Experience Memory — 从失败中学习
    ↓
② Execution Replay — 分析发生了什么
    ↓
③ Pattern Mining — 发现重复工作流
    ↓
    Skill Recommendations
```

### ① Experience Memory · 经验记忆

When a benchmark question fails, the system automatically extracts a structured "experience" — what scenario failed, what symptom it showed, and what lesson the Planner should follow.

当评测问题失败时，系统自动提取结构化的"经验"——什么场景失败、什么症状、规划器应遵循什么教训。

```
Benchmark Failure (L1-FRAME-01: SWOT analysis missing)
    ↓
Extractor analyses: category=framework, keywords_missed=[优势,劣势,机会,威胁]
    ↓
Structured Experience generated:
    scenario:    framework_analysis
    symptom:     keywords_missing_swot
    lesson:      "SWOT framework must output all 4 dimensions"
    confidence:  0.90
    applicable:  [framework_analysis]
    avoid_for:   [edge_case_simple]
    ↓
Stored in Redis + local JSON → retrieved at next planning session
```

- **18 experiences** extracted from benchmark failures
- Negative Transfer protection (metadata ensures experiences are only injected into appropriate scenarios)
- **Verified impact**: Coverage improved from 68.4% → 70.1% with Experience Memory enabled (+1.7%)

### ② Execution Replay · 执行回放

Every agent execution is automatically saved as a structured snapshot — a "flight recorder" that captures each plan step, tool call, intermediate result, and decision.

每次 Agent 执行自动保存为结构化快照——捕获每个规划步骤、工具调用、中间结果和决策。

```bash
python -m benchmark.replay <task_id>          # replay a single execution
python -m benchmark.replay --diff <a> <b>     # compare two versions
python -m benchmark.replay --list              # browse all saved runs
```

**Replay output example · 回放输出示例：**
```
Execution Replay: 15cae5c15e5e
  Query:  从知识库中找一份企业年报，提取营收数据
  Steps:  2 completed, 0 failures, 36.2s

  ✅ Step 1: search_knowledge_base  (8.6s)
     → Found 3 documents matching "年报"
  ✅ Step 2: list_documents         (11.7s)
     → Retrieved: 星辰科技 2024 年度报告
```

- **49 execution snapshots** saved, replayable at any time

### ③ Pattern Mining & Skill Discovery · 模式挖掘与技能发现

The Pattern Miner scans all saved Replay snapshots and identifies recurring tool-use sequences. High-frequency, high-success patterns become Skill Recommendations.

模式挖掘器扫描所有保存的回放快照，识别重复的工具使用序列。高频、高成功率的模式成为技能推荐。

```bash
python -m app.agent.mining.report           # view recommendations
python -m app.agent.mining.report --save    # persist as report
```

**发现结果（来自 47 次执行）：**
```
Top patterns found:
  list_documents                             14 times
  search_knowledge_base                      14 times
  search_knowledge_base → list_documents      5 times  ⭐
  get_current_time → web_search               3 times  ⭐
```

**Skill Recommendations · 技能推荐：**

| Skill | Pattern | Confidence | Observations |
|-------|---------|:----------:|:-----------:|
| `document_discovery` | `search → list_documents` | 70% | 5 |
| `get_web_workflow` | `get_current_time → web_search` | 63% | 3 |

### The Evolution Path · 进化路径

```
v1 → v2:   Manual fix (human analyses → human fixes → re-benchmark)
v2 → v3:   Experience Memory (auto-extract → auto-inject → benchmark)
v3 → v4:   Replay + Pattern Mining (observe → analyse → recommend)
Future:    Skill Auto-Registration (autonomous skill evolution)
```

---

## 🏗 System Architecture · 系统架构

### 5-Layer Architecture · 五层架构

```
┌─────────────────────────────────────────────────────────────┐
│                     表现层 (Presentation)                    │
│         Vue 3 + Naive UI + ECharts + Vue Flow              │
├─────────────────────────────────────────────────────────────┤
│                   API 网关层 (API Gateway)                   │
│          FastAPI + JWT + CORS + Rate Limit + SSE            │
├─────────────────────────────────────────────────────────────┤
│                   AI Agent 核心层 (Agent Core)               │
│   PER Loop │ Tool Registry │ Context Engine │ Skill Library │
│       │              ↑                        │             │
│       ↓              │                        ↓             │
│   RAG Pipeline │ Knowledge Graph │ Workflow Engine │ Doc Mgt │
├─────────────────────────────────────────────────────────────┤
│                    AI / LLM 层 (Intelligence)               │
│   DeepSeek V4 │ Embedding │ Reranker │ Langfuse SDK         │
├─────────────────────────────────────────────────────────────┤
│                   数据存储层 (Data Storage)                   │
│  MySQL 8 │ Elasticsearch 8 │ Redis 7 │ Kafka │ MinIO       │
└─────────────────────────────────────────────────────────────┘
```

> Open `docs/architecture.html` for the interactive diagram. 打开 `docs/architecture.html` 查看交互式架构图。

### PER Agent: Plan → Execute → Reflect

DocMind's core differentiator — a three-phase architecture that surpasses traditional ReAct:

DocMind 的核心差异化——超越传统 ReAct 的三阶段架构：

| | ReAct | PER (本项目) |
|---|---|---|
| 规划 (Planning) | 无，边想边做 | **先规划，再执行** — 生成完整 DAG |
| 执行 (Execution) | 单步串行 | **多步并行**，按依赖图执行 |
| 反思 (Reflection) | 无 | **执行完整体评估**，可触发重试/重规划 |
| 记忆 (Memory) | 无 | 持久化记忆 + 经验积累 |
| 子任务 (Sub-task) | 无 | 支持子 Agent 委派 |

```
用户提问
   ↓
┌──────────────────────────────────────────────┐
│  Phase 1: 规划 (Planner)                     │
│  • 分析任务意图                              │
│  • 制定分步执行计划（含依赖关系）              │
│  • 为每步推荐最佳工具                         │
├──────────────────────────────────────────────┤
│  Phase 2: 执行 (Executor)                    │
│  • 按计划顺序调用工具（25+ 内置工具）          │
│  • 独立步骤并行执行 (asyncio.gather)          │
│  • 每步结果经 LLM 自然语言合成                │
│  • 失败自动重试（指数退避，最多 3 次）          │
├──────────────────────────────────────────────┤
│  Phase 3: 反思 (Reflector)                   │
│  • 审查执行结果是否满足原始需求               │
│  • 检测错误/幻觉/缺漏/矛盾                   │
│  • 必要时触发重新规划或局部修复                │
└──────────────────────────────────────────────┘
   ↓
SSE 流式返回最终答案（含规划推理 + 执行过程 + 引用溯源）
```

### RAG Pipeline (Agent's Core Tool)

```
用户提问
   |
   v
┌─ 查询分析 ──────────────────────────────┐
│  QueryComplexityClassifier 判断复杂度     │
│  ├─ simple     → 仅关键词检索            │
│  ├─ medium     → 混合检索（默认）         │
│  └─ complex    → 混合 + HyDE + 查询改写  │
└──────────────────────────────────────────┘
   |
   v
┌─ 双路检索 ───────────────────────────────┐
│  关键词检索（ES multi_match + BM25）      │
│  向量检索（ES dense_vector + cosine)      │
│  复杂模式：HyDE 伪文档 + 多查询改写       │
└──────────────────────────────────────────┘
   |
   v
┌─ 结果融合 ───────────────────────────────┐
│  RRF（Reciprocal Rank Fusion, k=60）      │
│  MMR 多样性选择（λ=0.65）                 │
│  同文档去重（每文档最多 2 个块）           │
└──────────────────────────────────────────┘
   |
   v
┌─ 重排序 ────────────────────────────────┐
│  Cross-Encoder Rerank（优先智谱 rerank） │
│  兜底：LLM 重排                          │
└──────────────────────────────────────────┘
   |
   v
┌─ 生成回答 ──────────────────────────────┐
│  LLM + 检索上下文 → 带引用的回答          │
└──────────────────────────────────────────┘
```

**Quality Assurance · 质量保障：**

| 机制 | 作用 |
|------|------|
| 语义分块 (Semantic Chunking) | 分块边界落在语义转折处 |
| Contextual Retrieval | 块携带文档级上下文，避免"断章取义" |
| 查询复杂度自适应 | 简单问题不走复杂检索，节省 LLM 成本 |
| HyDE + Multi-Query | 查询改写解决"问法和文档写法不一致" |
| RRF 融合 | 关键词 + 向量双路结果排序融合 |
| MMR 多样性 | 避免返回结果同质化 |
| Cross-Encoder Rerank | 对 Top-K 结果精确重排，+30% 检索精度 |
| 语义缓存 (Semantic Cache) | 相似查询直接命中缓存，相似度 ≥0.92 |
| 上下文压缩 (Context Compression) | 控制输入 LLM 的 token 量 |

---

## 🔭 Observability · 可观测性 (Langfuse)

Every agent execution is traced through Langfuse:

每次 Agent 执行都通过 Langfuse 全链路追踪：

- **Full trace visibility**: plan steps, tool calls, LLM completions, timings
- **Failure classification**: API errors, timeouts, tool failures categorised automatically
- **Cost tracking**: per-conversation token usage and latency
- **Benchmark integration**: each benchmark question generates a trace
- **5 observation points**: registry, memory, planner, executor, reflector

---

## 🔌 MCP Bridge

DocMind can connect to external MCP (Model Context Protocol) servers, extending its toolset beyond built-in capabilities:

DocMind 可连接外部 MCP 服务器，扩展内置工具集之外的生态：

- **GitHub MCP Server** — repository operations, code search, PR management
- **Filesystem MCP Server** — file read/write access
- **Custom MCP servers** — any service exposing MCP tools

MCP tools are registered into the same Tool Registry as native tools, with the same permission and audit controls.

---

## ✨ Features · 功能特性

### 🤖 PER Agent (Core Differentiator · 核心差异)

| Feature | Description |
|---------|-------------|
| **PER 3-Phase Architecture** | Plan → Execute → Reflect, DAG decomposition + parallel tools + self-correction |
| **25+ Built-in Tools** | Knowledge retrieval, web search, document parsing, summarisation, deep analysis, code execution, translation, and more |
| **Feishu Integration** | Feishu bitable document sync and query (飞书文档接入) |
| **Tool Registry** | Unified registration, auth, sandbox isolation, audit trail |
| **Context Engine** | Multi-turn memory management, automatic token budget (system 2K / dialog 8K / tools 4K) |
| **Thinking Stream** | Real-time frontend visualisation of every Agent reasoning step |
| **Task Decomposition** | Complex tasks automatically broken into multi-step execution plans |
| **Self-Improvement** | Experience Memory, Execution Replay, Pattern Mining → Skill Discovery |
| **SSE Streaming** | 12 event types: thinking, plan, tool_call, reflection, chunk, done... |

#### Built-in Tools · 内置工具

| Tool | Description |
|------|-------------|
| `🔎 search_knowledge_base` | Hybrid search over enterprise knowledge base with relevance scoring |
| `🔎 vector_search` | Semantic vector search for related paragraphs |
| `📄 extract_insights` | Entity, metrics, claims, structure extraction from documents |
| `📊 cross_document_analysis` | Multi-document pattern analysis (common themes, differences, trends) |
| `📝 generate_report` | Polished markdown report generation from analysis data |
| `📝 summarize_document` | Long document summarisation |
| `🔑 extract_keywords` | Keyword extraction for tagging |
| `🗂️ list_documents` | List all accessible documents |
| `ℹ️ get_document_info` | Document metadata details |
| `🌐 web_search` | Real-time DuckDuckGo search to supplement knowledge gaps |
| `⌨️ code_execution` | Sandboxed Python execution for data analysis |
| `📊 data_analysis` | Data analysis toolkit |
| `🔗 content_crawling` | Web page fetching with automatic cleanup |
| `🌍 translation` | Chinese/English/Japanese/French, document and segment levels |
| `🧭 knowledge_graph` | Entity-relationship exploration, interactive browsing |
| `🔌 mcp_call` | External MCP server tool invocation |
| `📋 list_conversations` | Conversation history |
| `🔄 batch_processing` | Large dataset chunking with progress tracking |
| ... and more! |

### 💬 Smart Chat · 智能对话

- **SSE Streaming**: Token-level real-time display, typewriter effect
- **Multi-turn**: Conversation history awareness with session management
- **Agent Mode**: Agent decides when to use RAG or other tools
- **Citation Links**: `[1]` `[2]` references, click to view source
- **Markdown Rendering**: Code highlighting, LaTeX, tables, flowcharts
- **Export**: Conversations exportable as Markdown

### 🔗 Knowledge Graph · 知识图谱

- Canvas force-directed graph visualisation
- 7 entity types extracted automatically (Person, Organisation, Location, Technology, Concept, Event, Product)
- Interactive: drag, zoom, click for details, keyword filter

### ⚙️ Visual Workflow Editor · 可视化工作流编辑器

- Drag-and-drop DAG builder (Vue Flow based)
- **Node types**: LLM, API call, code execution, condition, smart routing, memory, data transform
- **Real-time debug**: execution trace drawer, node status colour coding
- **DAG Engine**: Kahn topological sort + DFS cycle detection, auto-optimised execution order

### 🏢 Enterprise Features · 企业级特性

| Feature | Description |
|---------|-------------|
| **RBAC** | User → Role → Organisation 3-tier multi-tenancy |
| **JWT Auth** | Token auth + 24h/7d dual-token mechanism |
| **Audit Log** | Full operation audit trail, compliance-ready |
| **Prometheus** | Request volume, latency, error rate, Agent tool call stats |
| **Grafana** | Pre-built dashboards (API perf, Agent stats, system resources) |
| **OpenTelemetry** | Distributed tracing |
| **i18n** | 中文 / English / 日本語 / Français, instant switch |

---

## 🛠 Tech Stack · 技术栈

| Layer | Technology | Notes |
|-------|-----------|-------|
| **Backend** | FastAPI + Uvicorn | Fully async, auto Swagger |
| **Database** | MySQL 8 + SQLAlchemy 2.0 | Async ORM + Alembic migrations |
| **Cache** | Redis 7 | Semantic cache + token blacklist + session store |
| **Search** | Elasticsearch 8 | KNN vector + BM25 keyword search |
| **Message Queue** | Kafka (aiokafka) | Async document processing pipeline |
| **Object Storage** | MinIO | S3-compatible document file storage |
| **LLM** | DeepSeek V4 (Flash/Pro) | Reasoning + deep analysis |
| **Embedding** | OpenAI-compatible API | 2048-dim vector embeddings |
| **Agent Architecture** | PER 3-phase | Plan → Execute → Reflect, DAG parallel scheduling |
| **Observability** | Langfuse | Full trace, failure classification, cost tracking |
| **MCP** | MCP Protocol Bridge | GitHub, Filesystem, custom servers |
| **Document** | LangChain + PyPDF + python-docx | Multi-format smart chunking |
| **Frontend** | Vue 3.4 + TypeScript 5.3 + Vite 5 | Composition API + type safety |
| **UI** | Naive UI + ECharts + Vue Flow | Enterprise components + charts + flow |
| **State** | Pinia | Vue 3 official |
| **i18n** | Vue I18n | zh/en/ja/fr |
| **Monitoring** | Prometheus + Grafana + OpenTelemetry | Metrics + dashboards + tracing |
| **Security** | JWT + RBAC + Multi-tenancy + Audit | Enterprise security |
| **Container** | Docker + Docker Compose + K8s | Dev/test/prod coverage |
| **CI/CD** | GitHub Actions | Test + lint + build + security scan |

---

## 🚀 Quick Start · 快速开始

### Prerequisites · 前置要求

- **Docker Desktop** (recommended) — one-click infrastructure
- Or manual: Python 3.11+, Node.js 18+, MySQL 8, Redis 7, Elasticsearch 8, Kafka, MinIO

### 1. Clone · 克隆

```bash
git clone https://github.com/sijie-Z/DocMind-RAG.git
cd DocMind-RAG
```

### 2. Start Infrastructure · 启动基础设施

```bash
cd backend
docker compose up -d
```

> Starts MySQL, Redis, Elasticsearch, Kafka, MinIO (~30s).

### 3. Configure · 配置

```bash
cp .env.docker.example .env.docker
```

Edit `.env.docker`:

```env
# LLM (DeepSeek / OpenAI-compatible)
DEEPSEEK_API_KEY=sk-your-api-key-here

# Embedding model
EMBEDDING_API_KEY=your-embedding-api-key

# Rerank model (optional)
RERANK_API_KEY=your-rerank-api-key

# Langfuse (optional, for observability)
LANGFUSE_PUBLIC_KEY=pk-...
LANGFUSE_SECRET_KEY=sk-...
```

### 4. Start Backend · 启动后端

```bash
cd backend
python -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 5. Start Frontend · 启动前端

```bash
cd frontend
npm install
npm run dev                      # Port 5173
```

### 6. Open App · 访问应用

| URL | Description |
|-----|-------------|
| http://localhost:5173 | Frontend UI · 前端界面 |
| http://localhost:8000/docs | Swagger API Docs · API 文档 |
| http://localhost:8000/health | Health check · 健康检查 |

### Demo Accounts · 演示账号

| Username | Password | Role |
|----------|----------|------|
| `guest` | `123456` | User |
| `admin` | `admin123` | Admin |

### 7. Seed Sample Data · 导入示例数据 (Optional)

```bash
cd backend
python seed_docs/seed.py
```

> Imports 2 sample documents to test Agent analysis immediately. 导入 2 份示例文档，立即测试 Agent 分析。

### Run Benchmark · 运行评测

```bash
# Baseline (RAG only)
python -m benchmark.run --questions benchmark/questions/v2.json --mode baseline

# PER Agent
python -m benchmark.run --questions benchmark/questions/v2.json --mode agent

# Compare results
python -m benchmark.run --compare benchmark/results/baseline_v2.json benchmark/results/agent_v2.json

# Experience Memory A/B test
python -m benchmark.run --mode agent --no-experience --output results/agent_no_exp.json
python -m benchmark.run --mode agent --experience --output results/agent_with_exp.json
python -m benchmark.run --compare results/agent_no_exp.json results/agent_with_exp.json
```

### Replay & Analyse · 回放与分析

```bash
# List all saved replays
python benchmark/replay.py --list

# Replay a specific execution
python benchmark/replay.py <task_id>

# Diff two versions
python benchmark/replay.py --diff <task_a> <task_b>

# Generate Skill Recommendation Report
python -m app.agent.mining.report --save
```

---

## 📁 Project Structure · 项目结构

```
DocMind/
├── backend/                          # Backend · 后端
│   ├── app/
│   │   ├── api/v1/endpoints/         # REST API (17 modules)
│   │   ├── agent/                    # PER Agent core
│   │   │   ├── loop.py               #   Main PER loop
│   │   │   ├── planner.py            #   Planner - task decomposition
│   │   │   ├── executor.py           #   Executor - tool orchestration
│   │   │   ├── reflector.py          #   Reflector - quality check
│   │   │   ├── registry.py           #   Tool registry
│   │   │   ├── context.py            #   Context engine
│   │   │   ├── events.py             #   SSE event model
│   │   │   ├── observability.py      #   Langfuse integration
│   │   │   ├── exec_context.py       #   Execution context (flight recorder)
│   │   │   ├── experience/           #   Self-improving: learn from failures
│   │   │   ├── replay/               #   Execution replay engine
│   │   │   ├── mining/               #   Pattern mining & skill discovery
│   │   │   └── tools/               #   Tool implementations (10+ modules)
│   │   ├── core/                    # Infrastructure (config, DB, ES, Redis)
│   │   ├── models/                  # SQLAlchemy ORM
│   │   ├── rag/                     # RAG pipeline
│   │   ├── schemas/                 # Pydantic schemas
│   │   ├── services/                # Business logic
│   │   └── worker/                  # Kafka async document processor
│   ├── tests/                       # 422+ test cases (25 files)
│   ├── benchmark/                   # Benchmark framework
│   │   ├── questions/               #   30 benchmark question sets
│   │   ├── results/                 #   Baseline & Agent result reports
│   │   ├── cases/                   #   Per-question case files
│   │   ├── run.py                   #   Benchmark runner
│   │   └── scorer.py                #   Scorer & classification
│   └── seed_docs/                   # Sample documents
├── frontend/                        # Vue 3 frontend · 前端
│   └── src/
│       ├── api/                     # API clients
│       ├── components/agent/        # Agent components (PlanTree, ThinkingStream, etc.)
│       ├── stores/                  # Pinia state
│       └── views/                   # Pages (chat, agent, knowledge, workflow, dashboard)
├── deploy/k8s/                      # Kubernetes manifests
├── docs/
│   ├── architecture.html            # Interactive architecture diagram · 交互式架构图
│   ├── product-definition.md        # Product definition · 产品定义
│   └── roadmap.md                   # Development roadmap · 开发路线图
└── .github/workflows/ci.yml         # CI/CD
```

---

## 🧪 Testing · 测试

```bash
# Backend · 后端 (422+ test cases, 25 files)
cd backend
python -m pytest tests/ -v --tb=short

# Coverage · 覆盖率
cd backend
python -m pytest tests/ --cov=app --cov-report=html

# One-shot check · 一键检查
make test
make lint
```

---

## 🚢 Deployment · 部署

| Method | Description | Command |
|--------|-------------|---------|
| **Docker Compose** | Single machine · 单机部署 | `cd backend && docker compose up -d` |
| **Kubernetes** | Cluster · 集群部署 | `kubectl apply -f deploy/k8s/` |
| **Manual** | Custom env · 自定义环境 | See `deploy/README.md` |

---

## 📝 Version History · 版本历史

See [CHANGELOG.md](CHANGELOG.md) for full details.

| Version | Date | Key Changes |
|---------|------|-------------|
| **v1.2.1** | 2026-05-24 | Agent crash fix, parallel execution, page transitions, ErrorBoundary |
| **v1.2.0** | 2026-05-24 | PER Agent architecture, 25+ tools, deep analysis, SSE pipeline |
| **v1.1.0** | 2026-05-17 | Agent mode toggle, sample docs, CJK tokenisation fix |
| **v1.0.0** | 2026-05-17 | First release: RAG pipeline, workflow editor, knowledge graph |

---

## 🤝 Contributing · 贡献指南

Issues and PRs welcome! See [CONTRIBUTING.md](CONTRIBUTING.md)

欢迎提交 Issue 和 PR！详见 [CONTRIBUTING.md](CONTRIBUTING.md)

Conventions:
- Backend: Python 3.11+, ruff code style
- Frontend: TypeScript strict mode, ESLint + Prettier
- Commits: Conventional Commits

---

## 📄 License · 开源协议

MIT License — see [LICENSE](LICENSE)

---

## 🔗 Links · 链接

- **Architecture Diagram**: [GitHub Pages](https://sijie-z.github.io/DocMind-RAG/architecture.html)
- **GitHub**: [sijie-Z/DocMind-RAG](https://github.com/sijie-Z/DocMind-RAG)
- **API Docs**: http://localhost:8000/docs (when running)
- **Issues**: [GitHub Issues](https://github.com/sijie-Z/DocMind-RAG/issues)
- **Benchmark v1**: tagged `benchmark-v1`

---

<p align="center">
  <strong>DocMind</strong> — PER-based Agentic RAG System
  <br>
  <sub>Built with ❤️ by the DocMind Team</sub>
  <br>
  <sub>基于 PER 架构的企业级 AI Agent 系统</sub>
</p>
