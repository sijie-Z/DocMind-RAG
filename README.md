<p align="center">
  <a href="README_EN.md">[英文]</a> · <strong>[中文]</strong>
</p>

<div align="center">
  <h1>DocMind</h1>
  <p><strong>工程仓库 — Agent 可观测性与可靠性基础设施</strong></p>
</div>

---

## 当前状态

| 项目 | 状态 |
|---|---|
| 研究仓库 ([DocMind-Agent-Causal-Study](https://github.com/sijie-Z/DocMind-Agent-Causal-Study)) | 冻结 |
| 本仓库定位 | 工程向，由研究结论驱动 |
| Capability Map（6层资产审计） | 完成 |
| RunReport MVP（observe-only） | 已完成，等待真实失败验证 |
| 战略方向讨论 | 暂停 |

---

## 路线回顾

### 阶段 1：战略收敛

确认本仓库从"研究驱动"转为**工程驱动**。研究仓库负责"为什么这样设计(Why)"，本仓库负责"能不能放心用(How)"。参照系：FastAPI / K8s / n8n / Supabase（靠稳定、规范、可贡献长期存活的基础设施项目）。

核心原则：
- 不要为新颖而优化，要为可维护性、可扩展性、可靠性而优化。
- 直觉生成假设，证据裁决是否发布。
- 制度落后于组织半步——不为一支还不存在的团队立法。

### 阶段 2：Capability Map（资产审计）

对工程仓库 6 层能力逐层审计，结论：**三处承重墙在设计层写了，运行层没通电。**

| 能力层 | 状态 | 说明 |
|---|---|---|
| Runtime / Session | 部分 | ExecutionContext 是一等公民，但 append-only 记账，无回滚 |
| Execution（工具调用） | 弱 | step_runner / circuit_breaker 是孤儿代码，hook 槽空，risk_level 是死元数据 |
| Trace / Observation | 弱 | 4 套不协调 trace，Decision.reasoning / Finding.confidence 是空壳字段 |
| Evaluation | 部分 | 三个评估器 inline 运行，但仅 retry 一根线 actuate，replan/reject/fatal 全丢弃 |
| Experience Loop | 断 | 抽取是手写规则 + bootstrap，runtime 不调抽取，experience 不接 runtime |
| Improvement Loop | 断 | 依赖 Experience，故断 |

### 阶段 3：第一刀落地 — RunReport MVP

目标：让一次 Agent run 结束后，系统能回答三个问题——**它做了什么、为什么这么做、结果如何**。

已完成的改动（3 个文件，~170 行）：

```
backend/app/agent/run_report.py  — 新增：纯函数，从 ExecutionContext 汇总成人类可读报告
backend/app/agent/loop.py         — run 结束生成 report + logger + yield run_report 事件
backend/app/agent/events.py       — EventType 新增 "run_report"
```

**边界：observe-only。** 不改 retry / replan / approval / memory / experience / self-improvement。不改任何运行时行为。

### 阶段 4：等待验证（当前阻塞点）

RunReport 代码已在本地仓库，但**未经过真实 Agent 失败验证**。

下一步只需要做一件事：

1. 在有完整运行环境（依赖 + MySQL/Redis/ES + API Key）的机器上启动项目
2. 触发一次真实 Agent 失败（如调不存在的文件、错误参数）
3. 从日志中拿到 `=== Run Report ===` 到 `=== end report ===` 的文本
4. 回答三个问题：
   - 以前定位失败要多久？现在看 report 有没有更快？
   - Report 有没有减少猜测？
   - 删掉 report 会不会明显变差？

## 🚦 接下来做什么（不知道就跟着走）

```
当前阻塞: RunReport 代码已写完，还没拿到真实 Agent 失败的输出。

Step 1: 启动项目
    cd backend && docker compose up -d     # 起 MySQL/Redis/ES/Kafka/MinIO
    python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

Step 2: 故意让 Agent 失败一次
    比如让它调一个不存在的文件、给个错误参数

Step 3: 复制日志里的 RunReport
    找到从 === Run Report === 到 === end report === 的文本

Step 4: 问自己三个问题
    ① 以前定位失败要多久？现在看 report 有没有更快？
    ② Report 有没有减少猜测？
    ③ 删掉 report 会不会明显变差？

Step 5: 根据结果分叉
    有用 → 补下一根墙（decision trace / evaluation actuate / experience loop）
    没用 → 砍掉 RunReport，换方向

以上。拿到 RunReport 输出之前，不加代码、不开战略会议、不写文档。
```

---

## 快速开始

### 前置要求
Docker Desktop（推荐）或 Python 3.11+ / Node.js 18+

### 启动

```bash
git clone https://github.com/sijie-Z/DocMind-RAG.git
cd DocMind-RAG/backend && docker compose up -d    # 启动 MySQL / Redis / ES / Kafka / MinIO
cp .env.docker.example .env.docker                # 填入 API Key

cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

cd ../frontend && npm install && npm run dev      # http://localhost:5173
```

### 演示账号

| 用户名 | 密码 | 角色 |
|--------|------|------|
| `guest` | `123456` | 普通用户 |
| `admin` | `admin123` | 管理员 |

---

## 项目结构

```
DocMind/
├── backend/
│   ├── app/
│   │   ├── api/v1/endpoints/     # REST API (17 模块)
│   │   ├── agent/                # PER Agent 核心
│   │   │   ├── loop.py           #   PER 主循环
│   │   │   ├── planner.py        #   规划器（结构化优先，LLM 兜底）
│   │   │   ├── executor.py       #   执行器（DAG/Serial + 重试 + fallback）
│   │   │   ├── reflector.py      #   反思器
│   │   │   ├── reviewer.py       #   对抗式 Reviewer
│   │   │   ├── run_report.py     #   RunReport MVP（observe-only）
│   │   │   ├── experience/       #   经验记忆
│   │   │   ├── replay/           #   执行回放
│   │   │   ├── mining/           #   模式挖掘
│   │   │   └── tools/            #   25+ 工具
│   │   ├── core/                 # 基础设施
│   │   ├── rag/                  # RAG 管道
│   │   └── worker/               # Kafka 异步处理
│   └── tests/
├── frontend/src/                 # Vue 3 前端
└── docs/
    └── research/                 # 实验报告（001-007，已冻结）
```

## 链接

- **研究仓库**：https://github.com/sijie-Z/DocMind-Agent-Causal-Study
- **GitHub**：https://github.com/sijie-Z/DocMind-RAG
