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
