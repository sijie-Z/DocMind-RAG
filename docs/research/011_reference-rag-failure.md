# RAG 检索失败诊断 —— 问《消费者权益保护法》「第七条」，为什么召回的却是「第十二/十三条」？

> **任务**：诊断一个具体检索失败并设计检索评估方案。
> **日期**：2026-10-06。**证据分级**：`【实测·面板】`=用户截图逐字转录；`【实测·代码】`=本次对系统源码的实读（附 `file:line`）；`【业界·本次核验】`=本会话实际抓到页面/源码/论文元数据（附抓取方式）；`【业界·未核验】`=凭已知文献给出 URL 但本次未能联网核验；`【推测】`=本报告推断（每条附可独立执行的验证实验）。
> **工件**：系统全仓库快照 本地证据副本（未入仓；可重新 clone）（含 `backend/rag/`、`backend/indexing/`、`frontend/`、`evals/`、`tests/`，2026-10-06 经 codeload 下载）；关键单文件另存 `supermew_raw/`；联网取证缓存 `_web_evidence/`。

---

## 0. 系统身份与证据基线

### 0.1 它是什么

| 项 | 值 | 来源 |
|---|---|---|
| 仓库 | `icey1287/SuperMew`（MIT，Agentic RAG w/ LangChain & LangGraph） | 【实测·代码】`supermew_full/` 全仓库；repo URL：https://github.com/icey1287/SuperMew |
| 检索栈 | Milvus 2.5+（HNSW 稠密索引 + SPARSE_INVERTED_INDEX + 原生中文 analyzer + BM25 Function）、RRF、Auto-merging | 【实测·代码】README「技术栈」节（supermew_full/README.md:715-716） |
| Embedding | `langchain_huggingface` 本地嵌入，默认固定 revision 的 `BAAI/bge-m3` | 【实测·代码】README:717-718；`backend/core/settings.py:99` |
| Rerank | 「可选 Jina-compatible Rerank Provider」（HTTP `POST {host}/v1/rerank`） | 【实测·代码】README:718；`backend/providers/rerank.py` |
| 评测 | `evals/rag/` Dataset/Observation/GatePolicy/baseline 四件套 + CI 门禁 | 【实测·代码】`evals/rag/`、`gates_v1.json` |

### 0.2 面板字段 → 代码字段对照（含 4 处需注意的转录/版本差异）

| 截图面板【实测·面板】 | 对应代码字段（证据） | 本次运行值 | 备注 |
|---|---|---|---|
| 检索阶段：initial | `rag_trace.retrieval_stage="initial"`（`backend/rag/pipeline.py:468`） | initial | 一致 |
| 相关性评分：yes | `evidence_relevance`，枚举 `none/weak/strong`（`pipeline.py:121-123`） | 转录为「yes」 | 提示词把该字段定义为**主题相关性**（见 §6） |
| 评分决策：generate_answer | `route`，main 分支枚举为 `answer/rewrite/clarify/scope_select/no_knowledge`（`pipeline.py:130-131`） | 语义=answer | 面板字面值 `generate_answer` 在 main 分支未出现（详见 §9 未确证清单） |
| 是否需要重写：否 | route≠rewrite（`pipeline.py:485-492`） | 否 | 一致 |
| 检索模式：hybrid | `meta.retrieval_mode`（`backend/rag/utils.py:838-843`） | hybrid | 一致 |
| 候选召回数：15 | `meta.candidate_k`；面板文案为「Milvus 候选池：15（top_k × N）」（`frontend/.../RetrievalTraceDetails.vue:339-355`；`utils.py:213-233`） | 15 | `.env.example` 预设为 30；15 的来源见 §9（未确证项 3） |
| 叶子召回层级：L3 | `meta.leaf_retrieve_level`=`LEAF_RETRIEVE_LEVEL`（默认 3，`utils.py:41`）；召回过滤 `chunk_level == 3`（`backend/documents/retrieval.py:110-114`） | L3 | 一致 |
| Auto-merging 启用/应用/阈值/替换片段/轮次 | `auto_merge_enabled / auto_merge_applied / auto_merge_threshold / auto_merge_replaced_chunks / auto_merge_steps`（`utils.py:365-412`） | 是/是/2/4/1 | 语义解码见 §3 |
| Rerank 已配置/已执行/模型：jina-reranker-v3 | `rerank_enabled / rerank_applied / rerank_model`（`backend/rag/reranking.py:236-250`）；模型名来自环境变量 `RERANK_MODEL`（`backend/core/settings.py:174`） | 是/是/jina-reranker-v3 | 模型核实见 §5 |
| 扩展查询：《消费者权益保护法》第七条内容 | main 分支**未找到**该字段（最接近的是 `rewritten_query`/`step_back_question`/`hyde_document`，且本次未触发重写） | — | 转录差异或较新版本 UI，见 §9 |
| 初次检索结果 … RRF 名次 #1 | `initial_retrieved_chunks[].rrf_rank`（Vue:282/315 渲染） | #1 | **`rrf_rank` 的真实语义见 §5.3（重要）** |
| 说明文字「三段级分块检索，叶子层 L3 召回，候选 15」 | 代码文案为「**三级**分块检索：叶子层 L{level} 召回，候选 {candidate_k}」（`pipeline.py:435-443`） | — | 转录近似 |

### 0.3 这次查询实际走的链路（代码级重建）

```
query:"《消费者权益保护法》第七条是什么"
  → 嵌入（bge-m3）
  → Milvus.hybrid_search：
       稠密通道(HNSW/IP, limit=candidate_k×2)   ←──┐
       稀疏通道(BM25 Function, chinese analyzer, limit=candidate_k×2) ←┤
       融合：RRFRanker(k=60)，输出 candidate_k=15 个 chunk_level=3 的叶子  ←┘
  → dedupe_documents（按 chunk_id 去重）
  → _auto_merge_candidates：L3→L2→L1 两趟；同一 parent 下命中 ≥2 个叶子即整组合并成父块
       （本次：应用=是，替换 4 个子块，1 趟生效）        → 候选集合（post_merge_candidate_count）
  → RerankStage：给每个候选打 rrf_rank=输入序号(1..n)；取前 30 条、每条 text 截 8000 字符、总 60000 字符
       → POST {RERANK_BINDING_HOST}/v1/rerank，model=jina-reranker-v3
       → 得到 rerank_score，按返回顺序成为最终 docs
  → grade_documents_node：LLM 按 EVIDENCE_GRADE_PROMPT 产出 EvidenceGrade
       （relevance=strong、answerability=sufficient）→ route=answer
  → 直接把 docs 交给生成
> 注意：面板上的「RRF 名次」不是 Milvus 融合排名本身，而是**进入重排时的输入序号**（`reranking.py:252-263` 打标），它已经包含了"去重 + auto-merge"两步的结果（详见 §5.3）。
```
（本图为【实测·代码】重建：`backend/rag/utils.py:659-708,711-870`、`backend/indexing/milvus_client.py:441-488`、`backend/rag/reranking.py:71-154`、`backend/rag/pipeline.py:427-478,636-717`。）

---

## 1. 一页结论：原因排序与最小验证实验

**先给结论**：这不是"某一步坏了"，而是**六个环节叠加**：一个**无结构**的检索塔（条号不是可寻址单元）→ 一个**对称融合**（等权 RRF，语义通道主导）→ 一次**过度合并**（把长父块顶上台）→ 一次**只看正文的重排** → 一个**按"主题"设计的判官**照单全收 → 生成。任何单点修复都不能保证修复此 case；但 P1+P5 两项低成本改动就能把此类查询从"答错"变"答对或拒答"。

| # | 原因（按怀疑度排序） | 机制一句话 | 最小验证实验 |
|---|---|---|---|
| **P1** | **切分无条号结构、无条号元数据（根因）** | 叶子是每页 800 字符盲切（L3），`第七条` 不是可过滤字段；"第X条"只能靠文本碰运气 | **E2**：`TEXT_MATCH(text, '第七条')` 查库，看它落在哪个叶子的什么位置、边界如何 |
| **P2** | **RRF 等权融合 + 候选池仅 15：向量通道可压过词法证据** | 该 query 语义含量≈0（只有法名+条号）；稠密通道给"通篇像消保法"的块都打高分；RRF(k=60) 又压缩"单通道第一名"的优势 | **E1**：分通道 dump（hybrid / dense-only / sparse-only）各 top-30，标注含"第七条"的块在各通道名次 |
| **P3** | **Auto-merging 阈值=2 过低 + 父块原位替换** | 同父 ≥2 个叶子命中→整组换成 1600–2400 字符的父块，放在"第一个命中子块"的位置，且不重排 → 长块（可跨多条、以"第十二条…"开头）直接顶到精排输入 #1 | **E3**：用 E1 的候选重放 `_auto_merge_candidates`，看被替换的 4 个块是谁、#1 是否成了 merged 父块 |
| **P4** | **重排只喂正文，且是 0.6B 小模型；它只能重排"已在候选里的"东西** | 若含"第七条"的叶子没进候选池，重排无能为力；即便在池里，v3 是 0.6B listwise 模型（能力需实测确认） | **E4**：原样重放 rerank 请求；再把文档加"条号前缀"重放对照 |
| **P5** | **判官按"主题相关性"设计，无条号一致性校验（最直接的最后防线失守）** | 提示词原文要求判"主题相关性"；route=answer 的条件只有 `relevance==strong && answerability==sufficient`，全链路**没有任何一处**检查"文档里的条号 == 问的条号" | **E5**：原 prompt 重放（预期复现 strong/sufficient）；换"结构化核验" prompt（先抽取片段条号再比对）看翻转 |
| **P6** | **文本层诱因：语料里条号书写不一致** | 人大网上"第七条"后**无分隔符**（"第七条消费者…"），而"第十二条"后有空格——同一页两种写法；官方 PDF 还有断字伪影（"第十 二届"）；PDF 抽取后更可能粘连/断行，影响分词与精确匹配 | **E6**：用 Milvus 同款 chinese analyzer 对"第七条""《消费者权益保护法》第七条是什么"做分词检查 |

> 【推测】各原因的相对权重：P1 与 P5 是"结构性"的（不修必然复发）；P2/P3 决定"这次 d 是第几名"；P4 是这次失败中**作用待定**的一环（取决于"第七条"叶子是否进候选池，E1 直接回答）；P6 为旁支但值得顺手修。

---

## 2. 法律文档的切分与检索：为什么"第七条"这类指代天然易错，以及推荐方案

### 2.1 法律文本的结构与"强结构化指代"的特殊性

- **规范结构**：编 → 章 → 节 → 条 → 款 → 项 → 目。**《立法法》第六十五条原文**："法律根据内容需要，可以分编、章、节、条、款、项、目。编、章、节、条的序号用中文数字依次表述，**款不编序号，项的序号用中文数字加括号依次表述，目的序号用阿拉伯数字依次表述**。"（【本次核验】上海市发改委官网 PDF，pdftotext 抽取：https://fgw.sh.gov.cn/cmsres/17/178b6e73e5684d1db5662b7aa7efc161/a7bd995c7df73639a986a41bc08e5395.pdf ）。更细的书写规范见《立法技术规范（试行）（一）》（法工委发〔2009〕62号，全文转载 https://mp.weixin.qq.com/s?__biz=MzUyNjUxMzQ2OA==&mid=2247486401&idx=1&sn=e937213b250400d95eede0ad090d6b9c ）与（二）（法工委发〔2011〕5号 https://mp.weixin.qq.com/s?__biz=Mzk0NjQxMDA2OA==&mid=2247491773&idx=3&sn=14a0eaab5cfca155cd7cb85bc50a86af ）——其中第 9.1-9.4 给出引用书写规则（"第×项"不加括号；"第×条第×款第×项"；连续项"第×项至×项"），第 11.1/11.2 给出数字规则（序数词用汉字数字；年月日与"目"的序号用阿拉伯数字）。**「第X条之一」是合法条号变体**（实例：刑法「第一百二十条之一 资助恐怖活动组织…」，官方 PDF https://fgw.sh.gov.cn/cmsres/93/93f47dfc6d8b4db3ba3d57a33a157e1a/d34c3ee959e2f6440f08e578d72cfe23.pdf ，pdftotext 实测）——解析正则必须覆盖 `(之[一二三四五六七八九十]+)?`；且"条之一"须按**出现顺序**排位、不能按整数排序。同 PDF 还可见官方文本的**断字伪影**（"个 人的""有 期徒刑""第十 二届"式空格插入）——条号文本形态在真实语料里极不稳定。另注意：官方文本中"阿拉伯数字条号（第7条）"**未找到实例**（§9.1）——查询侧"第7条→第七条"的归一化属必要推断。（以上【业界·本次核验】，由本会话 subagent curl+pdftotext 实测。）
- **实例与一个此前未注意的坑**（【业界·本次核验】，中国人大网〔http://www.npc.gov.cn/c1773/c1848/c21114/c21170/c21174/201905/t20190521_210511.html 〕，本会话 subagent curl 实测 200）：
  - 第七条原文：「**第七条消费者**在购买、使用商品和接受服务时享有人身、财产安全不受损害的权利。」（**条号后无分隔符**）
  - 第十二条原文：「**第十二条 消费者**享有依法成立维护自身合法权益的社会组织的权利。」（条号后有空格）
  - 第十三条原文：「**第十三条 消费者**享有获得有关消费和消费者权益保护方面的知识的权利。」（条号后有空格）
  - 国家法律法规数据库中同一法律的条目：https://flk.npc.gov.cn/detail?id=2c909fdd678bf17901678bf7670606ef&title=中华人民共和国消费者权益保护法 ；市场监管总局转载版本则用全角空格「第七条　消费者…」（https://www.samr.gov.cn/zfjcj/tzgg/art/2023/art_615af9ed6bcd4974bf853dd2e02bc663.html ）。
  - **意义**：同一部法、同一页里"第七条"与"第十二条"的**书写格式都不一致**。任何依赖"第N条 + 分隔符"习惯的切分/分词/正则都会踩到不一致；反过来，这也说明**条号必须被当作结构化字段显式提取**，不能指望文本形态稳定。
- **"第七条"为什么与"第十二条"在向量空间里难分**：查询的全部语义信号≈"消费者权益保护法"；条号本身是近零语义 token。任何 800 字符级别的块只要有大量"消费者……权利"语句（第二章全部是此类），稠密相似度就彼此接近——**区分靠的不是语义，而是条号这个标识符**。【推测，但由 E1 的分通道排名可直接验证】

### 2.2 三种切分的对照与推荐（SuperMew 现状 = 第三种的最弱形式）

| 方案 | 做法 | 优点 | 缺点 | 证据 |
|---|---|---|---|---|
| 固定长度 | 现 SuperMew：每页递归字符切分 2400/1600/800（默认 chunk_size=800 时），不识别条结构 | 简单 | 条被拆跨块、多条约挤在一块，条号无元数据 | 【实测·代码】`backend/indexing/document_loader.py:127-164`（L1=max(2000,3×800)=2400/重叠400；L2=1600/200；L3=800/100）；生产用默认值（`backend/api/resources.py:19`、`backend/documents/publication.py:193`） |
| 语义切分 | 按句/段语义边界切 | 块内语义完整 | 仍与"条"边界不对齐 | — |
| **按条切 + 父子块（推荐）** | **条 = 叶子/检索原子**；章/节或"条窗" = 父块；条号进字段 | 条号可过滤/可精确匹配；引用稳定；评估可自动化 | 需要一次解析工程 | 法条检索学术传统即"以条为检索单元"（见 2.4） |

**推荐落地设计（可直接改造 SuperMew）**：

1. **解析规则**（一次性的解析器，建议单独的 `law_parser.py`）：
   - 条号正则：`第[一二三四五六七八九十百千零〇0-9]+条(之[一二三四五六七八九十]+)?`（同时容忍"第 七 条""第七 条"的断行/空格变体：先做空格归一化再匹配）。
   - 章/节正则：`第[一二三四五六七八九十]+[章节]`。
   - 得到 `article_no`（整数）+ `article_no_cn`（"七"）+ `article_display`（"第七条"）。
2. **chunk 方案**：
   - **叶子（进 Milvus，被召回）**：一"条"一块。若某条 >800 字符，按款（自然段）拆多子块，但**每块都携带同一 `article_no` 元数据**（子块共享条身份）。
   - **父块（进 ParentChunkStore）**：章/节（或"条号窗口"，如每 5 条一组），供 auto-merge 提供上下文。
   - **块文本模板（关键）**：`《消费者权益保护法》第二章 消费者的权利 第七条\n消费者在购买、使用商品和接受服务时享有人身、财产安全不受损害的权利。…`——把**法名+章节路径+条号**放在正文之前（这与 Anthropic「Contextual Retrieval」给块补上下文再索引的做法同构，【业界·本次核验】其 cookbook 原文："The contextualization happens once at ingestion time"、"contextualized chunks aren't just retrieved more often, but rank higher when relevant (Pass@5)"，https://github.com/anthropics/anthropic-cookbook/blob/main/capabilities/contextual-embeddings/guide.ipynb ）。
3. **元数据 schema（Milvus 侧，新增字段）**：

| 字段 | 类型 | 用途 |
|---|---|---|
| `law_name` / `law_namespace` | VARCHAR | 精确限域（"消费者权益保护法" vs "实施条例"） |
| `article_no` | INT64 | **结构化过滤**（`article_no == 7`）+ 评估自动计算 |
| `article_display` | VARCHAR（`enable_match=True`） | `TEXT_MATCH(article_display, "第七条")` 精确命中 |
| `chapter_no/title`、`section_no/title` | VARCHAR | 显示、父块、rerank 前缀 |
| `version_id` / `effective_date` | VARCHAR/INT64 | 版本问题（新旧法条号漂移） |
| `references_out` | ARRAY | 该条正文中提到的其他条号（"依照第七条规定"）→ 可做**引用图**一跳扩展（开源解析器已可达 98.4% 解析率，见 §2.4；互引*网络*的应用仍偏前沿） |

4. **检索路由（三阶梯）**：
   - ① 规则解析 query → 命中 `(law, article_no)` → **直接 filter 精确取条**（0 成本、必胜）；
   - ② 解析失败但疑似条号 → `TEXT_MATCH(article_display)` 兜底；
   - ③ 语义查询 → 现 hybrid 管线（此路才是 RRF/rerank 的主场）。
   （正则命中率在中文法律 query 上很高；LLM 解析作为兜底，见 §6.3。）

### 2.3 本案例在现方案下的完整解释（把 §0.3 的链路对上现象）

- 叶子 800 字符 ≈ 消保法 5–10 条（本法条文多为 50–150 字）→ 显示的片段以"第十二条 消费者享有…第十三条…"开头，说明**该块的边界落在第十二条之前、第七条之后**，第七条根本不在这个块里。
- 含第七条的叶子是**另一个块**；它要么没进 15 个候选，要么进了但被 RRF 融合/合并/重排压到后面（E1/E3/E4 逐一判定）。
- 判官看到的每个文档是 `filename (Page N):\n{text}`（【实测·代码】`backend/rag/evidence.py:53-69`），每条最多 1200 字符、总额 4800 字符（`backend/core/settings.py:86-95`）——**条号在正文里但没有任何结构化强调**，且被测文档根本不含"第七条"。

### 2.4 学术与国内实践（有据可查的部分）

- **法条检索（Statutory Article Retrieval, SAR）是一个独立任务**：给定自然语言问题，检索适用法条（以"条"为单位）。【业界·本次核验】BSARD（《A Statutory Article Retrieval Dataset in French》，ACL 2022：https://aclanthology.org/2022.acl-long.468 ，arXiv 2108.11792）；《Finding the Law: Enhancing Statutory Article Retrieval via Graph Neural Networks》（EACL 2023：https://aclanthology.org/2023.eacl-main.203 ，arXiv 2301.12847）——**勘误：该文用的不是"法条互引图"，而是立法层级结构树**（PDF 原文："the edges represent the hierarchical connections between section and article nodes"）；**互引网络被该文明确列为未来工作**（原文："considering richer legal graph structures, especially legal citation networks, could increase effectiveness even more. However, building such citation networks from raw texts requires a considerable text-processing effort."）。其摘要中"结构依赖"的表述仍可引用："SAR deals with texts whose full sense depends on complementary information from the topological organization of statute law"——**且这一结构树用法恰是本文 §2.2"章做父、条做叶"设计在学术上的直接先例**。QABISAR（arXiv 2412.00934）；CuSINeS（arXiv 2404.00590）。**中文 SAR 亦有公开工作**：STARD（中文法条检索任务+数据集：1,543 条真实法律咨询查询、55,348 条候选法条，**最优方法 Recall@100 仅 0.907**：arXiv 2406.15313，https://github.com/oneal2000/STARD ）；LegalMALR（中文法条检索，多智能体查询改写+LLM 重排；另建 118 条疑难中文查询集 CSAID，指出的难点正是 "implicit, multi-issue, and expressed in colloquial or underspecified forms"：arXiv 2601.17692 ）。规模参照：BSARD=22,600+ 法条语料、1,100+ 真实问题（repo https://github.com/maastrichtlawtech/bsard ）；更早的"长条文只部分相关"问题研究见 arXiv 2212.13899（"legal documents often contain long articles and only some parts are relevant"，在 COLIEE 上做法条文档检索的层级注意力）。
- **"最小充分片段"优于大块**：《LegalBench-RAG》主张以最小充分片段（minimal sufficient snippets）检索并利于引用（arXiv 2408.10343，标题与主张经 subagent 核验）——与 §2.2"按条切分"的方向一致；对照 Anthropic Contextual Retrieval 官方博客：给块补上下文可把检索失败率降低 35%/49%/67%（不同配置；https://www.anthropic.com/engineering/contextual-retrieval ）。
- **法域检索里"词法仍强"**：《Yes, BM25 is a Strong Baseline for Legal Case Retrieval》（arXiv 2105.05686，标题实测）——与 P2 的建议方向一致：**结构化/条号状态下让词法通道主导**。
- **法条互引/引用闭环（前沿工作）**：【业界·本次核验】公开路线共三条——①**结构图**：G-DSR 的立法层级树（互引网络被其列为未来工作，见上条勘误）；②**上下文注入**：CRAwLeR（检测法条互引、把目标 chunk 与相关上下文链接；**其 Recall@10 也只有 55%/59%**，且发现 "Even when the target is retrieved in the top ten, labelled context chunks routinely outrank it"——**目标条进了前 10 仍常被"上下文块"压制**，与本案故障模式同构：arXiv 2606.21676 ）；③**引用闭包**：RefWalk（citation closure 跨文档遍历 + per-rule attribution：arXiv 2605.29742，https://github.com/yeongjoonJu/RefWalk ）。更早还有法典级引用网络分析（arXiv 1201.1262，"每个法典为顶点、法典间引用为边"）。**结论：引用感知检索在公开工作中是公认空白（G-DSR 列入 future work、CRAwLeR 2026 年才把"跨引用上下文"做成基准且未解决）——这正是"问第七条却召回第十二/十三条"缺少现成解的文献侧佐证。**
- **中文法律 LLM 的检索模块**：【业界·本次核验】DISC-LawLLM（README：公开了「法条检索」「带检索的法律咨询」，检索模块基于 Langchain-Chatchat，知识库以"法条库"为单位——800 多部法律/条例；论文 arXiv 2309.11325）；**智海-录问（浙江大学等）**（README 原文："法条库中**每条法条的关键词是所属法律和法条条数**"——**条级 key 的直接证据**；并含"多步检索""多路检索"；论文 arXiv 2604.06737，repo https://github.com/zhihaiLLM/wisdomInterrogatory ）；LaWGPT（pengxiao-song/LaWGPT README，6057★，强调"中文法律结构化知识"）。均为"以法条为知识单元"，条号级切分细节未公开。
- **国内商用产品：公开能力口径已核验**（【业界·本次核验】，均为 subagent 抽查 URL 200 且引文与原句一致；**算法级工程细节未公开**）：
  - **华宇元典**开放平台（本次调研中唯一到**字段级**的条级证据）：文档原文——"按自然语言 query 做证券行业相关**法条级语义检索**，可按时效性、一级效力级别和实施日期范围过滤。返回结果在 extra.fatiao，每条主要是法条内容及所属法规信息，**不是整部法规全文列表**"、"返回的是**法条列表，每条包含法条内容、法条编号、所属法规 ID/名称和法规元数据**"（https://open.chineselaw.com/ ）——本文档"条号即一等公民"的判断与这类产品口径一致；
  - **通义法睿**：官方文档原文"法律法规检索：根据输入的内容在法律法规库中进行法规检索，实现了对**法条语意理解**基础上的智能检索，支持精准检索和模糊检索"（https://help.aliyun.com/zh/model-studio/user-guide/tongyi-farui/ ）；
  - **北大法宝**：检索界面提供**条文级检索范围**——检索框旁"同篇/同条"选项（HTML value=Piece/Strip）、"法宝引证码"、列表含"引用量"排序；首页宣传"一键即可获得**可追溯**的专业法律结论"（https://www.pkulaw.com/ ，HTML 实抓）；
  - **法信**：法条页以 `tiao=` 参数按条定位（https://www.faxin.cn/v2/ftsy/detail.html?gid=A246789&tiao=2 ）；**幂律智能**（https://www.cls.cn/detail/1389428 ）；**秘塔 MetaLaw**（https://meta.law/ ，展示判决引用的法条）。
  - **官方库自身的检索形态（耐人寻味）**：国家法律法规数据库（flk）前端检索字段为 title/content/分类/制定机关/施行日期/公布日期（前端 JS 实抓 https://flk.npc.gov.cn/assets/index-BZ9XHUiV.js ）——**官方库的检索单元也不是天然条级**；"条级检索"是检索侧要自己补齐的工程，而不是现成能力。
- **竞赛任务**：COLIEE 设 Task 3 "Statute Law Retrieval and Entailment"（**官网本次核验**：https://coliee.org/COLIEE2025/tasks/task3 原文 "The task investigates the performance of systems that search a static set of civil law articles using previously unseen queries. The goal of the task is to return relevant articles in the collection to a query."；总览页 https://coliee.org/COLIEE2025/overview "Task 3: Statute Law Retrieval"；**2026 起 Task 3 要求同时提交"相关条文 + 蕴含判断"**：https://coliee.org/COLIEE2026/overview ）。官方任务页未见条号/互引感知机制的表述（未找到确证）。
- **开源确证："条级切分 + 互引提取"已被工程化**（本次核验的 GitHub 项目）：`13098806890/laws-data`——从 flk 官方 docx 起、七阶段条级解析 pipeline，覆盖 **78,788 条**、提取法条互引 **5,319 条**（跨法 2,559 + 自引 2,760）、**解析率 98.4%**；`extract_references` 用三类正则（`《法律名》第X条` / `刑法第X条` 类 / `本法·本条例·本规定第X条` 自引，刑法修正案中自动重定向到刑法主体），并用 `art_index` 把"法律名+条号"解析到节点；`lttxzmj/chinese-law-corpus`——446 部法律与司法解释 **26,000+ 条逐条结构化 JSON**（编/章/节/条层级）、**稳定条 ID**（如 `civil-code-0577` = 民法典第五百七十七条，跨版本可引用）、CC0，自述点题：**"一条 = 一个法条，天然的 chunk 边界"**（配套 MCP：https://github.com/lttxzmj/chinese-law-mcp ）。**含义**：§2.2 的解析器方案不仅可行，而且**现成开源数据可直接采用**（省掉 98% 的解析工程量）；互引字段 `references_out` 亦有 98.4% 解析率的现成实现（互引*网络*的应用仍偏前沿，见下条）。
- **解析边界的公开注脚**：`Teetrition/Chinese-Law-Formatter`（中国法纯文本→XML，依赖 cn2an 做汉字数字转换）在 README 中**明示不适配**"包含附件或附表的（如刑法）""包含**目**及更低级别的（如海商法）"与宪法——"目级/附表级自动解析在公开实现中仍未解决"；`dengcao/Chinese-Laws` 自述"每条法律条款明确标注来源法律及条款号"。**现状盘点**：中文法条解析均为个人项目（0–40★ 量级），**没有机构级维护的解析器**（GitHub 关键词 `law article segmentation` 搜索 total_count=0）。
- **中文社区切分实践（公开博客自述，未独立验证，仅作方向佐证——相关文章有明显 AI 辅助痕迹）**：CSDN"章节优先、条为最小单元"的自述（改按条切分后**自述** Hit Rate 62%→81%，并做了 `verify_citations` 把回答中的"第X条"回检原文：https://blog.csdn.net/weixin_29062613/article/details/166762318 ）；固定 512 字符切分把"甲方权利/甲方义务"切进不同块的踩坑记录（推荐"按条款编号切分、保留条款编号"：https://blog.csdn.net/2401_82469710/article/details/163878428 ）；父子块"小检索、大推理"明确点名适合法律文档（父 1024-2048 / 子 256-512 token：https://blog.csdn.net/DEVELOPERAA/article/details/163878676 ）；工具侧佐证：阿里云 DataPhin 官方文档提供"按文本结构切分"算子（https://help.aliyun.com/zh/dataphin/fullmanaged/user-guide/text-chunking ）。
- 案例检索侧（相邻域，供评估集参考）：LeCaRD/LeCaRDv2（**案例级、非条级**；SIGIR 2021 起，repo https://github.com/myx666/LeCaRD ，arXiv 2310.17609）；CAIL（https://github.com/china-ai-law-challenge 、https://cail.cipsc.org.cn/ ）。
- **「条做叶子、章做父」的直接公开推荐：未找到确证**（本会话多路核查）。最接近的公开实践（均为结构感知切分方向的 GitHub 仓库，README 已核验）：`zeroentropy-ai/legalbenchrag`（LegalBench-RAG 论文官方仓库）、`Snehashish-Das/Legal-GraphRAG`（自述 "Structure-Aware Chunking"）、`mralaminahamed/bd-legal-rag`（自述 section-hierarchy-aware）、`PKU-YuanGroup/ChatLaw`、`IAAR-Shanghai/Meta-Chunking`（LLM 逻辑感知切分）——**没有任何一个直接给出"条叶章父"的定量推荐**；本文 §2.2 的方案属于结合代码事实与规范的工程推导。

---

## 3. Auto-merging / 层级检索：机制、面板参数解码与业界对照

### 3.1 SuperMew 的实现（逐行要点）

【实测·代码】`backend/rag/utils.py:296-412`（线上 raw 抓取与本地快照 md5/cmp 逐字节一致：`f49ab8e93466b3e0167494994e7056cb`）：

- 召回后、精排前，对**全部候选**（15 条）执行 `_auto_merge_candidates`，做两趟：先 L3→L2，再 L2→L1（`:389-400`）。
- 合并判定：按 `parent_chunk_id` 分组，**`len(children) >= threshold`** 即把整组换成父块（`:309-313`；`threshold=AUTO_MERGE_THRESHOLD`，默认 2，`:40`）。
- 替换方式：父块（从 ParentChunkStore 取，PG + Redis 缓存）**放在"第一个被替换子块"的位置**，其余子块删除；**不重排序**（docstring：`在完整召回候选上执行 L3→L2→L1 合并；不改变顺序，精排由后续步骤负责`，`:382`）。
- 分数：父块取被合并子块的**最高**分（`_merge_rank_score_into`，`:256-274`）。
- 追踪字段：`auto_merge_replaced_chunks = 两趟替换的子块总数`；`auto_merge_steps = int(第一趟有合并) + int(第二趟有合并)`（`:402-411`）。

### 3.2 面板参数解码【实测·面板+代码】

| 面板 | 值 | 代码含义 |
|---|---|---|
| 合并阈值 | 2 | 同一父块下**≥2 个**召回叶子即触发合并（对页内 L1/L2 子树而言，阈值极低） |
| 合并替换片段 | 4 | 本次有 **4 个子块**被父块替换（分布未知：可能 1 组×4，或 2 组×2，或含第二趟的级联） |
| 合并轮次 | 1 | `auto_merge_steps=1`：两趟中**只有一趟**发生了合并（另一趟没有新组达到阈值） |

> 【推测】对本案例最相关的组合是："第十二条开头的那个 L2/L1 父块的多个子块被同时召回（≥2）"→ 父块原位顶上（其文本可跨十几条、以"第十二条"开头），同时**把原本更精确的窄片段从候选集合中移除**。E3 可 5 分钟内证伪或证实。
>
> 另注：这套参数语义**连项目自己的用户都不直观**——SuperMew issue 区有用户直接把 `_merge_to_parent_level(docs, threshold=2)` 代码贴出来询问含义（#8，已关闭），另有 #1（三级分块召回效果）、#9 等相关讨论（本次经 GitHub API 核验。URL 见 §9.2 #43）。这从侧面说明"计数阈值"这一设计的可解释性问题。

### 3.3 业界对照：正确用法与已知坑

| 维度 | LlamaIndex AutoMergingRetriever（官方源码） | SuperMew 实现 |
|---|---|---|
| 触发条件 | `ratio = 命中的子节点数 / 该父节点全部子节点数`，**`ratio > 0.5` 才合并**（`simple_ratio_thresh=0.5`） | `命中子块数 >= 2`（不看父块总共几个子块）——**激进得多** |
| 合并轮次 | `while is_changed:` **迭代合并直到稳定**，另有 `_fill_in_nodes` 把命中节点之间的"空洞"补上（利用 next/prev 链） | 固定两趟（L3→L2→L1），无补洞 |
| 合并后排序 | 合并完 `sort(key=score)` **重新按相似度排序** | **不重排**（保持原位置）→ 父块继承首个子块的位置 |
| 父块分数 | 命中子块分数**平均** | 子块最高分 |
| 出处 | 【业界·本次核验】源码 https://raw.githubusercontent.com/run-llama/llama_index/main/llama-index-core/llama_index/core/retrievers/auto_merging_retriever.py （本会话实测抓取；上述行为均读自该文件）；层级构造 `HierarchicalNodeParser` 默认 `chunk_sizes=[2048, 512, 128]`（同仓库 `node_parser/relational/hierarchical.py`，实测抓取） | 【实测·代码】如上 |

**同源补充（均本次核验）**：

- LlamaIndex 官方 notebook 的描述原句为 "merges subsets of leaf nodes that reference a parent node beyond a given threshold"，而**官方评估结论是 "The results are roughly the same."**——即 Auto-merging 相对基线的收益**基本持平**（https://raw.githubusercontent.com/run-llama/llama_index/main/docs/examples/retrievers/auto_merging_retriever.ipynb ）。**含义：不要把"自动合并"当成召回率的放大器；它本质是"上下文补充手段"，代价（替换/稀释）是真实存在的。**
- 已知缺陷参考：llama_index issue **#23224**（open，2026-09-24）报告层级切分的非根层节点 **NEXT 关系缺失**、`start/end_char_idx` 相对父块等（https://github.com/run-llama/llama_index/issues/23224 ）——层级实现的边角问题并非只发生在 SuperMew。
- 第三种对照：**LangChain `ParentDocumentRetriever`**（经 PyPI `langchain-classic` 1.0.8 wheel 源码核验）docstring 原句 "Retrieve small chunks then retrieve their parent documents"——**无阈值、无合并条件**：命中任一子块即返回其父块；`parent_splitter` 为空时父块可以**是整篇文档**（最激进）。
- **顺序核验（重要）**：LlamaIndex 标准组合下 reranker 的对象**也是"合并后的父块"**——`RetrieverQueryEngine.retrieve` 先 `nodes = self._retriever.retrieve(query_bundle)`，紧接着 `self._apply_node_postprocessors(nodes, ...)`（源码逐字，本次核验：https://raw.githubusercontent.com/run-llama/llama_index/main/llama-index-core/llama_index/core/query_engine/retriever_query_engine.py 第 160-162 行；边界：若用 QueryPipeline/自定义 flow 把 rerank 排在 AutoMergingRetriever 之前，顺序会相反）。即 **"merge → rerank"是两套系统的共同模式**，SuperMew 并非异类；差异只在合并门槛（LlamaIndex 比例 0.5 vs SuperMew 计数 ≥2）。这也意味着：要打破"重排对象被从命中小块换成大块"的通病，就得像 mrson_rag PR #5 那样把顺序反过来（小片段精排、扩展后置）——这是本文 §5.3/E4 建议的业界镜像。

**已知坑（对照本案例）**：

1. **叶子越小/越近，合并越容易"拉入无关内容"**：把多个子块换成父块＝用低精度、高覆盖率的长文本替换高精度命中——检索单元从"答案所在"变成"答案附近"。本案的"以第十二条开头的块"是典型表现。
2. **阈值语义要与树的形状匹配**：SuperMew 的 `≥2` 对"每页切出 3–6 个 L3 的子树"太容易触发；LlamaIndex 的"比例 >0.5"在设计上要求"大多数子块都被命中"才合并，保真度更高。
3. **合并改变了下游一切**：rerank 的对象、判官的输入、生成引用的定位都变成长父块——错误被"放大而不是发现"。
4. **合并 + 等权 RRF 的叠加效应**：融合把长块排到前面 → 长块更可能"聚众"触发合并 → 合并后位置不变、块更长 → 再进入重排。链条上没有一个环节"反对"这次错误。
5. **RAPTOR 的野问题**：递归聚类摘要（arXiv 2401.18059《RAPTOR: Recursive Abstractive Processing for Tree-Organized Retrieval》，标题本次核验；README 原句 "constructs a recursive tree structure from documents"）会用 LLM 摘要替换原文——对法律场景（逐字引用、条号必须原样）风险更高；本案未用 RAPTOR，但若将来引入，务必保留"原文叶子 + 条号元数据"的检索通道。
6. **真实工程的修正方向（本次核验的旁证，恰好说明"问题存在且业界已有对策"）**：
   - langchain4j PR **#6132** 新增 SIBLINGS 模式，目的原句 "avoiding an oversized parent in the LLM"——直指"整父块过大"这一已知病；
   - mrson_rag PR **#5** 明说 "Retrieval and reranking still run on small chunks… Before building the prompt, each chunk is expanded"——即**正确的修法是"小片段检索与小片段精排、进 prompt 前再扩展"**，恰与 SuperMew 的"先合并→再精排"顺序**相反**；
   - agenticos issue **#1651** 的诉求表述 "without diluting retrieval precision"——同一主题。
   （URL 见 §9.2 #40。）这组旁证与本文 §5.3（重排输入保留命中定位）和 §3.1 的改进方向互为印证。

**结论**：不要关掉层级检索——它提供上下文是对的；要改的是**（a）叶子改成"条"、（b）阈值从"计数 2"改成比例或提高、（c）合并若发生，rerank 输入必须仍能看到"命中的原始子块定位"（比如把子块原文附在父块文本前，或把 `article_no` 写进重排文本）**。

---

## 4. RRF 融合：正确姿势、为什么不适合这条查询、以及路由

### 4.1 公式与工程默认值（全部本次核验）

- **RRF 原论文（PDF 全文已提取）**：`score(d) = Σ_r 1/(k + rank_r(d))`；**"k = 60 was fixed during a pilot investigation"**——Table 1：k=60 时 MAP=.2145，而 k 在 10–100 区间仅 .2123–.2147，作者原话 **"near-optimal, but the choice was not critical"**（https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf ）。
- **Elasticsearch**：`rank_constant` 默认 **60**、`rank_window_size` 默认=size；"Each child retriever carries an equal weight"（默认等权）；RRF 自 **8.8** 引入（tech preview 横幅持续到 8.15，8.16 起无）；另提供 **linear retriever**（归一化加权和）。URL：https://www.elastic.co/guide/en/elasticsearch/reference/current/rrf.html · .../retrievers-overview.html
- **Azure AI Search**：官方文档写 "1/(rank + k) … **k to a small value, such as 60**"，且**支持每通道权重**（vector 权重默认 1.0，文档示例 0.5/2.0）。URL：https://learn.microsoft.com/en-us/azure/search/hybrid-search-ranking （2026-06-08 更新）。
- **Weaviate**：rankedFusion 公式官方写作 `1/(RANK+60)`；**v1.24 起默认 relativeScoreFusion**；混合检索 `alpha` 默认 0.75（alpha=0 即纯 BM25）。**其官方文档里有一个与本案同构的例子：关键词强的"第一名"，进入 rankedFusion 后被"向量第一名"挤到第二。** URL：https://docs.weaviate.io/weaviate/api/graphql/search-operators
- **Qdrant**：RRF 的 k **默认 2**（v1.16 起可配 k；v1.17 起支持**加权 RRF**）；官方原话："**BM25 may win on identifier-heavy ones. Assigning equal weight to both can let the weaker retriever drag down results.**"（没有 eval set 时建议保持 1.0/1.0）。URL：https://qdrant.tech/documentation/concepts/hybrid-queries/
- **OpenSearch**（2.19 引入）：每通道权重必须合计 1.0（文档示例：词法 0.3 / 向量 0.7）；其官方文档给出了一个**与本案数值完全同构的算例**（见 §4.2）。URL：https://docs.opensearch.org/latest/vector-search/ai-search/hybrid-search/rrf/
- **本案系统**：SuperMew 用的是 **Milvus `RRFRanker(k=60)`**（【实测·代码】`backend/indexing/milvus_client.py:441-488`，等权）；每通道各取 `candidate_k×2=30` 条进入融合、输出 15 条。Milvus 加权排序器（WeightedRanker）存在性仍未本次核验（pymilvus 源码路径 404；可用 milvus.io 文档人工确认）——**若要加权，应用层分通道融合可实现同样效果，不依赖该组件**。

### 4.2 k=60 的"第一名优势压缩"（本报告推导 + 官方同构算例）

**关键**：这不是本报告的孤例推导——**OpenSearch 官方文档给出了与本案数值完全同构的算例**（rank_constant=60）：

```
词法 #1 的文档：只有单通道贡献        → 1/(60+1) = 0.0164
双通道的文档（词法 #2 + 向量 #1）      → 1/(60+2) + 1/(60+1) = 0.0325   → 反超一倍
```
官方结论原文："**A document that appears near the top of several result lists receives a higher combined score than a document that appears at the top of only one list**"（https://docs.opensearch.org/latest/vector-search/ai-search/hybrid-search/rrf/ ）。Weaviate 官方示例同样演示"**关键词强的第一名被向量第一名在 rankedFusion 中挤到第二**"（https://docs.weaviate.io/weaviate/api/graphql/search-operators ）；Qdrant 官方原话更直白："**Assigning equal weight to both can let the weaker retriever drag down results.**"（另：BEIR 摘要"BM25 is a robust baseline"与 OpenSearch 关于"RRF 丢弃 score margin"的讨论亦为本次核验的同类佐证。）

映射到本案（示例数字，B 的稠密名次需 E1 实测）：

```
设 A=第十二/十三条块：稠密 #1 + BM25 #2 → 1/61 + 1/62 = 0.03252
设 B=第七条块：     BM25 #1 + 稠密 #20 → 1/61 + 1/80 = 0.02889 → B 输
若 B 掉出稠密通道 top-30（通道上限=candidate_k×2）→ B 只有 1/61 = 0.01639 → 输一倍
```

要压过"双通道高排名"的块，单通道第一名远远不够——**这正是"第七条"这种查询的致命结构**：词法证据集中在**一条**候选上（精确 token 极稀疏），而语义噪声弥散在**整个第二章**上（大量"消费者…权利"块）。**结论：对强结构化查询，要么给词法通道加权（Azure / OpenSearch / Weaviate 的 weights、Qdrant v1.17 加权 RRF、或应用层加权融合），要么根本不进 RRF——先走结构化过滤（§2.2 的三阶梯）。**

### 4.3 中文分词与"第七条"的匹配性

- Milvus 集合的 `text` 字段带 `enable_analyzer=True, analyzer_params={"type": "chinese"}, enable_match=True`（【实测·代码】`backend/indexing/milvus_client.py:285-292`）——即 **jieba 系中文 analyzer + TEXT_MATCH 能力**，但**代码从未用 TEXT_MATCH 检索过条号**（【实测·代码】全库 grep 仅剩 schema 定义）。
- "第七条"被切成什么 token 直接决定 BM25 的精确匹配强度；配合 §2.1 的"条号后有无分隔符不一致"，**不要假设"第X条"是稳定的词法单元**。
- **jieba 本地实测（PyPI 原包 0.42.1，R3 本次核验）**：`第七条` 在默认词典中**是单一 token**（词典词频 61/60,101,967）；整句切分为 `《/消费者/权益保护法/》/第七条/是/什么`；而 `第7条`（阿拉伯数字）被切为 `第/7/条`——**这是"查询侧数字归一化"的又一条硬理由**。IK 词典（main.dic）同样含"第七条"词条（推断 ik_smart 亦输出单 token）。
- **含义**：在 jieba 系 analyzer 下，`第七条` 具备成为**高区分度 BM25 token** 的条件——但有两个前提：（a）部署版 Milvus 的 chinese analyzer 行为与该词典一致（E6 用部署实例实测）；（b）文档侧不因分隔符/断字伪影破坏该 token（§2.1 的不一致与"个 人的"式伪影正是破坏源）。（E6 判读）

### 4.4 查询结构识别与路由（成熟做法）

- 行业既有模式：LlamaIndex **auto-retrieval / Router** 文档（`developers.llamaindex.ai`，本次核验可抓）；LangChain `SelfQueryRetriever` 的**现行官方页本次未找到**（R3 对 sitemap/llms.txt/Python 索引三级检索均无命中、旧 URL 已重定向——作为"该模式存在但文档在搬家"的如实记载）。概念本身即"把自然语言解析为'语义查询 + 元数据过滤'两部分"，是通行做法。【业界】
- 本报告建议（简单到能当天落地）：**正则优先、LLM 兜底**——
  1. 正则提取 `(法名?, 条号)`：`第[一二三四五六七八九十百千零〇0-9]+条`；法名从 `《…》` 或上下文取；中文数字→整数。
  2. 命中 → 走 §2.2 的三阶梯（filter → TEXT_MATCH → hybrid）。
  3. 未命中 → 现 hybrid 管线不变。
- 该模块的接口建议：在 `_finalize_retrieval` **之前**分流（`backend/rag/utils.py:659`），把"结构性命中"与"语义召回"的 trace 字段分开记（沿用现有 `rag_trace` 机制），保证面板可回放。

---

## 5. Reranker：jina-reranker-v3 核实结论、选型对照与输入构造

### 5.1 jina-reranker-v3 核实：**真实存在**（本次多源核验）

> 任务要求"如果查不到确证，如实写未找到确证"。**本次查到了确证**，来源均为本次实测抓取：

| 事实 | 值 | 来源（本次核验） |
|---|---|---|
| 模型存在 | `jinaai/jina-reranker-v3`（HF 模型仓，创建于 2025-09-18；下载 899,142、likes 146；含 `-GGUF`、`-mlx` 变体） | HuggingFace API（经 hf-mirror.com 镜像，`api/models/jinaai/jina-reranker-v3`，实测 200） |
| 论文 | 《jina-reranker-v3: Last but Not Late Interaction for Listwise Document Reranking》（Feng Wang, Yuqing Li, Han Xiao；v1 2025-09-29；v4 更新 2025-10-06） | arXiv 2509.25085（export.arxiv.org API 实测） |
| 架构/规格 | **0.6B 参数（596,836,352）多语 listwise 重排器**；底座 `Qwen/Qwen3-0.6B`（28 层 + MLP projector 1024→512→256）；"last-but-not-late" 交互：query 与全部候选在**同一上下文窗口**内做因果注意力 | HF 模型卡（经 hf-mirror 抓取其 README 原文）；arXiv 2509.25085 摘要（API 原文） |
| 上下文/吞吐 | **131K token 上下文，单次最多 64 篇文档** | HF 模型卡原文："processes up to 64 documents simultaneously within 131K token context" |
| 性能（自报） | BEIR nDCG@10 **61.94**；MIRACL 66.83；MKQA 67.92；CoIR 70.64；对比表中的 bge-reranker-v2-m3 为 56.51（MIRACL 69.32）、Qwen3-Reranker-0.6B 为 56.28、mxbai-rerank-large-v2 为 61.44 等 | HF 模型卡对比表（**Jina 自报数字，引用时注明**） |
| 许可 | **CC BY-NC 4.0（非商用）**；"If you need to use it … on-premises within your company, note that the model is licensed under CC BY-NC 4.0. For commercial usage inquiries, contact us"；模型已上架 AWS/Azure 市场 | HF 模型卡「License」节原文 |
| 部署 | 本地 `transformers`（`trust_remote_code=True`，`AutoModel`）；官方 API `POST https://api.jina.ai/v1/rerank`（`{"model": "jina-reranker-v3", "query", "documents", "return_documents": false}` → `{"results":[{"index","relevance_score"}]}`）；GGUF/MLX 量化版 | HF 模型卡「Usage/API」节；**该 API 响应格式与 SuperMew `HttpxRerankerAdapter` 的解析字段完全一致**（`backend/providers/rerank.py:237-258,337-376`）——即 SuperMew 面板中的 `jina-reranker-v3` 就是这套东西 |
| 后继版本 | **jina-reranker-v3.5**（2026-07-20，arXiv 2607.18152）：同为 0.6B listwise，混合注意力（3 滑窗层+2 全局层）+ 自蒸馏；BEIR 63.20（"matching a 4B model at roughly 7x fewer parameters"）；**论文原文："We release the model weights on Hugging Face under a non-commercial license"** | arXiv 2607.18152 摘要（API 实测）；HF 模型卡提示 "Prefer jina-reranker-v3.5 for new projects — drop-in upgrade, same API" |
| 旁证 | Bing 中国站可搜到：jina.ai 官方模型页 `jina.ai/zh-CN/models/jina-reranker-v3/`、知乎解读、ModelScope 镜像、CSDN（Jina_AI 官方号）、阿里云开发者社区文章 | 本次 cn.bing.com 抓取（链接列表见 `_web_evidence/bing_jina_v3.html`）；这些站点本身未直连 |

**对本案例的含义**：重排模型本身"没问题"（0.6B listwise、131K 上下文足以装下 SuperMew 的 30 条候选）。**能救它的前提是"第七条叶子在候选池里"**；如果不在，什么重排器都白搭——这是 E1 的判读价值。另一个待实测点是：v3 这类"每文档取末 token 嵌入"的 listwise 模型，对**近邻条号细粒度差异**的判别力（0.6B 规模），用 E4 的重放 + 对照即可给出本库上的经验答案。

### 5.2 2026 年 reranker 选型对照（尽量只列本次核验过的硬事实）

| 模型 | 类型/规模 | 上下文 | 许可 | 部署 | 中文/多语证据 | 证据等级 |
|---|---|---|---|---|---|---|
| **jina-reranker-v3** | listwise，0.6B | 131K，≤64 docs | CC BY-NC 4.0（商用需授权） | transformers / Jina API / GGUF/MLX | 多语（MIRACL 66.83 自报） | 本次核验（§5.1） |
| **jina-reranker-v3.5** | listwise，0.6B | 同族 | 非商用（论文原文） | 同 v3（同 API） | 多语 + 多域（含法律训练混合） | 本次核验 |
| **Qwen3-Reranker-0.6B / 4B / 8B** | instruction-aware（自定义任务指令，`<Instruct>/<Query>/<Document>` 模板；示例 max_length=8192） | **32K**（README 表） | **Apache-2.0**（本次核验） | transformers / vLLM 等 | 多语；MTEB-multilingual 重排：0.6B 65.80、4B 69.76、8B 69.02（Qwen 自报）；官方 prompt 模板与 MTEB-R/CMTEB-R 分数表在仓库 | 本次核验（QwenLM/Qwen3-Embedding README，raw.githubusercontent 实测） |
| **bge-reranker-v2-m3** | cross-encoder，0.6B | **config 支持 8192；官方示例/FlagEmbedding 默认 512 截断**（本次核验） | **Apache-2.0**（本次核验） | FlagEmbedding / TEI 等 | 多语；"lightweight cross-encoder, strong multilingual, easy to deploy" | 本次核验（FlagOpen/FlagEmbedding README，raw 实测；同上含 v2-gemma / v2-minicpm-layerwise / v2.5-gemma2-lightweight 全家桶；model card 由 R4 核验） |
| **Cohere Rerank 4.0 / 3.5** | API（`rerank-v4.0-pro`、`rerank-v4.0-fast`、`rerank-v3.5`——模型 ID 已核验；**4.0 于 2025-12-11 发布**） | **32K（4.0，为前代 4 倍）；`max_tokens_per_doc` 默认 4096、超长自动截断**（本次核验） | 商业 API | API | 官方称多语 | 本次核验（docs.cohere.com/docs/rerank-overview 与 cohere.com/blog/rerank-4、reference/rerank，R4 实抓）；价格未核验 |
| **Voyage rerank-3 / rerank-3-lite（当前）、rerank-2.5 系（前代）** | API；支持 query 附加指令（"optional instructions can be appended or prepended to the query"） | **32,000 token**（文档上限 16K/8K 按档） | 商业 API；**$0.05/$0.02 每百万 token，前 200M token 免费** | API | 官方多语 | 本次核验（docs.voyageai.com/docs/reranker 实测抓取——页面同列 rerank-3 与 rerank-2.5，本机复核；定价 R4 核验） |
| mxbai-rerank-base-v2 / large-v2 | cross-encoder 0.5B / 1.5B | **默认 8k（32k-compatible）** | **Apache-2.0** | 自部署 | 官方多语；**中文 NDCG 84.16**（官方）；BEIR 58.40 / 61.44（Jina 对比表，第三方） | 本次核验（R4；model card） |
| ColBERT / late-interaction | 多向量延迟交互 | — | — | — | 概念对照：v3 论文明确对比（"Unlike late interaction models like ColBERT…"） | 本次核验（论文摘要原文） |
| LLM listwise（RankGPT / RankZephyr 等） | LLM 做重排 | — | — | 以 API/大模型为主 | RankGPT=arXiv 2304.09542、RankZephyr=arXiv 2312.02724（标题本次核验） | 本次核验（R4，export.arxiv.org） |

**推荐（针对 SuperMew 的"法律条号一致性"场景）**：

1. **关键认知：先修输入与结构，再谈换模型。** 本案重排失败（如果确实在候选池里也失败）最可能的修补是**文档构造**（§5.3），不是换更大的模型。
2. 自部署优先：**Qwen3-Reranker-0.6B/4B**（instruction-aware 是刚需——可以写"注意判断文档是否为所询问的**第 N 条**法条"这类任务指令；官方建议自定义指令可带来 1–5% 提升【本次核验，README 原文】），或沿用/升级 bge-reranker-v2-m3（成熟、生态好）。
3. API 现状：系统已接 Jina-compatible 端点且在用 v3——**保持不动也合理**（新项目可切 v3.5，同为 0.6B、同 API、更强，注意权重许可仍是非商用；API 服务条款另行确认）。需要商用条款清晰、又想要 32K 上下文时，Cohere Rerank 4.0（2025-12-11 发布）与 Voyage rerank-3 是本次核验过的选项（上下文/截断/定价见上表）。
4. 无论选谁：**条号命中率（§7）必须作为选型对照实验的主指标**，而不是 BEIR 分数。

### 5.3 Rerank 输入构造（本案的重灾区）与 `rrf_rank` 语义澄清

**先说一个容易被面板误导的事实（重要）**：【实测·代码】`backend/rag/reranking.py:252-263`，重排阶段**只是把"到达重排时的输入顺序"编号为 `rrf_rank`**（`_with_recall_rank`），面板上的"RRF 名次 #1"**不保证等于 Milvus 融合排名第 1**——它是"**经过去重 + auto-merge 之后的精排输入序号**"。也就是说：这个 #1 已经**进了合并结果的帐**。如果 #1 是合并出来的父块，那它在 Milvus 端可能是来自某个子块的排位。**另一个细节**：模板写的是 `chunk.rrf_rank || sIndex + 1`（`frontend/src/components/Chat/RetrievalTraceDetails.vue:282,315`）——**当 `rrf_rank` 缺省时，面板直接显示列表位置**；即该数字在缺省场景下连"输入序号"都不是，纯粹是展示位。**诊断时不要把它当作融合排名的物证。**

**现状输入（全部实测）**：
- `documents` 载荷 = 每条候选的 `text`，**截断到 8000 字符**、总量 60000 字符、最多 30 条（`reranking.py:156-190`；`backend/core/settings.py:209-224`）；
- **不含 filename、页码、条号、章节路径等任何元数据**；query 是原文（本 case：整个问句）。
- 失败时回退到"重排前顺序"并在 trace 记录 `rerank_fallback_applied`（`reranking.py:125-137`）——面板本次显示"已执行=是"，即**没有回退**，这次排序是模型真实输出。

**建议的输入构造（格式模板）**：

```
Query（保持原句 + 结构化追加）：
  《消费者权益保护法》第七条是什么
  检索要点：法律=中华人民共和国消费者权益保护法；条款=第七条

Document（重排文本 = 结构头 + 正文）：
  [文件] 中华人民共和国消费者权益保护法.pdf 第2页
  [定位] 《消费者权益保护法》 第二章 消费者的权利 第七条
  [正文] 第七条 消费者在购买、使用商品和接受服务时享有人身、财产安全不受损害的权利。……
```
理由：
- Anthropic 的 Contextual Retrieval 实验（本次核验 cookbook）把"给块补上下文"用于**索引与检索**，pass@5 排名提升；重排是同一动机的末段应用。
- Voyage 官方支持"指令附加到 query"（本次核验）；Qwen3-Reranker 官方支持 instruction 模板（本次核验）——**当模型可接受指令时，把"必须核对条号"写进指令**。
- 只给正文的风险（本案实锤）：条号出现在正文中段/尾段时，被 8000 字符截断或长父块稀释后就等于消失；结构头保证**条号永远在文档文本的最前面 20 个字符内**。
- 另注意：8000 字符/文档的上限在"按条切分"后会**天然满足**（一条通常 <1000 字），这又是一项按条切分带来的联动收益。

---

## 6. 为什么"相关性评分 yes"会误判：判官缺陷、细粒度判定与查询分解

### 6.1 现场还原：这个 yes 是"按设计工作"的

【实测·代码】`backend/rag/pipeline.py:97-115` 的判官提示词原文（节选）：

> "你是 RAG 证据评分器。请只根据检索片段判断它们是否足以回答用户问题……"
> "- relevance: none 表示**主题不相关**；weak 表示**主题接近**但证据弱；strong 表示**主题明确相关**。"
> "- answerability: none 表示不能回答；partial 表示有部分线索但不足以给确定答案；sufficient 表示片段能直接或组合支撑答案。"
> "- route……answer: relevance=strong 且 answerability=sufficient。"

- `relevance` 的定义**三次都锚在"主题"上**——片段是消保法正文（同主题）→ 模型给 `strong` 几乎是必然。
- `answerability` 要求判断"能否支撑答案"，但**没有任何提示**要求核对"你被问的是第几条、片段讲的是第几条"。LLM 在"同一部法律的内容丰富、语气笃定"时倾向判 sufficient。
- 路由条件（`pipeline.py:583-587`）：`answer_is_supported = (relevance=="strong" and answerability=="sufficient")` → `route=answer`。**全流程唯一的"客观事实校验点"就是这段 LLM 判断本身**——没有条号、没有引用、没有确定性规则。
- 雪上加霜：判官看到的文本被截（1200 字符/文档、4800 总量，`settings.py:86-95`），且格式为 `filename (Page N): 正文`（`evidence.py:53-69`）——有利于判"主题"，不利于判"条号"。

### 6.2 业界怎么做"防主题级误判"

- **CRAG（Corrective RAG，arXiv 2401.15884《Corrective Retrieval Augmented Generation》，标题本次核验）**：其 retrieval evaluator 明确区分 **correct / incorrect / ambiguous** 三态，并用**细粒度拆条（knowledge refinement，把文档切成 strips 再逐条判信度）**——思路就是"不要对整篇文档做一个粗判断"。
- **Self-RAG（arXiv 2310.11511，标题本次核验）**：用 `ISREL`（检索内容是否相关）与 `ISSUP`（生成内容是否被支持）等**分项判定 token**，同样反对"一个总分决定一切"。
- **外部锚**：斯坦福法律 AI 评测《Hallucination-Free? Assessing the Reliability of Leading AI Legal Research Tools》（arXiv 2405.20362，标题本次核验）系统检查了法律工具回复中**引用的可靠性**——法律场景下"引用对不对"是与"有没有回答"并列的一等指标。
- 【未核验】RAGAS 的 context relevance 指标定义与已知偏差讨论（docs.ragas.io 本次域名受限未抓取）。

### 6.3 落地方案：把判官从"主题判定"升级为"属性核验"（本报告设计，锚定 CRAG 思路）

**第一层：确定性规则（零 LLM，先于判官）**
```python
# 若问题可解析出 (law, article_no)：
if required_article is not None:
    doc_articles = extract_articles(doc.text)          # 正则抽取片段中出现的所有“第X条”
    article_match = required_article in doc_articles   # 结构化字段/正则均可
    if not article_match:
        route = "rewrite_with_filter"   # 不进入 answer；带 article_no 过滤重检索（§2.2 三阶梯）
```
**第二层：判官 schema 扩展**（在现有 `EvidenceGrade` 上加字段，保持 Pydantic 约束的风格一致）：
```python
class EvidenceGradeV2(EvidenceGrade):
    doc_article_numbers: list[int] = []   # 判官必须先“逐文档报出它讲的是第几条”
    required_article: int | None = None   # 从问题解析
    article_match: bool = False           # 上一项的核对结论
```
并把提示词改为**先抽取、后判定**："请先列出每段文字实际讨论的条号；若问题询问特定条号而任何片段都不包含该条，`answerability` 不得高于 `partial`。"（claim-first / quote-first 判定是通行做法，【未核验】RAGAS、Prometheus-2 等出处。）
**第三层：uncertain 处理**：`partial` 应触发 **rewrite（带结构过滤）而非 clarify**——对"条号已知但没检到"的情况，用户补充信息的价值低，系统自己二次检索的价值高（现逻辑 `pipeline.py:595-606` 会把"重写用尽后的 partial"降级为 clarify，对这类 case 是错误分流）。

### 6.4 查询分解：把"第七条"解析成"过滤条件 + 语义查询"

- 正则 + 中文数字归一（§4.4 已给规则）；LLM 结构化解析作为兜底（输出 `{law_name, article_no, paragraph_no?, semantic_query}`）。
- 产物有三用：**① 结构化过滤**（§2.2）；**② 判官核验**（§6.3）；**③ 评估集自动标注**（§7.3 的"问题→条号"gold）。
- 注意 SuperMew 现有的两类重写（`step_back` / `hyde`，【实测·代码】`backend/rag/utils.py:467-562`）都**不解决结构问题**：HyDE 生成的"假设答案文档"对"第七条是什么"只会更像"一般消费者权利综述"，反而强化主题噪声。**结构查询不该走语义重写，应走结构过滤**。

---

## 7. 检索评估集设计（golden set + 条号命中率）

SuperMew 已有一套**完成度很高**的评测契约（【实测·代码】`evals/rag/`：Dataset（含 `gold_chunks`〔`chunk_id` + `content_sha256`〕、`expected.route/outcome`、`critical` 标记）、Observation（脱敏投影）、GatePolicy（`recall_at_10/mrr_at_10/ndcg_at_10 ≥ 0.95`、`gold_chunk_coverage ≥ 0.95`、`case_pass_rate = 1.0`、critical 零容忍、最大回归容忍）、baseline 逐字节可重建；`evals/rag/README.md` 明示目标"至少 200 条人工标注 case"）。**它缺的不是框架，而是——恰好——"条号类"的 case。第七条这个 case 如果进了库里，`recall@10` 会掉、门禁会红。** 本节给出扩展方案。另外一个顺手可收的收益：SuperMew 的评测里 `parent_expansion_precision` **恒为 None**，原因字符串写明 "retrieved parent chunks do not yet expose stable child lineage"（`backend/evaluation/rag.py` 的 `_UNAVAILABLE_METRICS`，本次核验）——**按条切分并保留 child→parent 血统后，这个"当前算不了的指标"就变为可算**（父块命中中有多少包含预期子条，即"合并有没有把正确的那一条带进来"）。

### 7.1 新增指标定义（补齐现有 14 项门禁之外的"条号硬指标"）

| 指标 | 定义 | 实现口径（零 LLM、零人工） |
|---|---|---|
| **条号命中率 ClauseHit@k** | `ClauseHit@k = (1/Q) · Σ_q 1[ gold_clause_ids_q ∩ retrieved_clause_ids(top-k) ≠ ∅ ]` | `retrieved_clause_ids`：对 top-k chunk 抽取条号——**按条切分后直接读元数据 `article_no`**；过渡期可用正则从文本抽（`第[一二三四五六七八九十百零〇0-9]+条`）。一个块含多条 → 全部展开；多条命中 → 记一次 |
| ClauseHitStrict@k | 子条级（"第十二条第 3 款"）命中才算 | 同上，正则加款号 |
| DocHit@k（对照） | 文档级命中（现有 `document_recall_at_10` 即此） | 现成 |
| MRR / nDCG@k | `MRR = (1/Q) Σ 1/rank_q`（首个命中条号块的名次）；`nDCG@k = DCG@k/IDCG@k`，`DCG = Σ (2^rel_i − 1)/log2(i+1)`，rel：2=条号精确命中、1=同文档相邻条、0=其他 | 现成 `_score_case` 按 k∈{1,3,5,10} 已算 recall/mrr/ndcg——**只需把 gold 从"任意 chunk"细化到"条号"** |
| 表述级 | **rerank 前/后各报一组** ClauseHit@k（SuperMew 的 `evals` 有 live adapter 可同时抓两段） | 验证 P2/P4 |

> 现有文档级指标是"软底线"，条号级是"硬指标"：**DocHit 命中而 ClauseHit 未命中 ⇒ 切分/排序/合并问题；DocHit 都没中 ⇒ 召回问题**。这一对数字把 §1 的 P1–P4 直接分流。

### 7.2 新增 case 模板（直接扩进 `rag_smoke_v1.json` 的同构 schema）

```jsonc
{
  "id": "clause_lookup_consumer_law_art7",
  "tags": ["clause_lookup", "near_miss_clause", "legal"],
  "critical": true,
  "question": "《消费者权益保护法》第七条是什么？",
  "expected": {"complexity": "simple", "route": "answer", "outcome": "ANSWERABLE", "hitl": "none", "acceptable_abstention": false},
  "gold_documents": [{"canonical_name": "中华人民共和国消费者权益保护法.pdf"}],
  "gold_chunks": [{"chunk_id": "…::p2::l3::N", "content_sha256": "…"}],   // 改造后=“第七条”叶子
  "gold_clauses": [{"law": "中华人民共和国消费者权益保护法", "article_no": 7}], // 新增字段（建议）
  "reference_answer": "消费者在购买、使用商品和接受服务时享有人身、财产安全不受损害的权利。…",
  "required_claims": ["人身、财产安全不受损害"],
  "near_miss": [ {"article_no": 12, "why": "同章相邻条，本次失败中被误召为 #1"} ],   // 新增（建议）
  "conflicts": [], "hitl_answers": []
}
```
（现 schema 用 `additionalProperties: false` 拒绝未知字段——新增 `gold_clauses`/`near_miss` 需同步改 `dataset_v1.schema.json` 与生成器 `--check`。）

### 7.3 golden set 构建（无标注自举，法条场景特化）

1. **模板通道（天然带 gt，最硬的样本）**：抽样"条"叶子 → 模板问句三变体：`《X法》第N条是什么？` / `《X法》第N条规定了什么？` / `《X法》第N条关于Y吗？`。gt 直接从元数据继承，零 LLM 零人工。
2. **LLM 通道（模拟真实问法）**：给 LLM 看条文 → 生成口语化问题（禁抄原词、禁出现"第N条"字样——这半边测语义检索）；另生成一半**含条号**的问题（测结构化路径）。
3. **Near-miss 负难例（本 case 的同款陷阱）**：对每个"第N条"问题，自动配 1 个"第N±1..±5 条"作为 near-miss，验证系统不把相邻条当答案。**这正是"第七条 → 第十二条"的自动化版本。**
4. **防泄漏三规则**（自举评估集的核心）：round-trip（只给该条让另一个 LLM 回答，答不上则弃）、反常识（不给任何文档能答对则弃——说明靠世界知识）、相似去重（与它块嵌入相似度 >0.95 的重复问题丢弃）。
5. **人检**：随机抽 30 条查"问题可答 + gt 条号正确"，有效率 ≥90% 冻结为 `golden_clause_v1`；留 20 条 holdout。
6. **规模**：法条类 100–200 条起步（覆盖：精确条号、内容描述、跨条引用、版本变化、同法近条 near-miss）；报告小样本一律带 Wilson 区间。

### 7.4 挂接 CI 与回归

- 沿用其现成机制：`scripts/evaluate_rag.py` + `rag-eval.yml`（paths 已覆盖 `backend/rag/**`、`evals/rag/**`）；把新指标加入 `gates_v1.json`（建议：`clause_hit_at_5 ≥ 0.95`、`clause_hit_at_10 ≥ 0.98`，`critical: true` 的条号 case 零容忍回归）。
- **先把本案登记为回归用例再修**（TDD 顺序）：`clause_lookup_consumer_law_art7`（§7.2）现在跑必红——这就把"用户偶遇的一次失败"变成"永久防回归资产"。

### 7.5 一次跑全链的评估命令蓝图（沿用其 CLI 风格）

```bash
uv run python scripts/evaluate_rag.py run \
  --dataset evals/rag/golden_clause_v1.json \
  --gates evals/rag/clause_gates_v1.json \
  --observations .artifacts/rag-eval/live-clause-obs.json \
  --report .artifacts/rag-eval/clause-report.json \
  --profile-id local-consumer-law-v1 --index-id <collection-version>
# 报告须含：clause_hit@5/10（rerank 前/后）、DocHit 对照、near_miss 误召率
```

---

## 8. 最小验证实验清单（每条可独立执行；预计单条 ≤30 分钟）

> 通用前提：在部署同环境跑；Milvus/Postgres/Redis 可用；`RERANK_*` env 保持与截图一致。所有脚本建议放 `scripts/diagnose/`。

| 实验 | 目标 | 步骤要点 | 预期判读 |
|---|---|---|---|
| **E1 分通道排名 dump** | 判定 P2：三条通道里"第七条叶子"的真实名次 | 用 `MilvusStore.hybrid_retrieve / dense_retrieve` +（自定义）`sparse-only` 查询（`AnnSearchRequest` 仅 sparse）各取 top-30；对结果逐条打印 `chunk_id/text[:80]/chunk_level/parent_chunk_id`；标出含"第七条"的块 | 若 BM25 通道 #≤3 而融合后名次很差 ⇒ RRF 融合/加权问题（P2 实锤）；若 BM25 通道也找不到 ⇒ 分词或库内缺失（转 E2/E6） |
| **E2 语料审计** | 判定 P1：条号是否被切成孤立单元 | 对集合跑 `TEXT_MATCH(text, '第七条')`（schema 已 `enable_match`）+ 正则扫描 `第七条`；打印命中块全文、页、层级、父链；再对全 PDF 列出所有"第X条"所在块分布 | 若"第七条"与"第十二条"在同块 ⇒ 切分粒度问题（P1）；若在不同块 ⇒ 记录两块各自的父块是否相同（进入 E3） |
| **E3 合并重放** | 判定 P3：合并是否吞掉了正确块 | 取 E1 的融合 top-15，直接调用 `_auto_merge_candidates`（注意 monkeypatch `_parent_chunk_store`）；对比合并前后列表：被替换的 4 个子块是谁？合并后 #1 是否为父块（文本是否以"第十二条…"开头）？ | 若"第七条叶子"在被替换集合里或被挤出前段 ⇒ P3 实锤（对策：阈值调比例/改合并策略） |
| **E4 Rerank 重放** | 判定 P4：重排对条号的判别力 + 输入构造效果 | 录制原请求体（query + 候选 text 数组）重放得基线分；对照组：每条文档加"结构头"（§5.3 模板）重放；再对照：把 gold 块（E2 找到的）人为塞进候选重放 | 若加结构头后 gold 排名显著前移 ⇒ 输入构造是主修复项；若 gold 在候选内仍是低分 ⇒ 换/升级重排器（Qwen3-Reranker + 指令） |
| **E5 判官重放** | 判定 P5 + 验证修补 | 用现 prompt + 本次实况文档（截 1200/4800 规则）重放 → 记录 grade；换"先抽取条号再判定"的 prompt 重放 → 记录变化；用 §6.3 规则层做确定性拦截看 route 是否变为二次检索 | 预期：原样复现 strong/sufficient；新 prompt 翻转。若模型连"抽取条号"都做不对 ⇒ 先上确定性规则层（不依赖 LLM） |
| **E6 分词检查** | 判定 P6 + 给 BM25 修正提供依据 | 两种方式：(a) Milvus 端 analyzer 探测（对 `text` 字段跑 analyzer/近义接口，或写入测试文本后观察 sparse 命中的 token）；(b) 本地 jieba 对 `第七条` / `《消费者权益保护法》第七条是什么` / `第七条消费者…`（无空格变体）分词 | 若"第七条"被拆开 ⇒ 建自定义词典/改为 n-gram 兜底；同时把文档侧"第X条"格式归一化（R1 已证官方文本分隔符不一致） |
| **E7 评估集回归** | 把结论固定资产化 | 按 §7.2 建 case（gold=第七条叶子）→ 旧管线跑一遍（应红）→ 依次施加修复（结构过滤 / 合并策略 / 输入构造 / 判官升级）各跑一遍 | 谁点亮指标谁就是主修复；全部点亮后本案从"偶发事故"变为"被门禁锁死" |

---

## 9. 未确证清单与来源索引

### 9.1 未确证清单（本次无法确证或存在版本差异，实施前需人工点验）

1. **面板字面值「扩展查询」**：main 分支 `RetrievalTraceDetails.vue` 无此字段（最接近为「重写检索查询」）。可能是用户部署版本较新或转录近义——**不影响本诊断主体**。
2. **面板字面值 `generate_answer`**：main 分支 route 枚举为 `answer`（`pipeline.py:130`）。语义一致，字面差异同上。
3. **候选召回数 15 的来源**：`candidate_k = env(RETRIEVAL_CANDIDATE_K)` 或 `top_k × RETRIEVAL_CANDIDATE_MULTIPLIER`（默认 8×3=24；`.env.example` 给 30）。15 ⇔ 或 `top_k=5×3`，或 `RETRIEVAL_CANDIDATE_K=15`。**需查部署 env**（面板若显示"（top_k × 3）"即为前者）。
4. **Milvus WeightedRanker 与官方文档页**：pymilvus 源码抓取 404（path 变更）；`milvus.io` 域名本次受限。URL 供人工核验：https://milvus.io/docs/multi-vector-search.md （替代路径：应用层分通道加权融合，无需依赖该组件）。
5. ~~**bge-reranker-v2-m3 的上下文长度与许可证**~~：**已核验关闭**——Apache-2.0；config 支持 8192，官方示例/FlagEmbedding 默认 512 截断（R4 本次核验，HF model card）。
6. ~~**Cohere Rerank 4.0/3.5 的上下文与价格**~~：**上下文已核验关闭**——4.0 为 32K、`max_tokens_per_doc` 默认 4096 超长自动截断（R4 本次核验）；**价格仍未核验**。
7. **国内产品**：公开能力口径已核验（§2.4：华宇元典"法条级语义检索……法条编号"、通义法睿、北大法宝"同篇/同条"、法信按条定位等），但**算法级工程细节全部未公开**；COLIEE 官网已核验（§9.2 #46）。
8. **jina-reranker-v3 API 的商用条款与价格**：模型**权重**许可（CC BY-NC 4.0）已核验；API 服务条款未核验。
9. **E1/E4 里的示例打分**：§4.2 的数值是推导示例，不是实测；用 E1/E4 实测替换。
10. **Route `insufficient_evidence`**：`outcomes.py:76-77` 引用该字符串，但 `EvidenceGrade.route` 枚举无此值——路由与 outcome 映射存在轻微命名不一致（【实测·代码】），修复时顺带澄清。
11. **Pinecone「small-to-big」原文、RAPTOR 论文正文细节（如 collapsed tree）**：未找到确证（域名阻断；RAPTOR 仅核验了 README 与 arXiv 标题）。
12. **「条叶章父」的直接公开推荐**：未找到确证（§2.4 已列最近邻仓库清单）。
13. **阿拉伯数字条号（"第7条"）在官方文本中的实例**：未找到确证（官方语料全部为汉字数字；"第7条→第七条"的查询侧归一化属**推断性必要设计**）。
14. **中文社区切分效果自述数字**（62%→81% 等）：未经独立验证（公开博客自述，详见 §2.4）。

### 9.2 来源索引（本次核验方式：curl → 通过 `--ssl-no-revoke`；HF 经 hf-mirror.com 镜像；arXiv 经 export.arxiv.org API）

| # | 来源 | URL / 位置 | 用途 | 核验 |
|---|---|---|---|---|
| 1 | SuperMew 全仓库 | https://github.com/icey1287/SuperMew （tarball 经 codeload 下载 → `supermew_full/`） | 全部【实测·代码】引用 | ✅ 本次 |
| 2 | 消保法原文（人大网） | http://www.npc.gov.cn/c1773/c1848/c21114/c21170/c21174/201905/t20190521_210511.html | 第七/十二/十三条原文 + 分隔符不一致 | ✅ 本次（subagent 实测 200） |
| 3 | 国家法律法规数据库 | https://flk.npc.gov.cn/detail?id=2c909fdd678bf17901678bf7670606ef&title=中华人民共和国消费者权益保护法 | 权威条目 | ✅ 本次（subagent） |
| 4 | 立法法结构规范（PDF） | https://fgw.sh.gov.cn/cmsres/17/178b6e73e5684d1db5662b7aa7efc161/a7bd995c7df73639a986a41bc08e5395.pdf | 编章节条款项目 | ✅ 本次（subagent） |
| 5 | Elastic RRF 文档 | https://www.elastic.co/guide/en/elasticsearch/reference/current/rrf.html | rank_constant=60、等权、influence 语义 | ✅ 本次（抓取原文引用） |
| 6 | LlamaIndex AutoMergingRetriever 源码 | https://raw.githubusercontent.com/run-llama/llama_index/main/llama-index-core/llama_index/core/retrievers/auto_merging_retriever.py | ratio>0.5、fill-in、迭代、重排序 | ✅ 本次 |
| 7 | LlamaIndex HierarchicalNodeParser 源码 | https://raw.githubusercontent.com/run-llama/llama_index/main/llama-index-core/llama_index/core/node_parser/relational/hierarchical.py | 默认 [2048,512,128] | ✅ 本次 |
| 8 | jina-reranker-v3 模型卡 | https://huggingface.co/jinaai/jina-reranker-v3 （本次经 hf-mirror.com 抓取 README/API） | 0.6B/131K/64docs/CC-BY-NC-4.0/API 格式/对比表 | ✅ 本次（镜像） |
| 9 | jina-reranker-v3 论文 | https://arxiv.org/abs/2509.25085 | listwise/LBNL/61.94 | ✅ 本次（API） |
| 10 | jina-reranker-v3.5 论文 | https://arxiv.org/abs/2607.18152 | 后继版本/63.20/非商用 | ✅ 本次（API） |
| 11 | Qwen3-Embedding/Reranker README | https://github.com/QwenLM/Qwen3-Embedding | 0.6B/4B/8B、32K、instruct、MTEB 数字 | ✅ 本次（raw） |
| 12 | FlagEmbedding README | https://github.com/FlagOpen/FlagEmbedding | bge-reranker 全家桶描述 | ✅ 本次（raw） |
| 13 | Cohere Rerank 文档 | https://docs.cohere.com/docs/rerank-overview | v4.0-pro/fast、v3.5 存在性 | ✅ 本次（抓取） |
| 14 | Voyage Rerank 文档 | https://docs.voyageai.com/docs/reranker | rerank-2.5、32K、指令支持 | ✅ 本次（抓取） |
| 15 | Anthropic Contextual Retrieval | https://github.com/anthropics/anthropic-cookbook/blob/main/capabilities/contextual-embeddings/guide.ipynb · 官方博客 https://www.anthropic.com/engineering/contextual-retrieval | 块加上下文、Pass@5 结论、失败率 -35%/-49%/-67% | ✅ 本次（raw + subagent） |
| 16 | CRAG | https://arxiv.org/abs/2401.15884 | 三态 evaluator / knowledge strips | ✅ 标题本次；机制转述 |
| 17 | Self-RAG | https://arxiv.org/abs/2310.11511 | ISREL/ISSUP | ✅ 标题本次；机制转述 |
| 18 | 斯坦福法律 AI 评测 | https://arxiv.org/abs/2405.20362 | 引用可靠性 | ✅ 标题本次 |
| 19 | RAPTOR | https://arxiv.org/abs/2401.18059 | 树检索 | ✅ 标题本次 |
| 20 | BSARD / Finding the Law / QABISAR / CuSINeS | arXiv 2108.11792 / 2301.12847 / 2412.00934 / 2404.00590 | 法条检索任务谱系 | ✅ 标题本次（API） |
| 21 | LeCaRDv2 / BM25 法域强基线 | arXiv 2310.17609 / 2105.05686 | 相邻域/词法依据 | ✅ 标题本次（API） |
| 22 | DISC-LawLLM README | https://github.com/FudanDISC/DISC-LawLLM | 法条库检索模块 | ✅ 本次（raw） |
| 23 | LaWGPT README | https://github.com/pengxiao-song/LaWGPT | 结构化法律知识 | ✅ 本次（raw） |
| 24 | RRF 原始论文 | https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf | 公式、k=60 由来、"not critical" | ✅ 本次（subagent，PDF 全文提取） |
| 25 | Milvus 多向量文档 | https://milvus.io/docs/multi-vector-search.md | hybrid/RRF/加权器 | ❌ 未核验（域名受限） |
| 26 | 结构化查询模式 | LlamaIndex auto-retrieval / Router（developers.llamaindex.ai，✅本次）；LangChain SelfQueryRetriever 现行页 ⚠️未找到（R3 三级检索无命中） | 结构化查询模式 | ✅/⚠️ |
| 27 | 兄弟文档 | `014_agent-metrics-spec.md`（指标/判官/golden set 总规）、`012_reference-rag-architecture.md`（架构与评测体系全解）、`008_rag-never-indexed.md`（**DocMind** 的活体探测：其生产索引为空是另一套系统的独立发现，与本文 SuperMew 案例互不冲突，且同样指向"条号不可过滤"的前提问题） | 本报告与 A3 的分工：A3 管"指标与流程"，A4（本文）管"这次失败的因果链与法条特化评估方案" | ✅ 本目录 |
| 28 | 立法技术规范（试行）(一)(二) | https://mp.weixin.qq.com/s?__biz=MzUyNjUxMzQ2OA==&mid=2247486401&idx=1&sn=e937213b250400d95eede0ad090d6b9c · https://mp.weixin.qq.com/s?__biz=Mzk0NjQxMDA2OA==&mid=2247491773&idx=3&sn=14a0eaab5cfca155cd7cb85bc50a86af | 引用书写/数字使用规则 | ✅ 本次（subagent） |
| 29 | 刑法官方 PDF（"第X条之一"实例 + 断字伪影） | https://fgw.sh.gov.cn/cmsres/93/93f47dfc6d8b4db3ba3d57a33a157e1a/d34c3ee959e2f6440f08e578d72cfe23.pdf | 条号变体与文本噪声 | ✅ 本次（subagent） |
| 30 | LegalBench-RAG | https://arxiv.org/abs/2408.10343 | "最小充分片段"论据 | ✅ 本次（subagent） |
| 31 | STARD（中文法条检索） | https://arxiv.org/abs/2406.15313 · https://github.com/oneal2000/STARD | 中文 SAR 数据集/任务 | ✅ 本次（subagent） |
| 32 | LegalMALR（中文法条检索） | https://arxiv.org/abs/2601.17692 | 中文 SAR（改写+重排） | ✅ 本次（subagent） |
| 33 | DISC-LawLLM 论文 / 智海-录问 | https://arxiv.org/abs/2309.11325 · https://arxiv.org/abs/2604.06737 | 中文法律 LLM 检索模块 | ✅ 本次（subagent） |
| 34 | COLIEE Task 3（参赛队论文转述） | https://arxiv.org/abs/2607.16603 | 法条检索竞赛任务 | ⚠️ 官网未核验 |
| 35 | CRAwLeR / RefWalk（引用感知检索） | https://arxiv.org/abs/2606.21676 · https://arxiv.org/abs/2605.29742 （+ https://github.com/yeongjoonJu/RefWalk） | 互引图/引用闭环前沿 | ✅ 本次（subagent） |
| 36 | 国内产品公开页（口径） | https://open.chineselaw.com/ · https://help.aliyun.com/zh/model-studio/user-guide/tongyi-farui/ · https://www.pkulaw.com/ · https://www.faxin.cn/v2/ftsy/detail.html?gid=A246789&tiao=2 · https://meta.law/ · https://www.cls.cn/detail/1389428 | 法条级检索能力口径 | ✅ 本次（subagent 抽查） |
| 37 | LlamaIndex 官方 notebook（Auto-merging） | https://raw.githubusercontent.com/run-llama/llama_index/main/docs/examples/retrievers/auto_merging_retriever.ipynb | "beyond a given threshold"；官方结论 "The results are roughly the same." | ✅ 本次（subagent） |
| 38 | llama_index issue #23224 | https://github.com/run-llama/llama_index/issues/23224 | 层级切分 NEXT 关系缺失（open） | ✅ 本次（subagent） |
| 39 | LangChain ParentDocumentRetriever 源码 | PyPI `langchain-classic` 1.0.8（wheel 解包）· https://pypi.org/pypi/langchain-classic/json | "small chunks → parent documents"，无阈值 | ✅ 本次（subagent） |
| 40 | 真实工程旁证（父块过大/精排对象） | https://github.com/langchain4j/langchain4j/pull/6132 · https://github.com/DoanCongPho/mrson_rag/pull/5 · https://github.com/vstorm-co/agenticos/issues/1651 | "小片段精排、prompt 前再扩展" | ✅ 本次（subagent） |
| 41 | RAPTOR README | https://raw.githubusercontent.com/parthsarthi03/raptor/master/README.md | 递归树结构 | ✅ 本次（subagent） |
| 42 | 法律结构切分仓库（最近邻） | zeroentropy-ai/legalbenchrag · Snehashish-Das/Legal-GraphRAG · mralaminahamed/bd-legal-rag · PKU-YuanGroup/ChatLaw · IAAR-Shanghai/Meta-Chunking（均 GitHub） | 结构感知切分公开实践 | ✅ 本次（subagent，README） |
| 43 | SuperMew 自身 issue 讨论 | https://github.com/icey1287/SuperMew/issues/8 （用户贴代码问 threshold 语义）· /issues/1 （三级分块召回效果）· /issues/9 | 参数语义的可解释性问题 | ✅ 本次（subagent，API） |
| 44 | Meta-Chunking（逻辑感知切分） | https://github.com/IAAR-Shanghai/Meta-Chunking （论文 arXiv:2410.12788，README 本次核验） | "逻辑连贯块"与按结构切分同向 | ✅ 本次（subagent，README） |
| 45 | LlamaIndex RetrieverQueryEngine（顺序） | https://raw.githubusercontent.com/run-llama/llama_index/main/llama-index-core/llama_index/core/query_engine/retriever_query_engine.py | retrieve → postprocessors（rerank 评合并后节点） | ✅ 本次（subagent） |
| 46 | COLIEE Task 3 官网 | https://coliee.org/COLIEE2025/tasks/task3 · https://coliee.org/COLIEE2025/overview | Statute Law Retrieval 任务原文 | ✅ 本次（subagent） |
| 47 | 条级解析开源项目 | https://github.com/13098806890/laws-data · https://github.com/lttxzmj/chinese-law-corpus | 78,788 条/98.4% 解析率；逐条 JSON+"天然 chunk 边界" | ✅ 本次（subagent） |
| 48 | LAW-GPT（獬豸，早期中文法律 LLM，含 retriever 版） | https://github.com/LiuHC0428/LAW-GPT | 中文法律 RAG 早期实现 | ✅ 本次（subagent） |
| 49 | Cohere Rerank 4.0（发布/API） | https://cohere.com/blog/rerank-4 · https://docs.cohere.com/reference/rerank | 32K / 4096 默认文档截断 | ✅ 本次（subagent） |
| 50 | Voyage 定价页 | https://docs.voyageai.com/docs/pricing | $0.05/$0.02 每百万 token、前 200M 免费 | ✅ 本次（subagent） |
| 51 | bge-reranker-v2-m3 model card | https://huggingface.co/BAAI/bge-reranker-v2-m3 | Apache-2.0、512 默认截断 | ✅ 本次（subagent，镜像/外部核验） |
| 52 | mxbai-rerank-v2 | mixedbread-ai 官方（HF） | Apache-2.0、8k/32k-compatible、中文 NDCG 84.16 | ✅ 本次（subagent） |
| 53 | RankGPT / RankZephyr | https://arxiv.org/abs/2304.09542 · https://arxiv.org/abs/2312.02724 | LLM listwise reranker 论文 | ✅ 本次（subagent，export.arxiv.org） |
| 54 | 中文社区切分实践（自述，未独立验证） | https://blog.csdn.net/weixin_29062613/article/details/166762318 · https://blog.csdn.net/2401_82469710/article/details/163878428 · https://blog.csdn.net/DEVELOPERAA/article/details/163878676 · https://help.aliyun.com/zh/dataphin/fullmanaged/user-guide/text-chunking | 条级切分/父子块方向佐证 | ⚠️ 自述 |
| 55 | 其他开源解析项目 | https://github.com/Teetrition/Chinese-Law-Formatter · https://github.com/dengcao/Chinese-Laws · https://github.com/lttxzmj/chinese-law-mcp | 目级未解决注脚；条号标注；配套 MCP | ✅ 本次（subagent） |
| 56 | 长条文检索早期研究 | https://arxiv.org/abs/2212.13899 | 法条文档检索的层级注意力 | ✅ 本次（subagent） |
| 57 | flk 官方库检索字段（前端 JS） | https://flk.npc.gov.cn/assets/index-BZ9XHUiV.js | 官方库非条级检索形态 | ✅ 本次（subagent） |
| 58 | BSARD / G-DSR 官方仓库 | https://github.com/maastrichtlawtech/bsard · https://github.com/maastrichtlawtech/gdsr | 数据与立法结构图代码 | ✅ 本次（subagent） |
| 59 | COLIEE 2026 总览 | https://coliee.org/COLIEE2026/overview | Task 3 新增蕴含判断 | ✅ 本次（subagent） |
| 60 | Azure 混合排序（权重） | https://learn.microsoft.com/en-us/azure/search/hybrid-search-ranking | k≈60、每通道权重示例 | ✅ 本次（subagent） |
| 61 | Weaviate 融合 | https://docs.weaviate.io/weaviate/api/graphql/search-operators | 1/(RANK+60)、alpha、同构示例 | ✅ 本次（subagent） |
| 62 | Qdrant 混合查询 | https://qdrant.tech/documentation/concepts/hybrid-queries/ | k 默认 2、加权 RRF、"identifier-heavy"原话 | ✅ 本次（subagent） |
| 63 | OpenSearch RRF | https://docs.opensearch.org/latest/vector-search/ai-search/hybrid-search/rrf/ | 同构数值例、weights 合计 1.0 | ✅ 本次（subagent） |
| 64 | jieba 分词实测 | PyPI jieba 0.42.1（R3 本机实测） | "第七条"单 token、词典词频 61 | ✅ 本次（subagent） |

---

## 附：三条"当天可做"的修复优先级（给实施者）

1. **半天**：把 §7.2 的回归 case 入库 + 判官规则层（§6.3 第一层）——**先止血**：此类查询最差也能从"答错"变成"重检索或明确揭缺口"。
2. **2–3 天**：法条解析器 + 按条切分 + `article_no/article_display` 元数据 + 三阶梯路由（§2.2/§4.4）——**断根**：结构查询走结构通道，从此不依赖运气。
3. **1 天**：合并阈值从"计数 2"改为"比例阈值（对齐 LlamaIndex 语义）或提高 + 保留命中子块定位"；重排输入加结构头（§5.3）——**减噪**：让 P2/P3/P4 的叠加失效。

> 本文所有【实测·代码】坐标均可在 本地证据副本（未入仓；可重新 clone） 复核；所有 ✅ 核验 URL 的实现方式（curl/镜像/API/site 检索）均已记录于 `_web_evidence/`，可复跑。
