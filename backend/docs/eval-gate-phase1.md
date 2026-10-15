# RAG 检索评估门禁（Phase 1 起步版，#208）

> 一句话：把「检索质量」变成可测量、可回归的数字。本阶段只做到
> **本地一键出分数**；CI 门禁与 baseline 回归（`--fail-on-regression`）是 Phase 2。

## 1. 范围与边界

- **被测对象**：`HybridRetriever.retrieve`（关键词+向量+RRF+MMR+后处理）。
  有意**不经** rerank / 语义缓存 / LLM 环节，且 LLM 辅助（查询改写、HyDE）关闭——
  保证可复现。「rerank 前/后」对照与缓存链路属于 Phase 2 的逐阶段 trace 实验。
- **语料**：仓库内提交的固定语料 `backend/evals/corpus/`（两份 fixture：
  条式《知识库服务管理办法》+ 节式《应急响应手册》）。fixture 是**评估夹具**：
  可精确构造 gold 与 near-miss；CI（Phase 2）可把同一批语料灌进临时索引复跑。
- **数据集**：`backend/evals/golden_set/clause_set_v1.json`（起步 28 条 = 25 正例 + 3 负例）。
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
2. bulk 写入 28 chunks（17+11）→ `indices.refresh` 刷到 visible；
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
