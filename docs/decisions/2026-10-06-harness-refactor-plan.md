# DocMind 重构方案书

> 生成时间：2026-10-06 · **修订：2026-10-07（修订记录见文末）**
> 依据：专项并行调研（产物均已归档）
> 原始产物：`docs/research/008–015`（RAG 审计 / Agent 内核审计 / 对标分析 / 指标规格 / 生态调研）

---

## 第 0 部分 · 三句话结论

1. **不要为了追新形态而重写，但要区分入口与执行平面。** DeerFlow 2.0 是自托管 Web、OpenHands 是 web-first、Supermew（你对标的那台）本身就是前后端架构——"人家都是桌面"这个前提不成立。但"形态只是外壳"这句话**过强**：真正决定能力的是 **执行平面**（可隔离、可恢复、可中断的独立运行层）与 **harness 内核成熟度**；当前 Agent 跑在 FastAPI 请求协程里、**没有独立执行平面**（1.1 的"写好了没接上"全部发生在这个层面）。**结论：保留 Web 形态，把执行平面当独立课题补，而不是换壳。**
2. **"RAG 做得不好"这个判断，一半对一半错。** 错的那半：**你的切分器在结构感知上更丰富**（条款感知 vs 纯字符窗口；**检索效果未被任何评估比较过**）。对的那半：**它现在一个字节的数据都没有** —— ES 0 篇、39/39 chunk 无向量、所有上传挂在余额不足；**而且失败是完全静默的**。
3. **当前 P0 首先不是算法，是接线与静默失败。** 条号早就抽出来了，只是断在三处；子 agent / 技能 / checkpoint / trace 的代码都写好了，只是零调用者。**"写好了没接上"是这套系统当前的主要病症。** 但注意：这是**当前阻塞的归因**，不等于算法层没问题——索引恢复后真正的瓶颈是接线还是算法（条号、RRF、截断顺序），需要用评估集（3.2）测出来再改；第 2 档相关项已标"假设待验证"。

---

## 第 1 部分 · 现状体检（全部为活体读回，非自报）

| 项 | 读回结果 | 来源 |
|---|---|---|
| `develop` / CI | `e5b2eb1`，CI 全绿；0 issue / 0 PR（**截至 2026-10-06 15:35 的读数**——该"0/0"仅持续约 4 小时，随后即有 #179–#209 等写操作；最新 `develop @ 8e098ba`） | git / GitHub API |
| `/health` | 五项服务全 healthy | 活体 |
| **ES 索引** | **`docs.count = 0`**（索引名实为 `paicongming_knowledge`） | 活体 |
| **MySQL chunk** | **39 条，39 条 `embedding_id` 为 NULL** | 活体 |
| **搜索端点** | **5 个查询全部 0 条，HTTP 200，`search_time: null`** | 活体 |
| 上传失败原因 | `429 Error 1113 余额不足或无可用资源包` | 活体 |
| **`/metrics`** | 494 行，**以 `agent_`/`rag_`/`llm_` 开头 = 0 行** | 活体 |
| Prometheus 指标定义 | **31 个，全部已埋点，默认 no-op** | `core/prometheus.py:80-118` |
| `PROMETHEUS_ENABLED` | **compose / 三个 env / K8s / 运行容器 —— 一个地方都没设** | grep + `docker exec env` |
| `eval_first_report.json` | 签进仓库：`passed: 0 / total: 10`，日期 **2026-06-15** | 仓库文件 |
| CI 里跑评估 | **`grep eval .github/workflows/*.yml` 零命中** | grep |

### 1.1 三个"写好了但没接上"（Agent 内核审计，`docs/research/010`）

| 能力 | 状态 | 位置 |
|---|---|---|
| 子 agent 委派（含 3 并发上限） | **零调用者** | `agent/subagent.py:24,73` |
| 技能学习闭环 | **零调用者** | `agent/skills.py:181,192` |
| `skills/` 的 SKILL.md 进 prompt | 只被 REST 端点读 | `curated_skills.py` |
| Checkpoint / 断点续跑 | 表建好了、状态机定义了、**零 CRUD** | `models/agent_task.py` |
| 两套 trace 设施 | 只被未挂载的 `agent_api.py` 用；**已按 dev-only 标注处置（#242）** | `core/trace_logger.py`、`agent/tracing.py` |
| 分层结果截断器 `_smart_truncate` | **零调用** | `loop.py:56` |
| 人工升级 `escalation` | 实例化了但**从不查询** | `escalation.py:51` |

**另有三个"配置存在但没人读"**：`max_iterations`（PER 循环实际没有迭代上限）、`budget_exceeded`（错误码定义了无处产生）、`load_skill_reference`（prompt 里承诺了，**函数不存在**）。

**最要命的一条**：`ExecutionContext.total_tokens` **全仓零赋值** → 系统答不出"这次任务花了多少 token"。

### 1.2 RAG 的四个 P0 缺陷（管线审计 + 活体检查，`docs/research/009` / `008`）

| # | 缺陷 | 位置 |
|---|---|---|
| 1 | 榜首"相关性评分"被构造性锁死在 ~1.0 —— `normalized = 0.1+0.9*(score-min)/(max-min)`，榜首恒为 `score_max` | `rag/retriever.py:341-361` |
| 2 | 送进 LLM 的每源正文只有 **260 字**，且找不到字面量时直接取 `text[:260]`；且该 snippet 经 `or` 短路使 `compress_context_list` 的全文压缩永远不生效（见 #186 补充证据） | `rag/retriever.py:257-267`、`pipeline.py:356-359` |
| 3 | **条号三重断链**：`metadata.enabled=false` → 顶层 `section_title` 无人写 → `_source` 白名单不含它 | `core/elasticsearch.py:156,140` |
| 4 | **每文档最多 2 个 chunk**，且在 **rerank 之前**丢弃 | `rag/retriever.py:321-324` |

另有 P1 六条 / P2 十二条，见 `009_rag-pipeline-audit.md` §4。

**其中 P2 里有一条会直接毁数据**：`recreate_knowledge_index()`（`elasticsearch.py:179-192`）**每次处理文档都会走到**，且 `VECTOR_DIMENSION` 一改就**无二次确认删除整个索引**。**（已于 #212 修复：不再自动重建，删除必须显式 `confirm=True`。）**

---

## 第 2 部分 · 对标：Supermew 的真实水平

我们对标的那台 `icey1287/SuperMew`（884 star，LangChain+LangGraph+Milvus，前后端架构）—— **它的检索底座在结构与融合策略上更简单**（是否存在检索质量差距，未经评估比较）：

| 维度 | SuperMew | DocMind |
|---|---|---|
| **切分** | **纯字符窗口递归**：L1=2400 / L2=1600 / **L3=800 字符**；全 `backend/` 搜 `条/款/章/article_no/clause` **零命中** | `semantic_chunker.py` 有 **8 条 CLAUSE_PATTERNS**（第X条/章/节/款、一、、（一）、(1)、【】、A.） |
| **条号进检索** | 无此概念 | **抽出来了**，但断在三处（见 1.2 #3） |
| **融合 / 重排** | `RRFRanker(k=60)` **等权**、候选池仅 15；auto-merge 阈值=2 过低且合并后不重排；rerank 只喂正文 | RRF k=60 **同样等权** + 候选池 80/臂 + cross-encoder，**同类问题但候选池更大**（P1 #5–#8，另见 2.9） |

> **⚠️ 一处需要精确化的认知（专项核验）**：`k=60` **本身不是问题**。
> RRF 原论文原文是 `k=60 was fixed during a pilot investigation`，且 k 在 10–100 区间内 MAP 只差
> `.2123–.2147`，作者原话 *"near-optimal, but the choice was not critical"*。
> **一个候选机制是「等权」**（注意：厂商文档背书的是"存在这种风险"，**不证明本案的主因就是它**——候选至少还有两个：候选池截断、每文档 2 条上限发生在 rerank 之前，见 5.2 与 2.5；区分它们需要最小实验）：
> - **Qdrant 官方原话**：*"BM25 may win on identifier-heavy ones. Assigning equal weight to both can let the weaker retriever drag down results."*
> - **OpenSearch 官方文档给了一个数值算例**：单通道的第 1 名得 0.0164，输给双通道的第 1 名 0.0325
> - **Weaviate 官方示例**演示"关键词第一名被向量第一名挤到第二"
>
> 而「《X法》第七条」正是 **identifier-heavy query**（查询的判别力几乎全在"第七条"这个标识符上，
> 语义含量≈0）。**等权 RRF 在这种情况下可能让向量通道把正确的词法命中拖下去**（风险有厂商文档背书；对本案的实际贡献待实验区分）。
>
> 相关事实：DocMind 跑的是 **ES 8.11.0**；ES 的**原生 RRF retriever 8.8 引入、8.16 才 GA**，
> 且**原生 RRF 不支持权重**（8.16 的 `linear` retriever 才支持加权融合）。
> 所以**自己实现 RRF 是对的选择 —— 前提是给它加上权重和查询侧路由。**

> **⚠️⚠️ 但权重不能凭手感调 —— 这是本轮唯一一处「计划顺序需要改」的地方。**
> **Qdrant 官方原话**：*"Hand-tuned weights without measurement are unlikely to beat the default reliably."*
> 它的官方指引是：**有评估集** → 按训练/验证切分做权重网格搜索（官方另提供 `tune_rrf_weights` notebook）；
> **没有评估集 → 保持默认 (1.0, 1.0)。**
> **OpenSearch 官方**同样警告：*"Weighting reintroduces the tuning effort that unweighted RRF avoids, so change the weights **only when you can measure the effect** on relevance."*
>
> **结论：3.2（建检索 golden set + 条号命中率）必须先于 2.9（加权 RRF）。** 否则就是在没有仪表盘的情况下拧旋钮 —— 而这恰恰是本次要纠正的那种做法。
>
> 另一个必须知道的数学事实（有官方数值算例）：k=60 时，单列表第 1 名得 `1/61 ≈ 0.016393`；
> **任意文档只要同时出现在两个列表里且名次 r < 62，就能压过"只在单列表排第一"的文档**
> （`2/(60+r) > 1/61` ⟺ `r < 62`）。OpenSearch 官方文档给的算例是
> `id2(BM25#2+向量#1)=0.032522473` 反超 `id1(仅BM25#1)=0.016393442`。
> **「两路都进前 61 名」在数学上几乎总压过「单通道自信」** —— 这是"第七条"可能输给"第十二条"的候选机制之一（另两个并存候选：每文档 2 条上限发生在 rerank 前、listwise/判官语义排序，见 5.2）。**三者均待最小实验区分**——`keyword_rank` / `vector_rank` 在 `retriever.py` 里本就可取到，缺的是实验，不是数据通道。
>
> 顺带一条**必须避免的写法**：**LangChain `SelfQueryRetriever` 的官方文档页已经不存在了**
> （旧 URL `python.langchain.com/docs/how_to/self_query/` 实测重定向到 overview；官方 sitemap/llms.txt 索引里也没有）。
> **不要在方案或文档里引用它。** 现行可引的相邻入口只有 LlamaIndex 的
> Auto-Retrieval（`VectorIndexAutoRetriever`）和 Router 模块。
| **"相关性评分"** | 判官按"主题相关性"设计，**无条号一致性校验** → 这就是"问第七条召回第十二条还判 yes"的直接来源 | 更糟：那个分是**批内 min-max**，榜首恒等于 1.0 |
| **面板可信度** | 「RRF名次 #1」实为 `chunk.rrf_rank \|\| sIndex + 1` —— **缺省时就是显示位置**，面板在骗人 | 面板只展示，不谎报（但分节可能整块不渲染，P2 #19） |
| **评估体系** | ⭐ **世界级**：14 指标带下限+回归容忍度、provenance 门禁、baseline 逐字节可重建、CI 强制 | **零件齐、门禁零**（见第 1 部分） |
| **Agent 内核** | 10 层固定中间件链（测试锁定）、Run/Checkpoint/Resume、`Last-Event-ID` 重放、skill 渐进加载、HITL | 六件没接上（见 1.1） |

**结论**：**该学的是它的评估纪律，不是它的检索实现。** 检索这块：**结构感知上你更强，但检索质量是否领先未经评估比较**（抽出来的条号还没接通检索，见 1.2 #3）。

---

## 第 3 部分 · 三档工作计划

> 每档都带**验收判据**。铁律：**新增的检查必须先证明它能红** —— 构造一次预期失败，确认它真的失败。

### 第 1 档 · 阻塞项（不做，后面全是空中楼阁）

> **状态（2026-10-07 更新）：本档代码侧已全部完成并合入 `develop`** ——
> 1.2 → #212、1.3 → #214、1.4/1.5 → #215、CI 补真实 Elasticsearch 服务容器 → #217，
> 并在过程中修复了"搜索端点恒返回空结果"的元组 bug（#219 → #221）。
> **1.1（嵌入服务）与运行侧（真实索引数据进 ES）仍待执行** —— 这是当前阻塞链上仅剩的一环。

| # | 动作 | 验收判据 |
|---|---|---|
| 1.1 | **决定嵌入服务**：给智谱充值，或切本地（settings 默认已指向 Ollama `localhost:11434` 的 `nomic-embed-text`） | 至少一次真实上传走完全链路，`knowledge_processing_jobs.status = SUCCESS` |
| 1.2 | **先修 `recreate_knowledge_index()`**：改成显式迁移路径，禁止"维度不符就删库" | 造一个维度不符场景 → 必须报错拒绝，**索引里的数据仍在** |
| 1.3 | **索引后校验**：`_mark_success` 之前断言**按 `document_id` 过滤后**该文档在 ES 的 chunk 数 > 0，否则置 FAILED 并写原因（实现见 #214；残余缺口另立 #224） | 把 ES 指向死端口上传一份文档 → 状态必须是 **FAILED 且带原因**，不能是 INDEXED |
| 1.4 | **搜索端点不再静默**：结果为空时给出可区分的状态（实现取四态 `ok` / `empty_index` / `index_error` / `unknown`，#215） | 空索引下搜索 → 响应里能读到"索引为空"，而不是裸的 `results: []` |
| 1.5 | **`/health` 增加知识库项**（三分支，实现 #215）：索引配置错误 → unhealthy；**有文档声称 INDEXED 而索引为空 → unhealthy**；索引为空且无声明 → healthy（全新部署合法，不再误报） | 全新部署（0/0）→ 必须 **healthy**；造一篇 INDEXED 文档且索引为空 → 必须 unhealthy。**残余（#223）**：部分丢失的中间态仍漏检；`/health` 恒 200、探针共用，unhealthy 传不到编排层 |

**这一档不需要你做任何架构决定**（除了 1.1 的充值/切换二选一）。

### 第 2 档 · 能感受到的改进（1–2 周）

> **2026-10-07 修订：以下 2.3 / 2.4 / 2.5 / 2.9 / 2.10 均属"待验证假设"。** 执行顺序必须是：
> **先建评估集（3.2 提前） → 逐阶段抓 trace 做最小可证伪实验 → 只修被实验证明的那一环。**
> 在拿到实验结论之前，不按任何单一解释（如"等权 RRF 必然…"）直接改权重/改顺序。

| # | 动作 | 验收判据 |
|---|---|---|
| 2.1 | **任务级结果 + token/成本落库** —— 在**统一入口**做 usage 归一化后写入 `ExecutionContext.total_tokens`（**不是逐点打补丁**：核心 PER 环实为 6 个调用点，另有 subagent / retry / 流式等未来调用者，点状方案必漏） | 跑一次 agent 任务 → 库里能读到该任务的 token 数与成本；**先构造一次"不累加"的失败证明检查能红** |
| 2.2 | **开启 31 个已有指标** —— compose/env 里设 `PROMETHEUS_ENABLED=true`（先验证 Windows+Grafana 下无多进程死锁） | `/metrics` 里出现 `agent_*` 行；Grafana 看板有数 |
| 2.3 | **接通条号** —— `section_title` 提到 `_source` 顶层并 `keyword` 索引（**当前只有 `section_title` 字符串——`article_no` 并不存在，全仓零命中**）；检索期命中「第N条」时加 `term` 过滤或高 boost。注意：`query_processor.py` 现有的 `section_title^1.8` boost 是 no-op（字段不在 mapping，见 #188 / #194） | 用「《X法》第七条」查询 → 返回的第一条 `section_title` 必须是第七条 |
| 2.4 | **修 P0 #1**（假的相关性评分）—— 改用绝对信号（ES `_score` / cosine / rerank `relevance_score`），把"相关性分"与"排序位置"分成两个字段 | 取两条命中（真相关/完全不相关）→ 榜首分**必须不同**（现在是恒 1.0） |
| 2.5 | **修 P0 #2 / #4** —— prompt 用全文交给 token 预算裁；每文档上限改成软惩罚且挪到 rerank 之后。**顺序必须是「小片段检索 → 小片段精排 → 进 prompt 前再扩展」**（langchain4j PR #6132、mrson_rag PR #5 的做法），**不要学 Supermew 的「先合并大父块、再精排」** | 构造第 3 名才命中的 chunk → 必须能出现在最终上下文里 |
| 2.6 | **rerank 不再静默降级** —— 返回 `(hits, strategy, ok)`，写进 `debug_info` | 把 rerank 指向死地址 → 面板必须显示"降级/跳过"（现在是静默原序） |
| 2.7 | **接线已写好的模块** —— subagent / skills / `_smart_truncate` / trace 落盘 | 每个模块：构造一次真实调用，确认日志里出现它 |
| 2.8 | **修 `executor.py:680` 静默吞 JSON 解析错误**（参数解析失败时 `args={}` 继续执行） | 造一个参数非法的 tool call → 必须是显式 tool_error。**注意：合法空参数对象 `{}` 必须仍能继续执行——"解析失败"与"空参数"不得合并处理**（当前两条路径都归 `{}`） |
| 2.9 | **给 RRF 加权重 + 查询侧路由**（假设待验证）—— 识别 identifier-heavy 查询（「第N条」只是其中一类：ISO / CVE / 版本号 / 工单号 / RFC / 法条编号…），让**词法通道主导**；`hybrid` 不再是唯一策略。**⚠️ 必须在 3.2（评估集）之后做，且先以最小实验确定"错在哪一阶段"再决定动不动它** —— 见下方注 | 用「《X法》第七条」查询 → 在**只有词法通道能命中**的构造下（把该 chunk 的向量故意置成噪声），结果必须仍然是第七条 |
| 2.10 | **查询侧数字归一化** —— 用户写"第7条"与"第七条"必须能命中同一批（当前**无任何归一化**：无中文数字↔阿拉伯转换、无全角处理） | 分别用「第七条」和「第7条」查询 → **核心正确 chunk 必须重合**（`ClauseHit@k` 一致、`Recall@k` 不降、Top-1 稳定）——不要求两次返回的集合逐字节相同 |

### 第 3 档 · 结构性改造（2–4 周，需要你拍板）

| # | 动作 | 说明 |
|---|---|---|
| 3.1 | **评估门禁上 CI** —— **对齐** SuperMew 的评估契约字段（Dataset / Observation / GatePolicy / Report），**按单人项目体量裁剪 Schema**（学纪律，不是搬产物）：provenance 门禁 + baseline 可重建 + `--fail-on-regression` | **这是"指标"的正解**：指标不是仪表盘好看，是"能不能红、红了拦不拦得住" |
| 3.2 | **建检索 golden set** —— **起步 20–30 条**（对齐仓库现存 `backend/tests/unit/eval_dataset.jsonl` 的 25 条；SuperMew 的 ≥200 是它的远期目标，随 case 增长），覆盖类别同原方案（单事实/定义/参数/跨文档/对比/多跳/时间版本/表格/代码/歧义HITL/无知识/来源冲突/近似实体/错别字/长问题/提示注入），**每条带 `gold_chunks`**。**加一条 SuperMew 没有的：条号命中率 `ClauseHit@k` + near-miss 负难例**（"问第七条、正确答案在第十二条旁"的自动化版本） | 有了它，"问第七条召回第十二条"会**立刻在 CI 变红** |
| 3.3 | **Run 级 resume / checkpoint** —— 照抄最小版：OpenHands"事件先落盘再分发" + LangGraph"每步 checkpoint + pending writes" | 中断一次 run → 能恢复继续；**先证明"不接 checkpoint 时会丢"** |
| 3.4 | **面板从"展示结果"升级为"展示在哪一步丢了什么"** —— **先定义 `rag_stage` 事件**（stage / input_count / output_count / drop_reason / run_id），每个数字必须能从 trace 重建；再做漏斗逐级计数（召回→去重→合并后→精排输入→输出）、覆盖缺口、降级标记、证据评分三维（相关性/可回答性/置信度）、评分决策 | 这是 SuperMew 面板最值得借鉴的一点：**可诊断性**，不是结果展示。**没有事件契约的漏斗数字只是"大屏好看"** |
| 3.5 | **"手和钥匙"** —— 把 DocMind 暴露为 **MCP server**，让用户已装的 Hermes / Pi / Claude Code 直接调用它的知识库与工具 | 反向整合，**成本低于自研桌面端**（估算，未做成本模型）；对上决策文档里"记忆引擎独立成 MCP Server"的构想。**注意：MCP 接入等于新增一个外部控制面**——认证 / 租户 / 限流 / 审计都要补齐，应先稳定内部事件契约（见 #204 / #222）再包适配层 |

---

## 第 4 部分 · 指标规格（"一定要有什么指标"的答案）

**MVP-12**（详细公式/采集点/阈值见 `014_agent-metrics-spec.md`）：

任务级：**任务成功率 TSR** · pass^k
工具级：**工具失败率** · **重复调用率** · **有效工具调用占比 EAR**
成本级：**单任务 token** · **单任务成本** · **P95 时长** · **LLM 缓存命中率**
RAG 级：**条号命中率@k** · **claim 级忠实度** · 负反馈率

**三条纪律**（对齐 SuperMew 的做法——这三条它做对了）：
1. **provenance 门禁** —— 静态数据**不能**冒充实时评估结果
2. **显式 unavailable** —— 算不了的指标**列出来并写明原因**，禁止用字符重合之类的代餐伪装
3. **门禁不可自我弱化** —— Release CLI 拒绝关闭 critical protection、降低绝对阈值、放宽回归容差

---

## 第 5 部分 · 明确不做的（避免范围蔓延）

- ❌ **不转 CLI / 桌面形态** —— **不为追新形态重写**（入口与执行平面是两个问题：执行平面要补，壳不用换）；多租户/RBAC/审计是 Web 的独有资产
- ❌ **不引入 LangChain / LangGraph** —— DocMind 的 PER 循环是手写状态机，等价能力可用显式状态 + 路由表达；引入框架的收益主要是 checkpointer 与 interrupt，代价是承认一个新框架。**"自研更划算"降级为待比较项**：只支持"不必为了像先进 Agent 而引入框架"，不支持"自研总成本更低"——实施 resume/checkpoint（#201）之前应做一次显式对比（迁移成本 / 运行时复杂度 / 可调试性 / 依赖成本 / 维护负担）再拍板
- ❌ **不照抄 SuperMew 的检索实现** —— 它的检索在结构与融合策略上更简单（见第 2 部分；是否有质量差距未经评估），只学它的**评估纪律**
- ❌ **不追 `jina-reranker-v3`** —— 两条独立的理由，第二条是决定性的：
  1. 它是 **CC-BY-NC-4.0 非商用**（HF 模型卡 + arXiv 2509.25085 双源核实）
  2. **它是 listwise 重排器，而且输入是纯 query-document pair、没有任何指令模板** —— 意味着你**没有任何接口把"条号必须等于第七条"这个硬约束告诉它**。listwise 做的是"整表相对排序"：当候选互为正例（相邻法条彼此高度相似）时，它给出的会是"整体语义最贴合"的排序，而点状硬约束会被淹没。**这是"第七条"可能输给"第十二条"的第三个候选归因**（与 §2 的"等权 RRF"、`P0 #4` 的"每文档 2 条上限"并列——同一现象目前有三处各自排他的解释，**均待最小实验区分**；注意：v3 是 131K 上下文，**不是截断问题**）
- ❌ **不要用通用 reranker 硬扛法律域** —— **LegalBench-RAG**（arXiv 2408.10343）的结论是：*"a general-purpose model such as the Cohere reranker actually **hinders** the performance of the retrieval in a legal context"*。**通用重排器在法律域可能帮倒忙**，必须用域内评测集验证

### 5.2 Rerank 这块该怎么做（专项核验后的结论）

**① 输入必须带结构信号 —— 这是最有官方依据的一条改动**

**Anthropic《Contextual Retrieval》官方实测数字**（原文）：
| 手段 | top-20 检索失败率 |
|---|---|
| 基线 | 5.7% |
| + Contextual Embeddings | **3.7%（降 35%）** |
| + Contextual BM25 | **2.9%（降 49%）** |
| + **rerank** | **1.9%（降 67%）** |

做法是把 50–100 token 的语境**拼在 chunk 正文前**，"before embedding it and **before creating the BM25 index**"。

> **你已经在做这件事了** —— `document_parser.py:418 / 430-434` 会生成 50-100 字文档摘要并前缀拼到每个 chunk（`【文档背景：…】`）。
> **但和 Anthropic 的做法有一个关键差别**：它前缀的是**文档级摘要**，Anthropic 的是**"把这个 chunk 放到全文里定位"的块级语境**。
> 而且管线审计（`docs/research/009`）查到 `retriever.py:98/176/408/421` 的 `_source` 白名单**不含这个前缀字段** —— **很可能连这个摘要都在检索期被丢掉了。**

另外有官方先例支持"把标题拼进待重排文本"：**Cohere 官方文档示例直接把文档写成 `"Title: …\nContent: …"` 传入**。

**② 选型（挂证据）**

| 场景 | 推荐 | 理由 |
|---|---|---|
| **自部署·中文** | **Qwen3-Reranker 0.6B（质量优先上 4B）** | **Apache-2.0 可商用**；32K 上下文；**instruction-aware —— 可以把"条号一致性"写进指令**；官方 CMTEB-R 中文重排 71.31（0.6B）/ 75.94（4B） |
| 备选基线 | `bge-reranker-v2-m3` | 生态最广、Apache-2.0，**但默认 `max_length=512`，必须显式提高** |
| 备选 | `mxbai-rerank-large-v2` | Apache-2.0、官方中文 NDCG 84.16、8K/32K、延迟低 |
| API | Cohere Rerank 4.0 / Voyage rerank-3 | Voyage 更便宜（$0.05/1M）且**官方给了法律域指令示例**："Retrieve regulatory documents and legal statutes, not court cases." |

**③ 截断坑（对照表）**：`bge-reranker-v2-m3` 能力 8192 但**默认 512**；Cohere `max_tokens_per_doc` **默认 4096**；Voyage `truncation=True` **默认静默截断**。**没有任何一家在截断时自动保留标题或编号** —— 所以**结构信号必须拼在最前面**。
- ❌ **不把 auto-merging 当召回率放大器** —— **LlamaIndex 官方 notebook 自己的评估结论是 "The results are roughly the same."**（相对基线基本持平）。所以 Supermew 那套「叶子召回 → 合并父块 → 精排」**不是在提升召回，而是在放大已经错了的召回**。你要的层级结构应该服务于"**命中小片段后按需把它的父块取回来**"，而不是"命中若干小片段就把它们一起换成一个大块"。顺带：llama_index issue #23224 记录了层级切分 NEXT 关系缺失的已知缺陷；LangChain 的 `ParentDocumentRetriever` 是**无阈值、命中即换父、父块可到整文档**——三种合并策略（LlamaIndex 比例 / LangChain 无门槛 / Supermew 计数≥2）都已被源码级核验
- ❌ **不动 DeerFlow 那套 Agent Teams / AISandBox** —— 见下方第 5.1 节的可移植性判断（沙箱那块强依赖容器编排，Agent Teams 那块**值得抄**）

### 5.1 DeerFlow 可移植性判断（一手核实：deer-flow main 分支 2026-10-06 快照）

**先纠三个前提**（都已用 grep 复核）：

1. **DeerFlow 本身就是前后端架构** —— `backend/`（FastAPI Gateway + LangGraph 运行时）+ `frontend/`（Next.js）+ Nginx 单一入口。四服务拓扑：Nginx `2026` / Gateway `8001` / Frontend `3000` / Provisioner `8002`。**"人家都是桌面"这个前提到此彻底不成立。**
2. **"Context Engineering 四件套 Write/Select/Compress/Isolate" 在该仓库里查无出处** —— `Write.*Select.*Compress.*Isolate` 全仓 **0 命中**。README 里确实有 "Context Engineering" 一节（`:2130`），但内容讲的是：表格工具输出摘要、**子 Agent 上下文隔离**、摘要压缩、工具调用恢复、可见的运行完成、run history。**别按那个四件套去建路线图。**
3. **`micro_compact` 不存在** —— 全仓 **0 命中**。

**真正值得抄的四件事**（都在 DocMind 上可落地）：

| 模式 | DeerFlow 怎么做的 | 对 DocMind |
|---|---|---|
| **harness 独立成包** | `backend/packages/harness/` → 独立可安装的 `deerflow-harness`（import `deerflow.*`），**与 Gateway 应用解耦**；另有 `deerflow-extension-api` 作为公开扩展契约 | ⭐ **最值得抄的一条**。这正是专项调研说的"内核与形态解耦"。DocMind 的 `app/agent/**` 可以往这个方向收 —— 内核不 import API 层、不依赖 FastAPI |
| **跨组件 JSON 契约目录** | `contracts/`：`run_event_stream_contract.json`、`subagent_status_contract.json`、`slash_skill_contract.json`、`scheduled_goal_notes_contract.json` | 你现在的事件类型漂移（`EventType` Literal 缺 `execution_context`，`loop.py:570` 却真的 yield 了它）**就是因为没有这份契约** |
| **Extension-first 政策** | "在改核心代码实现一个功能之前，先评估它能不能作为一个打包扩展发布"——扩展可贡献 middleware、生命周期观察者、Gateway 服务、FastAPI 路由 | 治"什么都往 loop 里塞" |
| **消息网关** | Gateway 统一接 Feishu / Slack / Telegram / Discord / DingTalk | 对上你决策文档"通道层已被大厂做透，不要卷" |

**不建议抄的**：
- **沙箱编排**（6 供应商矩阵 + provisioner/K8s 模式）—— 强依赖容器编排，DocMind 的 Docker 沙箱 + AST 兜底已够
- **43 槽中间件链**（"11 层"是 2026-03 的发布会快照，**当前 main 已到 43 槽、默认约 28 个生效**）—— 这个规模需要配套的治理，照搬会失控。**先抄它的"顺序被测试锁定"这条纪律**，不要抄数量
- **官方自己的评估体系仍在规划阶段**（issue #4243）—— 评估这块**跟 SuperMew 学，不要跟 DeerFlow 学**

---

## 第 6 部分 · 需要你拍板的

| 决策点 | 选项 | 我的建议 |
|---|---|---|
| **嵌入服务** | A. 给智谱充值 / B. 切本地 Ollama | **B**，但要先做 1.2（否则一动维度就删库）。若你短期要演示，A 更快 |
| **是否开始动手** | — | 第 1 档**代码侧已完成**（见 1 档状态注）；**下一步只等 1.1（嵌入服务）**；随后按"评估集先行"推进 3.2 起步版 + 逐阶段最小实验，第 2 档算法项以实验结论为准 |
| **第 3 档优先级** | 3.1+3.2（评估门禁）/ 3.3（resume）/ 3.5（MCP） | **3.1+3.2 优先** —— 没有它，其他都是在没有仪表盘的情况下改发动机 |

---

## 修订记录（2026-10-07）

本方案经外部书面审阅后，做了一轮**逐条核实**（对代码文件行号与命令输出取证），依据现场证据修订以下内容——**只降措辞 / 补限定 / 更新状态，未改方向**：

1. **§0-1**："形态只是外壳"降级，区分**入口形态**与**执行平面**（后者缺失是实锤：Agent 跑在请求协程里、无独立执行平面，见 1.1）。
2. **§0-2、§2、§2 结论**："切分器 / 检索底座更强"降级为"结构感知更丰富，**检索效果未经评估比较**"。
3. **§0-3**："不是算法问题是接线"收窄为"**当前 P0** 首先不是算法"；第 2 档算法项标"假设待验证"。
4. **§1 表**：`0 issue / 0 PR` 标注读数时点（该状态仅持续约 4 小时）。
5. **§2 与 §5.2**：三处"这就是机制"（等权 RRF / 两路前 61 / listwise）降级为**并列候选机制，待最小实验区分**。
6. **第 1 档**：补状态（代码侧已完成：#212 / #214 / #215 / #217，另修 #219 → #221）；1.3 补"按 `document_id` 过滤"；1.5 判据修正为三分支（全新部署不再误报）。
7. **第 2 档**：2.1 改为统一 usage 入口；2.3 修正 `article_no` 并不存在；2.8 补"合法空参数仍可继续"；2.9 扩展 identifier 集合并挂实验前置；2.10 验收从"集合一致"改为"核心命中重合 + 指标不劣化"。
8. **第 3 档**：3.1"照抄契约"→"对齐字段、按单人规模裁剪"；3.2 起步 20–30 条（对齐现存 25 条资产）；3.4 补 `rag_stage` 事件契约前置；3.5 删"便宜一个数量级"，补 MCP 的外部控制面成本。
9. **§5**："不转形态"改为"不为追新形态重写"；"自研 checkpoint 更划算"降级为待比较项。
10. **同步修正**：`docs/reviews/review-003` 头部补快照时点；`docs/research/013` 中"已收口"论据重写（瞬时读数不能当稳态论据）。

> 核实方式：对改动过的现行代码逐文件取证（行号级），关键调用链复读复核；未被证据支持的原话一律**降级而非删除**。本轮补充发现的问题已立为 #222–#225。
