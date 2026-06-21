# 面试准备素材

> 目标：大厂 AI Infra / Agent Platform / AI Engineering 方向实习 & 校招

---

## 1. 简历版 (4-5 bullet)

### 英文

```
DocMind — Agent Research & Evaluation Platform

• Built a benchmark-driven agent system (PER architecture: Planning → Execution → Reflection)
  with DAG-based parallel execution and 25+ integrated tools.

• Designed and conducted 5 controlled experiments on Planner granularity and execution
  architecture trade-offs; identified planning quality as the dominant performance factor.

• Discovered that DAG and serial execution achieve equivalent performance when
  avg_steps ≈ 1.2, and that LLM-based planners cannot be reliably controlled through
  prompt engineering alone.

• Developed a structured planning framework that decomposes tasks by type (comparison /
  analysis / research); improved L1 benchmark coverage from 69% to 93.1% (+24pp)
  and eliminated all L1 benchmark failures.

• Built a reproducible evaluation pipeline (30-question benchmark, keyword-coverage
  scoring, case-file tracking) enabling rapid hypothesis → experiment → analysis cycles.
```

### 中文

```
DocMind — Agent 研究与评测平台

• 构建了基于 PER（规划-执行-反思）架构的 Agent 系统，支持 DAG 并行调度和 25+ 内置工具。

• 设计并执行 5 轮控制实验，系统评估了规划粒度和执行架构对 Agent 性能的影响，
  发现"规划质量"而非"执行架构"是 Agent 性能的主导因素。

• 实验证明：avg_steps=1.2 时 DAG 和串行执行效果等价；LLM Planner 无法通过
  Prompt 工程可靠控制其任务分解粒度。

• 提出结构化规划框架（按任务类型分类：比较/分析/研究），将 benchmark 覆盖率
  从 69% 提升至 93.1%（+24 个百分点），消除所有 L1 能力层失败案例。

• 建立了可复现的评测流水线（30 题 benchmark + 关键词覆盖率评分 + 案例追踪），
  支持快速假设→实验→分析迭代循环。
```

---

## 2. 3 分钟项目介绍版

> 面试场景："介绍一下你的项目。"

### 开场（30s）

"我做了一个 Agent 研究与评测平台，叫 DocMind。它不是又一个 Agent 框架——而是一个把 Agent 当作实验对象来研究的平台。

核心是 PER 三阶段架构：规划→执行→反思，支持 DAG 并行调度和 25+ 工具。但这个架构本身不是项目的亮点，亮点是我们在上面做的 5 轮实验。"

### 问题发现（60s）

"第一轮实验比较 DAG 和串行执行器，结果发现它们没区别。DAG 理论上有并行优势，但实际跑下来覆盖率、耗时都差不多。

深入一看，原因很简单：Planner 平均只生成 1.2 步。83% 的任务单步完成。DAG 根本没机会发挥作用。

那能不能让 Planner 多拆几步？我们在提示词里明确要求'尽可能细粒度拆分'。结果呢？Planner 完全不听——步骤数还是 1.0-1.2。

这就引出了一个关键诊断：在这个系统里，LLM Planner 已经退化成 Query Rewriter，而不是 Task Decomposer。它'觉得'一步能搞定，就不会拆。"

### 解决方案（60s）

"既然 LLM 不拆，我们就别让它决定拆不拆了。我们引入了一个规则化的 Task Classifier + Template Engine——识别任务类型（比较、分析、研究），直接生成固定步骤的计划。

验证实验先做了 5 道典型题的手写多步 Plan 对比。结果：人工 Plan 覆盖率 92%，当前 Planner 76%，+16pp。而且步骤数增加 130%，耗时只增加 0.6s——因为 DAG 终于开始并行执行了，收益被撑住了。"

### 最终结果（30s）

"全量 30 题 benchmark 验证：L1 能力层覆盖率从 69% 提升到 93.1%，L1 失败从 5-6 题降到 0。重新测 DAG vs Serial——仍然等价。最终结论是：在这个任务分布上，规划质量远比执行架构重要。"

---

## 3. 面试深挖版

> 当面试官追问细节时的应对策略。

### Q: "93% 的提升会不会是因为模型换了？"

> ❌ 错误回答：直接否认。
> ✅ 正确回答：先承认 confound，再解释为什么结论依然成立。

"好问题。确实，001 实验用了 DeepSeek V4 Flash，004 实验用了智谱 GLM-4-Flash。模型有变化。

但这个结果仍然可信，原因有两个：

1. **内部对照组**：004 实验用的 '人工多步 Plan vs 当前 Planner' 对比，用同一个模型跑完。在那个 5 题的验证实验中，人工 Plan 是 92%，当前 Planner 是 76%——提升 16pp。这个对比是干净的。

2. **005 实验**：我们重新测了 DAG vs Serial，两个条件用同一个模型。结果仍然等价。所以 'DAG 等价 Serial' 这个核心结论不受模型影响。

所以虽然绝对数字有 confound，但三个核心结论（DAG≈Serial、Planner 不拆、结构化规划大幅提升）在控制实验内部是被验证过的。"

### Q: "你的覆盖率是怎么计算的？"

"用关键词匹配。每个 benchmark 题目预定义了 3-5 个预期关键词（比如 '毛利率'、'对比'、'SWOT'），Agent 回答后检查每个关键词是否出现在回答文本中。覆盖率 = 命中的关键词 / 总关键词。

这个方法的优点是简单、客观、可复现。缺点是它只测表面覆盖，不测语义正确性。所以我们在 L2 层设计了人工 review 来补充。

但公平地说——keyword coverage 是一个偏弱的指标。如果 LLM 只是泛泛提及关键词但没有真正回答对，也会过。不过我补充一点：我们的 case file 系统会保存每次回答的完整文本，所以任何一次通过都可以人工复核。30 个 case 我都看过，覆盖率的提升和质量提升是正相关的。"

### Q: "你的 93.1% 是不是因为模板太强，把 LLM 变成了检索工具？"

"这是个很好的观察。确实，结构化规划把一些灵活性去掉了——比如比较类任务直接生成 4 步固定的模板。但这恰恰是实验 003 的核心发现：当 LLM Planner '灵活'的结果是 1.2 步、不做分解的时候，这种灵活性没有带来价值。

我们做的是：在 3 种已经证明 LLM 无法可靠处理的场景（比较、分析、研究）上，用规则替代了 LLM 决策。其他场景（简单查询、歧义查询）仍然用 LLM Planner 做 fallback。

本质上这是一个 hybrid 策略：规则覆盖高频、可预测的任务模式，LLM 覆盖长尾、开放式的场景。这个思路在工程实践中很常见——比如 Meta 的 Agent 系统也是用 workflow 模板 + LLM fallback 的混合方案。"

### Q: "你觉得这个项目最大的 engineering challenge 是什么？"

"不是实现 PER 架构，也不是写工具。而是 **设计实验框架来隔离变量**。

Agent 系统是个复杂系统——Planner、Executor、Tools、Memory、Reflection 互相影响。你改了一行 planner 代码，测出来覆盖率下降，但你不知道是 planner 本身变差了，还是 executor 调度变了，还是工具返回变了，还是模型 response 随机波动。

最大的 engineering challenge 是搭建一套能稳定隔离变量的评测流水线：固定 30 题 benchmark、固定评分逻辑、每个 case 保存完整记录、支持版本对比。没有这套基础设施，前面 5 轮实验的数据都是不可靠的。所以 001 实验之前，我们花了最多精力的不是 Agent 架构本身，而是 benchmark 框架。"

### Q: "如果你有更多时间，你会做什么？"

"分三条线：

1. **扩展模板覆盖**——现在只支持 3 种任务类型（比较、分析、研究）。可以扩展到更多（数据提取、多步推理、代码生成等），但我会先验证收益。不是每种任务类型都需要结构化规划。

2. **动态步骤数**——现在模板是固定步数。更好的方案是根据查询复杂度动态决定步数。比如比较 2 个实体和比较 5 个实体应该有不同的步数。

3. **找到模板的边界**——LLM Planner fallback 在歧义查询上仍然很弱（30% 覆盖率）。改进这个比再扩展 3 个模板类型的 ROI 更高。

但我不会继续无限迭代这个项目了。主要原因：边际收益在下降。从 69% 到 93% 是质的飞跃，从 93% 到 94% 只是调的更 fine。现在是时候把项目经验转化成求职收益了。"

### Q: "你踩过最大的坑是什么？"

"实验 002——Planner Granularity。

我当时假设成功率与规划粒度之间存在'倒 U 型曲线'：步骤太少（coarse）效果差，步骤太多（fine）上下文碎片化，中间（normal）最优。

这是个很漂亮的假设。我写了实验设计文档，设计了 3 档 planning_mode，准备跑 90 次执行。

然后实验 003 证明：coarse、normal、fine 三档的平均步骤数都是 1.0-1.2。自变量根本没被操纵成功。

这是我在这个项目上踩的最大坑——**用 90 次执行去验证一个没有先做 manipulation check 的假设**。浪费了 1 次完整 benchmark 的运行。后来养成了习惯：任何控制实验的第一步，永远是验证自变量确实被改变了。这看起来是常识，但不犯一次不会真正记住。"

---

## 核心叙事框架

> 面试时用的三句话 pitch

**一句话：** 我搭了一套 Agent 实验平台，通过 5 轮控制实验发现规划质量比执行架构重要 24 倍。

**两句话：** DAG 和 Serial 没区别？不是 DAG 没用，是 Planner 不拆。解决了拆分问题之后，覆盖率从 69% 跳到 93%。

**三句话：** 这件事的独特之处不是我写了一个 Agent 系统，而是我把 Agent 当实验对象来研究，有假设、有实验、有推翻、有重新设计、有验证。而且整个故事线是五轮实验逐步收敛的。

---

*更新时间：2026-06-21*
