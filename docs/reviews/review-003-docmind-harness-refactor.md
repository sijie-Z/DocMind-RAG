# review-003：DocMind 的重构方向 —— 检索底座与 harness 内核

> **快照时间：2026-10-06（当日下午）。** 文中"当前状态"类事实为彼时读数；后续进展与措辞更正见 `docs/decisions/2026-10-06-harness-refactor-plan.md` 的「修订记录（2026-10-07）」。

> **给答复方的说明**：你看不到这个仓库的代码，也看不到运行环境。
> 所以本文**把关键事实的证据直接贴在正文里**，并对每条结论标注了获取方式。
> 请特别阅读 **§5 已完成/未完成基线** —— 上一轮（review-002）最大的问题就是我漏写了这一节，
> 导致你基于"可能还没做"的假设给了建议，而那些功能其实早就存在。
>
> 另外：**§6 逐条列出了我在本轮里出过的错和自我更正**，请直接挑战那里。

---

## 1. 背景

### 1.1 项目

**DocMind** —— 单人开发、public repo、求职作品性质的企业级 AI Agent 系统。

- **后端**：FastAPI + SQLAlchemy(async) + MySQL 8 + Redis 7 + **Elasticsearch 8.11** + Kafka + MinIO
- **前端**：Vue 3 + TypeScript + Vite + Naive UI + Pinia
- **Agent**：自研 **PER 循环**（Plan → Execute → Reflect → Learn），30 个工具，四层记忆，SSE 流式
- **RAG**：hybrid 检索（BM25 + 向量）→ RRF 融合（k=60）→ cross-encoder 重排
- **部署**：Docker Compose（MySQL / Redis / ES / Kafka / MinIO / 后端 / worker / Prometheus / Grafana）

约束：**单人维护**，长期可维护性优先，不做"单人维护灾难"。

### 1.2 触发这次讨论的三件事

**① 用户判断系统"跟不上节奏了"**
原话大意：一直在往 RAG 走，而"现在大家都在往 Pi、harness、Hermes 这些走"，
可能要大改、要引入新技术。

**② 用户提出了一个架构层面的疑问**

> "人家这些都是桌面版本的东西，但是我的这个 agent 为什么是前后端架构的？这两种一样吗，真正的效果上来说。"

**③ 用户给了一张截图，说"我希望我们也能有也能实现"**

截图是一个中文聊天界面，展开着一个**检索调试面板**，字段包括：
`检索阶段: initial` / `相关性评分: yes` / `评分决策: generate_answer` / `是否需要重写: 否` /
`检索模式: hybrid` / `候选召回数: 15` / `叶子召回层级: L3` / `Auto-merging 启用:是 应用:是
合并阈值:2 合并替换片段:4 合并轮次:1` / `Rerank 模型: jina-reranker-v3` / `扩展查询: …` /
`初次检索结果`（含 `RRF名次: #1`）

**用户同时说："目前 RAG 你都没做好，你可以看看截图。"**

---

## 2. 我做了什么

**方法**：8 路并行调研（外部技术调研 4 路 + 本仓库代码审计 2 路 + 活体实测 2 路），
产物落盘为九份专项调研文档（已归档于 `docs/research/008–015`）。

**证据分级**（本文沿用）：
- 【实测·活体】= 在运行中的容器 / 真实数据库 / 真实 ES 上读回的原值
- 【实测·代码】= 直接读源码得到的，带 `文件:行号`
- 【调研·多方一致】= 多个独立来源一致
- 【单一来源】/ 【推断】

**一个必须说明的限制**：本次会话的联网检索预算耗尽、多数域名被网络策略拦截，
**外部调研的证据等级普遍低于代码审计与活体实测**。本文凡涉及外部项目的结论，
若标注为【单一来源】或【推断】，请按该等级对待。

---

## 3. 关键发现

### 3.1 那张截图不是 DocMind —— 它是另一个开源项目

**这是第一步就要弄清楚的事**，因为"我要有它"和"我要修我自己"是两件事。

【实测·代码】在 DocMind 全仓检索截图上的字段名
（`Auto-merging` / `叶子召回层级` / `合并阈值` / `评分决策` / `是否需要重写` 等）：
**零命中**。全历史 378 个 commit 的 `git log --all --grep` 同样零命中。

【实测·活体】用 GitHub 代码搜索 API（全网公开代码）搜 `"叶子召回层级"` 与 `"合并替换片段"`：
**全世界只命中一个文件** —— `icey1287/SuperMew` 的
`frontend/src/components/Chat/RetrievalTraceDetails.vue`。

截图里那个助手自称"**喵喵**在线中"，而 **Mew = 喵**。

**结论**：截图来自开源项目 **SuperMew**（`icey1287/SuperMew`，MIT，884 star，
描述为 "Agentic RAG with LangChain & LangGraph"，Python + Vue 前后端架构）。

**顺带一条旁证**：【实测·代码】DocMind 的 `page_number` **恒为 NULL**
（解析器收集了页信息但从未赋给 chunk，ES mapping 里也没有这个字段）——
所以截图里的"（第 3 页）"**不可能由 DocMind 产出**。

### 3.2 用户想对标的那个面板，在它自己的截图上就答错了

**这是我认为最重要的一条。**

截图里用户问的是《消费者权益保护法》**第七条**，
而面板显示的 **RRF 名次 #1** 是同一个 pdf 里 **第十二条 / 第十三条** 的正文片段。
并且系统判定「**相关性评分: yes**」「**评分决策: generate_answer**」——
**它认为这次召回没问题，直接拿去生成答案了。**

【实测·代码】我在 SuperMew 全仓（499 个文件快照）里追这条链，找到六环：

| 环 | 机制 | 位置 |
|---|---|---|
| **P1** | 叶子层切块是**纯字符窗口**，无条号结构 | `backend/indexing/document_loader.py:129-160` |
| P2 | RRF 等权融合 + 候选池仅 15；向量通道压过词法精确信号 | Milvus 侧 `RRFRanker(k=60)` |
| P3 | auto-merging 阈值=2 过低，父块"原位替换"且**合并后不重排** | 面板"阈值2/替换4/轮次1"已解码到代码 |
| P4 | rerank 只喂正文（不带条号/文件名头）；**面板的「RRF名次 #1」实为重排输入的显示序号** | `RetrievalTraceDetails.vue:282` |
| P5 | 判官按"主题相关性"设计，**全链路无条号一致性校验** | `EVIDENCE_GRADE_PROMPT` 原文 |
| **P6** | 官方文本里"第七条"与"第十二条"的条号分隔符本身不一致 | 第三方实测 |

**P4 我亲手验过**，源码是：

```vue
<span class="source-page">RRF名次：#{{ chunk.rrf_rank || sIndex + 1 }}</span>
```

`rrf_rank` 缺省时**回退到列表显示位置** —— 那个"#1"不能当作"RRF 排第一"来读。

**P1 我亲手验过**，它的分层切块参数是：

```python
chunk_size: int = 800,
level_1_size = max(2000, chunk_size * 3)   # L1 = 2400 字符
level_2_size = max(1000, chunk_size * 2)   # L2 = 1600 字符
level_3_size = max(600, chunk_size)        # L3 =  800 字符  ← 叶子层
```

**纯字符窗口递归切分。** 我在它的整个 `backend/` 里搜
`第[一二三四五六七八九十]` / `article_no` / `clause_bound` —— **零命中**。
**它根本不知道什么叫"第七条"。**

### 3.3 结论：DocMind 的切分器**比对标对象强**

【实测·代码】DocMind 的 `backend/app/services/semantic_chunker.py:13-22` 有八条条款模式：

```python
CLAUSE_PATTERNS = [
    re.compile(r'^第[一二三四五六七八九十百\d]+[条章节款]'),   # 第一条 / 第二章 / 第三节
    re.compile(r'^[一二三四五六七八九十百]+[、．\.]'),          # 一、 / 二．
    re.compile(r'^\（[一二三四五六七八九十百]+\）'),              # （一）（二）
    re.compile(r'^\([一二三四五六七八九十百]+\)'),
    re.compile(r'^\d+[、．\.]'), re.compile(r'^\(\d+\)'),
    re.compile(r'^【.*?】'), re.compile(r'^[A-Z]\.'),
]
```

**切分阶段是条款感知的。**

**所以用户"RAG 做得不好"的感受，方向对了一半、错了一半：**
- **错的那半**：底座比对标对象强
- **对的那半**：见下一条

### 3.4 但 DocMind 的 RAG **从来没有索引进去过任何东西**

【实测·活体】在运行中的 `docmind-backend` 容器里，以真实用户身份
（`user_id=2 guest`，`organization_id=3`，正是那 10 份文档的所属组织）
调用 `POST /api/v1/knowledge/search`：

```
问：数据安全管理条例的修订背景是什么        → 200  results: []  total_count: 0
问：2024年修订版数据安全管理条例的核心变化   → 200  results: []
问：对企业的主要影响是什么                 → 200  results: []
问：AI 训练数据的合规要求                  → 200  results: []
问：SWOT 分析框架怎么用                    → 200  results: []
```

五个查询全部 0 条，且 `search_time: null`。
其中一个查询与库中 chunk 文本**几乎逐字对应**，仍然 0 条。

四条独立证据指向同一件事：

| 读回 | 结果 |
|---|---|
| `GET _cat/indices` | `paicongming_knowledge  docs.count = 0` |
| `SELECT COUNT(*), SUM(embedding_id IS NULL) FROM document_chunks` | `39, 39` —— **39 条 chunk 零向量** |
| `knowledge_processing_jobs.error_message` | `429 Error code 1113 余额不足或无可用资源包,请充值` |
| `document_chunks.meta_data` | `{"source": "benchmark_seed", ...}` —— 直写 MySQL 的种子数据，**从未进 ES** |

**它失败得完全静默**：搜索返回 200 + 空数组；文档状态显示 `INDEXED`；
`/health` 五项全绿。

### 3.5 用户提到的"指标"：31 个指标全部是空转的

【实测·代码】`backend/app/core/prometheus.py:80-118` 定义了 **31 个**指标
（RAG 检索 / 缓存 / LLM token / 重排 / 评估分数 / Agent 规划 / 工具调用 / 反思 / 记忆 / 反馈），
埋点分布在 `executor.py:722/736/758`、`planner.py:225`、`reflector.py:188` 等处。

【实测·代码】但模块 docstring 明写：*"ALL metrics are no-op by default.
To enable real metrics, set `PROMETHEUS_ENABLED=true` before the first import."*
（动机真实：`prometheus_client` 在 Windows + Git Bash 上有已知多进程死锁问题。）

【实测·活体】而这个开关**在整个项目里一个地方都没设**：

```
grep PROMETHEUS backend/docker-compose.yml              → 无
grep PROMETHEUS backend/.env .env.example .env.docker.example  → 无
grep PROMETHEUS deploy/k8s/                             → 无
docker exec docmind-backend env | grep -i prometheus    → 无
```

【实测·活体】活体 `/metrics` 返回 **494 行**，其中以 `agent_` / `rag_` / `llm_` 开头的：**0 行**。
（只有 `app_http_*` 是活的，来自另一套独立实现。）
而 Prometheus 抓取状态正常：`{"health": "up", "lastError": ""}`。

**监控大屏一直开着，展示的是这些 0。**

### 3.6 反复出现的形态：**写好了，没接上**

【实测·代码】

| 能力 | 状态 | 位置 |
|---|---|---|
| 子代理委派（含 3 并发上限） | **零调用者** | `app/agent/subagent.py:24, 73` |
| 技能学习闭环 | **零调用者** | `app/agent/skills.py:181, 192` |
| `skills/` 的 SKILL.md 进提示词 | 只被 REST 端点读 | `curated_skills.py` |
| Checkpoint / 断点续跑（表已建 + 状态机已定义） | **运行时零读写** | `models/agent_task.py` |
| 两套轨迹设施（含 JSONL 落盘） | 只被未挂载的模块用；**已按 dev-only 标注处置（#242）** | `core/trace_logger.py`、`agent/tracing.py` |
| 分层结果截断器 `_smart_truncate` | **零调用** | `loop.py:56` |
| 人工升级 `escalation` | 实例化了但**从不查询** | `escalation.py:51` |
| `ExecutionContext.total_tokens` | **全仓零赋值** | `exec_context.py:82` |

以及三个"配置存在但没人读"：`max_iterations`（PER 循环实际没有迭代上限）、
`budget_exceeded`（错误码定义了但无处产生）、
`load_skill_reference`（prompt 里承诺了这个工具，**函数不存在**）。

### 3.7 关于"桌面 vs 前后端"：**"人家都是桌面"这个前提不成立**

【调研·多方一致】

| 项目 | 形态 |
|---|---|
| **DeerFlow 2.0**（字节跳动，83k star） | **后端 FastAPI + 前端 Next.js + Nginx**（四服务拓扑：Nginx 2026 / Gateway 8001 / Frontend 3000 / Provisioner 8002） |
| **OpenHands** | web-first |
| **SuperMew**（用户截图里那个） | 前后端架构 |
| Hermes / Pi | TUI + 桌面端 + 消息网关 + IDE，**共享同一内核**（"一个内核、多种入口"） |

**要点**：形态（桌面/CLI/Web）是**外壳**；决定"效果"的是**行动面**与**内核成熟度**。

【调研·单一来源】支撑性数据：mini-SWE-agent（约 100 行 Python、纯 bash、无 tool-calling）
在 SWE-bench Verified 上 >74%；同一模型换 harness 公开榜单差 8–12 个百分点。

---

## 4. 我的判断（**这一节是请你重点审的**）

### 4.1 关于架构方向：不建议改形态

**理由**：
1. "人家都是桌面"不成立（§3.7）
2. 多租户 / RBAC / 审计是 Web 的独有资产，转 CLI 等于把差异化扔掉
3. 效果差距不来自壳

### 4.2 关于 RAG：不是算法问题，是"没接上"+"不吭声"

三个层次，代价递增：

1. **索引层**：从来没索引成功过（§3.4）—— 这是阻塞项
2. **接线层**：条号抽出来了但断在三处（ES `metadata.enabled=false` → 顶层 `section_title` 无人写 → `_source` 白名单不含它），
   所以「第七条」在检索里退化成两个普通 bigram，与法律名的 8 个 bigram 同权竞争
3. **可观测层**：「相关性评分」是**批内 min-max 归一化**，排第一的那条恒等于 1.0
   （`retriever.py:341-361`），它与"是否相关"无关；而唯一的分支判据是"上下文列表是否为空"

### 4.3 我提出的三档计划

| 档 | 内容 | 前置条件 |
|---|---|---|
| **1 · 阻塞项** | 换本地 embedding；修 `recreate_knowledge_index()`（维度不符即删整个索引且无确认）；索引后校验；搜索端点不再对空结果沉默；`/health` 加知识库项 | 无需架构决策 |
| **2 · 能感受到的改进** | token/成本落库；开启 31 个指标；接通条号；修评分锁死；修复 prompt 正文 260 字截断；接线已写好的模块 | 部分依赖 1 |
| **3 · 结构性改造** | 评估门禁上 CI；建检索 golden set；Run 级 resume；面板升级为"可诊断"；暴露为 MCP server | 需要决策 |

### 4.4 一个我必须承认的顺序错误（已自我更正）

我最初把「给 RRF 加权重、让词法通道在条号类查询上主导」放在**第 2 档**。

后来核实到**两家厂商的官方文档都在警告相反的事**：

> Qdrant 官方：*"Hand-tuned weights without measurement are unlikely to beat the default reliably."*
> 其指引是：**有评估集**才做权重网格搜索；**没有评估集就保持默认 (1.0, 1.0)。**
>
> OpenSearch 官方：*"change the weights **only when you can measure the effect** on relevance."*

**所以「建评估集」必须排在「调融合权重」之前。** 否则就是在没有仪表盘的情况下拧旋钮 ——
而这恰恰是本项目已经犯过的错。

### 4.5 一条会推翻常见直觉的外部结论

【调研·单一来源，厂商自家 notebook】LlamaIndex 官方对 auto-merging 的评估结论原文是
**"The results are roughly the same."** —— 相对基线基本持平。

若成立，则"叶子召回 → 合并父块 → 精排"这个顺序**不是在提升召回，而是在放大已经错了的召回**；
正确顺序应该是「**小片段检索 → 小片段精排 → 进 prompt 前再扩展**」。

**这一条我只有单一来源，且没做自己的 A/B 验证** —— 如果你认为它站不住，请直接说。

---

## 5. 已完成 / 未完成基线（**请先读这一节再给建议**）

> 上一轮我漏了这节，导致对方给的若干建议针对的是**早已实现**的功能。

### 5.1 本轮**已经做了**的（对外可见的写操作）

| 动作 | 结果 |
|---|---|
| 提交 30 条 issue | **#180–#209**（编号连续；23 个 `bug` + 7 个 `tech-debt`），每条含症状 / 根因 / 影响 / 复现，**按要求未写修复方案** |
| 补 issue 模板 | PR **#179**（Issue Forms：`bug_report.yml` / `tech_debt.yml` / `config.yml`）—— 仓库此前只有 PR 模板 |
| 新增标签 | 无 —— `tech-debt` 标签仓库里**早就有**（描述即"已实现但未接线 / 设计债"） |
| 提交 PR | 仅 #179 |

### 5.2 本轮**没有做**的（重要）

- **没有改任何代码**。全部调研与审计都是只读的；唯一的写操作是上面那两个 GitHub 动作。
- **没有修任何一个 issue** —— 用户明确要求"解决方案先不用，先把 issue 提出来"。
- **没有动任何数据**：MySQL / ES 均为只读查询；没有创建、修改或删除任何数据。

### 5.3 冲突项：**下面这些不是新问题，是已经做过的事**

如果你打算建议其中任何一条，请先看"现状"列：

| 常见建议 | 现状 |
|---|---|
| "加 CI 覆盖率门禁" | **已有**（`fail_under = 42`），且做过正负两向验证 |
| "把 Bandit 前置到 PR" | **已有** |
| "加 `concurrency + cancel-in-progress`" | **已有**（`ci-fast.yml:7-9`） |
| "确认 Secret Scanning 是否启用" | **已启用**，且 push protection 也开着 |
| "Dependabot 分支太多，加 grouping/weekly/limit" | **已配好**（`target-branch: develop`、weekly、limit 5、minor+patch 已分组） |
| "加 TDD / 要求测试覆盖" | **已有**，`CONTRIBUTING.md` 与 PR 模板里都写了 |
| "加 Conventional Commits 校验" | **已有**（commitlint 在 CI 里） |
| "用 `.venv` 跑测试" | 本机是 Windows，`.venv` 是 Linux venv **不可用**，必须用 `.venv-win` |
| "用 `gh` CLI" | 本机 **没有** `gh`，且 Python 的 HTTPS 已损坏，所有 GitHub 操作走 `curl` + REST API |

### 5.4 仓库的既有约定（请在这些约束内给建议）

- **分支**：`develop` 集成 → `main` 发布；PR 门禁检查名 `CI Pass`
- **远程**：工程仓库 `DocMind-RAG` 为 `origin`；另有一个已停用的研究仓库 remote
- **提交规范**：Conventional Commits，type ∈ `feat | fix | refactor | perf | docs | test | ci | chore`
- **PR 正文**：必须按仓库模板逐节写（缺节会被 CI 的 validate 拦）
- **规模**：单人开发，长期可维护性是硬约束

---

## 6. 我在本轮里出过的错（**请直接挑战这节**）

| 错误 | 更正 |
|---|---|
| 我最初把"给 RRF 加权"排在第 2 档 | **顺序错了**，必须排在评估集之后。见 §4.4 —— 两家厂商文档都警告"没有评估集就别调权重" |
| 我第一次验证"Context Engineering 四件套是否存在"时，搜的是 `Isolate` 一词，得到 101 个命中，一度以为该概念存在 | **检查设计错了**。在一个讲沙箱隔离的项目里这个词必然满屏。重测精确模式（四词连排 + 中文"上下文工程四件套"）→ **0 命中**。结论纠正为"查无出处" |
| 我最初怀疑"RRF 的 k=60 压缩了词法第一名优势"是主因 | **k=60 本身不是问题**。RRF 原论文原文 `k = 60 was fixed during a pilot investigation`，且 k∈10–100 时 MAP 只差 .2123–.2147，作者原话 *"near-optimal, but the choice was not critical"*。**真正的机制是"等权"** |
| 我上一条消息里说 DeckFlow 的中间件链是 11 层 | 那是**2026-03 发布会时代的快照**，当前 main 已到 **43 槽**（默认约 28 个生效）。用户提供的简历材料里"11 层"在当时是准确的，但**现在不是** |

其中第二条是本轮最有价值的自我更正 —— 它正是这个项目反复踩的那个坑的又一次复现：
**检查存在 ≠ 检查有效；判据没覆盖真实情况，就会给出错误信号。**

---

## 7. 我的不确定之处（欢迎直接指出）

1. **auto-merging 那句 "roughly the same"（§4.5）** —— 只有厂商自家 notebook 一个来源，
   我没有独立复现。如果它不成立，我关于检索顺序的建议需要重写。

2. **"形态是外壳"这个判断的边界** —— 我列出的"本质差异"是四条
   （可达面 / 信任合规边界 / 生命周期 / 隔离层级）。
   是否存在我没看到的第五条本质差异？特别是：**多租户 Web 架构在 Agent 场景下是否有结构性劣势**
   （例如无法为每个用户提供隔离的执行环境）？

3. **换本地 embedding 的迁移代价** —— DocMind 的 ES mapping 写死
   `"embedding": {"type": "dense_vector", "dims": 2048}`（对应智谱 embedding-3）。
   计划换成 Qwen3-Embedding-0.6B（**1024 维**，官方规格表实抓）。
   **我判断"必须改 mapping 并重建索引"，但没评估过：**
   对一个已经有数据的索引，ES 的 dense_vector 维度是否真的无法原地变更？
   有没有比"删库重建"更平滑的路径？我的结论建立在一段会删索引的代码上（issue #184），
   那段代码本身是问题——但"ES 的维度不可变"这个前提我**没有独立验证过**。

4. **第 3 档的优先级** —— 我建议"评估门禁 + golden set"优先于"Run 级 resume"，
   理由是"没有仪表盘就没法改发动机"。但这个判断假设了**检索质量是当前最大瓶颈**。
   如果真实瓶颈是"长任务跑不完"（resume 缺失），优先级应该反过来。**我没有长任务的实测数据。**

5. **"不需要重写架构"这个总结论** —— 它建立在我读到的代码上。
   但内核审计（`docs/research/010`）里明确列了"未核实"的部分
   （`memory_service.py` 约 700 行、`optimization.py` 419 行、前端流式渲染等都没逐行审）。
   **如果那些文件里藏着结构性缺陷，我的结论会变。**

6. **评估集的规模建议** —— 我引用了 SuperMew 的"生产门禁应至少 200 条人工标注 case"。
   对一个单人开发、以求职为目的的项目，**这个规模是否现实？** 有没有更务实的起点？
   我倾向于"先 20–30 条把门禁跑起来"，但没有依据。

---

## 附：本次调研的产物清单

| 文件 | 内容 |
|---|---|
| `015_deerflow-architecture.md` | DeerFlow 2.0 拆解（中间件链 43 槽、Agent Teams、沙箱矩阵） |
| `013_agent-harness-landscape.md` | harness 版图 + Pi/Hermes 核实 + 形态差异分析 |
| `014_agent-metrics-spec.md` | 指标规格（MVP-12 + 公式 + 采集点 + 阈值） |
| `011_reference-rag-failure.md` | 检索失败诊断（SuperMew 六环链 + E1–E7 验证实验） |
| `009_rag-pipeline-audit.md` | DocMind RAG 审计（22 条缺陷，带 `file:line`） |
| `010_agent-core-audit.md` | DocMind Agent 核心审计（能力清单 + harness 差距表） |
| `012_reference-rag-architecture.md` | SuperMew 完整拆解（含它的评估契约原文） |
| `008_rag-never-indexed.md` | 活体根因实测（四条证据链） |
| `docs/decisions/2026-10-06-harness-refactor-plan.md` | 三档计划（本方案的执行版） |
