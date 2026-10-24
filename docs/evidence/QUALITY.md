# DocMind 质量档案：把「静默失败」变成可执行断言

> 基线：`origin/develop` @ `653715d2`（2026-10-24）。只写该基线上**已合并**的代码、测试与 CI；所有引用给出 `file:line` 或命令输出；不引用未合并分支、未读回的自报与目标态数字。
> 路径相对仓库根；`$` 开头为命令输出；行号以基线提交为准。

## 数字溯源说明

`docs/METRICS.md` 在基线 **不存在**：

```
$ test -f docs/METRICS.md && echo EXISTS || echo MISSING
MISSING
```

因此本文不使用无出处的汇总数字。每个数字给出来源：`backend/docs/eval-gate-phase1.md`、`backend/evals/baselines/ci_baseline.json`、`docs/research/008_rag-never-indexed.md` 的原始命令输出，或 CI 工作流定义。功能计数的事实源是 `docs/facts.json`，本文未使用。

## 怎么读这份档案

- 第 1–3 节回答「问题是什么、证据是什么、根因是什么」；第 4–5 节回答「怎么修」；第 6 节是一个失败案例；第 7 节讨论人类与 AI 的分工；第 8 节列出同日合并的 9 个 PR；第 9 节是局限；附录给出引用清单与复现命令。
- 文中的「事实」都能链到代码行、测试行或命令输出；「判断」会明确写成判断。
- 本文不引用未合并分支，也不引用未读回的自报数字；无法验证的项集中列在第 9 节。

## 摘要

1. **真正的问题不是「检索效果差」，而是「从来没有索引进去过任何向量」。** 活体审计读到：ES 知识库索引 `docs.count = 0`，MySQL 39 条 chunk 中 39 条 `embedding_id` 为 NULL，文档状态却显示 `INDEXED`，健康检查五项全绿（`docs/research/008_rag-never-indexed.md:10-12,41-61`）。
2. **失败完全静默。** 检索返回 HTTP 200 加空数组，`search_time` 为 `null`；上传在 embedding 步骤 429；旧端到端断言是 `status < 500`，对空结果沉默（`docs/research/008_rag-never-indexed.md:24-39,63-72,125-128`）。
3. **根因是契约缺失，不是算法。** `_mark_success` 无条件写 `INDEXED`，不检查 ES；检索通道又硬过滤 `embedding` 与 `organization_id`——状态与可检索性之间没有强制关系（`backend/app/services/document_status.py:30-42`）。
4. **修复 = 三条可执行不变量 + 8 个集成测试 + 零容差 eval gate。** 前两者把「INDEXED ⟹ 可检索」钉在真实 ES 上（`backend/app/services/document_status.py:27-124`；`backend/tests/integration/test_index_verification.py:1-37,142-251`）；后者用 28 条 golden、7 项指标守住检索质量（`backend/docs/eval-gate-phase1.md:14,92-96,100-149`；`.github/workflows/ci-fast.yml:371-415,531-553`）。
5. **一个失败案例比十个成功故事更有信息量。** Phase 2 实验证明 4 条失败查询的条号被 4 个以上环节叠加淹没，「没有任何单一或已测两环组合能修好全部 4 条」；团队按纪律停下报告，如实记录 `gold 回收 = 0/4`（`backend/docs/eval-gate-phase2-experiment.md:49-60,90-95`）。

---

## 1. 问题：企业 RAG 的失败是静默的

企业知识库最常见的验收是「上传一份文档，问一个问题，看有没有答案」。这能发现「完全不能用」，却几乎发现不了「部分不能用」——后者恰是 RAG 最危险的失败形态。

检索坏掉时通常不抛异常：返回 200 加空数组；文档状态显示 `INDEXED`，因为写状态的代码不检查索引；健康检查显示绿色，因为只查进程与连接；上传任务显示 `FAILED`，但没人把「处理失败」与「检索为空」联系起来。DocMind 在 2026-10-06 的活体审计中同时命中这四条：

> 「这套 RAG 不是『做得不好』，而是从来没有索引进去过任何一个向量。而它失败的方式是**完全静默的**——搜索返回 HTTP 200 + 空数组，文档状态显示 `INDEXED`，健康检查五项全绿。**没有任何一个信号告诉你检索坏了。**」（`docs/research/008_rag-never-indexed.md:10-12`）

对企业场景，静默失败被三重放大：**信任错位**（用户把「检索不到」理解为「知识库里没有」）、**责任错位**（状态字段是运维、前端与监控判断可用性的唯一依据，而它可以撒谎——`backend/app/services/document_status.py:40-42`）、**回归不可见**（没有 golden set 与门禁时，mapping、阈值、依赖的任何变化都可能让召回率悄悄下跌）。

---

## 2. 证据：一次活体审计读到了什么

### 2.1 方法：不采信自报，只读回

审计在**运行中的生产容器**上直接探测并直读 MySQL/Elasticsearch，不采信任何自报，全部输出为原始读回（`docs/research/008_rag-never-indexed.md:3-5`）。只看接口 `status` 与健康检查会得到「一切正常」；绕过自报、直接读存储，才能看到真实状态。

### 2.2 证据一：5 个查询，5 次 HTTP 200，5 次空数组

用真实租户身份直接调用知识库检索接口，5 个查询全部返回：

```
HTTP 200
{"results": [], "total_count": 0, "search_mode": "hybrid", "search_time": null}
```

其中一条查询与库中某个 chunk 的文本几乎逐字对应，仍然返回 0 条（`docs/research/008_rag-never-indexed.md:24-37`）。`search_time: null` 是响应自己的自证：这次检索没有真的跑完（`:39`）。

### 2.3 证据二：Elasticsearch 索引是空的

直接读 ES 索引统计：`failure_records`、`test_cases` 与知识库索引的 `docs.count` 全部为 0，`store.size` 均为 249b。**知识库索引 0 篇。** hybrid 检索两路都走 ES（BM25 + 向量 knn），两路同时为空 = 必然 0 结果（`docs/research/008_rag-never-indexed.md:41-51`）。

### 2.4 证据三：MySQL 里的 chunk 一个向量都没有

```sql
SELECT COUNT(*) total,
       SUM(embedding_id IS NULL) null_emb,
       SUM(embedding_id IS NOT NULL) has_emb
FROM document_chunks;
→  39    39    0
```

**39 条 chunk，39 条 `embedding_id` 为 NULL，0 条有向量**（`docs/research/008_rag-never-indexed.md:53-61`）。即使 ES 里有文档，没有 embedding 的 chunk 也会被向量臂的 `exists: embedding` 硬过滤掉。

### 2.5 证据四：真实上传路径从来没有成功过

处理任务表连续三条真实上传全部 `FAILED`，都停在 embedding 步骤：`429`、错误码 `1113`、「余额不足或无可用资源包」。也就是说，`_mark_success()`（当时无条件置 `INDEXED`）在这套部署里从来没有被执行成功过（`docs/research/008_rag-never-indexed.md:63-76`）。

### 2.6 那 10 份「INDEXED」文档是怎么来的

chunk 的来源字段是 `{"source": "benchmark_seed", ...}`：它们是直接写进 MySQL 的种子数据（含 chunk 文本与 `section_title`），从来没有走过 ES 索引（`docs/research/008_rag-never-indexed.md:80-91`）。所以 `documents.status = 'INDEXED'` 在这 10 份文档上是**假状态**：只反映「chunk 文本写进 MySQL 了」，与「能不能被检索到」无关；当时的 `_mark_success` 没有任何索引后校验（`:90-92`）。

### 2.7 附带发现：结构化字段「存而不索引」

审计时 ES mapping 把 `metadata` 定义成 `{"type": "object", "enabled": false}`；`chunk_index` / `page_number` / `section_title` 装在其中，被存储却不被索引，无法做 term 过滤（`docs/research/008_rag-never-indexed.md:96-108`）。后续提交部分修复了这一点：mapping 新增顶层 `clause_no`（integer）与 `section_no`（keyword），`section_title` / `chunk_index` / `page_number` 也被提升到顶层（`backend/app/core/elasticsearch.py:176-201`；`backend/app/worker/doc_processor.py:390-427`），`metadata` 对象本身仍为 `enabled: False`（`backend/app/core/elasticsearch.py:201`）。但 Phase 2 实验证明，仅靠字段可过滤还不够：4 条条号直查的失败发生在分词、BM25 排序、msm 剪枝、doc 帽与终滤（`backend/docs/eval-gate-phase2-experiment.md:29-37`）。

### 2.8 为什么既有测试没有发现

审计里最刺眼的一条，是上一轮「全链路测试」的断言：

```python
record("POST /knowledge/search → 非 500（检索链路可达）",
       st is not None and st < 500, ...)
```

**「非 500」对空结果完全沉默。** 空数组同样满足 `st < 500`（`docs/research/008_rag-never-indexed.md:125-128`）。同一份笔记还记录：`status = INDEXED` 只表示 MySQL 有 chunk 行，与 ES 无关；`/health` 五项 healthy 并不检查知识库有没有数据（`:115-128`）。

### 2.9 证据边界与汇总

审计诚实标注了未核实项：没有验证「余额不足」是长期还是当日限额；没有验证换本地嵌入后的维度迁移；没有跑真实上传全链路；没有确认缓存是否有陈旧条目；探测只用只读操作（`docs/research/008_rag-never-indexed.md:156-166`）。这让「ES 0 篇 / 39 chunk 无向量 / 假 INDEXED」成为可复查事实，而不是情绪化判断。

| 信号 | 系统显示 | 实际含义 | 证据 |
|---|---|---|---|
| 检索响应 | HTTP 200，`results: []` | 两路检索都为空 | `docs/research/008_rag-never-indexed.md:24-32` |
| `search_time` | `null` | 检索没有真正完成 | `docs/research/008_rag-never-indexed.md:39` |
| ES 知识库索引 | `docs.count = 0` | 混合检索两路必然为空 | `docs/research/008_rag-never-indexed.md:44-48` |
| MySQL chunk | 39 条，39 条 `embedding_id` 为 NULL | 0 条向量 | `docs/research/008_rag-never-indexed.md:56-59` |
| 上传任务 | `FAILED`（429） | embedding 配额耗尽 | `docs/research/008_rag-never-indexed.md:66-72` |
| 文档状态 | `INDEXED`（10 份） | 只表示 MySQL 有 chunk 行 | `docs/research/008_rag-never-indexed.md:80-91` |
| 健康检查 | 五项全绿 | 不检查知识库是否有数据 | `docs/research/008_rag-never-indexed.md:11-12,122` |
| 端到端断言 | `status < 500` | 对空结果沉默 | `docs/research/008_rag-never-indexed.md:125-128` |

---

## 3. 根因：状态与检索之间没有契约

### 3.1 写侧：`INDEXED` 是无条件写入的

`document_status.py` 的模块 docstring 直接写明了缺陷：

> 「为什么必须校验（issue #181）：**`_mark_success` 是无条件把状态置成 `INDEXED` 的**——它不看 ES 里到底有没有东西。而『已索引』这个状态是运维、前端与监控判断『知识库到底能不能用』的唯一依据。」（`backend/app/services/document_status.py:40-42`）

当状态写入不依赖任何回读，`INDEXED` 就从「可检索」退化成「代码走到了这一行」。demo 种子路径此前还直接赋值 `doc.status`，现在统一走 `mark_document_indexed` / `mark_document_failed`（`backend/app/services/document_status.py:11-14`）——活体审计里的 10 份假 `INDEXED` 恰好全部来自种子路径（`docs/research/008_rag-never-indexed.md:80-91`）。

### 3.2 一个被实测推翻的因果故事

> 「**不要用『库会抛错』当判据。** 我最初在这里写的是『bulk 不抛错 ≠ 数据进去了』，但**实测不成立**：`async_bulk` 默认 `raise_on_error=True`，遇到 mapping 冲突会抛 `BulkIndexError`（对抗评审 F1 指出，已实测复现）。那条因果故事当时没验证过。」（`backend/app/services/document_status.py:44-51`）

真正的理由是：**不变量要在它被断言的地方强制**。「状态 = INDEXED ⟹ 文档在 ES 里查得到」此前唯一的执行者是「bulk 库大概会抛错」这个假设；回读校验把它变成可执行断言——库的行为变了、mapping 改了、bulk 被换写法，都不再能让它悄悄失效（`:48-51`）。

### 3.3 读侧：两条硬过滤让「存在」不等于「可见」

1. **向量臂硬过滤 `exists: embedding`。** 空值在 ES 里 `exists=false`；一条带 embedding 的 chunk 都没有时，文档对向量臂永远不可见（`backend/app/services/document_status.py:33-36`）。
2. **检索通道硬过滤组织。** chunk 的 `organization_id` 与文档不一致时，谁都搜不到它（`:37-38`）。

即使 chunk 已写进 ES，只要缺 embedding 或 org 写错，检索仍是 0 结果——而状态字段对此一无所知。

### 3.4 观测与评测：没有任何信号看「数据是否存在」

`/health` 五项全绿，但不看知识库有没有数据（`docs/research/008_rag-never-indexed.md:11-12,122`）；检索端点在索引为空时返回裸的 `results: []`，没有显式标记（`:143-145`）；没有 golden set 时回归没有信号，所以审计的修复路径第 4 条是「建评估集，让这件事永远不可能再悄悄发生。有了 golden set，`recall@10 = 0.0` 会在 CI 里立刻变红」（`:150-152`）。

### 3.5 根因链

| 层 | 缺失的契约 | 后果 | 证据 |
|---|---|---|---|
| 写侧 | `INDEXED` 不依赖回读 | 状态可以撒谎 | `backend/app/services/document_status.py:40-42` |
| 旁路 | 种子路径直接赋值状态 | 假状态长期存在 | `backend/app/services/document_status.py:11-14`；`docs/research/008_rag-never-indexed.md:80-91` |
| 读侧 | 状态与硬过滤无关 | 有 chunk 也可能搜不到 | `backend/app/services/document_status.py:33-38` |
| 观测侧 | 健康检查不看数据 | 静默失败不可见 | `docs/research/008_rag-never-indexed.md:122` |
| 评测侧 | 无 golden set / 无 CI 门禁 | 回归不可见 | `docs/research/008_rag-never-indexed.md:150-152` |
| 测试侧 | 断言 `status < 500` | 空结果照样通过 | `docs/research/008_rag-never-indexed.md:125-128` |

---

## 4. 修复 I：INDEXED ⟹ 可检索

### 4.1 三条不变量

修复把「文档已索引」定义成三条可回读断言（`backend/app/services/document_status.py:27-54`）：

| # | 不变量 | 为什么需要 | 代码 |
|---|---|---|---|
| 1 | 该文档至少有一条 chunk 落在索引里 | 一条都没落 = 不可检索 | `backend/app/services/document_status.py:56-66` |
| 2 | 其中至少一条带 `embedding` 字段 | 向量臂硬过滤 `exists: embedding`；`exists` 只证「存在」，不证「非退化」 | `backend/app/services/document_status.py:68-85` |
| 3 | chunk 的 `organization_id` 与文档一致 | 检索通道硬过滤组织；org 写错 = 谁都搜不到 | `backend/app/services/document_status.py:87-104` |

三条合起来就是一句可执行的话：**状态 = INDEXED ⟹ 文档可被检索到。**

### 4.2 `verify_indexed` 的执行语义

函数先 `refresh`，再分三步 `count`（`backend/app/services/document_status.py:56-104`）：

1. **按 `document_id` 计数。** 为 0 抛 `RuntimeError`，错误信息含文档 ID、索引名与后果说明（「拒绝把该文档标记为已索引——否则会出现『状态已索引、检索却查不到』」）。按 `document_id` 过滤同时防止「别人的文档为本文档挡枪」（`backend/tests/integration/test_index_verification.py:157-165`）。
2. **按 `document_id + exists: embedding` 计数。** 为 0 抛错，指出「向量通道硬过滤 `exists:embedding`，这些内容对混合检索的向量臂永远不可见」。
3. **按 `document_id + organization_id` 计数。** 为 0 抛错，指出「检索通道硬过滤组织，谁都搜不到它」。

三步通过后返回实际条数。两类「不完整但可用」不阻断但必须留痕（`:106-123`）：条数不符 → WARNING「索引条数与预期不符」；部分 chunk 缺向量或 org 不匹配 → WARNING「索引可检索性不完整」。**「不可检索」是错误，「不完整」是警告**——混为一谈会让运维在真正的失败面前麻木。

### 4.3 写侧的两条不变式

`document_status.py` 定义写侧两条不变式（`:3-9`）：**`INDEXED` 只能在校验通过之后写入**（`mark_document_indexed` 先 `verify_indexed`，通过后才写状态与 `indexed_at`——`:127-145`）；**`FAILED` 不得覆盖 `INDEXED`**（晚到的失败只能把非终态文档推进为 `FAILED`，已是 `INDEXED` 的终态不被覆盖并留 WARNING——`:148-159`）。

### 4.4 调用点：worker 主链与 demo 路径同源

worker 在 `_replace_document_state` 里完成「删旧 chunk → 写新 chunk → bulk 索引 → 回读校验」：

```python
await ElasticsearchTools.bulk_index_documents(es_docs)
# 回读确认把「INDEXED ⟹ 可检索」变成可执行断言（issue #181；
# bulk 的 raise_on_error 只防部分失败，不能代替这条断言……
await _verify_indexed(
    client, doc.id, len(es_docs), organization_id=str(doc.organization_id)
)
```

（`backend/app/worker/doc_processor.py:486-488`；导入见 `:28`）demo 种子路径也统一走 `mark_document_indexed` / `mark_document_failed`，堵住「种子数据制造假 INDEXED」的旁路（`backend/app/services/document_status.py:11-14`）。

### 4.5 8 个集成测试：把不变量钉在真实 Elasticsearch 上

测试在真实 ES 上运行（`RUN_INTEGRATION=1` 门控），用独立索引名，绝不碰真实索引（`backend/tests/integration/test_index_verification.py:21-28,90-135`）：

| # | 用例 | 行 | 钉住的行为 |
|---|---|---|---|
| 1 | `test_raises_when_nothing_landed` | `142-154` | 一条都没落 → 抛错；错误信息定位文档、带索引名、说清后果 |
| 2 | `test_other_documents_do_not_mask_missing_chunks` | `157-165` | 索引里有别人的文档时，本文档一条没落也必须抛错 |
| 3 | `test_raises_when_no_chunk_has_embedding` | `172-178` | 落库但无向量 → 抛错，说明向量缺失 |
| 4 | `test_raises_when_organization_mismatch` | `185-191` | `organization_id` 全不匹配 → 抛错 |
| 5 | `test_passes_when_all_chunks_present` | `198-202` | 全部落库 → 通过并返回条数 |
| 6 | `test_passes_when_actual_exceeds_expected` | `206-211` | 实际比预期多不算失败（目标是「有没有落进去」，不是精确对账） |
| 7 | `test_partial_index_warns_but_does_not_block` | `218-230` | 部分落库不抛错，但必须留 WARNING |
| 8 | `test_partial_missing_embedding_warns_but_does_not_block` | `234-251` | 部分缺向量不抛错但留痕；不能误报成「条数不符」 |

> 计数说明：`grep -c "^async def test_"` 得到 9，因为 fixture 名 `test_index` 也以 `test_` 开头（`:91`）；实际测试函数 8 个（`142,157,172,185,198,206,218,234`）。

### 4.6 测试工程：为什么这些用例有「牙」

- **变异验证。** 测试 docstring 写明判定力（`:30-37`）：删掉 `if indexed == 0: raise ...` →「一条都没落」必红；删掉 embedding 段 →「无 embedding」必红（旧实现只按 `document_id` 计数，该场景 `count=3≠0`，必不抛错，`pytest.raises` 收不到异常即红）；删掉 org 段同理。这比「回退到 HEAD」更有力：它证明断言真的在断言它声称的东西。
- **环境守卫。** `RUN_INTEGRATION=1` 控制是否允许跑集成测试，并额外做 ES 可达性检查；ES 不可达时整体 skip 而非 ERROR（`:50-79`）。不把「没跑」伪装成「通过」，也不把「环境缺失」伪装成「代码失败」。
- **索引隔离。** fixture 为每个用例建独立索引、用完删除；每个用例重建 ES 客户端，避免 pytest-asyncio 事件循环复用问题（`:90-135`）。

### 4.7 CI 位置与边界

8 个用例在 `integration` job 中运行：job 定义见 `.github/workflows/ci-fast.yml:224-230`；触发条件为 `develop` push 或目标为 `develop` 的 PR（`:227-230`）；`RUN_INTEGRATION: '1'` 见 `:303`；整份 `tests/` 同进程运行以暴露跨套件污染（`:341-344`）。

诚实边界：本文写作环境没有可用 ES（端口探测失败），也没有后端虚拟环境，因此**本文不声称本地复跑过这 8 个用例，也不引用任何通过数**；是否通过以 `develop` 上该 job 的 CI 结果为准。另一个边界：`verify_indexed` 只校验「有 chunk、有 embedding 字段、org 一致」，**不校验向量质量**（零向量由写入路径保证不产生——`backend/app/services/document_status.py:33-35`），也**不校验内容正确性**。它解决「可检索性」，不解决「检索质量」；后者由第 5 节的 eval gate 负责。

### 4.8 给评审者的检查点：一个 INDEXED 状态是否可信

1. **写入前是否回读？** 看调用链是否经过 `verify_indexed` / `mark_document_indexed`（`backend/app/services/document_status.py:127-145`；`backend/app/worker/doc_processor.py:486-488`）。
2. **回读是否按 `document_id` 过滤？** 否则索引里别人的文档会为本文档「挡枪」（`backend/tests/integration/test_index_verification.py:157-165`）。
3. **是否检查 `embedding` 字段？** 只数 chunk 数量不够；向量臂硬过滤 `exists:embedding`（`backend/app/services/document_status.py:68-85`）。
4. **是否检查 `organization_id`？** 组织写错 = 谁都搜不到（`:87-104`）。
5. **部分落库是否留 WARNING？** 不阻断不等于不记录（`:106-123`）。
6. **`FAILED` 是否会覆盖 `INDEXED`？** 终态回退会让监控看到假的失败或假的成功（`:148-159`）。

---

## 5. 修复 II：eval gate——让检索质量可回归

### 5.1 数据集与指标

起步版门禁只测 `HybridRetriever.retrieve`（关键词 + 向量 + RRF + MMR + 后处理），有意不经 rerank、语义缓存与 LLM 环节，保证可复现（`backend/docs/eval-gate-phase1.md:6-15`）。数据集为 `backend/evals/golden_set/clause_set_v1.json`，起步 28 条 = 25 正例 + 3 负例（`:14`）。主指标 ClauseHit@k：正例中「top-k 里存在某 chunk：文件名 = gold 文件且 gold 条号出现在该 chunk 的定义位」的比例；辅助指标有 NearMiss@k、MRR、EmptyAcc、Wilson 95% CI、errors；条号支持中文/阿拉伯数字归一化，命中用「定义位」，正文引用不计入，单条查询失败记为 `status=error` 且不进比例分母（`:38-58`）。

### 5.2 真实环境基线

2026-10-09 的真实环境冒烟基线（dev ES + 本地 Qwen3-Embedding 0.6B，无 LLM 辅助）：

| 指标 | 值 |
|---|---|
| ClauseHit@1 | 80%（20/25） |
| ClauseHit@3 / @5 | 84%（21/25），95% CI 65.3–93.6% |
| NearMiss@5 | 12.5% |
| MRR | 0.82 |
| 负例 EmptyAcc | 100% |
| errors | 0 |

（`backend/docs/eval-gate-phase1.md:92-96`）

### 5.3 CI 门禁的确定性协议

eval gate 不是「在某个索引上跑一遍分数」，而是一套固定协议（`:100-149`）：先删后建专用评估索引（索引名必须含 `eval`，防误指生产），重建后断言 `count==0`；bulk 写入 28 个 chunk（17+11）→ refresh 到 visible；回读断言 `count(org) == 写入块数` 且 `distinct document_id == 语料文件数(2)`；跑分与比对只发生在这一个物理状态上（无 tombstone）。

为什么必须这么严格？独立评审实测过：同一语料、同一代码，仅重灌一次 seed，`NearMiss@5` 会在 12.5% ↔ 16.7% 之间摆动（BM25 统计在 tombstone/merge 物理状态下的差异）；同物理状态内则逐位稳定（`:107-118`）。不固定物理状态，门禁会变成「随机红绿」。

### 5.4 live / fake 两种模式，数字不可混比

| | live 模式 | CI 模式 |
|---|---|---|
| 嵌入 | 真实服务（Ollama/API） | 确定性伪嵌入 `hash-bigram-char-v1`（dim=256） |
| 依赖 | ES + 嵌入服务 | 只需 ES 8.11，无需网络 |
| 用途 | 质量观测、逐阶段实验 | 门禁基线（`ci_baseline.json`）保护的对象 |

（`:120-134`）伪嵌入不是随机向量：非空白字符的 unigram+bigram 经 sha256 分桶计数 + L2 归一化，逐位可复现；`compare` 校验报告的 `embedding_mode`/`top_k`/`dataset_id`/`dataset_version`/`corpus_chunks` 与基线协议逐键一致，不一致直接红（`:129-134`）。

### 5.5 零容差、失败语义与 CI 位置

基线 `backend/evals/baselines/ci_baseline.json` 固定 7 项指标与容差：floor 为 `clause_hit@1 = 0.8`、`clause_hit@3 = 0.84`、`clause_hit@5 = 0.84`、`mrr = 0.82`、`empty_accuracy = 1.0`；cap 为 `near_miss@5 ≈ 0.1667`（文件内为 `0.16666666666666666`）、`errors = 0`；`tolerance = 0`（相等即通过，任何下跌即红、任何提升放行）。失败语义 fail-closed：指标越界 → exit 1；报告/基线缺失、损坏、协议不符 → exit 2；两者都是红（`backend/docs/eval-gate-phase1.md:144-149`）。

CI 位置：`Eval Gate` job 见 `.github/workflows/ci-fast.yml:371-415`；执行 `make eval-ci`（`:414-415`；目标定义 `Makefile:66`）；`CI Pass` 把 `eval-gate` 列入 `needs`，红即分支保护拦下（`:531-553`）。

### 5.6 基线维护纪律

门禁最容易腐化的方式是「红灯 → 直接重盖基线」。修复文档明确禁止：

> 「**改基线 = 显式 PR 行为。** 有意变更检索行为（并接受新数字）时：跑 `make eval-ci-baseline`，把 `ci_baseline.json` 的 diff 放进同一个 PR 供评审核对……『红灯 → 直接重盖基线』而不修代码 = 把回归合法化，评审拒收。」（`backend/docs/eval-gate-phase1.md:151-164`）

这把门禁从技术设施变成了组织契约：数字的每一次变化都必须有人签字。

### 5.7 数字索引

| 数字 | 含义 | 来源 |
|---|---|---|
| 28（25 正 + 3 负） | golden set 规模 | `backend/docs/eval-gate-phase1.md:14` |
| 7 | 固定指标数 | `backend/docs/eval-gate-phase1.md:145-147`；`backend/evals/baselines/ci_baseline.json` |
| 80% / 84% / 84% | ClauseHit@1 / @3 / @5（live） | `backend/docs/eval-gate-phase1.md:92-96` |
| 12.5% / 0.82 / 100% / 0 | NearMiss@5 / MRR / EmptyAcc / errors（live） | `backend/docs/eval-gate-phase1.md:92-96` |
| 0.8 / 0.84 / 0.84 / 0.82 / 1.0 / 0.16666666666666666 / 0 | CI 基线 7 项 | `backend/evals/baselines/ci_baseline.json` |

`docs/METRICS.md` 在基线不存在（见文首），因此上表直接链接到评估报告与 CI 基线文件；这两者是当前仓库内可复核的数字源。边界：只覆盖检索一层，rerank/LLM 生成链路不在 eval 内（`:6-15`）；28 条是小样本，live 数字带 Wilson 区间且绑定当次物理索引状态；live 与 fake 数字不可混比（`:120-134`）。

### 5.8 给评审者的检查点：一个 eval gate 是否可信

1. **索引是否每次先删后建并回读 count？** 否则 tombstone 会让 NearMiss 在 12.5% ↔ 16.7% 之间摆动（`backend/docs/eval-gate-phase1.md:107-118`）。
2. **嵌入是否确定性、可复现？** 伪嵌入算法必须有指纹锁（`:120-134`）。
3. **报告与基线是否逐键比对协议？** 模式、top_k、数据集版本不一致必须直接红（`:129-134`）。
4. **容差是否为零、失败是否 fail-closed？** exit 1/2 都是红，没有静默放行（`:144-149`）。
5. **改基线是否走显式 PR？** 红灯直接重盖基线 = 把回归合法化（`:151-164`）。
6. **门禁是否接入分支保护的汇聚检查？** 孤儿检查等于没有门禁（`.github/workflows/ci-fast.yml:531-553`）。

### 5.9 一次门禁运行的完整步骤

1. `make eval-ci` 调用 `seed --recreate`：先删后建评估索引，断言 `count==0`（`backend/docs/eval-gate-phase1.md:107-118`）。
2. bulk 写入 28 个 chunk 并 refresh；回读 `count(org)==28`、`distinct document_id==2`（`:115-118`）。
3. `run --embedding fake`：确定性伪嵌入跑 28 条查询，产出 `latest.{json,md}`（`:120-134`；`Makefile:66`）。
4. `compare`：7 项指标与 `ci_baseline.json` 逐项比对；越界 exit 1，报告/协议异常 exit 2（`:144-149`）。
5. CI 中该 job 的退出码进入 `CI Pass` 汇聚；红即分支保护拦下（`.github/workflows/ci-fast.yml:531-553`）。

---

## 6. 失败案例：Phase 2 实验——「没有单一环能修好 4 条」

这一节记录的不是成功，而是一次**按纪律停下来的失败**。

### 6.1 实验纪律与基线

目标是回答「条号在哪一段被淹」，而不是「证明某个修复有效」。纪律（`backend/docs/eval-gate-phase2-experiment.md:9-20`）：**同物理状态对照**（实验期间不重建、不 reseed）；**一次一变量**（每条假设配一个只改一个变量的判别探针）；**探针只读**（只调各臂与 ES `_search`，反事实变体在进程内由真实源码派生）。测前基线：ClauseHit@1 80% / @3=@5 84% / NearMiss@5 12.5% / MRR 0.82 / EmptyAcc 100%，22/28 条 query 路由 `keyword_only`（`:19-20`）。

### 6.2 四条失败查询

4 条失败全部是「带文档全称的条号直查」：`clause_arabic_3`、`clause_chinese_7`、`clause_arabic_7`、`clause_chinese_12`（`:6-7`）。前置事实：语料分词器是 cjk（bigram）；在「《文档全称》第 3 条规定了什么？」这类查询里，条号信息只剩「数字」一个 token——`第`/`条` 两个 token 在全部条款 chunk 里都出现，无区分度（`:22-27`）。

### 6.3 逐阶段判决

| 阶段 | 判决 | 实测证据 |
|---|---|---|
| ① 改写 | 无罪 | 两个改写变体都完整保留条号，只去掉尾标点 |
| ② 路由 | 非修复点 | 4 条路由 `simple→keyword_only`；强制 `hybrid` 重跑，4 条 gold 仍全 miss（全量 all-hybrid 变体：0 条修复） |
| ③ keyword 臂（剪枝） | 有罪 | 标题 bigram 抬高查询 token 总数 → `minimum_should_match: 30%` 阈值水涨船高，gold 只命中内容词、被整条剪掉；msm 改 10%（单变量探针）gold 立刻回到候选 |
| ③ keyword 臂（排序） | 有罪 | 前言块 43.4 分居首，gold（第 3 条）11.1 分列 kw#4；只加/删文档全称的对照里，候选数 1→4，gold 从 @1 命中变为被剪掉 |
| ③ keyword 臂（中文数字） | 有罪 | `第七` ≠ `第7`（bigram 不同）→ gold 连候选都进不去（vector 臂 v#3 明明找到了它） |
| ④ RRF/MMR | 有罪 | `clause_arabic_3` 的 gold 在 `_mmr_select` 的多样性惩罚下进不了 top5 |
| ⑤ post-process | 有罪（三重） | doc 帽 2/文档；终滤阈值 0.15；术语表只取前 12 个 bigram，`数据/删除` 被截断出列表 |

（`backend/docs/eval-gate-phase2-experiment.md:29-37`）

### 6.4 反事实矩阵

| 变体 | ClauseHit@1 | ClauseHit@5 | NearMiss@5 | MRR | 修复 | 副作用 |
|---|---|---|---|---|---|---|
| before（现状） | 80.0% | 84.0% | 12.5% | 0.82 | — | — |
| 仅 doc 帽放开 | 80.0% | 88.0% | 20.8% | 0.83 | `clause_arabic_3` | 2 条新 near-miss 进 top5 |
| 仅全量 hybrid | 84.0% | 84.0% | 12.5% | 0.84 | **0 条** | near-miss 原形翻转 |
| 全量 hybrid + 帽放开 | 84.0% | 88.0% | 29.2% | 0.8533 | `clause_arabic_7` | 4 条新 near-miss；`clause_arabic_3` 反被 MMR 挡死 |

（`:49-56`）

### 6.5 结论：没有单一环

> 「**没有任何单一或已测两环组合修好全部 4 条**：Q1 只有『帽放开+保持 keyword_only』能修，Q3 只有『hybrid+帽放开』能修（二者互斥）；Q2/Q4 还叠加中文数字分词桥接 + 术语表截断。全修 = 3~4 环同时动 + 数形归一化，且每环都是跨 query 类型的产品行为变更 → **按纪律停下报告**。」（`:58-60`）

这就是任务书里说的失败案例：实验没有产出「一行修复」，而是产出了一张「没有单一环能修好 4 条」的地图。它证明「只修被证明的那一环」在本案不存在——淹没是 4 环以上叠加，且候选修复全部触及产品语义（召回构成、多样性、阈值），互相拉扯。

### 6.6 决策落地与不美化的 0/4

实验把五个未定选择摆给拍板者（doc 帽、msm 口径、数形归一化范围、终滤与臂标记、MMR 对条号直查的适用性），随后按默认路线逐步落地（`:62-88`）：doc 帽保留、计数移到质量过滤之后（PR #323）；带《…》全称按剔除标题 token 后的 30% 计算（PR #323）；数形归一化先落查询侧；keyword_only 命中补 `has_keyword` 臂标记，终滤改走 0.10 档；新增 `_is_clause_lookup_query`，条号直查在 hybrid 路径跳过 MMR。收官记录没有美化：

> 「收官态（本 PR，#208 Phase 2 a–e 五环全部落地）：4 条失败 query 经 c/d/e 后的剩余扼杀环如实记录……gold 回收 = **0/4**（不美化）；四条失败 query 的下一步候选修复（帽上限 / 术语表截断）需单独拍板。」（`:90-95`）

### 6.7 这个失败案例教会我们什么

1. **可测量的失败 > 不可证伪的修复。** 只报告「调了权重，感觉好多了」，就不会知道 4 条失败分别死在哪一环。
2. **反事实必须同物理状态。** 否则 NearMiss 的 12.5% ↔ 16.7% 摆动会让所有结论不可信（`backend/docs/eval-gate-phase1.md:107-118`）。
3. **副作用必须同表列出。** 帽放开让 ClauseHit@5 从 84% 升到 88%，但 NearMiss@5 也从 12.5% 升到 20.8%——只报好消息是另一种静默失败。
4. **停下来报告是合法交付。** 实验明确写「修复点：无（本轮不落任何产品代码变更）」，把选择与数据摆出来，修复决策留给下一步（`:3-5,58-60`）。

---

## 7. 人类与 AI 的分工

### 7.1 分工表

| 环节 | AI 做了什么 | 人类做了什么 | 证据 |
|---|---|---|---|
| 发现 | 在运行中的容器里直读 MySQL/ES/HTTP，不采信自报 | 设定「只读、不采信自报、原始输出未加工」的审计纪律 | `docs/research/008_rag-never-indexed.md:3-5,156-166` |
| 根因 | 提出并验证「bulk 不抛错」假设；被对抗评审指出错误后实测复现并更正 | 要求「每条因果故事必须有实测」 | `backend/app/services/document_status.py:15-19,44-51` |
| 实验 | 设计单变量探针、逐阶段 trace、反事实矩阵 | 预先定纪律：同物理状态、一次一变量、探针只读、停止条件 | `backend/docs/eval-gate-phase2-experiment.md:9-20` |
| 决策 | 把五个未定选择与代价（含 NearMiss +8.3pt）摆出来 | 拍板 doc 帽保留、msm 口径、数形归一化范围、MMR 适用性 | `backend/docs/eval-gate-phase2-experiment.md:62-88` |
| 门禁 | 实现 `make eval-ci`、固定索引刷新协议、fail-closed 比对 | 定下「改基线 = 显式 PR 行为」「回归 ≠ 改基线」 | `backend/docs/eval-gate-phase1.md:100-164` |
| 测试 | 写 8 个集成测试、变异验证、隔离索引 fixture | 要求「测试必须有牙」：删掉断言对应用例必红 | `backend/tests/integration/test_index_verification.py:30-37` |
| 发布 | 起草文档、生成报告 | 复核、决定公开边界、承担发布责任 | 本文；`backend/docs/eval-gate-phase1.md:151-164` |

### 7.2 AI 擅长的三件事

1. **高密度读回。** 直接查 ES 计数、MySQL 聚合、HTTP 响应，把「系统自报」与「存储事实」分开；这类 I/O 密集、需多源交叉的工作，AI 带着自己的上下文跑完，只把结论带回来。
2. **单变量实验与反事实矩阵。** 一次只改一个变量、同一物理索引对照、副作用同表列出——AI 不会因为「结果不好看」而省略不利数字。
3. **构造性验证。** 变异测试、边界用例、错误信息断言：批量构造「删掉这段代码，哪个用例会红」的证明，比人工回退更有判定力。

### 7.3 人类不可让渡的三件事

1. **定判据与停止条件。** 什么算「修好」、什么算「不完整但可用」、实验到哪一步必须停——这些是产品语义，不是代码问题。Phase 2 的「按纪律停下报告」就是人类预设的停止条件在起作用。
2. **拍板产品行为。** doc 帽保留还是放开、msm 阈值怎么算、条号直查要不要跳过 MMR——每个选择都改变召回构成与用户体验，必须有人签字。
3. **承担发布与公开的责任。** 哪些数字可以写进公开档案、哪些内部审计材料不公开、哪些修复可以合并——风险由人承担，不能外包给 AI。

### 7.4 自我更正机制

这份档案里最有价值的三处，都是「AI 出错后被机制抓住」：错误的因果故事被对抗评审 F1 指出，随后实测复现并写进代码注释（`backend/app/services/document_status.py:15-19,44-51`）；NearMiss 摆动被独立评审实测发现，随后用固定索引刷新协议消除（`backend/docs/eval-gate-phase1.md:107-118`）；Phase 2 收官如实记录 gold 回收 0/4，不美化（`backend/docs/eval-gate-phase2-experiment.md:90-95`）。共同点是：**机制不依赖「AI 这次是否诚实」，而依赖「结论是否可被独立读回」**。

### 7.5 可复制的协作协议

1. **先定事实口径，再写结论。** 事实 = 可复现的命令输出或代码行；判断 = 基于事实的推论，两者分开标注。
2. **先测量，后动手。** 没有逐阶段 trace 的实验，不允许改产品代码。
3. **一次一变量，同物理状态对照。** before/after 必须能排除索引状态、缓存、随机性。
4. **不利数字与副作用必须同表。** 只报提升不报 NearMiss 上升，视为无效报告。
5. **改基线 = 显式 PR。** 门禁数字的每一次变化都要有人复核。
6. **停下来报告是合法交付。** 修复需要 3~4 环同时动且互相拉扯时，输出决策清单比强行落一行代码更有价值。

### 7.6 AI 产出中必须由人复核的清单

1. **任何「根因」叙事：** 必须能复现，否则只是假设；`backend/app/services/document_status.py:44-51` 的 bulk 故事就是反例。
2. **任何「修复有效」结论：** 必须有同物理状态 before/after 与副作用表（`backend/docs/eval-gate-phase2-experiment.md:49-60`）。
3. **任何「数字提升」：** 必须给出分母、样本量与置信区间（`backend/docs/eval-gate-phase1.md:92-96`）。
4. **任何「门禁变绿」：** 必须确认不是重盖基线（`backend/docs/eval-gate-phase1.md:151-164`）。
5. **任何公开材料：** 必须检查是否含密钥、文档正文、内部 endpoint——这也是本文的公开边界。

---

## 8. 同日合并的 9 个 PR：修复运动的闭合边界

2026-10-24，`origin/develop` 上合并了 9 个可靠性/契约修复 commit。它们不直接属于本文的 RAG 质量主线，但构成了「闭环=完成」这一工作方式的同期证据。本文只把它们作为**已合并事实**列出，不引用其运行时效果。

| commit | 主题 | issue / PR |
|---|---|---|
| `f67ef9f8` | `fix(ux): 登录校验与错误提示收敛 + MCP 异常类兼容` | #492 / #504 |
| `a80ae306` | `feat(deploy): 数据库迁移纳入启动与发布流程（一次性 migrate + schema guard）` | #495 / #503 |
| `ee30ae4c` | `fix(auth): bcrypt 校验/哈希移入线程池，解除事件循环阻塞` | #500 / #502 |
| `175006de` | `fix(sdk): stdio 运行时收口——stdout 纯净/cancel 收敛/严格 Redis/幂等与退出` | #493 / #506 |
| `0ff446e9` | `fix(acp): cancel 真取消 + stdout 零泄漏` | #494 / #520 |
| `00dbeaa7` | `feat(auth): API Key scope 强制——端点读写按 scope 门禁，api-keys 仅 JWT` | #496 / #517 |
| `1d4d4060` | `fix(webhook): 运行时加固——配置门控/限流前置/租约 fencing/复用历史` | #497 / #505 |
| `963aeaa1` | `feat(commands): 契约信封/Redis 锁与幂等/内置命令收口` | #498 / #515 |
| `653715d2` | `feat(agent): Session Fork 契约收口——路由/响应/幂等/seq/history_limit` | #499 / #518 |

验证命令与输出：

```
$ git rev-parse --short origin/develop
653715d2
$ for c in f67ef9f8 a80ae306 ee30ae4c 175006de 0ff446e9 00dbeaa7 1d4d4060 963aeaa1 653715d2; do
    git merge-base --is-ancestor $c origin/develop && echo "$c ancestor=yes"; done
f67ef9f8 ancestor=yes
a80ae306 ancestor=yes
ee30ae4c ancestor=yes
175006de ancestor=yes
0ff446e9 ancestor=yes
00dbeaa7 ancestor=yes
1d4d4060 ancestor=yes
963aeaa1 ancestor=yes
653715d2 ancestor=yes
```

这 9 个 commit 的意义不在「功能数量」，而在它们与本文的修复共享同一条验收标准：**代码合并只是起点，回读/契约/门禁才是完成**。本文不对它们的运行时行为做超出代码与测试的承诺；对 RAG 质量的承诺也只到「三条不变量 + 8 个集成测试 + eval gate」为止。

---

## 9. 局限与未验证项

1. **`docs/METRICS.md` 不存在。** 基线 `653715d2` 上没有这个文件；本文数字改为链接到 `backend/docs/eval-gate-phase1.md`、`backend/evals/baselines/ci_baseline.json` 与 `docs/research/008_rag-never-indexed.md` 的原始输出。这是缺陷，不是风格选择：项目应有一个数字单一事实源。
2. **本文未在本地复跑 8 个集成测试。** 写作环境 ES 不可达、无后端虚拟环境；不引用任何通过数。复跑方式见附录 B。
3. **eval gate 只覆盖检索层。** rerank/LLM 生成链路不在门禁内（`backend/docs/eval-gate-phase1.md:6-15`）。
4. **28 条 golden 是小样本。** live 数字带 Wilson 区间且绑定当次物理索引状态；扩语料与双标审计是后续工作。
5. **`verify_indexed` 不校验向量质量与内容正确性。** 只保证「有 chunk、有 embedding 字段、org 一致」；零向量由写入路径保证不产生（`backend/app/services/document_status.py:33-35`）。
6. **集成测试的 CI 触发范围有限。** 只在 `develop` push 或目标为 `develop` 的 PR 上运行（`.github/workflows/ci-fast.yml:227-230`），不是所有分支的必过检查。
7. **Phase 2 结论绑定物理状态。** 全部结论绑定当轮物理索引（28 chunks、cjk），重 seed 后须整体复测（`backend/docs/eval-gate-phase2-experiment.md:97-104`）。
8. **9 个 PR 只验证了「已合并」。** 不声称其生产运行时效果；它们是同期工作方式的证据，不是本文质量主张的论据。
9. **PDF 生成方式。** 同目录 1 页 PDF 由可打印 HTML 经无头浏览器打印生成；若字体有差异，以 Markdown 源为准。

### 9.1 主张与验证状态

| 主张 | 验证方式 | 本次状态 |
|---|---|---|
| ES 0 篇 / 39 chunk 无向量 / 10 份假 INDEXED | 2026-10-06 活体审计原始输出（`docs/research/008_rag-never-indexed.md:24-92`） | 已读回（历史快照，未复跑） |
| 三条不变量与写侧两条不变式 | 代码阅读（`backend/app/services/document_status.py:27-159`） | 已合并；本次未复跑 |
| 8 个集成测试与变异验证 | 测试代码（`backend/tests/integration/test_index_verification.py:142-251`） | 已合并；本次未复跑（ES 不可达） |
| eval gate 零容差 / fail-closed | 基线文件与 CI 定义（`backend/evals/baselines/ci_baseline.json`；`.github/workflows/ci-fast.yml:371-415,531-553`） | 已合并；本次未触发 CI |
| 28 条 golden 与 live 数字 | Phase 1 报告（`backend/docs/eval-gate-phase1.md:14,92-96`） | 历史基线，绑定当次索引 |
| 「没有单一环能修好 4 条」 | Phase 2 实验文档与反事实矩阵（`backend/docs/eval-gate-phase2-experiment.md:49-60`） | 已合并 |
| 9 个 PR 已合并 | `git merge-base --is-ancestor` 全部 yes | 已核验 |

---

## 10. 附录 A：引用清单

**代码**：`backend/app/services/document_status.py:1-15`（模块 docstring、两条写侧不变式、demo 路径收口）；`:27-54`（三条不变量与 bulk 更正）；`:56-66,68-85,87-104`（三步计数与抛错）；`:106-123`（两类 WARNING）；`:127-145,148-159`（`mark_document_indexed` / `mark_document_failed`）。`backend/app/worker/doc_processor.py:28,486-488`（导入与回读调用）；`:390-427`（`clause_no` / `section_no` / `page_number` 顶层字段）。`backend/app/core/elasticsearch.py:176-201`（结构化字段与 `metadata.enabled: False`）。

**测试**：`backend/tests/integration/test_index_verification.py:1-37`（不变量、变异验证、范围）；`:50-79`（`RUN_INTEGRATION` 与 ES 可达性守卫）；`:90-135`（独立索引 fixture）；`:142-251`（8 个测试函数）。

**CI 与门禁**：`.github/workflows/ci-fast.yml:224-230,303,341-344`（integration job、触发条件、`RUN_INTEGRATION`、同进程运行）；`:371-415,531-553`（Eval Gate、`make eval-ci`、CI Pass）。`Makefile:63,66,71`。`backend/evals/baselines/ci_baseline.json`（7 项基线与零容差）。`backend/docs/eval-gate-phase1.md:14,38-45,92-96,100-149,151-164`。`backend/docs/eval-gate-phase2-experiment.md:6-7,19-20,29-37,49-60,62-95`。

**审计与研究笔记**：`docs/research/008_rag-never-indexed.md:3-5,10-12`（方法与结论）；`:24-39`（5 个查询 200 + 空数组、`search_time: null`）；`:41-51,53-61`（ES 0 篇、39 chunk 无向量）；`:63-76,80-92`（429、10 份假 `INDEXED`）；`:96-108,115-128,150-152,156-166`（mapping、被推翻的认知与 `status < 500`、golden set、未核实项）。

**命令输出**：`test -f docs/METRICS.md` → `MISSING`；`git rev-parse --short origin/develop` → `653715d2`；9 个 commit 的 `git merge-base --is-ancestor` 全部 `ancestor=yes`；`grep -c "^async def test_" backend/tests/integration/test_index_verification.py` → `9`（8 个测试 + 1 个 fixture，fixture 见 `:91`）。

---

## 11. 附录 B：复现

```bash
# 1) 索引校验集成测试（需要真实 ES 8.11）
cd backend
export RUN_INTEGRATION=1
export ELASTICSEARCH_HOSTS='["http://<your-es-host>:9200"]'   # 指向你的本地/CI ES 8.11
python -m pytest tests/integration/test_index_verification.py -v

# 2) 评估门禁（与 CI 同一条命令；无需 Ollama）
export ELASTICSEARCH_HOSTS='["http://<your-es-host>:9200"]'   # 指向你的本地/CI ES 8.11
make eval-ci

# 3) 复核本文引用的合并事实
git rev-parse --short origin/develop
for c in f67ef9f8 a80ae306 ee30ae4c 175006de 0ff446e9 00dbeaa7 1d4d4060 963aeaa1 653715d2; do
  git merge-base --is-ancestor "$c" origin/develop && echo "$c merged"
done
```

环境要求见 `backend/tests/integration/test_index_verification.py:50-79` 与 `backend/docs/eval-gate-phase1.md:136-149`。

---

## 12. 结语

> **「已索引」不是一个可以自报的状态，而应该是一条可被回读的断言；「检索质量」不是一个可以感觉的指标，而应该是一个可以被门禁拦住的数字。**

DocMind 在 2026-10 的这次修复，把前者变成了三条不变量与 8 个集成测试，把后者变成了 28 条 golden set、7 项指标、零容差门禁。它仍有边界：只覆盖检索层、样本只有 28 条、集成测试不是所有分支的必过检查、`docs/METRICS.md` 仍然缺位。但这些边界被如实写下来了——这本身，就是「不再静默失败」的一部分。
