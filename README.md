<p align="center">
  <a href="README_EN.md">🇺🇸 English</a> · <strong>🇨🇳 中文</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/版本-v1.2.1-blue?logo=semver" alt="Version">
  <img src="https://img.shields.io/badge/Python-3.11+-blue?logo=python" alt="Python">
  <img src="https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi" alt="FastAPI">
  <img src="https://img.shields.io/badge/Vue_3-3.4-4FC08D?logo=vuedotjs" alt="Vue 3">
  <img src="https://img.shields.io/badge/DeepSeek-V4-8A2BE2" alt="DeepSeek">
  <img src="https://img.shields.io/badge/开源协议-MIT-green" alt="License">
</p>

<div align="center">
  <h1>🤖 DocMind</h1>
  <p><strong>企业级 AI Agent 系统 / 实验驱动的 Agent 研究平台</strong></p>
  <p>
    <a href="https://sijie-z.github.io/DocMind-RAG/architecture.html">📊 交互式架构图</a> ·
    <a href="docs/research/001_dag_vs_serial.md">📋 实验 001</a> ·
    <a href="docs/research/004_structured_planning.md">📋 实验 004</a>
  </p>
</div>

---

## 📊 研究发现（核心价值）

DocMind 是一个 **实验驱动的 Agent 系统研究平台**。通过 5 轮控制实验，得到三个反直觉结论：

### Finding 1: DAG 执行器 ≈ 串行执行器（当 avg_steps ≈ 1）

| 指标 | DAG | Serial | Δ |
|------|:---:|:------:|:-:|
| 覆盖率 | 66% | 69% | −3pp |
| 平均步骤数 | 1.2 | 1.0 | +0.2 |

**结论**：在 Planner 平均只生成 1.2 步的情况下，DAG 的并行能力从未被激活。两种执行架构没有区别。

> 详细报告：[001 — DAG vs Serial Execution](docs/research/001_dag_vs_serial.md)

---

### Finding 2: LLM Planner 无法被 Prompt 诱导拆分

在系统提示中明确要求"尽可能细粒度拆分"，Planner 仍然平均只生成 1.0-1.2 步。

| Mode | 系统提示指令 | 预期步骤 | 实际步骤 |
|:----:|-------------|:--------:|:--------:|
| coarse | "请一步完成" | ~1.5 | **1.0** |
| normal | 无额外指令 | ~4 | **1.1** |
| fine | "尽可能细分" | ~10 | **1.2** |

**结论**：现代 LLM 倾向于一步完成任务，因为这对模型来说成本更低。Planner 退化为 Query Rewriter，而不是 Task Decomposer。

> 详细报告：[003 — Planner Compliance](docs/research/003_planner_compliance.md)

---

### Finding 3: 结构化规划将覆盖率从 69% 提升至 93%

用规则化模板替代 LLM Planner，根据任务类型（comparison / analysis / research）生成固定的多步骤计划。

| 指标 | 实验 001（基线） | **结构化规划** | Δ |
|------|:--------------:|:-------------:|:-:|
| L1 覆盖率 | ~69% | **93.1%** | **+24pp** |
| L1 失败数 | 5-6 | **0** | 全部消除 |
| 平均步骤数 | 1.2 | **3.4** | +2.2 |
| 平均耗时 | ~30s | 59s | +29s |

**关键发现**：DAG 在数据层面展示了价值——4-7 步的计划在并行执行中耗时未线性增长（步骤数 +180%，耗时 +95%）。

> 详细报告：[004 — Structured Planning](docs/research/004_structured_planning.md)

---

### 最终结论

> 在 DocMind 的任务分布上，**规划质量远比执行架构重要**。
>
> 步骤 1.2 → 3.4 带来的覆盖率提升（+24pp）远大于 DAG vs Serial 的选择（−3pp）。
> 正确的思路是："别让 LLM 决定拆不拆。你来决定。"

**完整研究闭环：**

```
001: DAG ≈ Serial  (avg_steps=1.2, 并行未被激活)
  ↓
002/003: Planner 无法被诱导拆分  (LLM 倾向一步完成)
  ↓
004: 结构化规划  (steps 1.2→3.4, coverage 69%→93%)
  ↓
005: DAG ≈ Serial (再测)  (规划质量 > 执行架构)
```

---

## 🏗 系统架构

PER（Plan-Execute-Reflect）三阶段 Agent 架构 + 25+ 内置工具：

```
┌─ 表现层 ──────────────────────────────────────┐
│  Vue 3 + Naive UI + ECharts + Vue Flow        │
├─ API 网关 ─────────────────────────────────────┤
│  FastAPI + JWT + CORS + SSE                   │
├─ AI Agent 核心 ────────────────────────────────┤
│  PER Loop | Tool Registry | Context Engine     │
│  RAG Pipeline | Knowledge Graph | Workflow     │
├─ AI / LLM ─────────────────────────────────────┤
│  DeepSeek V4 | Embedding | Reranker | Langfuse │
├─ 数据存储 ──────────────────────────────────────┤
│  MySQL 8 | ES 8 | Redis 7 | Kafka | MinIO     │
└────────────────────────────────────────────────┘
```

### 核心能力
| 模块 | 说明 |
|------|------|
| **PER Agent 循环** | 规划 → 执行（DAG 并行调度）→ 反思 |
| **25+ 工具** | 知识检索 / 文档分析 / 网络搜索 / 代码执行 / 翻译 / MCP |
| **自我进化** | 经验记忆（18 条）+ 执行回放 + 模式挖掘 |
| **结构化规划** | 规则化任务分解（comparison / analysis / research） |
| **可观测性** | Langfuse 全链路追踪 / Prometheus 指标 |
| **MCP 兼容** | 支持外部 MCP Server 接入 |
| **30 题评测框架** | 三层评测（能力 / 系统 / 案例）+ 自动评分 |

---

## 📈 最新评测数据（004 Structured Planner）

30 题企业知识任务，三层评测结构：

### L1 能力层（20 题）

| 指标 | 值 |
|------|:--:|
| 平均覆盖率 | **93.1%** |
| PASS / PART / FAIL | 18 / 2 / **0** |
| 平均步骤数 | 3.4 |
| 平均耗时 | 59.0s |

### L2 系统层（10 题）

| 指标 | 值 |
|------|:--:|
| 平均覆盖率 | 50% |
| PASS / PART / FAIL | 5 / 0 / 5 |

> 5 个 FAIL 均为歧义/边界查询（"今天天气"、"分析苹果"），属系统测试层的预期行为。

### 运行评测

```bash
cd backend
# 纯 RAG 基线
python -m benchmark.run --questions benchmark/questions/v3.json --mode baseline
# PER Agent + 结构化规划
python -m benchmark.run_004
```

---

## 🚀 快速开始

### 前置要求
Docker Desktop（推荐）或 Python 3.11+ / Node.js 18+

### 1. 克隆 & 启动基础设施
```bash
git clone https://github.com/sijie-Z/DocMind-RAG.git
cd DocMind-RAG/backend && docker compose up -d
```

### 2. 配置 & 启动
```bash
cp .env.docker.example .env.docker
# 编辑 .env.docker，填入 API Key

# 后端
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 前端
cd frontend && npm install && npm run dev
```

### 3. 访问
| 地址 | 说明 |
|------|------|
| http://localhost:5173 | 前端界面 |
| http://localhost:8000/docs | Swagger API 文档 |

### 演示账号
| 用户名 | 密码 | 角色 |
|--------|------|------|
| `guest` | `123456` | 普通用户 |
| `admin` | `admin123` | 管理员 |

---

## 📁 项目结构

```
DocMind/
├── backend/
│   ├── app/
│   │   ├── api/v1/endpoints/     # REST API (17 模块)
│   │   ├── agent/                # PER Agent 核心
│   │   │   ├── loop.py           #   PER 主循环
│   │   │   ├── planner.py        #   规划器 (含结构化规划)
│   │   │   ├── executor.py       #   执行器 (DAG/Serial)
│   │   │   ├── reflector.py      #   反思器
│   │   │   ├── experience/       #   经验记忆
│   │   │   ├── replay/           #   执行回放
│   │   │   ├── mining/           #   模式挖掘
│   │   │   └── tools/            #   25+ 工具
│   │   ├── core/                 # 基础设施
│   │   ├── rag/                  # RAG 管道
│   │   └── worker/               # Kafka 异步处理
│   ├── tests/                    # 422+ 测试
│   └── benchmark/                # 30 题评测框架 + 5 轮实验结果
├── frontend/src/                 # Vue 3 前端
└── docs/
    ├── architecture.html         # 交互式架构图
    ├── research/                 # 5 轮实验报告
    │   ├── 001_dag_vs_serial.md
    │   ├── 002_planner_granularity.md
    │   ├── 003_planner_compliance.md
    │   ├── 004_structured_planning.md
    │   └── (005 实验数据)
    └── product-definition.md     # 产品定义
```

---

## 🧪 测试

```bash
cd backend
python -m pytest tests/ -v --tb=short
make test && make lint
```

## 🚢 部署

- **Docker Compose**：`cd backend && docker compose up -d`
- **Kubernetes**：`kubectl apply -f deploy/k8s/`
- 见 `deploy/README.md`

---

## 🧭 研究路线图

| 实验 | 假设 | 结果 |
|:----:|------|:----:|
| 001 | DAG 优于 Serial | ❌ 无差异（avg_steps=1.2） |
| 002 | 粒度控制可调 | ❌ 实验设计中 |
| 003 | Prompt 可控制粒度 | ❌ Planner 无法被诱导拆分 |
| **004** | **结构化规划优于 LLM Planner** | **✅ Coverage 69%→93%** |
| 005 | 多步后 DAG 优于 Serial | ❌ 仍然等价（规划质量 > 执行架构） |

**下一步方向**：
- 扩展模板类型（多实体排序、数据流水线）
- 动态步骤数自适应
- 探索 Act→Observe→Act 替代 Plan→Execute

---

## 📝 版本历史

| 版本 | 日期 | 说明 |
|------|------|------|
| **v1.2.1** | 2026-06 | 结构化规划、5 轮实验报告 |
| **v1.2.0** | 2026-05 | PER 架构、25+ 工具、自我进化 |
| **v1.0.0** | 2026-05 | 初版：RAG 管道、知识图谱 |

---

## 🔗 链接

- **架构图**：https://sijie-z.github.io/DocMind-RAG/architecture.html
- **GitHub**：https://github.com/sijie-Z/DocMind-RAG
- **实验 004**：`docs/research/004_structured_planning.md`
- **API 文档**：http://localhost:8000/docs

---

<p align="center">
  <strong>DocMind</strong> — 实验驱动的 Agent 系统研究平台
  <br>
  <sub>Built with ❤️</sub>
</p>

---

## 📌 Repository Status

**Frozen research snapshot.**
This repository contains the complete experimental trajectory of the DocMind Agent ablation study (7 experiments, layered bottleneck analysis).
- **Freeze commit:** `5784167`
- **Status:** No further modifications will be made.
- **Production implementation:** [sijie-Z/DocMind-RAG](https://github.com/sijie-Z/DocMind-RAG)

All engineering improvements (API service, reliability layer, observability) live in the production repository above.
