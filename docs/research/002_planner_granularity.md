# 002 — Planner Granularity

> **状态**: 提案（未开始实验）
> **假设阶段**: 基于 001 实验的根因分析提出
> **代码未改，预测已写**

---

## 1. Observation

实验 001（DAG vs Serial）发现：

- DAG 并未优于 Serial（成功率 50% vs 63%，覆盖率 65.8% vs 69.4%）
- **Avg Steps = 1.2** — 83% 的任务单步完成
- 当步骤数 ≈ 1 时，执行架构（DAG/Serial）不影响结果

**结论**: 当前系统的瓶颈不是 Executor（执行引擎），而是 Planner（任务分解）。Planner 倾向于将复杂任务压缩为单步 LLM 调用，导致 Agent 架构从未被真正激活。

---

## 2. Hypothesis

> Agent 的成功率与规划粒度之间存在**倒 U 型曲线**关系。

**推理**:

| 粒度 | 步骤数 | 预期问题 |
|------|:------:|---------|
| 过粗 (coarse) | ~1-2 | 依赖 LLM 自身能力，Agent 架构未被利用；复杂任务容易遗漏信息 |
| 适中 (normal) | ~3-5 | 适度拆分：子任务可独立执行，部分并行收益可得，上下文保持完整 |
| 过细 (fine) | ~8-12 | 上下文被切碎，工具调用次数增加，协调成本超过并行收益 |

**核心机制**: 步骤数存在两个竞争效应——

1. **分解收益**（diminishing）：更多步骤 → 更清晰的子目标 → 并行执行机会 → 降低单步复杂度
2. **协调成本**（increasing）：更多步骤 → 上下文碎片化 → 工具调用开销 → 依赖管理复杂度

最优粒度位于边际收益 = 边际成本的交叉点。

---

## 3. Prediction

### 3.1 主要预测

| Mode | Avg Steps | Success Rate | Avg Coverage | Avg Time |
|:----:|:---------:|:------------:|:------------:|:--------:|
| coarse | 1-2 | 45-55% | ~60% | ~25s |
| **normal** | **3-5** | **65-75%** | **~75%** | **~35s** |
| fine | 8-12 | 50-60% | ~65% | ~55s |

**关键**: Success Rate 呈倒 U 型，coarse 和 fine 两端都低于中间。

### 3.2 倒 U 型曲线的条件

如果结果符合以下模式，则假设成立：

```
Success Rate
  ^
  |        *         ← normal (~4步, 峰值)
  |      *   *
  |    *       *     ← coarse (~1步) 和 fine (~10步) 均下降
  |  *           *
  +--------------------→ Step Count
    1    4    8    12
```

如果结果不符合——例如 fine 模式成功率继续上升或持平——则假设不成立，需要新的解释。

---

## 4. Manipulation Check

**关键问题**: `planning_mode` 参数是否真的改变了规划粒度？

实验前需要验证的指标：

| 指标 | coarse | normal | fine |
|:----|:------:|:------:|:----:|
| avg_steps | 预期 ~1.5 | 预期 ~4 | 预期 ~10 |
| avg_dependencies | 预期 ~0 | 预期 ~2 | 预期 ~6 |
| avg_parallel_groups | 预期 ~0 | 预期 ~1-2 | 预期 ~3-5 |

**失败条件**:

如果 `coarse ≈ normal ≈ fine`（三组的 avg_steps 无显著差异），则实验操纵失败，不进入结果分析。需要先改进 Planner 的步数控制能力。

### 4.1 Planner Compliance 表

这是最重要的操纵检查。实验前必须验证：

| Mode | 预期 avg_steps | 预期 avg_dependencies | 预期 avg_parallel_groups | 通过条件 |
|:----:|:--------------:|:---------------------:|:------------------------:|:--------:|
| coarse | ~1.5 | ~0 | ~0 | avg_steps < 2.5 |
| normal | ~4 | ~2 | ~1-2 | avg_steps 2.5-6 |
| fine | ~10 | ~6 | ~3-5 | avg_steps > 7 |

如果任何一档落在预期范围之外，实验操纵失败，先修 Planner 再重跑。

---

## 5. Experiment Design

### 5.1 变量

- **自变量**: `planning_mode`（coarse / normal / fine）
- **因变量**:

| Metric | 意义 | 预期趋势 |
|--------|------|---------|
| step_count | 规划粒度 | 操纵变量，应随 coarse→fine 递增 |
| success_rate | 效果 | 倒 U 型（核心假设） |
| keyword_coverage | 质量 | 倒 U 型 |
| duration | 时间成本 | 单调上升（更多步骤 → 更多工具调用） |
| tool_calls | 工具成本 | 单调上升 |
| token_usage | LLM 成本 | 单调上升 |

**核心分析框架: Pareto Frontier**

如果 success_rate 呈倒 U 型而 duration 呈单调上升，则存在帕累托前沿——normal 模式在"效果 vs 成本"平面上占优，coarse 和 fine 分别在其中一个指标上落后。

- **控制变量**: 同 30 题（v3.json），同一套 Executor + Reflector，同一模型（DeepSeek V4 Flash）
- **执行模式**: 统一使用 DAG（既然 Serial 已被证明等价）

### 5.2 实现策略（预估改动）

Planner 的 `plan()` 方法需要接受 `mode` 参数：

- **coarse**: 最大步数 = 1，直接生成单步回答
- **normal**: 当前行为（max_plan_steps=5），自由的分解决策
- **fine**: 强制每个子目标拆为独立步骤（max_plan_steps=12），降低每步的"容量"上限

预估改动范围：
- `app/agent/config.py`: 新增 `planning_mode` 字段
- `app/agent/planner.py`: `plan()` 方法增加 `mode` 分支（~30 行）
- `benchmark/run.py`: 新增 `--planning-mode` CLI 参数

### 5.3 执行计划

```
Round 1: coarse → 30 题 → benchmark/results/agent_coarse.json
Round 2: normal → 30 题 → benchmark/results/agent_normal.json
Round 3: fine   → 30 题 → benchmark/results/agent_fine.json
```

共 90 次执行，预计耗时 45-60 分钟。

---

## 6. 如果假设成立

### 6.1 工程含义

如果倒 U 曲线被验证，则 Agent 系统存在一个**可量化的最优规划粒度**。这意味着：

1. Planner 不应有固定的最大步数，而应根据任务复杂度动态调整粒度
2. 可以在上线前通过 benchmark 标定当前任务集的最优粒度区间
3. 粒度参数可作为智能自适应策略的优化目标

### 6.2 研究含义

倒 U 曲线意味着 Agent 架构存在**规模效应边界**——类似于计算系统中的 Amdahl's Law：

- 串行部分（Planner）的质量决定了并行部分（Executor）能发挥的上限
- 超过最优粒度的分解是负收益的

这指向一个更通用的设计原则：**Agent 架构的每一层都需要与上下层的粒度匹配**。

---

## 7. 局限与风险

1. **LLM 不稳定**: 同一步骤数下，不同次执行的 LLM 响应差异可能掩盖粒度效应
2. **任务集偏差**: 30 题可能不足以覆盖粒度敏感的任务类型
3. **Planner 控制精度**: 当前 LLM 驱动的 Planner 可能无法严格按预期粒度执行（见 Manipulation Check）
4. **单模型依赖**: 实验仅使用 DeepSeek V4 Flash，结果可能不泛化到其他模型

---

## 8. References

- 实验 001: `docs/research/001_dag_vs_serial.md`
- 题目集: `benchmark/questions/v3.json`
- 实验框架: `benchmark/run.py`（支持 `--planning-mode` 参数）
