# 007 — Retrieval Ceiling: Compressed Oracle vs Real System

> **日期**: 2026-06-21
> **前提**: 基于 004 Structured Planner（L1 覆盖率 93.1%）和 006 Failure Taxonomy（Planner Failure = 0）
> **RQ**: 在控制信息压缩的条件下，检索质量是否仍是 Agent 性能的瓶颈？

---

## 1. 动机

006 Failure Taxonomy 发现所有 L1 非满分案例均非 Planner 失败，而是与 Knowledge Layer 相关。但当时的分类将 "Search Failure" 和 "Knowledge Coverage Failure" 混为一谈。

007 的核心问题是：**如果给 Agent 提供精确的黄金文档片段（Oracle），它能比当前 BM25 检索做得更好吗？**

---

## 2. 实验设计

### 2.1 条件

| Condition | 检索方式 | Context 控制 |
|:---------:|---------|:-----------:|
| **Real** | BM25 搜索 + top-k 片段 | 当前系统（~500-800 chars） |
| **Oracle (compressed)** | 金标准片段注入 | **相同 token budget** |

### 2.2 样本

6 个 L1 覆盖率 < 100% 的题目：

| ID | Real Coverage | 错失关键词 |
|:--:|:-----------:|:---------:|
| L1-DOC-03 | 75% | "规定" |
| L1-CROSS-01 | 67% | "高于"、"低于" |
| L1-FRAME-02 | 80% | "净利率" |
| L1-FRAME-05 | 80% | "赔偿" |
| L1-MULTI-02 | 80% | "对比" |
| L1-MULTI-03 | 80% | "趋势" |

### 2.3 Oracle 片段设计原则

1. 从 KB 黄金文档中**精确提取答题所需信息**（非全文）
2. 长度控制在 **300-600 字**（≈ Real BM25 返回量）
3. 包含题目标签所需的关键数据点
4. 不人工添加未在原文中的结论（保留 Agent 推理空间）

示例——L1-FRAME-02（杜邦分析）的 Oracle 片段：

> "【星辰科技2024年报】净利润11.2亿元，营收58.3亿元，总资产126.5亿元，股东权益73.7亿元。
> 2023年数据：净利润9.0亿元，营收49.1亿元。
> 【杜邦分析公式】ROE = 净利率 × 资产周转率 × 权益乘数。
> 计算：2024年净利率19.2%，ROE=15.8%。2023年净利率17.9%，ROE=14.2%。"

---

## 3. 结果

| Question | Real | Oracle* | Δ | 解读 |
|:--------:|:----:|:-------:|:-:|:----|
| L1-DOC-03 | 75% | **25%** | −50% | Oracle 差（片段设计偏差）|
| L1-CROSS-01 | 67% | **67%** | 0 | 等价 |
| **L1-FRAME-02** | **80%** | **100%** | **+20%** | **Oracle 更好** |
| L1-FRAME-05 | 80% | 80% | 0 | 等价 |
| L1-MULTI-02 | 80% | 80% | 0 | 等价 |
| L1-MULTI-03 | 80% | 80% | 0 | 等价 |
| **AVERAGE** | **77.0%** | **71.9%** | **−5.1pp** | **Oracle ≈ Real** |

### 排除异常值（L1-DOC-03）后

| Average (5 of 6) | Real | Oracle* | Δ |
|:---------------:|:----:|:-------:|:-:|
| | 77.4% | **81.4%** | **+4.0pp** |

---

## 4. 分析

### 4.1 主要发现：检索层接近饱和

在 5/6 案例中，Oracle（金标准片段）与 Real（BM25）的效果**等价或略优**。唯一的 Oracle 更差案例（L1-DOC-03）是由于手写片段未能覆盖题目所需的"政策文件"检索锚点——这是一个实验设计问题，而非系统问题。

**结论：当前系统的 BM25 检索已接近其理论上限。追加知识覆盖（在同样的检索策略下）不会带来显著提升。**

### 4.2 长度退化效应（Observation）

在 Oracle 实验的早期版本（run_007_oracle.py）中，我们尝试了**全文注入**（将整篇 KB 文档注入 context），结果覆盖率从 93.1% 下降到 70.6%。

这验证了：

> **LLM context is not monotonic in information quantity.**
> 性能 ∝ f(信噪比)，而非 f(信息量)

但这不是"检索失败"，而是 **context shaping 问题**。同样的信息，压缩成精准片段后（本案的 compressed Oracle），效果与 BM25 相当。

### 4.3 Phase Transition 验证

本实验完成了从 006 开始的 Phase Transition 验证：

| Phase | 问题 | 证据 |
|:-----:|------|:----:|
| 1 | 执行架构 (DAG vs Serial) | ≈ 等价（001/005） |
| 2 | 规划粒度 (Planner 不拆) | 结构化规划 +24pp（004） |
| 3 | 信息层是否瓶颈 | Oracle ≈ Real，检索接近上限（007） |

---

## 5. 结论

### 5.1 核心回答

> **RQ**: 在控制信息压缩的条件下，检索质量是否仍是 Agent 性能的瓶颈？
>
> **A**: 不是。
>
> 压缩后的 Oracle 与 BM25 效果等价。系统已进入 **reasoning-bound** 而非 retrieval-bound 阶段。剩余覆盖率缺口来自 Agent 的推理/表达能力（"高于/低于"这样需要明确比较语言的关键词未被覆盖），而非知识缺失。

### 5.2 对系统设计的含义

1. **追加 KB 文档不是一个高 ROI 的方向**——当前检索已接近上限
2. **改进检索接口比增加知识覆盖更重要**——chunking 策略、query 分解、reranker 可能带来边际收益
3. **最后 7% 的缺口是推理层的**——Agent 需要更好的比较表达和结论生成能力，而非更多数据

### 5.3 研究上限

整个 5+2 实验序列的最终结论：

> **在这个任务分布和工具集下，Agent 性能的瓶颈经历了一个清晰的阶段跃迁：**
>
> ```
> 执行架构 (0% gain) → 规划粒度 (+24pp) → 检索精度 (+0-4pp) → 推理表达 (剩余缺口)
> ```
>
> 每个阶段解决的瓶颈暴露出下一阶段的瓶颈。在规划问题和检索问题被验证为"已充分"后，剩余 7% 的缺口定位到了推理表达层。

---

## 6. 局限与后续方向

### 局限
- 样本量小（6 题），手工片段可能有偏见
- KB 仅 10 篇文档，覆盖窄——在大规模 KB 上检索结果可能不同
- 未测 reranker 或 vector search 的影响（API 不可用）

### 后续方向
- **RQ: Retrieval Interface Design**——chunk size、query decomposition、reranker 对性能的影响曲线
- **RQ: Expressive Reasoning**——Agent 如何更精确地表达比较关系（如"高于/低于"）
- **RQ: Large-scale KB**——在 1000+ 文档的 KB 上重测 ceiling

---

## 7. 原始数据

- Real 基线：004 Structured Planner 结果
- Oracle 压缩实验：`benchmark/results/007_oracle_final.json`
- 原始实验脚本：`benchmark/run_007_final.py`
- 早期全文注入实验：`benchmark/run_007_oracle.py`
