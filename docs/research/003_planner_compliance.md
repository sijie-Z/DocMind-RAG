# 003 — Planner Compliance: Granularity Manipulation Failure

> **实验日期**: 2026-06-20
> **状态**: 操纵失败，报告发现
> **核心结论**: Planner 无法被诱导拆分任务——即使系统提示明确要求"尽可能细分"，平均步骤数仍为 1.0-1.2

---

## 1. Background

实验 001（DAG vs Serial）发现 avg_steps = 1.2，提出假设：Planner 的任务分解粒度不够，导致 DAG 的并行能力未被激活。

实验 002（Planner Granularity）设计了三档 `planning_mode`（coarse/normal/fine），预期步骤数分别为 ~1、~4、~10，以验证成功率与粒度之间的倒 U 型曲线关系。

---

## 2. Manipulation Check 结果

| Mode | System Prompt 指令 | 预期 avg_steps | 实际 avg_steps | 通过？ |
|:----:|-------------------|:--------------:|:--------------:|:-----:|
| coarse | "请一步完成，不要拆分" | ~1.5 | **1.0** | ✅ |
| normal | 无额外指令 | ~4 | **1.1** | ❌ |
| fine | "请尽可能细粒度拆分，每个独立子目标作为一个步骤，宁可多步也不要合并" | ~10 | **1.2** | ❌ |

**结论: Manipulation Check Failed.**

`planning_mode` 参数未成功改变 Planner 的规划粒度。coarse、normal、fine 三档的平均步骤数均在 1.0-1.2 之间，差异不显著。

因此，002 提案中的倒 U 型曲线假设**未经有效检验**——自变量未被操纵，因变量结果不能用于支持或否定原假设。

---

## 3. 真正的发现

实验 001 发现 "Planner 平均只生成 1.2 步"，当时怀疑是 Planner **倾向**不拆。实验 002 证明：即使通过系统提示**明确要求**拆解，Planner 仍然不拆。

问题从"Planner 倾向不拆"升级为 **"Planner 几乎无法被诱导拆分"**。

### 3.1 可能原因

**假设 A — 模型自身行为**: 现代 LLM（DeepSeek V4 Flash）能力足够强，倾向于一步解决问题。对模型而言，多步分解在数学上成本更高（更多 token），且无明显收益。

**假设 B — Prompt 权重冲突**: `fine` 指令要求"尽可能拆分"，但 Planner 的默认 prompt 中包含"总步骤数不超过 {max_steps}"等平衡性约束，模型倾向于选择最安全的中间路径。

**假设 C — Benchmark 任务太简单**: 30 题中单文档检索和简单分析占比过高，模型天然认为一步足够。需要更高复杂度的任务来测试拆分解空间。

### 3.2 初步证据

在 `fine` 模式下，部分题目确实出现了多步计划（如 L2-EDGE-04 产生 4 步）。这表明指令并非完全无效——但只对极少数复杂边界情况生效。对于大多数标准任务，模型判断"一步即可"。

---

## 4. 对后续实验的启示

### 4.1 关于 Granularity 实验

在 Planner 的可控性解决之前，**任何粒度的效果分析都无效**。后续方向：

1. **改进 Planner 的拆解能力**: 不是在 prompt 层面"建议"，而是通过输出格式约束（强制输出多步 JSON schema）或后处理拆分（Planner 输出单步 → 启发式再拆分）
2. **降低 Benchmark 中的简单任务比例**: 当前 30 题中 18 题是强依赖型（C 类），大部分单步可完成。如果目标是研究分解，需要更多高复杂度任务

### 4.2 关于 Agent 系统的架构

这两轮实验共同揭示了一个更深层的问题：

```
假设链路:
DAG 并行 → 降低延迟 → 提升效果
            ↓
现实链路:
Planner（LLM）→ 单步完成 → Executor 无并行 → Reflector 无事可做
                            ↓
                     Agent 退化为"带工具调用的 LLM 直答"
```

当前系统的 Agent 特性可能被夸大了——LLM 自行完成了大部分推理和合成，Planner 和 Executor 只在极少数场景下被真正激活。

---

## 5. 后续方向建议

### 方向 A: Planner 拆解能力工程改进
- 输出格式强制多步（JSON Schema 要求 steps 数组长度 >= 3）
- Planner 输出后启发式拆分（规则：含"、"/"和"的查询拆为多步）
- 两步 planner 架构：第 1 步列子目标列表，第 2 步转执行计划

### 方向 B: 复杂度分层研究
- 按任务复杂度（单步/多步/多步+外部资源）分层统计 Planner 行为
- 验证"不拆"在什么复杂度阈值下开始失效

### 方向 C: 更换 Planner 的 LLM
- 使用更强的推理模型（DeepSeek V4 Pro 而非 Flash）进行规划
- 对比不同模型在拆解任务上的差异

---

## 6. 原始数据

- coarse: `benchmark/results/agent_coarse.json`（avg_steps=1.0, success=17/30）
- normal: `benchmark/results/agent_normal.json`（avg_steps=1.1, success=17/30）
- fine: `benchmark/results/agent_fine.json`（avg_steps=1.2, success=16/30）
- 实验提案: `docs/research/002_planner_granularity.md`

> **注意**: 由于 Manipulation Check Failed，上述 success/coverage/time 数据不能用于验证原假设。此处仅作记录。
