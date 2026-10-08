# DocMind RAG 检索链路审计

> 审计基线：本仓库（`sijie-Z/DocMind-RAG`）的 `develop` 分支 @ `e5b2eb1`。
> 只读：本次审计未创建、修改、删除任何文件。
> 审计人：Explore 子 agent（只读），由监督方落盘。落盘时间：2026-10-06。

---

## 0. 首要结论：题面给的调试面板字段，在本仓库里**不存在**

对整棵树做排除 `node_modules/.venv-win/.git/venv` 的全量检索，关键词包括
`自动合并 / auto_merge / auto-merging / 叶子召回 / 召回层级 / 合并阈值 / 替换片段 / 评分决策 / 是否需要重写 / 候选召回数 / hierarch / parent_chunk / child_chunk / leaf / RAPTOR`：

- **零命中**。
- `git log --all --grep="auto.merg|hierarch|leaf|层级|raptor"` 在**全部 378 个 commit** 上只返回 `8abd4ba chore(frontend): vue-router 4 -> 5`（无关）。
- `jina-reranker-v3` 在本仓库**不存在**。整树 `jina` 的命中全部落在 `backend/venv/Lib/site-packages/elasticsearch/**`（第三方 SDK docstring）。

**真实存在的调试面板**（这才是本仓库返回的 debug 载荷）：

| 面板真实分节 | 渲染位置 | 数据来源 |
|---|---|---|
| 🔍 检索调试面板 标题栏（`elapsed_ms` / `strategy` / `缓存命中`） | `frontend/src/views/chat/components/DebugPanel.vue:4-13` | 后端 `debug_info` |
| 子查询: `<sq>` 分组 | `DebugPanel.vue:32-38` | `debug_info["stages"]` |
| 📄 最终 LLM 上下文（含 `score:` 数值） | `DebugStage.vue:4-40` | `chat_service.py:242-251` |
| 🔤 BM25 关键词检索 | `DebugStage.vue:43` | `retriever.py:467 / 487` |
| 🧠 向量相似度检索 | `DebugStage.vue:46` | 同上 |
| 🔀 RRF 融合结果（k=60） | `DebugStage.vue:49-53` | `retriever.py:493` |
| 📊 Cross-Encoder 重排结果 | `DebugStage.vue:56` | `pipeline.py:227-235` |

前端类型定义（权威字段清单）：`frontend/src/types/chat.ts:59-91`（`RetrievalDebugData` / `DebugHit` / `FinalContextItem`）。

---

## 1. 检索链路端到端地图

| # | 阶段 | `文件:行号` | 做什么 |
|---|---|---|---|
| 1 | 上传 / 入队 | `api/v1/endpoints/knowledge.py`、`services/file_service.py:44` | `CHUNK_SIZE=5MB` 是**文件字节分块**，不是文本分块 |
| 2 | 解析 | `services/document_parser.py:138-205`（PDF）/ `:207-283`（DOCX）/ `:303`（XLSX）/ `:351`（TXT） | pdfplumber 逐页抽文本 + 表格转 md + OCR 兜底；页信息进 `metadata["pages"]`（`:191-195`） |
| 3 | **切分** | `document_parser.py:415-447` → `services/semantic_chunker.py:164-207` | 条款感知分区 → 每区语义切分 → 尾块合并 |
| 4 | 上下文增强 | `document_parser.py:418 / 430-434` | LLM 生成 50-100 字文档摘要，**前缀拼到每个 chunk 正文**（`【文档背景：…】`） |
| 5 | 落库 + 建 ES 索引 | `worker/doc_processor.py:163-233` | MySQL `document_chunks` + ES `_source`（`:188-217`） |
| 6 | 查询分类 | `rag/query_processor.py:16-59` | `simple→keyword_only` / `medium→hybrid` / `complex→hybrid_hyde` |
| 7 | 精确缓存 | `rag/cache.py:79-127` | key = `org\|topk\|query` |
| 8 | 语义缓存 | `rag/cache.py:144-243`（阈值 `:162` = 0.92） | Redis ZSet + 余弦；命中直接返回旧 sources |
| 9 | 查询分解（仅 complex） | `rag/pipeline.py:146-170` → `query_processor.py:195-239` | LLM 拆 2-4 个子问题 |
| 10 | 查询改写 / 扩展 | `retriever.py:48-64` → `query_processor.py:127-159`（本地）+ `:162-192`（LLM） | **只喂给关键词臂** |
| 11 | 关键词召回（BM25/ES） | `retriever.py:46-126` | 每路 rewrite 各发一次 `multi_match + wildcard` |
| 12 | 向量召回 | `retriever.py:130-179` | `script_score = cosineSimilarity+1.0`，`min_score=1.15` |
| 13 | HyDE | `retriever.py:138-159` → `query_processor.py:242-271` | 仅 `hybrid_hyde`，权重 `RAG_HYDE_WEIGHT=0.3` |
| 14 | **RRF 融合** | `retriever.py:183-219` | `k=60`，**无权重，纯名次** |
| 15 | MMR 去冗 | `retriever.py:234-254`，调用点 `:504-510` | λ=`RAG_MMR_LAMBDA=0.65`；**会替换掉整个 fused 列表** |
| 16 | 后处理/打分/过滤 | `retriever.py:297-399` | 归一化分数、freshness、overlap 过滤、**每文档最多 2 块** |
| 17 | 多子查询合并 | `pipeline.py:205-220` | 按 `chunk_id` 去重 |
| 18 | **Rerank** | `pipeline.py:225` → `rag/reranker.py:188-233` | 本地 CE → 智谱 API → LLM → 原序 |
| 19 | 截断 top_k | `pipeline.py:241` | `top_k=5`（聊天）/ 8（绑文档） |
| 20 | 写缓存 | `pipeline.py:243-249` | |
| 21 | debug 载荷 | `pipeline.py:261-263`、`chat_service.py:242-255`、`chat.py:816-819` | SSE `event: debug` |
| 22 | 组 prompt | `pipeline.py:330-333 / 337-348 / 356-359` → `context_compressor.py:39-58` | **每源最多 260 字**（见缺陷 #2） |
| 23 | 生成 | `pipeline.py:375-424` | `build_rag_messages` 走 token 预算（`context_window.py:187-220`） |
| 24 | 引用/落库 | `chat_service.py:306-328` | 按 `[n]` 回标 `cited` |

---

## 2. 调试面板字段 → 代码位置对照表

| 面板字段 | 本仓库对应 | 位置 |
|---|---|---|
| 检索阶段 | **未找到**。最接近：Agent 侧 `tracer.start_phase("retrieval")` | `agent_api.py:263 / 282`、`core/trace_logger.py:104-107` |
| **相关性评分** | 存在但语义不同 —— 是**批内 min-max 归一化分**，不是相关性判定 | `retriever.py:303-304, 341-361`；随源返回 `chat_service.py:49-62`（键名 `relevanceScore`） |
| 评分决策 (yes/no / generate_answer) | **未找到**。全链路无任何"评分→决策"分支；`chat_service.py:227-296` 无条件进入生成 | — |
| 是否需要重写 | **未找到**。改写无条件执行，无判定 | `retriever.py:47-64` |
| 检索模式 hybrid | `debug_info["strategy"]`，取值 `keyword_only/hybrid/hybrid_hyde` | `query_processor.py:52-59`；`retriever.py:462`；`pipeline.py:192`；`DebugPanel.vue:7-9` |
| 候选召回数 15 | **不是真实候选数**。真实 `candidate_size = max(top_k*4, 12)` = **80/臂**；15 是展示截断 | `retriever.py:455`；截断 `:493` + `:518` |
| 叶子召回层级 (L3) | **未找到** | — |
| Auto-merging（阈值 2 / 替换片段 4 / 轮次 1） | **未找到**。切分侧只有一个无关的 `_merge_small_tails`（阈值 **200 字符**，不是 2） | `semantic_chunker.py:307-319`，调用 `:207` |
| Rerank 已配置 | `RAG_ENABLE_RERANKER` + 客户端构造 | `config/ai.py:72`、`dependencies.py:68-79` |
| Rerank 已执行 | **无此信号**。面板只展示重排后的列表，无法区分"真排了"与"静默原序返回" | `pipeline.py:225-240`；静默降级 `reranker.py:216-231` |
| Rerank 模型 jina-reranker-v3 | **未找到**。默认 `BAAI/bge-reranker-base`；`.env` 实际 `RERANK_USE_LOCAL=false` + `RERANK_MODEL=rerank`；且该字段被硬编码覆盖 | 默认 `config/ai.py:77-78`；`backend/.env:52-55`；**硬编码 `"model": "rerank"` `reranker.py:117`** |
| 扩展查询 | 存在但**从不展示改写文本**。只有 `rewrite_hits` 计数与 `rewrite×N` 徽标 | `query_processor.py:127-192`；消费 `retriever.py:48-64, 103`；计数 `:202, 528` |
| 初次检索结果（含 RRF 名次） | `stages[<子查询>].rrf_result`，每项含 `keyword_rank/vector_rank/score` | `retriever.py:467-493`；`pipeline.py:191-192`；`DebugPanel.vue:32-38` + `DebugStage.vue:49-53` |

---

## 3. 根因专节：为什么「问第七条、召回第十二条」还判定为相关

按证据强度排序，**A/B/C 三条足以独立造成该现象**。

### A. 条号从未作为结构化条件参与检索（根本缺陷）

1. 切分阶段**确实识别了条标题**：`CLAUSE_PATTERNS` 第一条即 `^第[一二三四五六七八九十百\d]+[条章节款]`（`semantic_chunker.py:13-22`），`find_clause_boundaries`（`:112-122`）以它为分区边界，`detect_section_title`（`:29-39`）取标题写入 `metadata.section_title`（`:186, :201, :444`）。
2. **但这个字段到不了检索期**，三重断链：
   - 入库只写 `metadata.section_title`（`doc_processor.py:212-213`），而 ES mapping 把 `metadata` 声明为 `{"type": "object", "enabled": False}`（`elasticsearch.py:156`）→ **存而不索引**，不可检索不可过滤。
   - mapping 里**确实有**顶层 `section_title: {"type": "text"}`（`elasticsearch.py:140`），但**没有任何写入方填充它**（`doc_processor.py:192-215` 的 `_source` 里没有该键）→ **死字段**。连带 `QueryIntentClassifier.get_boost_fields`（`query_processor.py:87-99`）返回的 `section_title^1.8` / `definition^2.5` / `list_content^2` / `title^1.5` 里，**只有 `content`/`chunk_text`/`filename` 真实存在**，其余全是空字段。
   - 检索期 `_source` 白名单（`retriever.py:98, 176, 408, 421`）不含 `metadata`/`section_title` → 应用层**根本看不到条号**。
3. 过滤条件只有组织与文档：`retriever.py:448-453`。**全链路没有任何地方把「第七条」解析为条号并作为 filter/boost 使用。**

→ **「第七条」在检索里退化成两个普通 bigram（`第七`、`七条`），与法律名的 8 个 bigram 同权竞争。**

### B. 打分函数的判据是「主题相关」，而且榜首分数被构造性地锁定在 1.0

- 重叠度：`overlap = _compute_overlap(f"{filename} {text[:1600]}", query_terms)`（`retriever.py:327`），`query_terms` 是 CJK bigram（`query_processor.py:102-124`）。
  对 `《消费者权益保护法》第七条是什么`，terms ≈ `消费/费者/者权/权益/益保/保护/护法/第七/七条/条是/是什`——**11 个里 7 个是法律名 bigram，出现在该法每一个 chunk 里**。一个「第十二条」chunk 的 overlap 仍在 0.6+，过滤线（`:338` 的 `0.02`、`:391-395` 的 `0.15/0.10/0.07`）形同虚设。
  真正区分的 `第七/七条` 只占 2/11，**权重被稀释到无法翻盘**。
- 归一化分：`normalized = 0.1 + 0.9*(score - score_min)/(score_max - score_min)`（`retriever.py:341-351`），再乘 `fresh_factor∈[0.75,1]`、`modality∈{1.0,1.03,1.1}`、`overlap_factor∈[0.9,1.1]`（`:353-361`）。
  **榜首 = score_max → normalized ≡ 1.0 → final_score ∈ [0.675, 1.0]，绝大多数情况就是 1.0。**
  面板里那行绿色的 `score: 1.0`（`DebugStage.vue:30-35`）和来源里的「相关度: 100%」，**是批内相对排名的产物，不含任何绝对相关性信息**。这就是"相关性评分 = yes"的机械来源。

### C. 每文档只保留 2 个 chunk，第七条在重排之前就被丢掉

```python
doc_id = str(source.get("document_id") or "")
seen_docs[doc_id] = seen_docs.get(doc_id, 0) + 1
if doc_id and seen_docs[doc_id] > 2:
    continue
```
`retriever.py:321-324`。

单文档语料（问一部法律）时：RRF 前两名之外的 chunk **一律被丢弃**，且这个过滤发生在 `rerank()` 之前（`pipeline.py:225`）。**只要第十二/十三条的两个 chunk 在 RRF 里排在第七条之前，第七条就被物理删除，重排器连见都见不到。**

### D. 融合与召回侧放大（推波助澜）

- **RRF 纯名次、无权重**：`row["_score"] += 1.0/(k+rank)`，`k=60`（`retriever.py:184-219`）。1/61 与 1/63 的差距不足以对抗关键词臂的排序噪声。
- **wildcard 子句对中文恒为空转**：
  ```python
  wildcard_terms = [f"*{t}*" for t in q_text.split() if len(t) < 20]
  ```
  `retriever.py:72`。中文查询没有空格 → `split()` 返回**一个** token = 整句 → 退化成 `*《消费者权益保护法》第七条内容*`，**永远匹配不到**。第二个 `should` 分支（`:80-88`）对中文查询是死代码。
- **扩展查询只进关键词臂**：LLM 改写结果仅被 `_keyword_hits` 使用（`retriever.py:48-64`，`tasks = [_es_search(rq) …]` `:103`）；向量臂只 embed 原始 query（`retriever.py:130-134`）。**「扩展查询」对向量召回零作用。**
- **MMR 替换掉整段 fused**：`:504-510` 用 MMR 结果替换 `fused`，只保留有 `_source.embedding` 的候选；embedding 缺失/为空时候选被静默剔除。

### E. 重排器不掌握条号，且失败静默

- 输入拼装：`pairs.append([query, passage])`，`passage = _hit_text(h)[:512]`（`reranker.py:73-76`）。**没有文件名、没有 section_title、没有条号，且正文截到 512 字。**
- 输出只取索引，**丢弃相关性分**：`idx = res.get("index")`（`reranker.py:127-129`）——智谱返回的 `relevance_score` 被扔掉，因此**不可能做分数阈值**。
- 失败路径：本地 CE 返回 `None` → 智谱 `None` → LLM `None` → `return hits`（原序）（`reranker.py:216-227`）。`_get_cross_encoder` 连 `ImportError` 都吞（`:59-61`）。
- `.env` 里 `RERANK_USE_LOCAL=false`（`backend/.env:52`），所以**实际生效的是智谱 API 分支**，本地分支被跳过（`reranker.py:211-213`）。
- `_rerank_zhipu` 请求体里 `"model": "rerank"` 是**字面量**（`reranker.py:117`），完全忽略 `settings.RERANK_MODEL` / `RAG_RERANK_MODEL`。**面板上的 `jina-reranker-v3` 无法由本代码产生。**

### F. 生成阶段用 260 字摘要，且只要求引「资料[n]」

- 每源进 prompt 的正文 = `(item.get('snippet') or item.get('text', ''))[:3000]`（`pipeline.py:356-359`），而 `snippet` 来自 `_build_snippet(text, query)`（`retriever.py:326`），`max_len=260`（`:257-267`）。
  且 `_build_snippet` 找不到 query 字面量时返回 **`text[:260]`（chunk 前 260 字）**（`:264-265`）。
- 副作用：`compress_context_list` 只覆盖 `ctx_copy["text"]`（`context_compressor.py:52`），prompt 却优先读 `snippet` → **`compress()` 在 prompt 路径上实际是死代码**。
- 引用要求只到「资料下标」层级：system prompt 要求 `[n]` 格式（`pipeline.py:384`），**没有任何"必须给出条号/条款出处"的要求，也没有"检索不相关则拒答"的判据**——唯一的相关性闸门是**「上下文列表是否为空」**（`pipeline.py:375` 的 `has_context = bool(compressed)`；空则走 `:391-401` 通用知识兜底）。`chat_service.py:258` 的 `report_grounded(bool(sources_list))` 只累加 Prometheus 计数，不影响分支。
- 可选拒答只存在于严格模式 `STRICT_MODE_PROMPT`（`chat_service.py:27-35`），需请求显式开启。

### G. 第七条的正文可能在切分阶段就被并进别的条

- `_merge_small_tails`（`semantic_chunker.py:307-319`）把 **<200 字符**的块并入**前一块**，`all_chunks` 是跨分区的扁平列表（`:195-207`）→ **合并会跨条边界**。
- 更糟：`:315-316` 只在前块**没有** `section_title` 时才继承，因此**被并进来的第七条正文，留在了一个标题为「第六条 …」的 chunk 里**。
- `detect_section_title` 的长度护栏是空操作：
  ```python
  title = stripped[:80].strip()
  if len(title) <= 80:      # 恒为真
      return title
  ```
  `semantic_chunker.py:36-38`——注释写"不超过 50"，实现是恒真。**并且它切的是整行前 80 字**：若 PDF 把「第七条」和正文排在同一行，所谓 section_title 就是条文正文，而不是标题。
- 另有一条结构性风险：`find_clause_boundaries` 用 `re.finditer(r'^', text, re.MULTILINE)`（`:115`）——**要求每条必须从行首开始**。若某份 PDF 抽取后条号不在行首，则 `boundaries == [0]`，整部法律成为**单一分区**，之后交给 p90 阈值的语义切分 + 1200 字合并（`:263-305`），**切点完全由 embedding 距离决定，条边界彻底失守**。

---

## 4. 缺陷 / 风险清单（按严重度）

### P0

**#1 榜首"相关性评分"被构造性地锁死在 ~1.0**
- 现象：面板/来源里 rank-1 恒显示 100%，用户据此认为"检索对了"
- 位置：`retriever.py:341-361`（归一化）；消费 `chat_service.py:245-251`、`DebugStage.vue:30-35`、`ChatMessages.vue:121-122`
- 为什么是问题：`normalized` 是**批内 min-max**，`score_max` 的那一条恒等于 1.0。它衡量"在这批里排第一"，与"是否回答了第七条"无关
- 验证：取同一次 retrieval 的 `fused_hits`，对任何一条命中造两种情况（真相关 / 完全不相关），观察榜首 `final_score` 都是 1.0
- 修法：改用绝对信号——ES `_score`、cosine、rerank 的 `relevance_score`；把"相关性评分"与"排序位置"分成两个字段；低于绝对阈值时置 0 并触发拒答

**#2 送进 LLM 的每源正文只有 260 字，且常常是 chunk 头部**
- 位置：`retriever.py:257-267`（`max_len=260`）、`:326`；`pipeline.py:356-359`
- 为什么是问题：`_build_snippet` 找不到 query 字面量时直接 `text[:260]`。若 chunk 是 `_merge_small_tails` 拼出来的（跨条），第七条的内容在尾部，**永远进不了 prompt**
- 验证：对任一 length>260 的 chunk，用一个不在其中的 query 调用 `_build_snippet`，断言返回 `text[:260]`
- 修法：prompt 用 `text`/`content`（全文，交由 `build_rag_messages` 的 token 预算裁剪），或把 `compress()` 真正接进 prompt 路径

**#3 条号存在但全程不可用（三重断链）**
- 位置：`elasticsearch.py:156`（`metadata.enabled=false`）、`:140`（`section_title` 无人写）、`doc_processor.py:192-215`、`retriever.py:98/176/408/421`（`_source` 白名单）
- 验证：`GET docmind_knowledge/_mapping` 确认 `metadata` 未索引；`GET /_search` 取任一 `_source`，确认无 `section_title`
- 修法：把 `section_title` / `article_no` 提到 `_source` 顶层并 `keyword` 索引；切分时用正则抽出纯条号（`第七条` → `article_no=7`）单独落字段；检索时命中「第N条」就加 `term` filter 或高 boost 的 `should`

**#4 每文档最多 2 个 chunk**
- 位置：`retriever.py:321-324`
- 为什么是问题：单文档语料下前两名之外全灭，且发生在 rerank **之前**（`pipeline.py:225`）
- 验证：单文档检索，构造第 3 名命中的 chunk，断言它不在返回列表里
- 修法：改成软惩罚（分数打折）而非硬丢弃，或把上限与 `top_k` 挂钩；至少挪到 rerank 之后

### P1

**#5 Rerank 失败完全静默，"已执行"不可证伪**
- 位置：`reranker.py:216-227`（None 链 → `return hits`）、`:59-61`（吞异常）、`pipeline.py:225-240`（debug 只记结果不记路径）
- 验证：把 `RERANK_API_URL` 指向不可达地址，观察请求仍返回 200 且顺序不变
- 修法：返回 `(hits, strategy_used, ok)`；把 `rerank_strategy` / `rerank_ok` 放进 `debug_info`；失败时打 WARN 计数器

**#6 `RERANK_MODEL` 被硬编码覆盖**
- 位置：`reranker.py:117` `json={"model": "rerank", ...}`；配置 `config/ai.py:83/86`；`.env:55`
- 验证：`.env` 改 `RERANK_MODEL=jina-reranker-v3` 后抓包，请求体仍是 `"rerank"`
- 修法：`settings.RERANK_MODEL or settings.RAG_RERANK_MODEL or "rerank"`

**#7 rerank 相关性分被丢弃**
- 位置：`reranker.py:124-135`（只用 `index`）
- 为什么是问题：即便重排正确，也没有任何绝对分数可供下游做拒答判定；面板也显示不出重排分（`pipeline.py:231` 显示的是**旧的 `score`**，不是 rerank 分）
- 修法：保留并透传 `relevance_score`，写入 hit 的 `rerank_score`，面板展示它

**#8 重排输入不含出处信息**
- 位置：`reranker.py:73-76`（`[query, text[:512]]`）、`:108-111`（智谱同）
- 为什么是问题：模型无法利用"第X条"这一强信号；512 字截断还可能切掉关键句
- 修法：拼接 `f"{filename} {section_title}\n{text}"`；条号作为前缀；把截断放宽

**#9 跨条合并 + 标题继承错误**
- 位置：`semantic_chunker.py:307-319`（尤其 `:315-316`）、`:36-38`（恒真护栏）、`:115`（要求条号在行首）
- 验证：构造 `第一条…（250字）` + `第二条…（80字）`，断言结果块数是 1 且 `section_title` 仍是「第一条」
- 修法：`_merge_small_tails` 增加"同 section_title 才合并"的判据；修正长度护栏为 `<= 50`；`find_clause_boundaries` 增加不依赖行首的兜底

**#10 没有任何"检索不合格→拒答"的闸门**
- 位置：`chat_service.py:227-296`（无条件生成）、`pipeline.py:375`（唯一判据是列表空否）、`chat_service.py:258`（只计数）
- 修法：在 `search_knowledge_base` 之后加绝对阈值门（rerank 分或 cosine），不达标走拒答模板；把 `report_grounded` 的结果真正接到分支上

### P2

**#11 中文 wildcard 子句恒空转** — `retriever.py:72, 80-88`。`q_text.split()` 对无空格中文返回整句。

**#12 向量臂拿不到扩展查询** — `retriever.py:103` vs `:130-134`。

**#13 MMR 静默替换 fused** — `retriever.py:504-510`；embedding 缺失时候选被无声剔除（`get_embedding` 出错返回 `[]`，`retriever.py:31-42`）。

**#14 boost 字段大半不存在** — `query_processor.py:87-99` vs mapping `elasticsearch.py:126-157`（无 `title/list_content/definition/comparison/pros_cons/reason/result/summary/conclusion`）。

**#15 `_get_cross_encoder` 的 `@lru_cache` 只缓存一个模型** — `reranker.py:43-44`；改配置需重启才生效。

**#16 维度不符会删除整个索引** — `elasticsearch.py:179-192` → `recreate_knowledge_index()` `:203-220`；调用点在**每次文档处理**都会走到（`doc_processor.py:226`）。`VECTOR_DIMENSION` 一改（`.env:47` 现为 2048）→ **全量数据被删且无二次确认**。

**#17 `page_number` 恒为 None** — `document_parser.py:415-447` 的 `metadata` 形参从未被使用，页面信息停在 `metadata["pages"]`（`:191-195`）；`document_parser.py:500`、`doc_processor.py:183/212` 读到的永远是 `None`；ES mapping 里也**没有** `page_number` 字段（`elasticsearch.py:126-157`）。
→ **本管线不产出页码。"第 3 页"不可能来自这套元数据。**

**#18 `chunkIndex` 前端永远 undefined** — `build_sources_list`（`chat_service.py:49-62`）不返回该键，而类型里声明了（`types/chat.ts:42`）。

**#19 DebugPanel 的 `stages` 分支可能整块不渲染** — `debug_info["stages"]` 初始为 `{}`（`pipeline.py:95`），空对象在前端 `v-if` 为假（`DebugPanel.vue:32`）→ 走 `v-else` 的顶层 `DebugStage`，而 `keyword_top/vector_top/rrf_result` 只写在 `stages[子查询]` 里（`pipeline.py:191-192`）→ **分节全空**。

**#20 RRF 分区配色失真** — `DebugSection.vue:61-66` 的阈值是 2.0/1.0/0.3，但 RRF 分值上限 ≈ 2/61 ≈ 0.033 → **RRF 行永远渲染成"灰色=低分"**。

**#21 语义缓存阈值 0.92 可能跨问题命中** — `cache.py:162, 233`。"第七条" 与 "第十三条" 的 query embedding 余弦有越过 0.92 的可能，命中后会**直接返回另一问的 sources**。命中时 `debug_info.cache_hit=True`（`pipeline.py:140-143`），可据此排除/确认。

**#22 两套 embedding 客户端** — 索引期 `embedding_service`（`embedding_service.py:12-38`，导入即建，无 key 直接抛）vs 检索期 `_build_embedding_client`（`dependencies.py:52-65`，key 回退到 `DEEPSEEK_API_KEY`）。配置漂移会导致索引与查询不同模型/维度。两者都不传 `dimensions`（`retriever.py:35-38`、`embedding_service.py:48-51`），维度纯靠服务端默认值。

---

## 5. 读过的文件清单

**后端（逐行读过）**
- `app/rag/retriever.py`（534 行，全文）
- `app/rag/pipeline.py`（470 行，全文）
- `app/rag/reranker.py`（234 行，全文）
- `app/rag/query_processor.py`（311 行，全文）
- `app/rag/cache.py`（294 行，全文）
- `app/rag/context_compressor.py`（59 行，全文）
- `app/rag/context_window.py`（221 行，全文）
- `app/rag/evaluator.py`（229 行，全文）
- `app/services/semantic_chunker.py`（323 行，全文）
- `app/services/chat_service.py`（359 行，全文）
- `app/services/embedding_service.py`（102 行，全文）
- `app/services/knowledge_service.py`（246 行，全文）
- `app/services/rag_service.py`（全文）
- `app/services/document_parser.py`（120-283、390-519 段 + 函数清单）
- `app/worker/doc_processor.py`（120-239 段）
- `app/core/elasticsearch.py`（100-341 段）
- `app/core/config/ai.py`（118 行，全文）
- `app/dependencies.py`（107 行，全文）
- `app/api/v1/endpoints/chat.py`（28-80、795-860 段 + grep）
- `app/api/v1/endpoints/knowledge.py`（128-198 段 + 端点清单）
- `app/agent/core_tools.py`（15-105 段）
- `tests/unit/test_semantic_chunker.py`（226 行，全文）
- `tests/behavior/test_rag_behavior.py`（1-80 段）
- `backend/.env`、`.env.docker`、`.env.docker.example`（去密钥）

**前端**
- `frontend/src/views/chat/components/DebugPanel.vue`（全文）
- `DebugStage.vue`（70 行，全文）
- `DebugSection.vue`（1-80 段）
- `ChatMessages.vue`（95-140 段）
- `frontend/src/types/chat.ts`（1-91 段）

**元信息**：`git remote -v`、`git branch -a`、`git log --all`、`git ls-remote`、本轮的执行台账（未入仓）

---

## 6. 未读 / 未核实的部分

1. **题面调试面板的真实出处未核实。** 该字段集在本仓库（含全历史、两个 remote、`_wt/`、`1_demo/`）**完全不存在**。可能来源：另一套系统 / 未 push 的本地改动 / 与本仓库无关的产品。我只确认了"本仓库没有"。
2. **实际运行数据未核实**：没有运行后端，没有查 `docmind_knowledge` 索引的真实 mapping、没有查 MySQL 里《消费者权益保护法》的 `document_chunks` 行、也没有那次 trace 的 `debug_info` JSON。第 3 节 A–G 是**代码级可推导机制**，不是对该次失败的事后取证。
3. **未核实 ES analyzer**：`_holder.es_analyzer` / `es_search_analyzer` 的定义在 `app/core/elasticsearch.py` 的 1-99 行，**没有读**。因此 `第七`/`七条` 到底被切成什么 token、`minimum_should_match: 30%` 的真实效果，未能确证——这是唯一一处无法完全闭环的环节。
4. **未核实的调用路径**：Agent 路径（`agent/service.py:127`、`agent/core_tools.py:50-74`）用的是同一个 `search_knowledge_base`，但**没有逐行走 Agent 的生成侧**；`graph_rag_service.py` 只做了函数清单级浏览。
5. **`_semantic_split` 的实际切点未实测**：第 3 节 G 里"整部法律成为单一分区"的分支成立与否，取决于具体 PDF 的抽取结果。
6. **未核实的配置**：`SEMANTIC_CACHE_THRESHOLD`、`TOP_K_RESULTS`、`RAG_MMR_*` 是否被 `.env` 覆盖。**（追注，#242 终单 2026-10-08：已闭 —— `TOP_K_RESULTS` 现接线为 `/search` 的 `top_k` 默认值（此前零消费）；`RAG_MMR_*` 经 settings 读取（`retriever.py:460/515`），`.env` 生效；`SEMANTIC_CACHE_THRESHOLD` 在 Settings 无对应字段（`extra="ignore"`，`.env` 配了也无效），属反向缺口、另行立档。）**
7. **未读**：`app/api/v1/endpoints/monitoring.py:756-810`（RAG 评估接线）、`app/agent/quality_gate.py:71`、`deploy/k8s/*`、`docker-compose*.yml` 全文、`backend/benchmark/**`。

---

## 7. 监督方补充：第 6 节第 2 条的缺口已由活体探测补上

A5 未能核实的"实际运行数据"，已由监督方在**运行中的容器**上实测（见 `008_rag-never-indexed.md`）：

| A5 想核实但没核实的 | 活体实测结果 |
|---|---|
| `docmind_knowledge` 的真实 mapping | **确认**：`"metadata":{"type":"object","enabled":false}` 属实（索引名实为 `paicongming_knowledge`） |
| ES 索引里有没有数据 | **0 篇** —— 索引是空的 |
| MySQL chunk 行 | 39 条，**39 条 `embedding_id` 为 NULL** |
| 检索实际返回什么 | **5 个查询全部 0 条**，HTTP 200，`search_time: null` |

→ **A5 推断的机制（P0 #1–#4）全部成立，但更严重的现实是：检索链路当前连一个字节的数据都没有。**
→ 同时**印证了 A5 §0 的结论**：截图面板不来自本仓库（`page_number` 恒为 None，本管线根本不产出页码，
而截图里写着「第 3 页」）。
