# Agent 系统指标与评估规格说明书

- **版本**：v1.0 · 2026-10-06
- **适用仓库**：`AI_projects/DocMind`（remote `rag`，唯一工程线；分支 `fix/agent-core-and-pipeline-audit` / v1.21.0）
- **读者**：单人开发者本人（本规格假设执行者=维护者，实施按「半天粒度」设计）
- **口径约定**：
  - `[通行]`= 有公开出处、业界沿用（附录 B 给 URL）；`[建议]`= 本规格提案，无直接出处。
  - `run` = 一次用户任务（一次 PERAgentLoop.run）；`calls` = 工具调用次数；`k` = 检索 top-k。
  - 所有公式均为**可直接计算**形式；找不到可计算定义的指标已明确标注"无业界标准定义"。
- **事实核验状态**（本次调研 2026-10-06，会话 Web 搜索预算已耗尽 200/200，部分 URL 未能逐一实测）：
  - ✅ 本次实测/搜索核验：OTel GenAI 语义约定（span/属性名）、τ-bench pass^k 公式与数据、TRAIL 数据集规模与定位准确率、DeepSeek 缓存字段名（官方文档 curl 实测可达）。
  - ⚠️ 经典引用未本次核验：附录 B 中标 ⚠️ 的 URL（多为 arXiv ID 凭训练知识给出，写入文档前建议点击一次）。
  - 📌 代码事实：全部来自 2026-10-06 对工作区代码的实读，附 `file:line`。

---

## 0. 一页结论：最小可行指标集（MVP-12）

### 0.1 三句现状诊断（先说清楚站在哪里）

1. **已有的远比想象多**：Prometheus 指标**定义+埋点都已存在**（`app/core/prometheus.py`，30 个指标；工具调用/规划/反思/记忆/反馈/RAG 全部有 `.inc()` 调用点），但**默认 no-op**（`PROMETHEUS_ENABLED` 未设时全部静默，prometheus.py:18）。Langfuse 组件级 span 已接入。`run_report` 已产出 `clean_success`。`TokenUsageRecord` 已在 MySQL。
2. **真正缺的只有四样**：① 任务级结果没进时序库（只在 SSE 事件和 Redis replay 里，无法聚合查询）；② agent 路径的 token/成本不落库（`claim: chat_service 调 record_token_usage，agent 不调`，已实读核实）；③ 无 Langfuse 根 trace（任务 ID 无法串起子 span）；④ 无检索 golden set（条号命中率无从算起）。
3. **策略**：先把已有信号接进 Prometheus/MySQL（1 天），再加 3 个新埋点（任务结果、参数哈希、token 聚合）（2 天），最后建 golden set 跑夜间批（2-3 天）。LLM judge 只用在 3 个抽样场景，其余全部用确定性规则。

### 0.2 MVP-12 表（先看这张表就能动手）

| # | 层级 | 指标 | 可计算公式 | 采集点（代码坐标） | 存储/聚合 | 告警阈值 [建议初始值] |
|---|------|------|-----------|-------------------|-----------|----------------------|
| 1 | 任务 | **任务成功率 TSR** | `TSR = clean_success_runs / finished_runs`（clean_success 现有定义：fatal_issues 空 且 review≠reject，run_report.py:74） | `loop.py:559`（run_report 生成处）→ 新增 `AGENT_TASK_OUTCOME{status,failure_stage}` counter | Prometheus（实时）+ MySQL 明细 | 先观察 2 周取基线，之后 7d 滚动 < 基线−10pp |
| 2 | 轨迹 | **工具调用失败率** | `ToolFailRate = Σ calls{result=error} / Σ calls` | `executor.py:722/736/758`（**已埋点**） | Prometheus（已有） | >15%（7d 滚动） |
| 3 | 轨迹 | **重复调用率**（并入 #4） | `RedundantRate = 重复调用数 / 总调用数`；重复 := 同一 run 内 `(tool_name, canonical_json(args))` 已出现过 | **新增**：`executor.py:673` 工具循环内加哈希集合 | Prometheus counter + 明细 | >10% |
| 4 | 轨迹 | **有效动作占比 EAR** | `EAR = (calls − failed − redundant) / calls`（注意：这是可计算代理，不含语义正确性；语义交给 §4 onset） | 由 #2+#3 合成（Grafana 表达式，无需新埋点） | Prometheus 公式面板 | <85% |
| 5 | 成本 | **单任务 token**（in/out/cache 分列） | `TokenPerTask = Σ_llm_calls (prompt_tokens + completion_tokens)` | **新增聚合**：`planner.py:296`、`executor.py:605/827`、`reflector.py:155`、`loop.py:336` 把 `resp.usage` 累加进 `ctx.total_tokens`（字段已存在，exec_context.py:82） | Prometheus histogram + MySQL | P95 超预算 |
| 6 | 成本 | **单任务成本 ¥** | `Cost = in×p_in + out×p_out + cache_hit×p_hit`（DeepSeek 价目见附录 B；**必须分 cache hit/miss 计价**） | 同 #5；扩展 `token_usage.py:19 record_token_usage(task_id, cached_hit, cached_miss)` + alembic 007 加列 | MySQL（批聚合）+ 日报 | 日成本 > 设定预算 |
| 7 | 成本 | **单任务墙钟 P95** | `TaskLatency = ctx.duration_ms`（mark_done 已填充，exec_context.py:132-134） | **已存在** | Prometheus histogram + Langfuse | P95 > 120s [建议] |
| 8 | 成本 | **LLM 缓存命中率** | `CacheHitRate = prompt_cache_hit_tokens / (hit + miss)`（DeepSeek usage 字段，✅已核验） | 同 #5 的 usage 捕获点 | Prometheus gauge/公式 | <30%（提示 prompt 前缀抖动） |
| 9 | 检索 | **条号命中率@k** | `ClauseHit@k = (1/Q) Σ_q 1[gt_clause_ids_q ∩ retrieved_clause_ids@k ≠ ∅]` | golden set 夜间 job；检索侧 `rag/retriever.py`（HybridRetriever 返回 `_source` 含 chunk 元数据） | MySQL/JSON 报告 | <0.80 |
| 10 | 质量 | **忠实度（claim 级）** | `Faithfulness = supported_claims / total_claims`（judge 拆 claim 逐条对照 context） | 升级现有 `rag/evaluator.py`（现为整段打分）；夜间全量 + 在线 10% 抽样 | MySQL + 趋势图 | <0.85 |
| 11 | 业务 | **负反馈率 / 人工接管率** | `NegFeedbackRate = 负反馈会话数 / 总会话数`（现有 `agent.py:208` AGENT_FEEDBACK_TOTAL）；到期版：`TakeoverRate = (escalate 触发 + 用户中止) / runs` | 反馈：**已有**；escalation：未接线（P1，见 §2.A5） | Prometheus（已有 counter） | 负反馈率 >5% |
| 12 | 稳定性 | **pass^k（离线）** | `pass^k = E_task[ C(c,k) / C(n,k) ]`（k 次独立重跑全部成功的概率） | 离线 eval harness（`agent/evaluation.py` 已有场景框架，扩展即可） | eval 报告 JSON | 周环比下降 >10pp |

**P0 = #1-#10（本周可上，全部零外购依赖）；P1 = #11（escalation 部分）、#12 及补充指标（见 §2 全表）。**

### 0.3 落地顺序（半天粒度）

| 步骤 | 工作量 | 做什么 | 文件 |
|------|-------|--------|------|
| 1 | 半天 | 设 `PROMETHEUS_ENABLED=true`；加 `AGENT_TASK_OUTCOME` counter 并在 run 结束处 `.inc()` | 环境变量；`core/prometheus.py`、`loop.py:559` 附近 |
| 2 | 1 天 | token 聚合：4 个 LLM 调用点累加 `ctx.total_tokens`（+cache 字段）；run 结束时调用扩展版 `record_token_usage`；alembic 007 | `planner.py`、`executor.py`、`reflector.py`、`loop.py`、`token_usage.py` |
| 3 | 1 天 | 工具循环加 args 哈希（重复检测）+ JSON 解析失败计数 | `executor.py:673-681` |
| 4 | 半天 | Langfuse 根 trace：`loop.run()` 开头建 trace（`id=ctx.task_id`），组件 span 挂 parent | `loop.py`、各组件已有 span 冒烟点 |
| 5 | 2-3 天 | golden set v1（100-200 条）+ 条号命中率 + 夜间评估 job | 新脚本 `scripts/eval/`；`rag/evaluator.py` 升级 |
| 6 | 持续 | Grafana「DocMind Agent Ops」看板 + 告警规则 + 每周五 10 分钟 review | `backend/config/alerts.yml`、`monitoring/` |

### 0.4 明确不做（本阶段）

- 不引入 ClickHouse / Tempo / Jaeger / OTel Collector（理由见 §6.4）。
- 不接 LangSmith / Braintrust / W&B Weave / Helicone（理由见 §6.4）。
- 不做全量在线 LLM judge（只 10% 抽样 + 100% 失败任务，见 §3.4）。
- 不做 TRAIL 级全自动错误定位（确定性信号先行，见 §4）。

---

## 1. 指标分层模型

```
L1 任务级   —— 一个 run 一个数：成功与否、完成度、接管与否        （指挥层）
L2 轨迹级   —— run 内聚合：步数、调用数、EAR、onset、恢复率       （过程层）
L3 单步级   —— 单次工具/LLM 调用：成功、重试、超时、参数失败      （原子层）
L4 成本效率 —— token、¥、墙钟、缓存命中                          （资源层）
L5 检索生成 —— recall@k、条号命中、忠实度、拒答                  （RAG 质量层）
L6 判定质量 —— judge 自身校准：κ、解析失败率、复现率             （元层，评估系统的评估）
```

设计原则：
1. **每层只回答一个问题**——L1"能不能用"，L2"顺不顺"，L3"哪里断"，L4"贵不贵"，L5"答得准不准"，L6"测的准不准"。
2. **能确定性计算的绝不用 LLM**（条号命中率、失败率、重复率零 LLM；LLM 只留给语义判定）。
3. **低基数入 Prometheus，高基数入 MySQL/Langfuse**（task_id/session_id 严禁做 Prometheus label，见 §6.5）。

---

## 2. 指标对照表（详表）

> 列说明：**采**=采集点；**存**=存储与聚合方式；**阈**=建议初始告警阈值（无基线时先观察再定，这是[建议]而非硬性）。每个指标均给可计算公式。

### A. 任务级（L1）

| ID | 指标 | 定义与公式 | 采 | 存 | 阈 | 来源 |
|----|------|-----------|-----|-----|-----|------|
| A1 | 任务成功率 TSR | `TSR = clean_success_runs / finished_runs`。严格版（P1）：再加"无 failed 步" `strict = clean_success ∧ failures=∅`。 | `run_report.py:74`（clean_success 已有）→ `loop.py:559` 处新增 `AGENT_TASK_OUTCOME` counter | Prom counter（实时）+ MySQL `agent_run_metrics`（明细） | 7d 滚动 < 基线−10pp | SWE-bench resolved rate ✅[通行] (arXiv:2310.06770)；τ-bench 以"数据库终态 vs 目标态"判定成功 ✅ (arXiv:2406.12045) |
| A2 | pass@k（离线） | `pass@k = 1 − C(n−c,k)/C(n,k)`，n=重跑次数，c=成功次数，按任务平均。用途：同一 golden 任务集重跑 k 次，衡量"能不能做对"。 | 离线 harness（`agent/evaluation.py` 扩展） | eval 报告 JSON | 无告警，做版本对比 | HumanEval/Codex ✅[通行] (arXiv:2107.03374) |
| A3 | pass^k（离线，稳定性） | `pass^k = E_task[ C(c,k) / C(n,k) ]`（k 次**全部**成功）。τ-bench 实测：GPT-4o retail pass^1≈61% → pass^8 <25%——"偶尔能做对"的真相。用途：DocMind 全量任务"每周同一 golden 集重跑 k=5，看 pass^5"。 | 同上 | eval 报告 JSON | 周环比降 >10pp | τ-bench ✅ (arXiv:2406.12045, ICLR 2025) |
| A4 | 部分完成度 | `PartialCompletion = steps_completed / plan.total_steps`（skipped 单独计数，不算完成）；另有 `Progress = plan.progress`（事件已带 `plan_progress`）。 | `ctx.completed_steps` / `plan.total_steps`（loop 最终日志已有） | Prom histogram + 明细 | 报告不告警 | AgentBoard "progress rate" ⚠️URL待核 (arXiv:2401.13178) [通行] |
| A5 | 人工接管率 | 现行版：`EscalateRate = escalate判定数 / runs`（reflector 已可输出 `escalate` 判定，reflector.py:235，但 loop 未消费）；**过渡版（今天可算）**：`AbandonRate = ABANDONED 任务数 / 总任务数`（TaskStatus.ABANDONED 已定义，agent_task.py:51）。接线后：`TakeoverRate = (escalation 触发且人工介入 + 用户主动中止) / runs`。 | escalation 未接线（== 缺口 G7）；反馈侧 `agent.py:208` 已有 | Prom counter | >3%（过渡期盯 AbandonRate） | 客服 agent 行业的 containment rate = 1 − escalation rate ⚠️URL待核 [通行·语义] |
| A6 | 拒答率 / 正确拒答率 | 拒答判定（规则+抽样校准）：答案命中拒答模板清单（"无法回答/未找到相关信息/建议咨询人工"…）**且**无实质结论。`RefusalRate = refusals / answers`；`CorrectRefusalRate = 正确拒答数 / 不可答题目数`（来自 golden set 不可答分层）；`OverRefusalRate = 误拒数 / 可答题目数`。 | 答案文本 + golden set | MySQL/报告 | OverRefusal >5% 告警 | SQuAD 2.0 不可答约定[通行] (arXiv:1806.03822)；XSTest 过度拒答 ⚠️URL待核 (arXiv:2308.01263) |

### B. 轨迹级与单步级（L2/L3）

| ID | 指标 | 定义与公式 | 采 | 存 | 阈 | 来源 |
|----|------|-----------|-----|-----|-----|------|
| B1 | 步数 / 工具调用数 | `StepsPerTask = len(ctx.completed_steps)`；`CallsPerTask = Σ tool_call 事件`。报 P50/P95（分布比均值有用）。 | run_report dict（`run_report.py:48-58`）/ SSE 事件 | Prom histogram | P95 >12 步告警（防跑飞） | τ-bench 报步数 ✅；Anthropic 多 agent 博客：agent ≈4× chat token、多 agent ≈15× ⚠️URL待核 [通行] |
| B2 | 工具调用失败率 | `ToolFailRate = calls{result=error} / calls`（分面 by tool→找 top offenders）。 | `executor.py:722/736/758` **已埋点** | Prom（已有） | >15% | 现有代码 + `tracing.py:get_tool_failure_ranking()` 同思路 |
| B3 | 工具重试率 | `RetryRate = calls{retry_attempt ≥ 1} / calls`；重试语义已实现（`retry_policy.py` 按 error_type 分类、`_execute_step_with_retry` executor.py:354）；事件已带 `retry_attempt`（events.py:82）。 | executor 重试循环处新增 counter；或从 SSE 事件聚合 | Prom counter | >20% | [建议] |
| B4 | 单步超时率 | `TimeoutRate = calls{error_type ∈ {timeout,TimeoutError}} / calls`。硬超时已在 `executor.py:600`（StepRunner timeout+margin）。 | `executor.py:620` 分支处 | Prom counter | >5% | [建议] |
| B5 | 参数校验失败率 | `ArgFailRate = json_parse_failures / calls`。**现有缺陷**：`executor.py:680-681` JSON 解析失败静默 `args={}` 继续执行——先补计数再谈治理。 | 新增埋点 `executor.py:681` | Prom counter | >3% | [建议]（顺带修静默降级） |
| B6 | 重复调用率 | `RedundantRate = redundant / calls`；`redundant := (tool_name, canonical_json(args))` 在**同一 run 内**已出现过。canonical：键排序+去空白。 | **新增**：`executor.py:673` 循环内维护 seen 集合 | Prom counter + 明细 | >10% | **无业界标准定义**（检索确认）；TRAIL 的 span 级错误标注是最接近的学术形态 ✅ |
| B7 | 有效动作占比 EAR | `EAR = (calls − failed − redundant) / calls` = B2、B5、B6 的合成式。**诚实声明**：EAR 是"可计算代理"，度量"动作没白花钱"，不度量"动作在语义上是否正确"（后者交给 §4 的 onset judge）。 | Grafana 表达式合成（无需新埋点） | Prom 公式面板 | <85% | **无业界标准定义**；用户简报中提出，本规格给出首个可计算公式 [建议] |
| B8 | 首次错误步号 onset | `onset(run) = min{ i : step_i 命中无效信号 }`，信号瀑布见 §4.2。报表：onset 直方图 + `EarlyOnsetShare = onset≤2 的失败 run 数 / 失败 run 总数`（高=规划层问题，低=执行/工具层问题）。 | 近线批：从 Redis replay（`exec_context.py:191`，key `agent:replay:{task_id}`，TTL 7d）+ SSE 事件重建 | MySQL `agent_run_metrics.onset_step` | 报告不告警 | TRAIL ✅：148 traces / 841 errors，span 级标注，最佳模型定位准确率仅 11% (arXiv:2505.08638)；Who&When ⚠️待核 (arXiv:2505.00212) [通行·概念] |
| B9 | 错误恢复率 | `RecoveryRate = P(成功 | 出错) = runs{有≥1 tool error ∧ clean_success} / runs{有≥1 tool error}`。 | run_report.failures 非空 ∧ clean_success | MySQL/批 | <50% 提示重试策略无效 | 自校正综述 ⚠️URL待核 [通行·概念]；`retry_policy.py` 是现成实验场 |
| B10 | 规划-执行偏差 | `PlanHintAccuracy = matched_hints / total_hints`（planner 建议的工具 vs 实际执行的匹配度）；现有 `tracing.py` ExecutionTrace 已记 `planner_tool_hints`/`executor_tool_calls` 两字段，`evaluation.py` 已在做同类统计。 | `tracing.py:99-100` | 内存 TraceStore（建议落 MySQL） | 报告不告警 | [建议]（`evaluation.py` §4 目标"Planner accuracy"已有原型） |

### C. 成本与效率（L4）

| ID | 指标 | 定义与公式 | 采 | 存 | 阈 | 来源 |
|----|------|-----------|-----|-----|-----|------|
| C1 | 单任务 token | `TokenPerTask = Σ_calls prompt_tokens + completion_tokens`（分 input/output/cached 三列）。**缺口 G2**：`ctx.total_tokens`（exec_context.py:82）无人填充；usage 现仅流向 Langfuse（executor.py:639）。 | 4 个调用点聚合：`planner.py:296`、`executor.py:605/827`、`reflector.py:155`、`loop.py:336` | Prom histogram + MySQL | P95 超预算 | Anthropic 博客 token 乘数 ⚠️ |
| C2 | 单任务成本 | `Cost(¥) = in×p_in + out×p_out + cache_hit×p_cache_hit`（DeepSeek 对 cache hit/miss 差别定价，必须分开算）。现有 `_estimate_cost`（token_usage.py:54）缺陷：单价表粗（不分 cache）、无 task_id。 | 扩展 `record_token_usage(task_id=..., cached_hit=..., cached_miss=...)`；agent loop 接入（现仅 chat_service.py:333 调用） | MySQL 批聚合 + 日报 Grafana | 日成本 > 预算 | DeepSeek 官方价目页 ✅可达 (api-docs.deepseek.com/quick_start/pricing) |
| C3 | 缓存命中率 | `CacheHitRate = prompt_cache_hit_tokens / (prompt_cache_hit_tokens + prompt_cache_miss_tokens)`。诊断含义：<30% ⇒ system prompt/工具 schema 前缀不稳定（每次请求换了前缀=白花钱）。 | usage 捕获点（字段名 ✅已核验） | Prom gauge | <30% | DeepSeek Context Caching ✅ (api-docs.deepseek.com/guides/kv_cache) |
| C4 | 墙钟时长 | `TaskLatency = ctx.duration_ms`（✅已填充，exec_context.py:132-134）；阶段拆分：planning（`AGENT_PLANNING_LATENCY` ✅planner.py:225/387）、execution（Σ step duration_ms）、reflection。 | 已存在 | Prom histogram（新增 task 级）+ Langfuse | P95 >120s | [通行] |
| C5 | TTFT | `TTFT = 首个 chunk 事件时刻 − 请求开始`。现有 Grafana 面板已声称"LLM Latency (Time to First Token)"——实现待核（可能是整段延迟）。 | SSE 事件时间戳 | Prom histogram | P95 >10s | [通行] |
| C6 | 每成功任务成本 | `CostPerSuccess = Σcost / clean_success_runs`。联合指标：防止"便宜但全失败"的自我欺骗。 | 批聚合 | MySQL 日报 | 报告不告警 | [建议]；SWE-bench 系工作普遍报告 per-instance cost ⚠️ |

### D. 检索与生成质量（L5）

| ID | 指标 | 定义与公式 | 采 | 存 | 阈 | 来源 |
|----|------|-----------|-----|-----|-----|------|
| D1 | Recall@k / HitRate@k | `Recall@k = (1/Q) Σ_q |rel_q ∩ ret@k| / |rel_q|`；`HitRate@k = (1/Q) Σ_q 1[rel_q ∩ ret@k ≠ ∅]`（单相关条时二者相等）。k∈{5,10,20}，**rerank 前后分别报**。 | golden set 夜间 job；`rag/retriever.py` HybridRetriever | MySQL/JSON | <0.80 告警 | BEIR ⚠️URL待核 (arXiv:2104.08663) [通行] |
| D2 | **条号命中率@k** | `ClauseHit@k = (1/Q) Σ_q 1[gt_clause_ids_q ∩ retrieved_clause_ids@k ≠ ∅]`；strict 变体到子条级（"第十二条第 3 款"）。chunk 含多条号时展开对比；一条号多 chunk 时去重。**为什么它便宜且硬**：零 LLM、零人工（条号由文档结构生成时直接标注）、与用户痛点同构（用户问政策就是要条号）。 | golden set + `_source` 元数据（retriever.py:193 返回 `_source`） | MySQL | <0.80 | 锚：Stanford 法律 AI 引用可靠性研究（citation precision/recall）⚠️待核 (arXiv:2405.20362)；本体 [建议] |
| D3 | MRR | `MRR = (1/Q) Σ_q (1/rank_q)`，rank_q = 第一个相关条目排名，无命中记 0。 | 同 D1 | JSON | 趋势 | [通行] |
| D4 | nDCG@k | `nDCG@k = DCG@k / IDCG@k`，`DCG@k = Σ_{i=1..k} (2^rel_i − 1)/log2(i+1)`；相关性分级：2=条号精确命中，1=同文档/同条相邻，0=其他。 | 同 D1 | JSON | 趋势 | [通行] |
| D5 | 忠实度 | `Faithfulness = supported_claims / total_claims`（judge 先拆 claim 再逐条对照 context 判定，每条附 evidence 引文）。现有 `rag/evaluator.py` 为整段 0-1 打分+regex 兜底解析——**升级点**：claim 级、evidence 字段、解析失败不计 0 分（记 None）。 | `rag/evaluator.py` 升级；夜间全量（便宜模型）+ 在线 10% | MySQL + 趋势 | <0.85 | RAGAS ⚠️URL待核 (arXiv:2309.15217) + docs.ragas.io [通行] |
| D6 | 幻觉率 | 口径 A：`HallucinationRate = 1 − Faithfulness`；口径 B（保守）：含 ≥1 条 unsupported claim 的答案占比。**报告必须注明口径**。 | 同 D5 | 同 D5 | >15%（口径 B） | RAGTruth ⚠️待核 (arXiv:2401.00396) [通行] |
| D7 | Groundedness（零 LLM 代理） | `grounded_hit / grounded_total`（"答案含引用且引用可解析"）；✅现有：`rag/metrics.py` window 聚合 + `RAG_GROUNDED_*` counter + Grafana 面板。 | 已存在 | Prom（已有） | 并入看板 | 现有代码 |
| D8 | 答案相关性 / 正确性 | 相关性：judge 0-1（现有 relevancy prompt）；正确性：有参考答案时 `verdict ∈ {pass, partial, fail}` 三档 judge（§3.6 骨架 a）；无参考时只报相关性+忠实度。 | `rag/evaluator.py` | MySQL | 趋势 | RAGAS [通行] |

### E. 判定质量（L6，评估系统自身）

| ID | 指标 | 定义与公式 | 采 | 存 | 阈 | 来源 |
|----|------|-----------|-----|-----|-----|------|
| E1 | judge 人工一致率 | `κ = Cohen's kappa(judge 判定, 人工判定)`，在 30-50 条固定校准集上（人工判定=用户自己标 1-2 小时）。κ<0.6 ⇒ judge 不可用，先修 prompt/rubric。 | 校准集 + judge | 版本化 JSON | κ ≥0.6 才允许上线 | MT-Bench 报告人类一致率≈80% ⚠️待核 (arXiv:2306.05685) [通行·方法] |
| E2 | judge 解析失败率 | `ParseFailRate = JSON 解析失败次数 / judge 调用次数`。>2% ⇒ 改 prompt 或换输出约束（现有 `_extract_score` regex 兜底要收窄）。 | judge 封装 | Prom counter | >2% 告警 | [建议] |
| E3 | judge 复现率 | `ReproRate = 同输入两次跑判定完全一致次数 / 重跑次数`（temperature=0 下应 >98%；低于此说明模型/prompt 不稳定）。 | 每周对 20 条重跑 | 报告 | <98% 报警 | [建议] |

### F. 与现有代码的信号盘点（缺口清单）

| 信号 | 现状 | 位置 |
|------|------|------|
| 工具成功/失败、延迟 | ✅已埋点（no-op 待启用） | executor.py:715/722/736/758/759 |
| 执行步数、规划次数/延迟、反思判定、记忆命中、用户反馈 | ✅已埋点 | executor.py:232/287、planner.py:199/225/387、reflector.py:188、memory_bridge.py:120、agent.py:208 |
| run_report（clean_success/steps/tool_calls/failures） | ✅生成但仅进日志+SSE+Redis | run_report.py、loop.py:559/564、exec_context.py:191（Redis `agent:replay:*`，TTL 7d） |
| TokenUsageRecord（MySQL） | ⚠️只有 chat_service.py:333 调用；agent 不写；无 task_id/cache 列 | token_usage.py:19、models/token_usage.py |
| Langfuse | ✅组件级 span（planner/executor/reflector/reviewer/memory/registry）；❌无根 trace | observability.py、planner.py:192、executor.py:536/587 |
| Prometheus /metrics | ✅HTTP 层（MetricsCollector + PerformanceASGIMiddleware）+ prometheus_client registry；`METRICS_TOKEN` 可保护 | main.py:525、core/middleware.py |
| Grafana/Prometheus 部署 | ✅compose 服务 + provisioning + alerts.yml（19 面板 RAG 看板，无 agent 面板） | docker-compose.yml:226-258、monitoring/grafana-dashboard.json、backend/config/ |
| Escalation（人工接管） | ⚠️配置类存在（EscalationConfig 已实例化 executor.py:102），但无触发消费方；TaskStatus.WAITING_HUMAN 定义但 executor 未使用 | escalation.py、models/agent_task.py |
| AgentTask 持久化模型 | ⚠️Phase 1 数据模型，executor 未接入（docstring 自述） | models/agent_task.py:6-8 |
| TraceStore（失败排行/语义有效性） | ✅内存版（ring buffer，重启即失）；evaluation.py 已消费 | tracing.py、evaluation.py |
| 重复调用检测 / 参数解析失败计数 / onset | ❌全缺 | — |

---
## 3. LLM-as-judge 可复现做法（含 judge prompt 骨架 + 偏差与缓解）

### 3.1 四条铁律（可复现的全部秘密都在这）

1. **证据锚定**：判分前先抽取证据，分数必须挂引文（quote-first grading）。模型只允许基于"能从上下文原句定位的断言"下结论；找不到证据的断言一律算 unsupported。防止"看起来合理就给分"。
2. **版本固定**：`judge_model_id + prompt_hash + rubric_version` 三元组随每条判定结果入库。任何一项变化 ⇒ 校准集上重跑 §3.6。禁止"悄悄换模型换个分"。
3. **结构化输出**：JSON schema 强制输出；解析失败**不计 0 分**，记 `parse_fail` 并排除出均值（现有 `rag/evaluator.py:67` 解析失败 return 0.0 是错误做法——会把均值往下污染，升级时一并修）。
4. **分档而非连续分**：3-4 档判定（pass / partial / fail + 可选 refuse），档位定义写死在 rubric 里。0-1 连续分（现有 evaluator 做法）的可复现性差——同一答案两次给 0.7 和 0.8 无法解释，档位制有明确边界。`[通行]`（MT-Bench 用单点数字但报告人类一致率≈80% ⚠️arXiv:2306.05685；Prometheus 2 用 1-5 分带 rubric ⚠️arXiv:2405.01535——业界并无"连续分胜过档位"的证据，档位制对单人维护更省心）

### 3.2 rubric 写法模板（挂证据的分档标准）

反例（现状）与正例对比：

```text
❌ 现状（rag/evaluator.py:72-77）：
  "1.0：回答完全基于参考文档；0.7-0.9：主要基于文档…"
  问题：0.7-0.9 与 0.4-0.6 的边界靠感觉；判分不要求引用；无法复现。

✅ 正例（本规格）：
  claim 分解 → 逐条判定 supported/unsupported（必须给出上下文原句作为 evidence）
  → 档位由计数器决定，不由"感觉"决定：
    pass    := unsupported_claims = 0
    partial := unsupported_claims ≥ 1 且 不影响主旨结论
    fail    := 主旨结论依赖至少 1 条 unsupported claim
```

关键：**档位定义必须是前一步输出的确定性函数**（计数、阈值），这样 rubric 里没有形容词，只有规则。

### 3.3 judge prompt 骨架（两个，均可直接落地）

**骨架 A —— 答案忠实度判定**（升级 `rag/evaluator.py` 用）：

```text
System:
你是 DocMind 的评测裁判。你只输出 JSON，不输出任何其他文字。
规则：(1) 每个结论必须引用原文；(2) 找不到证据的事实断言一律计为不支持；
(3) 只依据给定材料，禁止使用外部知识补充判断。

User:
## 上下文（检索片段，含编号）
[1] {chunk_1}
[2] {chunk_2}
## 回答
{answer}
## 输出（严格 JSON）
{
  "claims": [
    {"claim": "<回答中的一条事实断言>",
     "supported": true|false,
     "evidence": "<上下文原句；supported=false 时填 'NONE'>"}
  ],
  "unsupported_count": <int>,
  "verdict": "pass" | "partial" | "fail",
  "reason": "<一句话说明>"
}
```

**骨架 B —— 轨迹单步有效性判定**（§4 onset 用）：

```text
System:
你是 Agent 轨迹审核员。你只输出 JSON。
判定问题：在给定上文下，这一步是否"合理且被正确执行"？
不要事后诸葛：只用该步实际拥有的信息判断，不引入该步之后才知道的结果。

User:
## 任务目标
{goal}
## 该步之前发生了什么（压缩）
{step_history_condensed}
## 当前步骤
action: {tool_name}({args_json})
observation: {result_truncated}
## 无效类别（valid=false 时必选其一）
tool_error | wrong_args | hallucinated_tool | planning_error |
context_ignored | premature_stop | other
## 输出（严格 JSON）
{"valid": true|false,
 "category": null|"<上表之一>",
 "evidence_quote": "<从上方材料中原样引用一句依据>",
 "reason": "<一句话>"}
```

两个骨架与业界实践的对应：证据锚定/逐 claim 验证 ≈ RAGAS faithfulness 的 claim 分解法 ⚠️arXiv:2309.15217；分档+rubric ≈ Prometheus 2 的 rubric 格式 ⚠️arXiv:2405.01535；步骤级验证 ≈ "Let's Verify Step by Step" 的过程监督思路 ⚠️arXiv:2305.20050。`[建议·锚定于上述来源]`

### 3.4 成本控制（单人预算下的三级瀑布）

| 级别 | 手段 | 覆盖比例目标 | 成本 |
|------|------|-------------|------|
| L0 规则（零 LLM） | 拒答模板匹配、引用存在性检查、JSON/schema 校验、工具名存在性、重复调用、空答案、数字/日期与上下文一致性 | 60-80% 的判定 | 0 |
| L1 便宜 judge | 骨架 A/B 用快模型（deepseek 快档），**夜间批处理** + 在线 10% 抽样 | 剩余大部分 | 低 |
| L2 强 judge | 仅：L1 低置信样本、档位边界样本、校准集（§3.6）、用户负反馈任务 | <5% | 中 |

采样策略 `[建议]`：**在线随机 10% + 100% 失败任务 + 100% 负反馈任务**（失败与负反馈是信息量最高的样本，全判）。按任务类型分组批处理（同一模板的多条判定合成一次调用），但**逐条独立给 evidence，禁止跨样本互相参照**。
样本量：估成功率 ±5pp@95% 需 n≈385（1.96²×0.25/0.05²）；小样本必须报 Wilson 置信区间；A/B 对比用同题配对设计（比无配对省一半样本）。锚：Anthropic "Adding Error Bars to Evals" ⚠️arXiv:2411.00640；级联路由思想 ≈ FrugalGPT ⚠️arXiv:2305.05176。`[通行·方法]`+`[建议·参数]`

### 3.5 已知偏差与缓解（新埋点前必读）

| 偏差 | 现象 | 依据 | 缓解（DocMind 具体做法） |
|------|------|------|--------------------------|
| 位置偏差 | 先出现的内容得分偏高 | MT-Bench ⚠️ (arXiv:2306.05685) | 对有"两选一"性质的判定交换顺序跑两次，**仅取一致的判定**；不一致→ L2 升级 |
| 长度/冗长偏差 | 长答案得分偏高 | MT-Bench；AlpacaEval 2.0 专设 length-controlled win rate ⚠️ (arXiv:2404.04475) | rubric 禁止"详细/全面"作为加分项；统计时按答案长度分桶检查是否有系统性偏移 |
| 自我偏好 | 裁判偏爱同族模型输出 | MT-Bench（self-enhancement） | **生成与判分用不同模型家族**（DocMind 生成=DeepSeek ⇒ judge 尽量换家族）；至少不共用同一 system prompt 风格 |
| 谄媚/顺从 | 裁判倾向同意"自信"表述 | 综述类文献 ⚠️ | 先判 claims 再判 verdict（先事实、后结论）；evidence 字段强制非空 |
| 权威/引用偏差 | 带引用（甚至假引用）的答案被默认可信 | 法律 AI 引用可靠性研究 ⚠️ (arXiv:2405.20362) | 引用必须能在 context 中 locate 才算数（citation ≠ evidence）；L0 规则做引用存在性检查 |
| 分数漂移 | 模型/版本更新后整体打分偏移 | — | §3.1 铁律 2 版本固定 + §3.6 校准集定期重跑（指标 E1/E3） |

### 3.6 校准流程（上线前置条件）

1. 从真实 run 抽 30-50 条（分层：成功 / 失败 / 边界），人工标 verdict（预计 1-2 小时，一次投入可复用）。
2. judge 在**盲态**（隐藏人工结果）下判同一组。
3. 算 Cohen's κ（指标 E1）；**κ ≥ 0.6 才允许 judge 结果进入正式指标**；κ 不足时优先修 rubric——经验上问题出在"partial 的定义模糊"，而非模型能力。
4. 冻结：rubric_version + prompt_hash + judge_model_id 写入判定记录与镜像文件。
5. **触发重校准**：judge 模型变更、rubric 变更、连续两周 E3 复现率 <98%。

---

## 4. Failure Onset：首个错误决策步定位

### 4.1 定义与用途

```text
onset(run) = min{ i : step_i 命中无效信号 }        （步骤序 = 事件时间序，i 从 0 起）
            None                                   （全轨迹无错误信号）
```

两个用途（这是它值得做的全部理由）：
1. **数据治理**：给 ExperienceStore（`loop.py:402 _write_experiences`）和未来的任何轨迹学习，提供"轨迹截断点"——只用干净数据。
2. **诊断归因**：`EarlyOnsetShare = onset≤2 的失败 run / 失败 run`——高 ⇒ 规划层有问题（第一步就错）；低 ⇒ 执行/工具层有问题。一个数就分流了两类修法。

### 4.2 三级信号瀑布（低成本优先，这是与 TRAIL 全自动定位的本质区别）

**L1 确定性信号（零 LLM，覆盖目标 60-70%，在 executor/事件里全部已有原始数据）**：

| 信号 | 判定 | 数据来源（现状） |
|------|------|------------------|
| S1 工具执行失败 | 该步 tool_call 的 `ToolResult.success=False` | executor.py:720 `raw_result.success` ✅ |
| S2 参数解析失败 | `json.JSONDecodeError` 分支命中 | executor.py:680 ✅（需补计数） |
| S3 幻觉工具名 | registry 返回 tool-not-found 类 error code | `registry.execute_detailed`（error.code）✅ |
| S4 重复调用 | 第 2+ 次 `(tool,args)` 重复 | §2.B6 新埋点 |
| S5 连续失败 ≥3 | `EscalationConfig.consecutive_tool_failures=3` 的语义 | escalation.py:55 ✅（阈值现成） |
| S6 硬超时 | `error_type ∈ {timeout, TimeoutError}` | executor.py:600 StepRunner ✅ |

**L2 judge 兜底（抽样，只处理"没有确定性信号但任务失败"的轨迹）**：取失败轨迹做骨架 B 步骤判定，**从第一个工具失败步的前 2 步开始**向后扫描（window），第一个 `valid=false` 即 onset；若全 valid 仍失败 ⇒ onset = 末步，类别 `premature_stop`（早停）。每 run 最多 5 次 judge 调用，成本封顶。

**L3 全局归因（本阶段不做）**：Who&When 式"哪个 agent、哪一步、哪类原因"的全自动归因 ⚠️arXiv:2505.00212；TRAIL 全谱系错误分类 ⚠️arXiv:2505.08638。理由：TRAIL 论文自证这类定位极难——148 条人工标注轨迹/841 个错误，最强模型定位准确率仅 **11%**（✅本次搜索核验）。单人项目先吃确定性信号，别为 11% 建流水线。

### 4.3 伪代码（可直接翻译成 Python）

```python
def find_onset(run) -> tuple[int | None, str | None]:
    seen = set()
    consecutive_fail = 0
    for i, step in enumerate(run.steps):                  # 重建自 Redis replay ctx + 事件
        call = step.tool_call
        if call.json_parse_failed:            return i, "wrong_args"        # S2
        if call.error_code == "tool_not_found": return i, "hallucinated_tool" # S3
        if (call.tool, canonical(call.args)) in seen: return i, "redundant"  # S4
        seen.add((call.tool, canonical(call.args)))
        if call.error_type in ("timeout", "TimeoutError"): return i, "timeout" # S6
        if not call.success:
            consecutive_fail += 1
            if consecutive_fail >= 3:         return i, "tool_error"        # S5
        else:
            consecutive_fail = 0
    if run.failed:                                        # L2 兜底
        return judge_first_invalid(run, window_from=first_fail_step(run)-2, max_calls=5)
    return None, None
```

### 4.4 轨迹筛选规则（onset 的下游消费）

```text
if run.clean_success:        usable = full_trajectory            # 全量可用
elif onset is not None:      usable = trajectory[:onset]          # 保留 [0, onset) 前缀
else (失败且判全 valid):     usable = None                        # 存疑数据，不进经验库

筛选后门槛（写进 ExperienceStore 写入条件）：
  len(usable) ≥ 2 且 前缀内无 error 步 才写入。
```

依据：从"成功/正确的部分"学习 ≈ STaR（用成功轨迹自举训练数据）⚠️arXiv:2203.14465、Rejection-sampling FT ⚠️arXiv:2308.01825、DeepSeek-R1 的 RFT 阶段 ⚠️arXiv:2501.12948。`[通行·思想]`+`[建议·具体规则]`

### 4.5 与 TRAIL/Who&When 的关系（诚实定位）

- TRAIL 提供了**错误分类法**（reasoning / execution / planning 三大类、span 级标注）和评测协议（Location Accuracy / Category F1 / Joint Accuracy）✅——DocMind 的 onset_category 词汇表（S1-S6 + L2 的 7 类）是它的简化子集，便于将来对齐。
- Who&When 提供**"自动失败归因"**的任务定义和数据集 ⚠️——DocMind 单 agent 架构下"哪个 agent"问题不存在，价值打折，暂不引入。
- 结论：**DocMind 的目标不是 TRAIL 的语义级定位（11% 天花板），而是确定性级定位（预计 60-70% 覆盖率）+ judge 兜底**。这是单人项目唯一可持续的形态。

---
## 5. RAG 评估集从零建起（golden set bootstrapping，无人工标注版）

### 5.1 六步流程（预计 2-3 天，一次性投入）

```text
① 抽样取种子   → ② 双通道生成问题 → ③ 自动过滤（round-trip） → ④ 条号标注（自动）
→ ⑤ 抽样人检 30 条（1 小时） → ⑥ 冻结 golden_v1 + 变更触发重跑
```

**① 抽样取种子**：从注册文档里按类型分层抽 chunk：每篇抽 3-5 个，覆盖 条款正文 / 表格 / 数字密集段 / 定义段。目标总量 100-200 个种子 chunk。

**② 双通道生成问题**：
- *结构通道（零 LLM）*：从 chunk 元数据（文档标题、条号、小节标题）生成模板问句——"《X 办法》第十二条规定了什么？""关于 Y 事项的条件有哪些？"。这条通道**天然带 gt_clause_ids**，是最硬的样本。
- *LLM 通道*：给 LLM 看 chunk，生成"一位员工会真实提出的问题"（口语化、含同义词替换，模拟真实检索难度），要求问题**仅凭该 chunk 可答**。

**③ 自动过滤（三条硬规则，全部程序化）**：
- *Round-trip*：把生成的问题**只配该 chunk** 交给另一个 LLM 回答，答不上来 ⇒ 丢弃（问题不可答）。
- *反常识检查*：把问题**不给任何 chunk** 让 LLM 答，能答对 ⇒ 丢弃（靠世界知识就能答，不测检索）。
- *歧义去重*：与其它 chunk 的嵌入相似度 >0.95 的问题只留一条；同一 clause 的问题去重。

**④ 条号标注（零成本，本规格的关键设计）**：`gt_clause_ids` 不是"标注"出来的，而是**从生成时所在 chunk 的元数据直接继承**。条款感知切分（仓库已有 `services/semantic_chunker.py`）保证 chunk ↔ 条款对应关系。

**⑤ 抽样人检**：随机抽 30 条，人工只看两个问题："问题是否言之有物且可答？""gt 条号是否正确？"有效率 ≥90% 才冻结；不足则把不合格样本模式反馈给生成 prompt 后重跑 ②。

**⑥ 冻结与版本**：`golden_v1.jsonl` 写入并记录 hash；**私有 holdout**（20 条永不用于任何 prompt 调优）。变更触发重跑：换 chunker / 换 embedding / 换 reranker / 换检索策略时，只跑老集（回归）+ 补新集。

### 5.2 golden set 记录格式

```jsonl
{"qid": "g1-017", "question": "员工出差住宿费报销标准是多少？", "type": "single_hop",
 "answerable": true, "gt_clause_ids": ["费用管理办法#第十二条"],
 "gt_doc_id": "doc_8f3a", "source_chunk_ids": ["chunk_a12"], "generator": "struct_v1"}
{"qid": "g2-003", "question": "公司有没有规定无人机采购流程？", "type": "unanswerable",
 "answerable": false, "gt_clause_ids": [], "generator": "llm_v1"}
```

分层维度（每层都要有量）：`single_hop / multi_hop / unanswerable / numeric / table`；预计分布 ≈ 60/20/10/10（数字与表格各占一成，用来抓"数错数"这类硬错误）。

### 5.3 条号命中率：便宜的硬指标（实现细则）

```text
ClauseHit@k = (1/Q) · Σ_q 1[ gt_clause_ids_q ∩ retrieved_clause_ids(top-k) ≠ ∅ ]

细则：
- retrieved_clause_ids = top-k chunk 的 clause 元数据展开（一个 chunk 含多条号 ⇒ 全部展开）；
  多条号命中按"命中一次"计。
- strict 变体 ClauseHitStrict@k：要求到子条级（第十二条第 3 款）才能命中。
- 同时报 DocHit@k（文档级）——条号级是"硬指标"，文档级是"软底线"，两个数一起看：
  文档命中但条号未命中 ⇒ 切分或排序问题；文档都没命中 ⇒ 召回问题。
- k 取 {5, 10, 20}，**rerank 前/后各报一组**（现有 HybridRetriever 是 ES 关键词 + 向量 + RRF
  融合 + MMR，rag/retriever.py:184-254——每一级的变化都要能被这三组数看见）。
```

为什么它便宜且硬：零 LLM、零人工、可完全确定性复核；且**与用户价值同构**——用户问制度就是要"哪条规定的"。法律 AI 领域对应的学术形态是 citation precision/recall（Stanford "Hallucination-Free?" 研究了法律检索工具的引用可靠性 ⚠️arXiv:2405.20362）。`[建议·本体]`

配套零 LLM 指标（同一次评估跑出）：数字命中（答案数字 ∈ 上下文数字集）、日期命中、拒答正确率（见 §2.A6 公式）、引用存在性（答案引用条号在检索集中可 locate）。

### 5.4 规模与统计

- 冒烟套件 **100-200 条**足够跑周度回归（A/B 对比用同题配对设计，比绝对值省一半样本）。
- 要报告 ±5pp@95% 的绝对准确率 ⇒ n≈385（1.96²×0.25/0.05²）；小样本一律报 Wilson 区间。锚：Anthropic "Adding Error Bars to Evals" ⚠️arXiv:2411.00640。
- 运行方式：夜间 GitHub Actions / 本地计划任务 → 结果写 MySQL → Grafana 面板（trendline）。**评估跑在离线，绝不进线上请求路径。**

### 5.5 工具选择

自建脚本（推荐：和 golden set 格式、条号指标完全贴合，~200 行）为主；`ir_measures`（pytrec_eval 系，标准 IR 指标库）用于交叉验证 nDCG/MRR 实现 ⚠️；RAGAS / TruLens / DeepEval / promptfoo 作为思路参照，不强引依赖——它们的 faithfulness 实现与 §3 骨架 A 同类，自建版可控性更强。`[建议]`

---

## 6. 可观测性选型（OTel 语义约定 + 用什么 / 为什么先不用什么）

### 6.1 OTel GenAI 语义约定：落地用的确切名字（2026-10 核验态）

> 状态：Development（2025-2026 仍在演进，已发生 `gen_ai.system` → `gen_ai.provider.name` 等改名——**照抄下表，别用旧记忆**）。以下名字本次经 Microsoft 蒸馏文档 + AWS Bedrock AgentCore 文档交叉核验 ✅。

**Span 命名规则**：`{gen_ai.operation.name} {target}`——如 `chat gpt-4o-mini`、`invoke_agent DocMindAgent`、`execute_tool knowledge_search`。

| 层面 | 确切名字（backtick 内为逐字） | 说明 |
|------|------------------------------|------|
| 操作名 | `gen_ai.operation.name` ∈ {`chat`, `embeddings`, `execute_tool`, `invoke_agent`, `create_agent`, `fetch_response`, `generate_content`, `invoke_workflow`, `plan`, `retrieval`, `search_memory`, `text_completion`, `update_memory`} | DocMind 映射：run 根 span=`invoke_agent`；工具=`execute_tool`；LLM 调用=`chat`；检索=`retrieval` |
| 提供方 | `gen_ai.provider.name`（**`deepseek` 是谱内合法值** ✅，不是自造值） | 替代废弃的 `gen_ai.system` |
| 模型 | `gen_ai.request.model`、`gen_ai.request.temperature`、`gen_ai.request.max_tokens`、`gen_ai.response.model`、`gen_ai.response.id`、`gen_ai.response.finish_reasons` | 请求/响应侧 |
| 用量 | `gen_ai.usage.input_tokens`、`gen_ai.usage.output_tokens` | **缓存 token 无稳定标准属性**——用自定义 `gen_ai.usage.cached_input_tokens` 并做好未来被改名的准备 |
| Agent | `gen_ai.agent.name`（invoke_agent 上必填）、`gen_ai.agent.id`、`gen_ai.agent.description` | DocMind 填 `{"name": "PERAgentLoop"}` |
| 工具 | `gen_ai.tool.name`、`gen_ai.tool.call.id`、`gen_ai.tool.type`（通常 `"function"`）、`gen_ai.tool.call.arguments`、`gen_ai.tool.call.result` | execute_tool 上 |
| 会话 | `gen_ai.conversation.id`（或 `session.id`） | 对应 DocMind conversation |
| 内容 | 事件 `gen_ai.content.prompt`/`gen_ai.content.completion`（旧，opt-in）；消息属性 `gen_ai.input.messages`/`gen_ai.output.messages`（新） | **含 PII，默认不采**，用开关 |
| 错误语义 | 工具错误由 `execute_tool` span 承载；`invoke_agent` 只有在整个 run 中止时才标错 | 与 DocMind 现有"单工具失败不杀 run"语义天然吻合 |
| token 聚合 | 子模型 span 的 token 汇总到父 `invoke_agent`（仅当父拿得到总数） | §2.C1 聚合逻辑的语义依据 |

指标（Prometheus 侧，名字固定）：`gen_ai.client.token.usage`（histogram，dimension=token type）、`gen_ai.client.operation.duration`。⚠️（指标页本次未直连，名字为长期稳定值，落地时点一次官方页。）替代约定 OpenInference（Arize 系）不是必须——只在你换用 Phoenix 时再考虑。

### 6.2 DocMind 现状（实读）与策略

现状（§2.F 已列）：Langfuse 组件级 span（无根 trace）；Prometheus 30 指标已埋点但默认 no-op；Grafana/Prometheus compose + 19 面板 RAG 看板；MySQL TokenUsageRecord（agent 不写）；HTTP 中间件指标完整。

**策略（本规格建议的方案 A）= 全部沿用，补四处**：

1. `PROMETHEUS_ENABLED=true` + 新增 6 个埋点（§7 清单）——**这是唯一必做的"启用"级动作**。
2. Langfuse 根 trace：`loop.run()` 开头 `lf.trace(name="agent_task", id=ctx.task_id)`，现存的 6 处组件 span（planner.py:195 / executor.py:587 / reflector / reviewer / registry / memory_bridge）改为挂到根 trace 下。半天工作量，解决"任务 ID 串不起子 span"。
3. MySQL 侧：`agent_run_metrics` 表（附录 A）+ `token_usage_records` 加 `task_id/cached_*` 列（alembic 007）——明细与批分析。
4. Grafana 新增「DocMind Agent Ops」看板（与现有 RAG 看板并列），面板布局见附录 B。

四处都不引入新进程、新数据库、新云服务。

### 6.3 为什么先不用 X（每个都给出触发条件）

| 不引入 | 它买的是什么 | 为什么现在不要 | 什么时候再启动 |
|--------|-------------|----------------|----------------|
| Langfuse 全自托管（v3，含 ClickHouse） | 数据完全自持 + 无限量 | 现状已接入（`LANGFUSE_HOST` 可用云端免费层）；v3 自托管要**新增 Postgres + ClickHouse 两个数据库引擎**——为了一张 trace UI 不值得 | 数据不能出路、或月 trace 量超免费额度时；届时用 compose 一键起 |
| OTel Collector + Tempo/Jaeger | 厂商中立的统一 trace 管道 | 当前只有 Langfuse SDK 一个消费方，接 Collector 是"为管道而管道"；Jaeger 本地临时排障可 `docker run` 一次性用 | 有 ≥2 个服务要发 OTel、或想同时喂 Langfuse+Phoenix+Prom 时 |
| SigNoz（OTel-native 全家桶） | APM+trace+log 一体（ClickHouse 底） | 同上：新引擎+新运维面 | 真要自建完整可观测平台时（不早于上一个触发条件） |
| LangSmith | tracing + **datasets/experiments UI** + prompt playground（LangChain 生态最优） | 数据出境 + 无需协作；datasets/experiments 可用"golden set + JSON 报告"平替 | 需要给人演示实验对比、或开始密集调 prompt 需要 playground 时 |
| Braintrust | eval-first + CI 集成 + 对比 UI | 同上；单人 CI 用脚本即可 | 评估套件大到需要治理（>500 条、多实验并行）时 |
| W&B Weave | W&B 生态内的 LLM 观测 | 没有 W&B 使用习惯，别为工具换生态 | 已在用 W&B 做训练实验时 |
| Helicone | **代理式**缓存/计费/限流（请求经它转发） | 引入额外网络跳点；DocMind 已能从 DeepSeek usage 直接算成本 | 需要跨项目统一 API 网关时 |
| Arize Phoenix | OTel-native + 内嵌 eval UI（`pip install` 即用） | 与 Langfuse 功能重叠，二选一即可 | **Plan B**：想要纯本地/纯 Python 的 eval 工作台时替换 Langfuse |
| MAST/Who&When 类全自动失败归因 | 研究级定位 | 见 §4.5：TRAIL 全自动定位天花板 11%，投入产出不成比例 | 有多 agent 架构或专门研究选题时 |

### 6.4 工程注意（三条禁令 + 一条技巧）

1. **高基数禁令**：`task_id / session_id / user_id / query` 一律不进 Prometheus label——进 Langfuse trace、MySQL、或结构化日志。Prometheus 最佳实践明确禁止高基数 label ⚠️（prometheus.io/docs/practices/instrumentation/）。DocMind 现有 label 集（tool、result、decision、direction、intent）是低基数典范，保持这个纪律。
2. **命名先对齐**：新埋点直接采用 §6.1 的名字（`invoke_agent`、`execute_tool`、`gen_ai.usage.*` 的语义），即便现在走的是 Langfuse SDK——将来切 OTel 时是"加装导出"而不是"重命名考古"。
3. **内容默认不采**：prompt/completion 原文只进 Langfuse（可关），Prometheus 永不携带内容。
4. 技巧：Grafana 里用 exemplar 把延迟尖峰跳到对应 Langfuse trace（prometheus_client Python 支持 exemplars）——`[建议]`，`histogram_quantile` 面板配 trace 跳转是排障效率的最大单点提升。

---

## 7. 实施路线图与告警规则

### 7.1 文件级任务清单（承接 §0.3）

| # | 任务 | 文件与坐标 | 验收标准 |
|---|------|-----------|----------|
| 1 | 启用 Prometheus + 任务结果 counter | env `PROMETHEUS_ENABLED=true`；`core/prometheus.py` 加 `AGENT_TASK_OUTCOME{status,failure_stage}`；`loop.py` run 结束处 `.inc()`（clean_success 已有，run_report.py:74） | `/metrics` 出现 agent_task_outcome_total |
| 2 | token 聚合与落库 | `planner.py:296`、`executor.py:605/827`、`reflector.py:155`、`loop.py:336` 加 `ctx.total_tokens += usage...`；`token_usage.py:19` 扩展签名 + alembic 007 | agent 任务在 token_usage_records 里可见，含 task_id |
| 3 | 重复调用 + 参数失败埋点 | `executor.py:673` 循环加 seen 集合 + `AGENT_REDUNDANT_TOOL_CALLS`；`:681` 加 `AGENT_TOOL_JSON_PARSE_FAILURES` | 指标出现，且 JSON 解析失败不再静默 |
| 4 | Langfuse 根 trace | `loop.py:216` run() 开头建 trace（id=task_id），各组件 span 挂 parent | Langfuse 里一个 task 一棵树 |
| 5 | golden set v1 + 夜间评估 | 新 `scripts/eval/build_golden.py`、`run_eval.py`；`rag/evaluator.py` 升级（§3 骨架 A） | 100-200 条 JSONL + 首份评估报告 |
| 6 | 看板与告警 | `monitoring/` 新 dashboard JSON；`backend/config/alerts.yml` 加规则 | 下述 PromQL 生效 |

### 7.2 告警规则（PromQL，直接写进 alerts.yml）

```yaml
- alert: AgentTaskSuccessRateDrop
  expr: >
    sum(rate(agent_task_outcome_total{status="success"}[7d]))
    / sum(rate(agent_task_outcome_total[7d])) < 0.7      # 基线建立后改为 基线-0.10
  for: 1h
- alert: ToolFailureRateHigh
  expr: >
    sum(rate(agent_tool_calls_total{result="error"}[1h]))
    / clamp_min(sum(rate(agent_tool_calls_total[1h])), 0.001) > 0.15
  for: 30m
- alert: TaskLatencyP95High
  expr: histogram_quantile(0.95, sum(rate(agent_task_latency_seconds_bucket[1h])) by (le)) > 120
  for: 30m
- alert: LLMCacheHitRateLow
  expr: >
    sum(rate(agent_llm_tokens_total{direction="cache_hit"}[1d]))
    / clamp_min(sum(rate(agent_llm_tokens_total{direction=~"cache_hit|cache_miss"}[1d])), 0.001) < 0.3
  for: 6h
- alert: RedundantCallRateHigh
  expr: >
    sum(rate(agent_redundant_tool_calls_total[1d]))
    / clamp_min(sum(rate(agent_tool_calls_total[1d])), 0.001) > 0.10
  for: 1h
```

（阈值均为建议初始值；TSR 阈值在积累 2 周基线后按"基线−10pp"改为动态。）

### 7.3 Grafana「DocMind Agent Ops」看板布局

```text
Row 1 任务健康   : TSR（7d 滚动 gauge）| 失败阶段分布（stacked bar）| 每天任务数（bar）
Row 2 过程效率   : 步数 P50/P95（timeseries）| 工具失败率 by tool（top-10 bar）|
                   重复调用率 | EAR（stat）
Row 3 成本       : token/天（stacked: input/output/cache_hit/miss）| 成本 ¥/天 |
                   缓存命中率 | CostPerSuccess（stat）
Row 4 RAG 质量   : 条号命中率@10（trendline，夜更）| 忠实度（trendline）|
                   拒答率/误拒率 | pass^5（周更 stat）
Row 5 诊断       : onset 分布直方图 | EarlyOnsetShare | 负反馈率
```

### 7.4 每周 10 分钟 review ritual `[建议]`

周五看 8 个数：TSR、工具失败率、EAR、P95 时长、缓存命中率、条号命中率、忠实度、负反馈率。任一超阈 → 开一个 issue（按单任务流程修）。**指标的全部意义在这 10 分钟；没有 review ritual 的仪表盘会在两周内变成装饰。**

---

## 附录 A：MySQL DDL 草稿（alembic 007）

```sql
-- 扩展 token 记录：补 task_id 与缓存分列
ALTER TABLE token_usage_records
  ADD COLUMN task_id VARCHAR(36) NULL COMMENT 'agent run id',
  ADD COLUMN cached_hit_tokens  INT NOT NULL DEFAULT 0,
  ADD COLUMN cached_miss_tokens INT NOT NULL DEFAULT 0,
  ADD INDEX idx_tu_task (task_id);

-- 任务级明细（Prometheus 管实时，这里管 SQL 分析/周报/onset）
CREATE TABLE agent_run_metrics (
  task_id            VARCHAR(36) PRIMARY KEY,
  user_id            INT NULL,
  organization_id    INT NULL,
  started_at         DATETIME NOT NULL,
  ended_at           DATETIME NULL,
  status             VARCHAR(16) NOT NULL,       -- success | partial | failed | abandoned
  clean_success      TINYINT(1)  NOT NULL DEFAULT 0,
  failure_stage      VARCHAR(24) NULL,           -- planning | execution | quality_gate | review
  steps_total        SMALLINT NOT NULL DEFAULT 0,
  steps_completed    SMALLINT NOT NULL DEFAULT 0,
  steps_skipped      SMALLINT NOT NULL DEFAULT 0,
  tool_calls         SMALLINT NOT NULL DEFAULT 0,
  tool_calls_failed  SMALLINT NOT NULL DEFAULT 0,
  tool_calls_redundant SMALLINT NOT NULL DEFAULT 0,
  retries            SMALLINT NOT NULL DEFAULT 0,
  replan_count       TINYINT  NOT NULL DEFAULT 0,
  input_tokens       INT NOT NULL DEFAULT 0,
  output_tokens      INT NOT NULL DEFAULT 0,
  cache_hit_tokens   INT NOT NULL DEFAULT 0,
  cache_miss_tokens  INT NOT NULL DEFAULT 0,
  cost_cny           DECIMAL(10,4) NULL,
  duration_ms        INT NOT NULL DEFAULT 0,
  onset_step         SMALLINT NULL,
  onset_category     VARCHAR(32) NULL,
  query_hash         CHAR(64) NULL,              -- sha256(query)，用于聚类，不存原文
  INDEX idx_started (started_at),
  INDEX idx_status  (status),
  INDEX idx_onset   (onset_step)
);
```

写入点：`loop.py` run 结束的 finally 路径（与 `ctx.save()` 同一处，exec_context.py:191 模式），失败也要写（status=failed + failure_stage）。

## 附录 B：来源总表（URL + 核验状态）

| # | 主题 | 来源 | 状态 |
|---|------|------|------|
| 1 | OTel GenAI 语义约定总览 | https://opentelemetry.io/docs/specs/semconv/gen-ai/ | ✅名经两个独立镜像交叉核验；⚠️官网本次未直连 |
| 2 | OTel gen_ai 属性注册表 | https://opentelemetry.io/docs/specs/semconv/registry/attributes/gen-ai/ | ⚠️待点验 |
| 3 | OTel semantic-conventions-genai 仓库 | https://github.com/open-telemetry/semantic-conventions-genai | ⚠️待点验 |
| 4 | GenAI agent traces 蒸馏参考（本次实际依据） | https://github.com/microsoft/skills/blob/main/.github/skills/applicationinsights-web-ts/references/agent-traces.md | ✅搜索结果读到 |
| 5 | spanmetrics connector | https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/connector/spanmetricsconnector | ⚠️待点验 |
| 6 | Prometheus 埋点最佳实践（基数禁令） | https://prometheus.io/docs/practices/instrumentation/ | ⚠️待点验 |
| 7 | Langfuse 自托管 | https://langfuse.com/self-hosting | ⚠️待点验 |
| 8 | Arize Phoenix | https://github.com/Arize-ai/phoenix | ⚠️待点验 |
| 9 | OpenLLMetry | https://github.com/traceloop/openllmetry | ⚠️待点验 |
| 10 | OpenInference（替代约定） | https://github.com/Arize-ai/openinference | ⚠️待点验 |
| 11 | LangSmith | https://docs.smith.langchain.com/ | ⚠️待点验 |
| 12 | Braintrust | https://www.braintrust.dev/ | ⚠️待点验 |
| 13 | W&B Weave | https://weave-docs.wandb.ai/ | ⚠️待点验 |
| 14 | Helicone | https://docs.helicone.ai/ | ⚠️待点验 |
| 15 | SWE-bench / resolved 定义 | https://www.swebench.com/ · arXiv:2310.06770 | ⚠️ |
| 16 | SWE-bench Verified 构建法 | https://openai.com/index/introducing-swe-bench-verified/ | ⚠️ |
| 17 | τ-bench（pass^k 公式、终态判定、pass^8 数据） | arXiv:2406.12045 · https://github.com/sierra-research/tau-bench | ✅本次搜索核验公式与数字 |
| 18 | GAIA | arXiv:2311.12983 | ⚠️ |
| 19 | WebArena | arXiv:2307.13854 · https://webarena.dev | ⚠️ |
| 20 | AgentBench | arXiv:2308.03688 | ⚠️ |
| 21 | AgentBoard（progress rate） | arXiv:2401.13178 | ⚠️ |
| 22 | Terminal-Bench | https://github.com/laude-institute/terminal-bench | ⚠️ |
| 23 | HumanEval / pass@k 估计式 | arXiv:2107.03374 | ⚠️ |
| 24 | TRAIL（分类法、148 traces/841 errors、11% 定位准确率） | arXiv:2505.08638 · https://github.com/patronus-ai/trail-benchmark · https://huggingface.co/datasets/PatronusAI/TRAIL | ✅本次搜索核验 |
| 25 | Who&When 失败归因 | arXiv:2505.00212 | ⚠️ID待点验 |
| 26 | MAST 多 agent 失败分类 | arXiv:2503.13657 | ⚠️ |
| 27 | Anthropic 多 agent 系统（token 乘数） | https://www.anthropic.com/engineering/multi-agent-research-system | ⚠️ |
| 28 | Anthropic Building Effective Agents | https://www.anthropic.com/engineering/building-effective-agents | ⚠️ |
| 29 | MT-Bench / LLM-as-judge 偏差 | arXiv:2306.05685 | ⚠️ |
| 30 | G-Eval | arXiv:2303.16634 | ⚠️ |
| 31 | Prometheus 2（rubric judge） | arXiv:2405.01535 | ⚠️ |
| 32 | JudgeBench | arXiv:2410.12784 | ⚠️ |
| 33 | AlpacaEval 2.0（length-controlled） | arXiv:2404.04475 | ⚠️ |
| 34 | FrugalGPT（级联） | arXiv:2305.05176 | ⚠️ |
| 35 | Adding Error Bars to Evals（样本量） | arXiv:2411.00640 | ⚠️ |
| 36 | Let's Verify Step by Step（过程监督） | arXiv:2305.20050 | ⚠️ |
| 37 | STaR / RFT / DeepSeek-R1（轨迹自举） | arXiv:2203.14465 · arXiv:2308.01825 · arXiv:2501.12948 | ⚠️ |
| 38 | 自校正综述 | arXiv:2406.01297 | ⚠️ID待点验 |
| 39 | RAGAS（claim 级 faithfulness） | arXiv:2309.15217 · https://docs.ragas.io/ | ⚠️ |
| 40 | BEIR | arXiv:2104.08663 | ⚠️ |
| 41 | SQuAD 2.0（不可答约定） | arXiv:1806.03822 | ⚠️ |
| 42 | RAGTruth（幻觉标注） | arXiv:2401.00396 | ⚠️ |
| 43 | CUAD / LegalBench | arXiv:2103.06268 · arXiv:2308.11462 | ⚠️ |
| 44 | 法律 AI 引用可靠性（条号命中率锚点） | arXiv:2405.20362 | ⚠️ |
| 45 | TruLens RAG triad | https://www.trulens.org/ | ⚠️ |
| 46 | OR-Bench / XSTest（过度拒答） | arXiv:2405.20947 · arXiv:2308.01263 | ⚠️ |
| 47 | ir_measures（IR 指标库） | https://github.com/terrierteam/ir_measures | ⚠️ |
| 48 | DeepSeek Context Caching（`prompt_cache_hit_tokens`/`prompt_cache_miss_tokens`） | https://api-docs.deepseek.com/guides/kv_cache | ✅curl 实测可达 |
| 49 | DeepSeek 定价页 | https://api-docs.deepseek.com/quick_start/pricing | ✅curl 实测可达 |
| 50 | Anthropic prompt caching | https://docs.claude.com/en/docs/build-with-claude/prompt-caching | ⚠️ |

> **给用户的提示**：本会话 Web 搜索预算耗尽（200/200 上限）且有域名网络限制，标 ⚠️ 的 URL 未能逐一实测——建议实施前用浏览器快速过一遍（尤其 #2、#25、#38、#46 的 arXiv ID）。核心结论（OTel 名字、pass^k 公式、TRAIL 数据、DeepSeek 字段名、全部代码坐标）均已核验。

## 附录 C：评审用检查单（实现完成后自查）

- [ ] `/metrics` 出现全部新增指标，且 PROMETHEUS_ENABLED=true 已生效（重启后不丢）
- [ ] Langfuse 中随机抽 1 个 task_id：能看到一棵完整树（invoke_agent → planning/chat/execute_tool/reflection）
- [ ] 造一个必失败任务：agent_run_metrics 有行、failure_stage 正确、token 与缓存分列非零
- [ ] 跑一遍 golden set：条号命中率可复算（用脚本从 JSONL + 检索结果独立重算一遍对得上）
- [ ] 校准 30 条：κ ≥0.6 记录在案；judge 输出全部可解析、全部带 evidence
- [ ] 告警演练：手动把工具失败率推过 15%（kill 一个工具）→ 告警触发 → 恢复
- [ ] 周五 ritual 日历提醒已建

