# RAG 检索评估门禁（Phase 1 起步版，#208）

> 一句话：把「检索质量」变成可测量、可回归的数字。本阶段只做到
> **本地一键出分数**；CI 门禁与 baseline 回归（`--fail-on-regression`）是 Phase 2。

## 1. 范围与边界

- **被测对象**：`HybridRetriever.retrieve`（关键词+向量+RRF+MMR+后处理）。
  有意**不经** rerank / 语义缓存 / LLM 环节，且 LLM 辅助（查询改写、HyDE）关闭——
  保证可复现。「rerank 前/后」对照与缓存链路属于 Phase 2 的逐阶段 trace 实验。
- **语料**：仓库内提交的固定语料 `backend/evals/corpus/`（v1 起步为两份 fixture：
  条式《知识库服务管理办法》+ 节式《应急响应手册》；**v1.5 已扩到 8 份**：上述 2 份 +
  6 份公开法律/行政法规，见 §7）。fixture 是**评估夹具**：
  可精确构造 gold 与 near-miss；CI（Phase 2）可把同一批语料灌进临时索引复跑。
- **数据集**：v1 `backend/evals/golden_set/clause_set_v1.json`（28 条 = 25 正例 + 3 负例）；
  **v1.5（当前默认）** `backend/evals/golden_set/clause_set_v1_5.json`（80 条 = 60 正例 + 20 负例，见 §7）。
- **不做**：LLM-as-judge、端到端答案质量、rerank 指标——都排在 Phase 2/3。

## 2. 快速开始

```bash
cd backend
export ELASTICSEARCH_HOSTS='["http://localhost:9200"]'
export ELASTICSEARCH_INDEX_NAME=docmind_knowledge_eval_v1   # 专用评估索引，勿混生产
export EMBEDDING_API_URL=http://localhost:11434/v1 EMBEDDING_API_KEY=ollama
export EMBEDDING_MODEL=qwen3-embedding:0.6b VECTOR_DIMENSION=1024

python -m evals check   # 只校验 golden set 自洽（不联网）——改数据集后先跑这个
python -m evals seed    # 固定语料 → 分块 → 向量化 → 写入评估索引（可反复执行）
python -m evals run     # 跑全套 → evals/reports/latest.{json,md}
```

- 等价入口：`make eval-check` / `make eval`（Makefile 目标）。
- `run` 默认拒绝在「索引里该组织 0 篇」时跑分（防止产出误导性的全 miss）；
  确实要空跑加 `--allow-empty`。
- 参数：`--index/--org-id/--es-hosts/--top-k/--out`；参数先写环境变量再 import app。

## 3. 指标口径（ClauseHit@k 怎么算）

| 指标 | 定义 |
|---|---|
| **ClauseHit@k**（主指标） | 正例中，「top-k 里存在某 chunk：文件名 = gold 文件 **且** gold 条号出现在该 chunk 的**定义位**」的比例 |
| ClauseHit@1 / @3 | 同上，把 k 收窄到 1 / 3（名次门槛） |
| NearMiss@k（诊断，越低越好） | 声明了 near_miss 的正例中，top-k 里出现任一 near_miss 条号的**定义位**的比例。「gold 未中 + near-miss 中了」=「问第七条召回第十二条」的原形 |
| MRR | 首个 gold 命中的名次倒数均值（未命中记 0） |
| EmptyAcc | 负例（`expect: empty`）中检索结果为空的比例 |
| 95% CI | 小样本一律带 Wilson 区间 |

**条号提取规则**（`evals/clauses.py`，纯标准库）：

1. **条号**：`第<数字>条`，数字支持中文数字（一/二/…/十/百/千、零〇、两）与阿拉伯数字。
   归一化：`第七条` = `第7条` = `第 7 条` → `第7条`。
2. **章节编号**：`X.Y`/`X.Y.Z`（至少一个小数点），归一化为 `§X.Y`。
3. **命中判定用「定义位」**：条号/编号必须出现在**行首**（允许 `#` 标题前缀与
   后续空格/点号/冒号分隔符）。正文引用（「与第 7 条冲突…」「依据 §2.3」）
   **不计入命中**——防止「引用了该条号的 chunk」冒充「命中了该条」。
4. 一个 chunk 可定义多个条号（全展开）；命中多条只记一次（按名次判定）。

**错误语义**：单条查询执行失败记为 `status=error`，单独计数、不进任何比例分母，
绝不静默当 miss。

## 4. 怎么加一条 golden

1. （如需新语料）在 `backend/evals/corpus/` 加 fixture 并写进 JSON 的 `corpus_files`。
   fixture 约定：条式文档每个 `第N条` 独占行首；节式文档用 `## X.Y 标题`；
   **不要**让条号出现在正文行首（引用写在句中）。
2. 在 `clause_set_v1.json` 的 `items` 追加一条：

```jsonc
{
  "id": "unique_id",                 // 全局唯一
  "type": "clause_lookup",           // clause_lookup|semantic|numerical|cross_doc|conflict|unanswerable
  "query": "用户的问法（尽量口语化）",
  "expect": "hit",                   // hit=必须命中 gold；empty=必须返回空（负例）
  "gold": [{ "file": "01-….md", "clause": "第7条" }],       // 报告证明哪一条是对的
  "near_miss": [{ "file": "01-….md", "clause": "第8条", "why": "相邻条，词面高度重合" }],
  "critical": false,                 // Phase 2 门禁的零容忍标记（本阶段不生效）
  "notes": ""
}
```

3. 跑 `python -m evals check`；再跑单元测试 `tests/unit/test_eval_harness.py`
   ——它会**替你把关**：
   - 每个 gold/near-miss 条号必须在语料中**恰好一个 chunk 的定义位**上可证
     （不许 gold 在语料里根本不存在、也不许条号泄漏到多个 chunk）；
   - 每个 chunk 至多一个定义位（一事实一 chunk）；
   - 负例不许有 gold；id 唯一；`clause` 必须是「第7条/§2.3」形式（裸整数拒绝）。

## 5. 现状与路线

- 起步集统计：28 条（正例 25 / 负例 3），24 条正例声明 near-miss；
  覆盖 条号直查（含中文/阿拉伯数字对照对，作为 #2.10 数字归一化的基线证据）、
  语义/数值/跨文档/来源冲突 与 3 条负难例（不存在的条号、语料外主题、词面陷阱）。
- 真实环境冒烟基线（2026-10-09，dev ES + 本地 Qwen3-Embedding 0.6B，无 LLM 辅助）：
  **ClauseHit@1 80% / @3 = @5 84%**（95% CI 65.3–93.6%，n=25）、NearMiss@5 12.5%、
  MRR 0.82、负例 EmptyAcc 100%、0 错误。失败集中在「带文档全称的条号直查」
  （title token 淹没条号）与中文/阿拉伯数字写法对照——原始证据见 PR #311 正文与
  `_orchestrate/round2/exec_eval208.md`（含 seed/run 原文与报告 JSON/MD）。
- Phase 2 计划：把 `run` 接入 CI（临时索引 + baseline 对比 + `--fail-on-regression`）、
  逐阶段 trace（rerank 前/后、RRF 权重、条号过滤）最小实验——**只修被实验证明的环**。

## 6. CI 门禁（#208 第⑤步，2026-10-09 起）

> 一句话：`make eval-ci` 每次**从零重建**专用索引、用**确定性伪嵌入**跑全套、
> 与入库基线 `evals/baselines/ci_baseline.json` 比对，任何指标越界 = 非零退出码 = 红。
> CI（`.github/workflows/ci-fast.yml` 的 `Eval Gate` job，已接入 `CI Pass` 汇聚）
> 与本地跑的是**同一条命令**。

### 6.1 为什么需要「固定索引刷新协议」

独立评审（`_orchestrate/round2/review_eval208.md` §6）实测：同一语料、同一代码，
仅重灌一次 seed，`NearMiss@5` 会在 12.5% ↔ 16.7% 之间摆动（BM25 统计在
tombstone/merge 物理状态下的差异）；同物理状态内则逐位稳定。因此**不能**拿
「随便哪个索引状态」跑出的数字当门禁基线。协议（写死在 `seed --recreate` 里）：

1. `recreate_knowledge_index(confirm=True)`——**先删后建**，索引名必须含 `eval`
   （护栏：防误指生产索引）；重建后断言 `count==0`；
2. bulk 写入 362 chunks（v1.5：8 文件；v1 时代为 28 chunks = 17+11）→ `indices.refresh` 刷到 visible；
3. 回读断言：`count(org) == 写入块数` 且 `distinct document_id == 语料文件数(2)`；
4. 跑分、比对都只发生在这一次重建后的物理状态上（无 tombstone：`docs.deleted=0`）。

### 6.2 两种模式：live（本地工具）与 fake（CI 门禁）

| | live 模式（`--embedding live`，默认） | CI 模式（`--embedding fake`） |
|---|---|---|
| 嵌入 | 真实服务（本地 Ollama / API），`VECTOR_DIMENSION` 自定 | `evals/deterministic_embedding.py` 的确定性伪嵌入（`hash-bigram-char-v1`，dim 固定 256，`VECTOR_DIMENSION` 被 CLI 强制覆盖） |
| 依赖 | ES + 嵌入服务 | 只需 ES 8.11（**无需 Ollama / 无网络**） |
| 数字用途 | 质量观测、逐阶段实验 | **门禁基线**（`ci_baseline.json`）保护的对象 |
| 索引 | `docmind_knowledge_eval_v1`，可反复 seed 不重建 | `docmind_knowledge_eval_ci_v1`，每次先删后建 |

伪嵌入不是随机向量：非空白字符的 unigram+bigram 经 sha256 分桶计数 + L2 归一化
（语义 ≈ 字符 n-gram 余弦相似度——实测 hybrid 查询与其 gold chunk 的 cos 到
0.34–0.62，向量臂真实参与检索），且**逐位可复现**（算法指纹锁在
`tests/unit/test_eval_ci_gate.py`）。两条模式的数字**不可混比**：`compare`
会校验报告的 `embedding_mode`/`top_k`/`dataset_id`/`dataset_version`/`corpus_chunks`
与基线协议逐键一致，不一致直接红（fail-closed）。

### 6.3 一键与产物

```bash
make eval-ci            # seed --recreate → run --embedding fake → compare（红=非零退出）
make eval-ci-baseline   # 有意识地重盖基线（见 6.4）
```

产物：报告 `backend/evals/reports/ci/latest.{json,md}`（gitignore）；基线
`backend/evals/baselines/ci_baseline.json`（**入库**）。基线 schema：
`protocol`（协议记录）+ `metrics`（7 项固定指标：`clause_hit@1/@3/@5`、`mrr`、
`empty_accuracy` 为 floor；`near_miss@5`、`errors` 为 cap；**容差 = 0**——
固定协议下相等即通过，任何下跌即红、任何提升都放行）。门禁的失败语义
（章程 §9/§10）：指标越界 → exit 1；报告/基线缺失、损坏、协议不符 → exit 2；
两者都是红，绝无「静默放行」。

### 6.4 基线维护纪律（最重要）

- **改基线 = 显式 PR 行为。** 有意变更检索行为（并接受新数字）时：跑
  `make eval-ci-baseline` 或 `python -m evals compare --update-baseline`，把
  `ci_baseline.json` 的 diff 放进同一个 PR 供评审核对（stamp 时会打印旧→新逐项对照；
  报告含 errors 时拒绝盖章）。「红灯 → 直接重盖基线」而不修代码 = 把回归合法化，
  评审拒收。
- **回归 ≠ 改基线。** 红灯先看 `compare` 打出的越界行与 `reports/ci/latest.md`
  的失败明细；是回归就修代码。
- **物理问题卫生：永远经 `seed --recreate` 重建后再跑/比。** 不要对「反复 seed
  过的旧索引」跑门禁比对（tombstone 状态会让分数摆动）；本地手跑也与
  CI 同一命令（`make eval-ci`），数字逐位一致才说明复现成功。
- 语料 / golden set / 伪嵌入算法 / ES 大版本任一变化都会改变基线语义——都在
  PR 里显式可见（语料分块数与伪嵌入指纹都有锁测试）。
- 本地复现 CI 门禁只需要 ES 8.11（无需 Ollama）：

```bash
docker run -d --name es-eval -p 9200:9200 -e discovery.type=single-node \
  -e xpack.security.enabled=false -e ES_JAVA_OPTS="-Xms512m -Xmx512m" \
  docker.elastic.co/elasticsearch/elasticsearch:8.11.0
export ELASTICSEARCH_HOSTS='["http://localhost:9200"]'
make eval-ci   # 与 CI 逐位一致；门禁红/绿与 CI 相同
```

- 基线首次盖章环境（2026-10-09）：WSL2 ES 8.11.0（cjk、1 shard、no replica）+
  Python 3.12；连续两次 `seed --recreate + run` 报告除 `generated_at`/`elapsed_ms`
  外**逐字段 bit 级一致**（证据存档在 `_orchestrate/round2/`）。CI（Linux runner +
  同版本 ES 容器）与本地逐位一致，由本门禁自身的首次运行验证。

## 7. v1.5（issue #512，2026-10-24）：扩语料、80 条 golden、dense/hybrid 双基线

### 7.1 语料扩容（8 文件 / 362 chunks / 354 条 clause-level 定义）

在 v1 的 2 份 fixture 基础上新增 6 份**公开法律/行政法规**（均为公共领域文本，
《中华人民共和国著作权法》第五条第（一）项不适用著作权保护；每份文件头部记录
来源 URL、检索日期与文本版本）：

| 文件 | 文本 | 条款数 | chunks |
|---|---|---:|---:|
| `03-data-security-law.md` | 中华人民共和国数据安全法（2021） | 55 | 56 |
| `04-personal-information-protection-law.md` | 中华人民共和国个人信息保护法（2021） | 74 | 75 |
| `05-cybersecurity-law.md` | 中华人民共和国网络安全法（2025 修正文本） | 81 | 82 |
| `06-archives-law.md` | 中华人民共和国档案法（2020 修订） | 53 | 54 |
| `07-cii-security-protection-regulation.md` | 关键信息基础设施安全保护条例（2021） | 51 | 52 |
| `08-cybersecurity-incident-reporting-measures.md` | 国家网络安全事件报告管理办法（2025） | 14 | 15 |

为保持确定性分块（一条款一 chunk），章/节标题与目录已省略，仅保留条文；
来源页面快照与清洗脚本不进入仓库（仅提交清洗后的 Markdown）。

### 7.2 golden set v1.5（80 条 = 60 正 + 20 负）

- 文件：`evals/golden_set/clause_set_v1_5.json`（当前默认）；v1 文件保留作历史。
- 题型：`clause_lookup` 28、`semantic` 18、`numerical` 8、`cross_doc` 4、`conflict` 2、
  `unanswerable` 20（合计 80；相对 issue 目标 ±2 内）。
- **每条正例都声明 near-miss**（相邻/最易混条款）；负例为语料完全无法回答的主题
  （含「不存在的条号」难例）。
- 标注：单标注 + 20% 双标盲审计（16/80）；审计记录、分歧裁决与全量题目—条款
  可追溯表见 `evals/golden_set/annotation_v1_5.md`。主标与复核均为 AI，无人类法律专家
  逐条复核（已在审计文件披露）。
- `python -m evals check` 现在额外校验：每条 gold/near-miss 必须在语料中**恰好一个
  chunk 的定义位**上可解析，并打印文件数/chunks/条款定义数。

### 7.3 双基线（dense / hybrid，fake 可复现）

| 基线 | 检索方式 | 文件 |
|---|---|---|
| CI 门禁（hybrid） | `HybridRetriever.retrieve`（关键词+向量+RRF/MMR+后处理） | `evals/baselines/ci_baseline.json` |
| hybrid 对照 | 同上 | `evals/baselines/v1_5_hybrid_baseline.json` |
| dense 对照 | 纯向量 cosine top-k（无关键词/RRF/后处理） | `evals/baselines/v1_5_dense_baseline.json` |

`python -m evals run --retriever {hybrid,dense}` 选择基线；报告 meta 增加
`retriever_mode`（同时纳入 compare 的协议键，防拿 dense 报告对 hybrid 基线）、
`corpus_digest`（语料内容 sha256）、`git_commit`。`python -m evals report`
读取 hybrid/dense 两份报告，输出 per-slice（按题型）对比与失败分类。

### 7.4 公开复现（`make eval-public`，<30 分钟）

```bash
# 仅需 Docker ES 8.11；无需 Ollama / 网络
cd backend
python -m evals check
make eval-public   # seed --recreate → run(hybrid) → run(dense) → report
```

产物：`backend/evals/reports/v1.5/latest.{json,md}`（hybrid）、
`reports/v1.5/dense/latest.{json,md}`、`reports/v1.5/comparison.md`，
并重写 `backend/evals/REPORT.md`。CI 门禁仍是 `make eval-ci`（hybrid + fake +
7 项指标 + 基线比对，容差 0）。

### 7.5 live rerank 报告（单独工具，绝不伪造数字）

`python -m evals.live_rerank`（或 `make eval-rerank-live`）在**真实** embedding +
rerank provider 下跑 before/after，记录 `path/model/threshold/latency`；任何路径
degraded/disabled/empty 时只写 `status=unavailable` 的占位报告（含 mode/date/
语料 commit/原因），**不写任何指标数字**，退出码 2。仓库内提交的占位报告：
`evals/baselines/live_rerank_placeholder.{json,md}`。

### 7.6 失败分类与对外引用纪律

`evals/REPORT.md` 固定五类失败（title-token 淹没、中文/阿拉伯数字不归一、
chunk 边界、near-miss 误召、负例误召），每类最多 2 个真实例子；某类 0 例时
明确写「0 例」而不是伪造。对外引用任何数字必须同时标注：
`mode=live|fake`、日期、语料 commit（或 `corpus_digest`）。fake 与 live 数字不可混比。

### 7.7 口径不变

保留 Wilson 95% CI；**不引入 bootstrap**（重采样单位与收益未论证）。生成层指标
（Citation Accuracy/Coverage/Gap）仍不进入零容差 CI。

### 7.8 v1.5 首次盖章（2026-10-24，mode=fake，ES 8.11.0 zip）

环境：Windows 11 + ES 8.11.0（single-node、xpack.security 关闭、磁盘水位阈值关闭），
Python 3.12.8；伪嵌入 `hash-bigram-char-v1` dim=256；无 LLM/rerank。

| 指标 | hybrid（CI 门禁口径） | dense-only |
|---|---:|---:|
| ClauseHit@1 | 70.0% | 61.7% |
| ClauseHit@3 | 76.7% | 68.3% |
| ClauseHit@5 | 80.0%（Wilson 68.2–88.2%） | 73.3%（Wilson 61.0–82.9%） |
| NearMiss@5 | 25.0% | 30.0% |
| MRR | 0.7400 | 0.6542 |
| EmptyAcc（n=20） | 100.0% | 0.0% |
| errors | 0 | 0 |

- 语料：8 文件 / 362 chunks / 354 条 clause-level 定义；语料指纹
  `sha256:6b7f5fff7516e8887887853bed31e091c3419016149c605c96306364d3a0e12e`。
- 复现证据：同一协议下独立两次 `seed --recreate → run(hybrid) → run(dense)`，
  `aggregates`、逐条判定与 top-k 结果**逐字段一致**（不含 `generated_at`/`elapsed_ms`）；
  seed + 两个 run 合计约 23 秒，CI 门禁三步约 21 秒，远低于 30 分钟目标。
- 基线文件：`ci_baseline.json` 与 `v1_5_hybrid_baseline.json` 为 hybrid；
  `v1_5_dense_baseline.json` 为 dense。三者均为显式 PR 工件，CI 不自动改写。
- 失败分类与真实例子见 `evals/REPORT.md`；混合检索的 12 条失败集中在
  「带《…》全称的条号直查」被标题/前言 chunk 淹没，以及中文/阿拉伯数字写法对照。
