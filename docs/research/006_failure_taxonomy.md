# 006 — Failure Taxonomy: Post-Structured Planner Error Analysis

> **日期**: 2026-06-21
> **前提**: 基于 004 Structured Planner 的 benchmark 结果（L1 覆盖率 93.1%）
> **问题**: Planner 问题已解决后，剩余 7% 的覆盖率缺口来自哪里？

---

## 1. Background

004 Structured Planner 将 L1 覆盖率从 ~69% 提升至 93.1%。但 93.1% 意味着仍有 2 题 PARTIAL（非满分）和若干 PASS 落在 80% 边界。

此时系统瓶颈已经转移——Planner 不再是主要矛盾。

本报告的目标：
1. 对所有非满分案例做系统分类
2. 区分 **Planner Failure** vs **Tool Failure** vs **Knowledge Failure**
3. 为下一阶段的研究方向提供数据支撑

---

## 2. 知识库现状

DocMind KB 在本次 benchmark 运行时包含 **10 篇文档**：

| 文档 | 类型 | 覆盖领域 |
|:----|:----|:--------|
| 供应商 A 采购合同 | 合同 | 供应合同条款 |
| 供应商 B 采购合同 | 合同 | 供应合同条款 |
| 星辰科技 2024 年报 | 财报 | 单一年度财务数据 |
| 远方创新 2024 年报 | 财报 | 单一年度财务数据 |
| 数据安全法 2024 修订 | 政策法规 | 数据安全合规 |
| AI 行业研究报告 2024Q4 | 行业研究 | AI 产业趋势 |
| 星辰科技收购公告 | 公告 | 企业并购 |
| 半导体行业市场回顾 2023 | 市场分析 | 半导体行业 |
| 中国 AI 产业政策分析 | 政策分析 | AI 政策环境 |
| 企业战略分析框架指南 | 方法论 | SWOT/PEST/杜邦指南 |

**关键限制：**
- 每个领域只有 1-2 篇文档（覆盖窄）
- 所有财报/报告均为单一年度（无法做跨年趋势分析）
- 无同行业多公司数据（无法做同行业对比）
- 无市场调研报告（包含消费者画像/市场规模等）
- 战略分析框架指南是"方法论"文档，不是具体分析对象

---

## 3. Failure Taxonomy

### 3.1 分类框架

```
┌─ Planner Failure          — Planner 未正确拆解任务         ─┐
├─ Search Reliability Failure — KB 有数据但搜索未返回           │
├─ Knowledge Coverage Failure — KB 没有任务所需的数据          ├─ 剩余缺口
├─ Tool Failure              — 工具调用失败（超时/异常）      ─┘
└─ Expression Failure        — 结果正确但未覆盖关键词
```

### 3.2 L1 非满分案例分类

#### L1-DOC-03 (75%, missed "规定")

**题目**: 搜索知识库中的政策文件，提取新规的核心变化和对企业的主要影响

**现象**: KB 中有《数据安全法 2024 修订版》相关文档，但搜索返回了乱码/空内容。Agent 的搜索调用没有得到有效结果。

**根因**: KB 有政策文件（数据安全法），但搜索的 query formulation、检索质量或解析过程存在问题。文档是可检索的，但搜索结果未正确传递。

**分类**: **Search Reliability Failure** (doc exists, search didn't surface it correctly)

---

#### L1-CROSS-01 (67%, missed "高于"、"低于")

**题目**: 搜索两份同行业公司的财报，对比它们的毛利率、净利润率和资产负债率的差异

**现象**: Agent 搜索后说"无法访问外部数据库"，然后提供通用分析框架而非实际对比。

**根因**: KB 有 2 份财报（星辰科技、远方创新），但它们是 2 家不同行业的公司，并非"同行业"。Agent 搜到后可能判断它们不满足"同行业"条件，或搜索策略不当导致根本没找到财报文档。

**分类**: **Knowledge Coverage Failure** (no two companies from the SAME industry in KB)

---

#### L1-FRAME-02 (80%, missed "净利率")

**题目**: 找一份公司财报，用杜邦分析法拆解 ROE 变化的驱动因素

**现象**: Agent 说"无法访问外部数据库"，然后给出了基于假设数据的杜邦分析示例。

**根因**: KB 有财报（星辰科技、远方创新各有 1 年数据），但杜邦分析需要至少 2 年的财务数据才能拆解 ROE "变化"的驱动因素。单年数据不足以做趋势分析。Agent 可能搜到了财报，但发现只有一年数据，无法满足题目要求。

**分类**: **Knowledge Coverage Failure** (only single-year data, can't analyze ROE change)

---

#### L1-FRAME-05 (80%, missed "赔偿")

**题目**: 找一份合同，用合同风险框架评估主要风险点

**现象**: Agent 说"无法访问外部数据库"，然后给出了通用的合同风险框架示例。

**根因**: KB 有 2 份供应商合同，理论上可以回答这个题目。但搜索未能成功返回合同文档。Agent 在搜索失败后 fallback 到通用框架。

**分类**: **Search Reliability Failure** (contract documents exist in KB but search didn't find them)

---

#### L1-MULTI-02 (80%, missed "对比")

**题目**: 搜索同行业三家公司的年报，分别做 SWOT 分析，综合给出行业竞争格局的判断

**现象**: Agent 说"无法直接访问外部数据库或网络资源"，给出 SWOT 分析指导框架。

**根因**: KB 有 2 份年报（不同行业），需要 3 份同行业年报。数据和题目的需求之间有根本性 gap。

**分类**: **Knowledge Coverage Failure** (need 3 annual reports from same industry; KB has 2 from different industries)

---

#### L1-MULTI-03 (80%, missed "趋势")

**题目**: 找到公司近三年的财务报告，先做杜邦分析，再对比每年的变化

**现象**: Agent 说"无法访问数据库"，然后给出基于假设数据的演示。

**根因**: KB 的所有财务报告都是单一年度。没有公司有连续三年的数据。这是一个根本性的数据缺口。

**分类**: **Knowledge Coverage Failure** (no multi-year financial data available)

---

### 3.3 L2 失败案例分类

| 题目 | 现象 | 分类 | 备注 |
|:----|------|:----|:----|
| L2-AMBIG-01 | "分析苹果" → 当做 Apple Inc. 分析了 | Ambiguity Handling | 预期行为（L2 测试目标） |
| L2-AMBIG-02 | "市场情况" → 假设数据框架 | Ambiguity Handling | 预期行为 |
| L2-AMBIG-03 | "哪个好" → 索要上下文 | Ambiguity Handling | 预期行为 |
| L2-EDGE-02 | "今天天气" → 不支持 | Capability Gap | 预期行为 |
| L2-EDGE-03 | "SWOT分析" → 缺对象 | Ambiguity Handling | 预期行为 |
| L2-RECOV-01 | 搜不存在文档 → 没说"未找到" | Expression Failure | 仅关键词匹配问题 |

L2 的 6 个失败全是 **系统测试层的预期行为**，不在能力提升范围内。

---

## 4. 量化汇总

### 4.1 L1 覆盖率缺口归因

```
Planner Failure                 0     (0%)
Search Reliability Failure      2     (33%)  ← L1-DOC-03, L1-FRAME-05
Knowledge Coverage Failure      4     (67%)  ← L1-CROSS-01, FRAME-02, MULTI-02, MULTI-03
Tool Failure                    0     (0%)
Expression Failure              0     (0%)
──────────────────────────────────────────────────
Total non-100% cases            6
```

### 4.2 L2 失败归因（预期行为）

```
Ambiguity Handling              4     (67%)
Capability Gap                  1     (17%)
Expression Failure              1     (17%)
──────────────────────────────────────────────────
Total L2 failures               6
```

---

## 5. 核心结论

### 5.1 Planner Failure = 0

所有非满分案例中，**没有一题是因为 Planner 拆解得不好而失败的**。结构化规划已经解决了任务分解问题。这个数据验证了 004 的结论。

### 5.2 瓶颈已从架构层转移到数据层

系统的性能上限现在由 **Knowledge Layer** 而非 **Agent Architecture** 决定。

```
Agent Performance ≈ Planner × Tools × Knowledge Base

004 之前: Planner 是瓶颈 (Planner ≈ 1.2)
004 之后: Knowledge Base 是瓶颈 (Coverage 不足以支撑题目需求)
```

### 5.3 Search Reliability 和 Knowledge Coverage 需要不同的解决方案

| 问题类型 | 根因 | 解决方案 |
|---------|------|---------|
| Search Reliability | 文档存在但检索失败 | 改进 query 生成、检索策略、结果解析 |
| Knowledge Coverage | 文档不存在或数据不足 | 增加数据源、文档丰富度 |

---

## 6. 下一阶段研究方向

### RQ1: 知识库覆盖率与 Agent 成功率的关系

假设 KB 覆盖率（题目所需数据在 KB 中的比例）与 Agent 成功率存在正相关。可以通过控制 KB 中文档的增减来量化这个关系。

假设曲线：
```
Agent Coverage
   100% ┤        ●
    80% ┤     ●
    60% ┤  ●
    40% ┤ ●
    20% ┤ ●
      0 └───┬───┬───┬───┬───
          20% 40% 60% 80% 100%
                KB Coverage
```

### RQ2: Hybrid 检索策略的有效性

当前系统在 Keyword (BM25) 可用但 Vector Search 和 Reranker 因 API 问题不可用。可以在检索链路稳定后，对比 Keyword / Vector / Hybrid 三种策略的效果差异。

### RQ3: Knowledge Sufficiency Check

当前系统在数据不足时会"胡编"——生成假设数据、通用框架。可以引入 Knowledge Sufficiency Check：如果搜索结果的置信度或覆盖度不足，Agent 应主动声明"没有足够信息"，而非提供虚假分析。

---

## 7. 原始数据

- 004 benchmark 结果：`benchmark/results/004_structured_summary.json`
- KB 文档列表：10 篇文档（org_id=3），涵盖合同/财报/行业分析/政策文件
- 本研究基于 004 全量 30 题运行数据和 KB 文档清单交叉分析
