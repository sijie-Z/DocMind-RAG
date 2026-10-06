# DocMind RAG 检索链路的活体实测

> 方法：在**运行中的生产容器**上直接探测 + 直读 MySQL/Elasticsearch，**不采信任何自报**。
> 时间：2026-10-06。全部输出为原始读回，未加工。

---

## 结论（一句话）

**这套 RAG 不是"做得不好"，而是从来没有索引进去过任何一个向量。**
而它失败的方式是**完全静默的** —— 搜索返回 HTTP 200 + 空数组，文档状态显示 `INDEXED`，
健康检查五项全绿。**没有任何一个信号告诉你检索坏了。**

---

## 证据链（四条独立证据，互相印证）

### 证据 1 · 活体搜索：5 个查询全部返回 0 条

用真实用户身份（`user_id=2 guest`，`organization_id=3`，正是那 10 份文档的所属组织）
在容器内直接打 `POST /api/v1/knowledge/search`：

```
auth/me -> 200 {"username":"guest","organization_id":3, ...}

════ 查询：数据安全管理条例的修订背景是什么   -> HTTP 200
    {"query": "...", "results": [], "total_count": 0, "search_mode": "hybrid", "search_time": null}
════ 查询：2024年修订版数据安全管理条例的核心变化有哪些   -> HTTP 200
    {"results": [], "total_count": 0, "search_mode": "hybrid", "search_time": null}
════ 查询：对企业的主要影响是什么   -> HTTP 200   results: []
════ 查询：AI 训练数据的合规要求   -> HTTP 200   results: []
════ 查询：SWOT 分析框架怎么用     -> HTTP 200   results: []
```

其中一个查询（「数据安全管理条例的修订背景是什么」）与库中 chunk 文本
「2024年修订版数据安全管理条例于2024年6月1日正式施行……修订的核心背景包括：」
几乎是逐字对应 —— **仍然 0 条**。

> `search_time: null` —— 响应自己这个字段就在说"这次检索没真的跑完"。

### 证据 2 · Elasticsearch 索引是空的

```
index                 docs.count store.size
failure_records                0       249b
test_cases                     0       249b
paicongming_knowledge          0       249b      ← 知识库索引
```

**0 篇。** 而 DocMind 的 hybrid 检索两路都走 ES（BM25 一路 + 向量 knn 一路），
所以两路同时为空 = 必然 0 结果。

### 证据 3 · MySQL 里的 chunk 一个向量都没有

```
SELECT COUNT(*) total, SUM(embedding_id IS NULL) null_emb, SUM(embedding_id IS NOT NULL) has_emb
FROM document_chunks;
→  39    39    0
```

**39 条 chunk，39 条 `embedding_id` 为 NULL，0 条有向量。**

### 证据 4 · 真实上传路径从来没有成功过（余额不足）

```
id  document_id                            status   error_message
13  9452d3e0-09b3-4675-bfe8-cface7db6940   FAILED   Embedding batch failed after retries:
                                                     Error code: 429 - {'error': {'code': '1113',
                                                     'message': '余额不足或无可用资源包,请充值。'}}
12  a8a7714f-4993-439b-bf1b-37ac4c708ca2   FAILED   同上
11  787a807b-1389-40ac-afdf-31ad3fa83a95   FAILED   同上
```

**每一次真实上传都在 embedding 这一步挂掉。** 也就是说：
`_mark_success()`（`app/worker/doc_processor.py:240-258`，把状态置为 `INDEXED`）
**在这套部署里从来没有被执行成功过**。

---

## 那 10 份"INDEXED"文档是怎么来的

```sql
SELECT LEFT(IFNULL(meta_data,''), 200) FROM document_chunks ... LIMIT 2;
→ {"source": "benchmark_seed", "section": "修订背景与总则"}
```

它们是 **`benchmark_seed`** —— 直接写进 MySQL 的**种子数据**（含 chunk 文本与 `section_title`），
**从来没有走过 ES 索引**。

**所以 `documents.status = 'INDEXED'` 在这 10 份文档上是一个假状态。**
它只反映了"chunk 文本写进 MySQL 了"，与"能不能被检索到"无关。
而 `_mark_success` 里也没有任何"索引后校验"——它不检查 ES 里到底有没有落进去。

---

## 顺带发现：ES mapping 把结构化过滤这条路堵死了

```
"metadata": {"type": "object", "enabled": false}
```

`metadata` 里装的是 `chunk_index` / `page_number` / `section_title`
（`app/worker/doc_processor.py:209-214`），但 **`enabled: false` 意味着这个对象
被存储却不被索引** —— **无法按它做 term 过滤或查询**。

后果：**即使把"条号"抽出来存进 `metadata`，也过滤不了。**
（`section_title` 虽然单独映射成了 `text`，但 `text` 是分词的，做不了精确 term 匹配；
`page_number` 只存在于未索引的 `metadata` 里。）

这与 SuperMew 的对照非常刺眼：SuperMew 的 `gold_chunks` 用 `content_sha256` 标身份、
case 里能标 `route` / `complexity` / `outcome` 做切片统计 —— **它是有结构化字段可筛可切的**。

---

## 被这四条证据推翻的既有认知

| 原以为 | 实际 |
|---|---|
| 「RAG 检索算法不行」（用户的原话：「目前 rag 你都没做好」） | **算法还没轮到被检验 —— 输入是空的** |
| 检索失败会报错 | **返回 200 + 空数组 + `search_time: null`**，完全静默 |
| `status = INDEXED` 表示已索引 | **只表示 MySQL 里写了 chunk 行**，与 ES 无关 |
| `/health` 五项全 healthy 表示系统正常 | **健康检查不看知识库有没有数据** |
| 上一轮的「全链路测试」验证过检索链路 | **那条断言是 `status < 500`** —— 空结果照样通过 |

> 最后一行是我自己要认的：本轮使用的端到端脚本（未入仓）第 149 行 写的是
> `record("POST /knowledge/search → 非 500（检索链路可达）", st is not None and st < 500, ...)`。
> **「非 500」对空结果完全沉默。** 这正是本会话反复出现的那条教训的又一例：
> **检查存在 ≠ 检查有效；判据没覆盖真实失败面，就会给出错误信号。**

---

## 修复路径（按顺序）

1. **让检索先能返回东西**（最高优先级，且与后面所有工作都无关地阻塞一切）
   - **A. 给智谱账户充值**，或
   - **B. 切本地嵌入**（`app/core/config/ai.py:48` 已有 `LOCAL_EMBEDDING_MODEL = "nomic-embed-text"`，
     settings 默认 `EMBEDDING_API_URL = "http://localhost:11434/v1"` 即 Ollama ——
     但 `.env` 覆盖成了智谱）。**注意**：ES mapping 是
     `"embedding": {"type": "dense_vector", "dims": 2048}`（智谱 embedding-3 的维度），
     换模型必须**同时改 mapping 并重建索引**，否则写入直接失败。
2. **给索引加校验，把静默失败变成响亮的失败**
   - `_mark_success` 之前断言该文档在 ES 里的 doc count > 0，否则置 FAILED 并写原因
   - `/health` 增加一项：知识库索引的文档数（0 就应该显性告警）
   - `search` 端点在"索引为空 / 检索结果为 0"时，响应体里带一个显式标记
     （而不是裸的 `results: []`）
3. **把结构化字段变成可过滤的**
   - `metadata.enabled` 改成 `true`，或把 `page_number` / 条款号提成独立 `keyword` 字段
   - 这是后续「第七条 → 第十二条」类问题的**前提**：没有可过滤的条号，
     纯语义检索在相邻条款上必然混淆
4. **建评估集，让这件事永远不可能再悄悄发生**
   - 有了 golden set，`recall@10 = 0.0` 会在 CI 里立刻变红
   - 这是 SuperMew 的那半套（见 `012_reference-rag-architecture.md` §6）

---

## 未核实 / 边界

- **没有验证** 智谱账户"余额不足"是长期状态还是当日限额；只读了错误码 1113 的原文
- **没有验证** 换成本地 Ollama 后 `embedding-3` 的 2048 维与 `nomic-embed-text` 的 768 维
  在 ES mapping 层面的具体迁移步骤（需要重建索引，成本未评估）
- **没有跑** 真实上传的全链路（因为余额问题必然失败）；种子文档是直接写库的
- **没有确认** `retrieval_cache` / `semantic_cache` 里是否有陈旧条目会干扰（本轮返回 0 条，
  说明缓存没有错误命中；但缓存失效逻辑未审）
- 本次探测**只用了只读操作**：查询 MySQL、查询 ES、调用只读的 search 端点。
  **没有修改任何数据、没有创建/删除任何东西。** 唯一写入是 minted JWT 里带了一个
  `jti`（写 Redis 黑名单检查路径为只读）。
