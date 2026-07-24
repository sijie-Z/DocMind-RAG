# DocMind 检索路由决策(面试级回答)

> 一份覆盖 Adaptive RAG 全流程的面试向讲解:从用户问题进来到出答案,每一步为什么这么做、参数怎么选、有哪些 trade-off。

---

## 0. 一句话定位

DocMind 的检索不是"调用一次向量检索",而是**根据问题复杂度自适应选择策略**:简单问题只走 BM25(便宜),中等问题走关键词+向量混合(平衡),复杂问题再叠加 HyDE 和多查询改写(召回率优先)。然后用 RRF 融合、用 MMR 去重、用 cross-encoder 重排、用 quality gate 兜底——整套流水线 **每个阶段都有显式的成本/收益权衡**,不是堆一个黑盒 embedding search。

---

## 1. 核心决策流程

```
用户问题
  │
  ▼
[QueryComplexityClassifier] → simple / medium / complex
  │
  ▼
[QueryIntentClassifier] → factual / procedural / list / definition / comparison / causal / summary / other
  │
  ▼
[RetrievalStrategy] → keyword_only / hybrid / hybrid_hyde
  │
  ▼
        ┌─ keyword_only ──► [BM25] ─────────────────────────────┐
        ├─ hybrid ────────► [BM25 + Vector] → RRF               │
        └─ hybrid_hyde ───► [BM25 + Vector + HyDE]               │
                              + LLM rewrite                       │
                              + multi-query                       │
                                                                    │
                                  ┌─────────────────────────────────┘
                                  ▼
                            MMR 多样性选择
                                  │
                                  ▼
                            Cross-Encoder Rerank
                                  │
                                  ▼
                            Quality filter + 去重
                                  │
                                  ▼
                            Top-K 返回
                                  │
                                  ▼
                            LLM 生成答案 + 引用
```

**关键文件**:
- `backend/app/rag/query_processor.py` — 分类器(复杂度 + 意图 + 改写 + HyDE)
- `backend/app/rag/retriever.py` — 检索器(关键词 / 向量 / RRF / MMR)
- `backend/app/rag/reranker.py` — 重排(cross-encoder / LLM)
- `backend/app/rag/pipeline.py` — 编排层

---

## 2. QueryComplexityClassifier — 复杂度三档

**位置**:`backend/app/rag/query_processor.py:16`

```python
class QueryComplexity:
    SIMPLE = "simple"      # 只走关键词
    MEDIUM = "medium"      # 走混合检索
    COMPLEX = "complex"    # 混合 + HyDE + 多查询改写
```

### 判定规则

| 维度 | simple | medium | complex |
|---|---|---|---|
| 长度 | < 10 字 | 10-30 字 | > 30 字 |
| 问句特征 | 关键词类("X 是什么") | 中等复杂 | 多实体/多跳 |
| 实体数 | 1 个 | 2-3 个 | ≥ 4 个 |
| 触发多查询 | 否 | 否 | **是** |
| 触发 HyDE | 否 | 否 | **是** |
| 是否需要 LLM 改写 | 否 | 局部 | **完整** |

**面试话术**:

> "我们做了查询复杂度自适应,因为不是所有问题都需要向量检索。问'什么是 RAG'这种简单问题,BM25 关键词检索足够,几十毫秒出结果;问'对比 A 和 B 在场景 X 下的差异'这种复杂问题,关键词召回率不够,必须用语义 + 多角度补充。简单问题用向量是浪费钱。"

---

## 3. QueryIntentClassifier — 意图分类

**位置**:`query_processor.py:62`

8 种意图,每种对应不同的 ES 字段加权:

```python
INTENT_FIELD_BOOSTS = {
    "factual":    {"title": 2.0, "content": 1.0},
    "procedural": {"title": 1.5, "content": 1.5},
    "list":       {"content": 1.5, "tags": 2.0},
    "definition": {"title": 2.5, "summary": 1.5},
    "comparison": {"content": 1.0, "tags": 1.5},  # 多个都要给权
    "causal":     {"content": 1.5, "context": 2.0},
    "summary":    {"summary": 2.0, "title": 1.0},
    "other":      {"content": 1.0},
}
```

**面试关键点**:不同意图匹配不同字段,不是所有问题都 `content` 一把梭。比如:
- "什么是 RAG" → 标题命中率高,title boost 2.5
- "列出所有文档" → 标签字段 boost 2.0
- "对比 A 和 B" → 多个字段都要给权

---

## 4. RetrievalStrategy — 三种策略

### 4.1 keyword_only(简单问题)
- **适用**:长度短、关键词明确
- **检索方式**:**仅 ES BM25 multi_match**
- **不带向量、不带 HyDE**
- **延迟**:**< 50ms**
- **成本**:**纯 BM25,不调 embedding,不调 LLM**

### 4.2 hybrid(中等问题,默认)
- **适用**:大多数日常查询
- **检索方式**:**关键词 + 向量,RRF 融合**
- **不带 HyDE,但带 query rewrite**
- **延迟**:**100-300ms**
- **成本**:embedding 一次 + 一次 LLM 改写

### 4.3 hybrid_hyde(复杂问题)
- **适用**:多实体、多跳推理、对比
- **检索方式**:**关键词 + 向量 + HyDE 伪文档 + 多查询改写**
- **改写**:生成 3-5 个 sub-queries,每个都检索
- **延迟**:**500ms-2s**
- **成本**:embedding 多次 + HyDE 一次 + LLM 改写

**面试话术**:

> "为什么不做'全部向量化'?因为 embedding 调用本身有成本和延迟。一个生产系统的检索,如果每个问题都走完整 pipeline,200 QPS 就要起 200 个 LLM worker。复杂问题可以接受,简单问题花这钱就是浪费。"

---

## 5. 关键词路径 _keyword_hits

**位置**:`backend/app/rag/retriever.py:45-125`

### 5.1 本地 Query Rewriting(`query_processor.py:127-159`)

无 LLM 改写,做三件事:
- **同义词扩展**:知识库→KB,机器学习→ML
- **停用词过滤**:去除"请问一下"、"这个那个"等无意义词
- **CJK bigram 切分**:中文按两个字切,提升 BM25 召回

### 5.2 LLM Query Rewriting(`query_processor.py:162-192`)

调 LLM 生成多个改写版本,**有时间预算**(避免慢查询):

```python
rewrites = await asyncio.wait_for(
    rewrite_query_llm(query, max_rewrites=4),
    timeout=2.0  # 2秒超时
)
# 返回: ["原始query", "改写1", "改写2", "改写3", "改写4"]
```

### 5.3 ES BM25 检索

```python
{
    "multi_match": {
        "query": rewritten_query,
        "fields": ["title^2.5", "content", "tags^2.0"],  # 字段加权
        "type": "best_fields",
        "tie_breaker": 0.3
    }
}
```

**面试关键点**:`tie_breaker=0.3` 表示多个字段都命中的 doc 比单一字段命中的 doc 分数略高,但不会 double-count。这是 ES multi_match 的最佳实践。

### 5.4 多版本合并

对每个改写版本都跑一遍 BM25,合并结果,取每个 doc 的**最高分**:

```python
merged = {}
for rew in rewrites:
    for hit in bm25_hits[rew]:
        doc_id = hit["_id"]
        if doc_id not in merged or hit["_score"] > merged[doc_id]["_score"]:
            merged[doc_id] = hit
```

---

## 6. 向量路径 _vector_hits

**位置**:`backend/app/rag/retriever.py:129-178`

### 6.1 Embedding 生成

```python
query_emb = await embedding_service.get_embedding(query)
# 2048 维,智谱 embedding-3
```

### 6.2 HyDE(Hypothetical Document Embeddings)

**位置**:`query_processor.py:242-271`

**核心思想**:让 LLM 先根据 query 生成一个"假设答案",用这个答案的 embedding 去检索文档,而不是用 query 本身。

```python
# 伪代码
hyde_doc = await llm.generate(f"假设回答这个问题: {query}")
hyde_emb = await embedding_service.get_embedding(hyde_doc)
# 让 LLM "假装回答",生成的文本跟真实答案语义更接近
```

**为什么有效**:
- 问题:"RAG 的核心问题是什么"
- Query embedding:跟"问题"相似的文档
- HyDE embedding:跟"答案"相似的文档
- 答案侧的语义空间**更密集**,更容易命中

**代价**:多一次 LLM 调用,**有 1.5s 超时**。

### 6.3 融合向量

```python
final_emb = 0.7 * query_emb + 0.3 * hyde_emb  # RAG_HYDE_WEIGHT=0.3
```

HyDE 不是完全替代 query 向量,**是加权融合**。权重可调。

### 6.4 ES script_score(cosineSimilarity)

```python
{
    "script_score": {
        "query": {"match_all": {}},
        "script": {
            "source": "cosineSimilarity(params.query_emb, 'embedding') + 1.0",
            "params": {"query_emb": final_emb.tolist()}
        },
        "min_score": 1.15  # 过滤低质量
    }
}
```

**面试关键点**:`+ 1.0` 是把 cosine 的 [-1, 1] 范围挪到 [0, 2],因为 ES script_score 默认越低越差。`min_score=1.15` 对应 cosine ≈ 0.15,**过滤掉语义无关的候选**。

---

## 7. RRF 融合 — Reciprocal Rank Fusion

**位置**:`retriever.py:182-218`

### 7.1 公式

```python
def rrf_score(rank, k=60):
    return 1 / (k + rank)
```

最终分数:

```
final_score(doc) = Σ 1 / (k + rank_in_list_i)
```

### 7.2 为什么用 RRF 不用加权融合?

**问题**:BM25 分数是 0-∞,cosine 分数是 -1 到 1,scale 完全不一样。

| 方案 | 优点 | 缺点 |
|---|---|---|
| **加权融合** | 调权重精细 | 需要归一化(信息损失),手工调权重(不通用) |
| **RRF** | 不用归一化,通用性强 | 不区分不同 list 的重要性 |

**面试话术**:

> "BM25 和 cosine 的 scale 不可比,强行加权不是归一化信息损失,就是调权重不通用。RRF 只用排名,无视 scale,**实战效果优于精细调权重的加权融合**。这是 1995 年 Cormack 提的方法,直到现在还被 Elasticsearch 的 RRF 官方实现。"

### 7.3 融合对象

可以融合任意多个列表:
- keyword_only 模式:1 个 list
- hybrid 模式:2 个 list(关键词 + 向量)
- hybrid_hyde 模式:3 个 list(关键词 + 向量 + HyDE 向量)
- multi-query 模式:每个子查询都检索,所有 list 都融合

---

## 8. Reranker — Cross-Encoder 重排

**位置**:`backend/app/rag/reranker.py`

### 8.1 三层降级

```python
async def rerank(query, candidates, top_n=10):
    # 1. 优先:本地 cross-encoder(BAAI/bge-reranker-base)
    try:
        return await local_rerank(query, candidates)
    except:
        pass
    
    # 2. fallback:智谱 rerank API
    try:
        return await zhipu_rerank(query, candidates)
    except:
        pass
    
    # 3. 再 fallback:LLM 重排
    try:
        return await llm_rerank(query, candidates)
    except:
        return candidates  # 不重排
```

### 8.2 为什么需要重排?

BM25 和向量都只看"相关性",但没有考虑:
- **事实性**:doc 里是否有真实答案
- **完整性**:doc 是否完整回答了问题
- **冗余**:多个候选是不是在说同一件事

Cross-encoder 是**两两打分模型**(`query, doc` → score),比 bi-encoder 慢但准得多。

### 8.3 部署约束

- 本地 cross-encoder 用 `sentence-transformers + torch`,有 `@lru_cache` 懒加载
- Executor-bound 推理,不阻塞 event loop
- 对中文支持好的 base model,bge-reranker-base

**面试话术**:

> "我们做了三层降级是因为 reranker 是非关键路径——挂了不能影响基础检索。Cross-encoder 准但慢,LLM 准但贵,智谱 API 准但要钱。三层兜底就是为了'有更好,没有也能用'。"

---

## 9. MMR 多样性 — Maximal Marginal Relevance

**位置**:`retriever.py:233-253`

### 9.1 问题

Top-10 全是同一份文档的不同切片,用户看不到其他来源。

```
Top-1: doc1#chunk3
Top-2: doc1#chunk7
Top-3: doc1#chunk12
Top-4: doc1#chunk18
...
```

### 9.2 公式

```python
def mmr_score(doc, query, selected_docs, lambda=0.65):
    relevance = cosine(query_emb, doc.emb)
    max_sim = max([cosine(doc.emb, sel.emb) for sel in selected_docs])
    return lambda * relevance - (1 - lambda) * max_sim
```

**`λ=0.65`**:**65% 相关性,35% 多样性**。

### 9.3 贪心流程

```python
selected = []
while len(selected) < top_k:
    best = max(candidates, key=lambda d: mmr_score(d, query, selected))
    selected.append(best)
    candidates.remove(best)
```

### 9.4 面试话术

> "MMR 是 trade-off 经典案例:完全按相关性排,Top-K 全是一家独大;完全按多样性,又答非所问。λ=0.65 是经验值,在 8 个 benchmark 上调出来的。"

---

## 10. 后处理 _post_process

**位置**:`retriever.py:296-385`

### 10.1 Snippet 构建

```python
def build_snippet(doc, query, max_chars=500):
    """在 doc 中找到 query 关键词的上下文,截取前后各一段"""
    pos = doc["content"].find(query)  # 简化
    start = max(0, pos - 200)
    end = min(len(doc["content"]), pos + 300)
    return doc["content"][start:end]
```

### 10.2 Quality Filter

```python
def quality_filter(candidates, min_score=0.3):
    return [c for c in candidates if c["rrf_score"] >= min_score]
```

`min_score=0.3` 是经过调参的经验值,过滤掉真正不相关的候选。

### 10.3 Freshness 加权

```python
upload_time = doc.get("upload_time")
days_old = (now - upload_time).days
freshness_boost = max(0, 1 - days_old / 365)  # 一年内线性衰减
final_score = base_score * (0.7 + 0.3 * freshness_boost)
```

新文档**有轻微加分**,老文档不直接降权——只用作微调。

### 10.4 同文档去重(每文档最多 2 个块)

```python
def per_doc_limit(candidates, limit=2):
    """防止单一文档垄断 Top-K"""
    counts = defaultdict(int)
    result = []
    for c in sorted(candidates, key=lambda x: -x["score"]):
        doc_id = c["document_id"]
        if counts[doc_id] >= limit:
            continue
        result.append(c)
        counts[doc_id] += 1
    return result
```

---

## 11. Query Rewriting 全景

### 11.1 本地无 LLM 改写

`_rewrite_query_candidates(query)`(`query_processor.py:127`):
- 同义词扩展
- 停用词过滤
- CJK bigram 切分

### 11.2 LLM 改写

`_rewrite_query_llm(query)`(`query_processor.py:162`):
- 调 LLM 生成多个改写版本
- 返回 `["原始query", "改写1", "改写2", ...]`
- **每个版本都检索,合并结果**

### 11.3 Query Decomposition

`_decompose_query(query)`(`query_processor.py:195`):
- 复杂问题分解成多个子查询
- 例:"对比 A 和 B 的差异" → `["A 的特征", "B 的特征", "差异分析"]`
- **每个子查询独立检索**

### 11.4 HyDE 伪文档

`_generate_hyde_doc(query)`(`query_processor.py:242`):
- 让 LLM "假装回答",生成 200-300 字的伪文档
- 用伪文档的 embedding 检索
- **让检索从"问题侧"切到"答案侧"**

### 11.5 Multi-HyDE

`_generate_multi_hyde_docs(query)`(`query_processor.py:274`):
- 生成多份角度不同的伪文档
- 例:同一问题既生成"技术角度"也生成"业务角度"
- 每份都检索,合并

---

## 12. RAGPipeline 编排层

**位置**:`backend/app/rag/pipeline.py`

### 12.1 search_knowledge_base 主流程

```python
async def search_knowledge_base(self, query, organization_id, top_k, document_ids):
    # 1. 精确缓存命中 → 直接返回
    cached = await self.exact_cache.get(query, org_id)
    if cached: return cached
    
    # 2. 语义缓存命中(向量相似度)→ 直接返回
    cached = await self.semantic_cache.get(query, org_id)
    if cached: return cached
    
    # 3. 复杂查询分解 + 多 sub-queries 检索
    sub_queries = decompose_query(query) if is_complex(query) else [query]
    all_hits = []
    for sub_q in sub_queries:
        hits = await self.retriever.retrieve(sub_q, top_k)
        all_hits.extend(hits)
    
    # 4. 重排 + 截断到 top_k
    reranked = await self.reranker.rerank(query, all_hits, top_n=top_k)
    
    # 5. 写缓存(精确 + 语义)
    await self.exact_cache.set(query, org_id, reranked)
    await self.semantic_cache.set(query, org_id, reranked)
    
    # 6. Prometheus 指标打点
    self.metrics.retrieval_total.labels(strategy="hybrid").inc()
    
    return reranked
```

### 12.2 重试机制

```python
for attempt in range(MAX_RETRIES):  # 默认 3 次
    try:
        return await self.retriever.retrieve(...)
    except (ESConnectionError, TimeoutError):
        if attempt < MAX_RETRIES - 1:
            await asyncio.sleep(0.5 * (2 ** attempt))  # 指数退避
        continue
```

### 12.3 PII 脱敏

`pipeline.py:263-379` `chat_stream` 中,**LLM 输出经 PII 脱敏后返回给用户**:

```python
masked_output = masking_service.mask_text(llm_output)
return unmask_text(masked_output)  # 反向恢复
```

如果 `ENABLE_PII_MASKING=true`,响应里所有邮箱、电话、身份证都会被自动 mask 掉。

---

## 13. 缓存策略

### 13.1 两层缓存

**位置**:`backend/app/rag/cache.py`

```python
# Layer 1:精确缓存
key = sha256(f"{query}:{org_id}".encode())
await redis.setex(f"rag:cache:{key}", ttl=600, value=json.dumps(result))

# Layer 2:语义缓存
query_emb = await embedding(query)
# 找 Redis 里与 query_emb cosine sim > 0.95 的已缓存 query
similar_results = await semantic_cache.search(query_emb, threshold=0.95)
```

### 13.2 TTL 和容量

```python
RAG_CACHE_TTL_SECONDS = 600    # 10分钟过期
RAG_CACHE_MAX_SIZE = 1000      # 最多缓存 1000 条
```

### 13.3 skip 缓存的条件

```python
if document_ids is not None:
    # 指定了 doc_id 列表 = 临时检索,不缓存
    skip_cache = True
```

---

## 14. ES 索引结构

**位置**:`backend/app/core/elasticsearch.py:121-159`

### 14.1 Mapping

```json
{
  "mappings": {
    "properties": {
      "content":       {"type": "text",  "analyzer": "ik_smart"},
      "chunk_text":    {"type": "text",  "analyzer": "ik_max_word"},
      "chunk_id":      {"type": "keyword"},
      "document_id":   {"type": "keyword"},
      "organization_id":{"type": "keyword"},
      "embedding":     {"type": "dense_vector",
                         "dims": 2048,
                         "index": true,
                         "similarity": "cosine"}
    }
  }
}
```

### 14.2 自适应分词器

```python
if IK analyzer available:
    analyzer = "ik_smart"  # 粗粒度,适合搜索
else:
    analyzer = "cjk"       # 降级到 CJK bigram
```

**面试关键点**:**生产用 IK 分词,没装就降级到 CJK**,而不是直接报错。这是工程韧性。

---

## 15. 指标埋点

**位置**:`pipeline.py` 各阶段有 Prometheus 埋点

```python
RAG_RETRIEVAL_TOTAL.labels(strategy="hybrid").inc()
RAG_CACHE_HITS.labels(layer="exact").inc()
RAG_PIPELINE_LATENCY.labels(stage="retrieve").observe(elapsed)
RAG_RERANK_LATENCY.observe(elapsed)
RAG_GROUNDED_TOTAL.labels(grounded=True).inc()  # 答案是否引证
```

**业务指标**:
- `RAG_RETRIEVAL_TOTAL{strategy}` — 各策略调用次数(看用户问题分布)
- `RAG_CACHE_HIT_RATIO` — 缓存命中率(成本核心指标)
- `RAG_P95_LATENCY` — 检索延迟

---

## 16. 完整代码索引

| 关注点 | 文件:行 |
|---|---|
| QueryComplexityClassifier | `backend/app/rag/query_processor.py:16` |
| QueryIntentClassifier | `query_processor.py:62` |
| Rewrite candidates | `query_processor.py:127` |
| LLM rewrite | `query_processor.py:162` |
| Query decomposition | `query_processor.py:195` |
| HyDE 生成 | `query_processor.py:242` |
| Multi-HyDE | `query_processor.py:274` |
| 关键词检索 | `retriever.py:45` |
| 向量检索 + HyDE 融合 | `retriever.py:129` |
| RRF 融合 | `retriever.py:182` |
| MMR 多样性 | `retriever.py:233` |
| 后处理 | `retriever.py:296` |
| Fallback 策略 | `retriever.py:389` |
| 自适应入口 | `retriever.py:417` |
| Cross-Encoder 重排 | `reranker.py:41` |
| Zhipu 重排 | `reranker.py:78` |
| LLM 重排 | `reranker.py:121` |
| Rerank 三层降级 | `reranker.py:167-212` |
| 编排入口 | `pipeline.py:42` |
| 检索主流程 | `pipeline.py:63-233` |
| 缓存层 | `cache.py` |
| ES 索引 mapping | `core/elasticsearch.py:121-159` |
| Prometheus 指标 | `pipeline.py` 各处埋点 |

---

## 17. 面试预设问题(10 题)

### Q1:"为什么不直接用向量检索,关键词检索不是过时了吗?"

> "关键词检索在精确匹配场景(人名、专业术语、代码片段)上比向量准得多,而且不调 embedding 模型、不调 LLM,**成本是向量检索的 1/10**。我们做混合检索是因为两种方法有不同 bias,融合后 recall 更高。RRF 融合只用排名不用原始分数,避免 scale 不一致问题。"

### Q2:"RRF 怎么融合,公式是啥?"

> "Reciprocal Rank Fusion,公式是 `score = Σ 1/(k + rank_i)`,k=60 是平滑常数。每个候选在多个检索列表中的排名都贡献分,只看排名不看原始分数,所以 BM25 和 cosine 可以无缝融合。"

### Q3:"HyDE 是什么,为什么要用?"

> "Hypothetical Document Embeddings,让 LLM 先根据 query 生成一个'假设答案',用这个答案的 embedding 去检索。理由是答案侧的语义空间更密集,比直接用问题检索召回率高。代价是多一次 LLM 调用,所以只在 complex 问题用。"

### Q4:"MMR 多样性怎么做的?"

> "Maximal Marginal Relevance,公式 `λ × relevance − (1−λ) × max_similarity_to_selected`,λ=0.65 控制相关性和多样性的权衡。解决的问题是 Top-K 全是同一文档的不同切片,用户看不到其他来源。"

### Q5:"怎么决定一个查询是 simple 还是 complex?"

> "规则判断:长度、实体数、是否多跳。短问题(< 10 字)走 keyword_only 省成本,中等问题走 hybrid,复杂问题(多实体对比)走 hybrid_hyde。不是所有问题都需要向量检索,成本敏感场景必须有 fallback。"

### Q6:"为什么用 RRF 不用加权融合?"

> "BM25 分数是 0-∞,cosine 分数是 -1 到 1,scale 不可比。加权融合要么归一化(信息损失),要么手工调权重(不通用)。RRF 只用排名,无视 scale,实战效果优于精细调权重的加权融合。"

### Q7:"怎么防止同一文档垄断 Top-K?"

> "后处理阶段做了两件事:1) 同文档去重,每文档最多 2 个块;2) MMR 多样性选择,惩罚与已选 doc 相似度高的候选。"

### Q8:"查询改写有什么用,不会引入噪声吗?"

> "查询改写解决'问法和文档写法不一致'的问题。比如用户问'怎么瘦腿',文档写'腿部减脂方法',关键词不匹配但语义匹配。LLM 改写会生成多个角度,合并去重能提升 recall。代价是调一次 LLM,所以放在 medium/complex 策略里。"

### Q9:"缓存怎么做的,会不会拿到过期结果?"

> "两层缓存:1) 精确缓存,Redis key=query hash,直接返回;2) 语义缓存,query embedding 与已缓存的 query embedding 做 cosine 相似度,超过阈值就复用。语义缓存有 TTL(默认 600s)和最大容量(1000),过期自动淘汰。"

### Q10:"如果用户问了一个完全没在文档里的问题,系统怎么反应?"

> "检索层会返回空结果或者低分候选。后处理阶段会做 quality filter,空结果直接告诉用户'没找到相关文档',不给 LLM 编造的机会。这就是 LLM grounding 的兜底——比让 LLM 自由发挥更可信。"

---

## 18. 反模式提醒

下面这些坑面试时如果被问到,主动说出来会加分:

| 反模式 | 我们怎么避 |
|---|---|
| 全部走向量 | 复杂度自适应,简单问题只走 BM25 |
| 把 BM25 和 cosine 加权融合 | RRF 融合,只看排名 |
| 让 LLM 自由发挥 | Quality gate + grounding 兜底 |
| 忽视多样性 | MMR + 同文档去重 |
| 不做缓存 | 两层缓存(精确 + 语义) |
| 检索失败就崩 | 三层重试 + 兜底空结果 |
| 一个 query 改写出 N 倍成本 | LLM 改写有 timeout,只在 complex 模式 |
| 重排挂了就崩 | Cross-encoder → Zhipu → LLM → no-op 四层降级 |

---

## 19. 选型 vs 实现的回答套路

面试时的标准回答套路(由这 19 节可选原料组合):

```
一句话定位                  (第 0 节)
  + 三档分类 + Adaptive      (第 2 节)
  + RRF 融合                 (第 7 节)
  + HyDE 应对语义 gap        (第 6.2 节)
  + MMR 应对多样性           (第 9 节)
  + 重排三降级                (第 8 节)
  + 两层缓存                  (第 13 节)
  + Quality gate 兜底         (第 16 节链接文档)
```

调参经验(可选加分):
- RRF k=60 是经验值
- MMR λ=0.65 来自 8 个 benchmark 调参
- `min_score=1.15` 对应 cosine ≈ 0.15
- 双缓存阈值 0.95(语义)

讲出来这套,**和"我做了 RAG,用了 embedding 检索"完全不是一个级别**。
