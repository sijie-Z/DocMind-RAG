<p align="center">
  <a href="README_EN.md">🇺🇸 English</a> · <strong>🇨🇳 中文</strong>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/版本-v1.3.0-blue?logo=semver" alt="Version">
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
| **v1.3.0** | 2026-06 | 工作流编辑器升级（LLM多配置/SKILL.md/引擎切换） |
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

---

## 🔭 架构复盘：2026 视角下的下一步

> 本节是一次**外部评审视角**的复盘（2026-07）——先把 `agent/` 核心逐文件读了一遍，再对照 2026 年 Agent 领域的主流范式（Anthropic Agent SDK、12-Factor Agents、Arize Agent Eval、Mem0/AWM 记忆研究、OTel GenAI 语义规范），落到具体文件行号上的判断。不粉饰、可执行。

### 一句话结论

> **DocMind 缺的不是"2026 的能力"，而是把已经造好的线接上。**
> 它的基础设施（Hybrid RAG、工具注册表、四层记忆 + 负迁移保护）比多数生产级 Agent 更完整；真正的短板是**接线断点**和**重复实现**——最先进的功能（结构化反思、经验学习、生产链路追踪、上下文引擎）都造好了，却没有接到主环路上。

### 一、当前定位：基础设施领先，Agent 环路半开

2026 年 Agent 的参考环路已从 ReAct / Plan-Execute 演进为 **「收集上下文 → 行动 → 验证 → 重复」**——关键的新增项是**显式的验证阶段**。DocMind 的 Reflector + 对抗式 Reviewer 恰好实现了这个模式，**在这一点上是领先的**。问题出在环路是**开环**而非闭环：

| 已造好但没接上 | 证据（文件:行） | 后果 |
|---|---|---|
| **Reflector 的结构化结果被丢弃** | `reflector.py:143/184/209` 构造了 `ReflectionResult(achievement/gaps/quality_issues)` 却是**裸语句 + 空 `return`** | `achievement`% 和 `gaps` 是死代码，只有粗粒度 pass/retry 决策存活 |
| **`replan` 判定零处理** | `loop.py` 中 `grep replan` 无命中 | 反思只能重跑单步（step-retry），无法重新规划——"自我纠错"名不副实 |
| **学习闭环未激活** | `skills.py:190` 定义了 `learn_from_plan_success`，**无任何调用点** | 成功的多工具计划不会被蒸馏成可复用技能（本可对标 Agent Workflow Memory，+24~51% 成功率） |
| **ContextEngine 空转** | `loop.py:121` 实例化，全程无 `.fit()` 调用 | "上下文工程"层存在但从不生效；长对话无 compaction |
| **生产流量对优化层不可见** | `TraceStore`/优化分析只覆盖 `Orchestrator`/eval 路径 | `PERAgentLoop` 的真实流量不进分析——整个优化循环在测量错误的样本 |

**这五条是 Tier 1：不是新功能，是把断线接上。** 按用户一贯偏好——"改进要能被感受到"——这一层 ROI 最高、见效最快。

### 二、2026 坐标系 × DocMind 现状

| 2026 主流范式 | DocMind 现状 | 差距 |
|---|---|---|
| 验证在环（linter/judge 反馈闭环） | ✅ Reflector + 对抗 Reviewer | 反思信号被丢弃（见上） |
| Workflow 引擎 × Agent 环路收敛（确定性骨架 + LLM 尾部） | ✅ 结构化模板规划正是这个思路 | 双执行引擎重复（`PERAgentLoop.Executor` vs `Orchestrator`），DAG/retry/fallback 逻辑写了两遍 |
| 可靠性是结构性瓶颈（误差复利：99%/步 × 20 步 = 82%；τ-bench pass^8 < 25%） | ⚠️ 有 quality_gate 但仅"建议不阻断" | 缺 **pass^k** 一致性评测；单次绿灯已不算证据 |
| 非参数化经验记忆 + 多信号检索（Mem0）+ 主动清理 | ✅ 四层记忆 + **负迁移保护**（罕见且高级） | 检索仍是关键词/CJK-bigram，非 embedding；无去重/弃用；提取在环内烧 token（应挪到 sleep-time 离线） |
| SLM-first 异构模型舰队 | ✅ 已有 deep/quick 双模型路由 | 未形式化——可把分类/合成/简单工具步固定路由到小模型 |
| OTel GenAI 语义规范（可移植可观测性） | ✅ Prometheus + Langfuse + 自建 TraceStore | 未采用 OTel `invoke_agent`/`execute_tool` span 约定 |

### 三、评测方法学：最该换范式的地方

当前 `scorer.py` 用**关键词子串覆盖率**评分（0.8/0.4 阈值）。这个指标对中文尤其脆弱——忽略语义/否定/同义词，且与经验提取器形成**关键词堆砌的奖励黑洞**（提取的教训多半是"补齐这些关键词" → 回过头又按关键词打分）。2026 标准做法：

- **LLM-as-judge + faithfulness/answer-relevance**（RAGAS 式），替换/增强子串覆盖率；
- 按 PER 阶段套 Arize 六类评测：**Planner → Planning Eval（ideal/valid/invalid）+ 路径收敛；Executor → Tool Selection / Parameter Extraction；Reflector → Reflection Eval**；
- **pass^k**（每场景跑 k 次）+ 效率指标 **最优步数 ÷ 实际步数**（检测"绕路问题"）。

### 四、RAG 层：唯一的现代化缺口

检索栈本身已是 2026 竞争力水准（Hybrid + RRF、HyDE、MMR、cross-encoder rerank、语义缓存、查询分解、自适应路由）。唯一缺的是**忠实度/引用校验门**——`report_grounded()` 只记录"是否有来源"，不校验答案是否真的由来源支撑。加一个 LLM-judge 或 NLI 的 groundedness gate 即可闭合。

> 附带修一个隐藏 bug：`reranker.py:87` 的 `_rerank_zhipu` 读 `_source.chunk_text`，而 ES `_source` 实际存的是 `content`——Zhipu 重排路径会发空文档（本地重排是默认值，暂时掩盖了它）。

### 五、优先级路线

```
Tier 1（接线，低成本高感知）
  ① Reflector 返回值接回 loop + 实现 bounded replan 闭环
  ② learn_from_plan_success 接进 Phase 4（成功轨迹 → 可复用技能）
  ③ 生产 PERAgentLoop 流量写入 TraceStore（让优化层看见真实样本）
  ④ ContextEngine.fit() 接入主环路（长对话 compaction）

Tier 2（去重，降维护风险）
  ⑤ 双执行引擎抽出共享的 retry/fallback/parallel 组件
  ⑥ 三套技能系统（procedural / 文件 SKILL.md / 复合工具）统一检索接口

Tier 3（新范式，战略性）
  ⑦ 评测：LLM-judge + faithfulness + pass^k + 步数效率
  ⑧ RAG groundedness/引用门
  ⑨ 记忆检索升级到 embedding + 多信号融合 + 主动弃用 + sleep-time 离线提取
  ⑩ 形式化 SLM-first 异构路由；可观测性迁移 OTel GenAI 约定
```

> **待核实的一处疑点**：记忆里记录 Phase B 曾建 `TaskOutcome` 模型，但当前代码中只找到过时的 `.pyc`——疑似回归，接 Tier 1 前值得确认一下源文件是否还在。

*—— 复盘依据：`agent/` 全量逐文件读 + 2026 Agent 范式扫描（Anthropic / 12-Factor Agents / Arize / Mem0 / AWM / OTel GenAI）。所有代码论断均已按文件行号核实。*
