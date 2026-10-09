# RAG 检索评估门禁 Phase 2 · 首实验：条号在哪一段被淹（#208）

> 五步计划（eval 先行）的 ③「逐阶段抓 trace 做最小实验」+ ④「只修被实验证明的那一环」。
> **本实验只测量：没有修改任何检索行为。** 结论是「无有界单环修复」——把选择与数据摆出来，
> 修复决策单列在 §5。配套的「锁现状」判别测试见 `tests/unit/test_issue208_phase2_characterization.py`。
> 落点：4 条失败全部是「带文档全称的条号直查」（`clause_arabic_3` / `clause_chinese_7` /
> `clause_arabic_7` / `clause_chinese_12`）+ near-miss 原形 `clause_9_audit_space`。

## 1. 实验纪律

- **同物理状态对照**：固定评估索引 `docmind_knowledge_eval_v1`（28 chunks @ org 9901，
  analyzer=cjk，1 shard），实验期间**不重建、不 reseed**——评审已警告物理状态会摆动 NearMiss
  （12.5% ↔ 16.7%），before/after 必须在同一索引上比。基线复跑与 Phase 1 报告逐条全等后才开测。
- **一次一变量**：每条假设配一个「只改一个变量」的判别探针（改路由 / 改查询文本 / 改
  minimum_should_match / 放开 doc 帽），观察同 query 的排序变化。
- **探针只读**：只调 `HybridRetriever` 的各臂与 ES `_search`，不写索引；反事实变体在进程内
  由真实源码派生（唯一差异 = 被改的那一个变量）。

测前基线（before，与 Phase 1 报告一致）：ClauseHit@1 80% / @3=@5 84% / NearMiss@5 12.5% /
MRR 0.82 / EmptyAcc 100%，22/28 条 query 路由 `keyword_only`。

## 2. 逐阶段轨迹结论（条号在哪一段被淹）

前置事实：语料分词器 = **cjk（bigram）**。`《知识库服务管理办法》第3条规定了什么？` 的查询
token ≈ `知识,识库,库服,服务,务管,管理,理办,办法`（8 个标题 bigram）+ `第,3,条` +
`规定,定了,了什,什么`。**条号信息只剩「数字」一个 token**——`第`/`条` 两个 token 在全部
条款 chunk 里都出现，无区分度。

| 阶段 | 判决 | 实测证据 |
|---|---|---|
| ① 改写（`rewrite_query_candidates`） | **无罪** | 两个改写变体都完整保留条号，只去掉尾标点 |
| ② 路由（`QueryComplexityClassifier`） | **非修复点** | 4 条路由 `simple→keyword_only`；但 monkeypatch 强制 `hybrid` 重跑，4 条 gold 仍全 miss（全量 all-hybrid 变体：0 条修复） |
| ③ keyword 臂（multi_match + BM25） | **有罪（剪枝）** | 标题 bigram 抬高查询 token 总数 → `minimum_should_match: 30%` 阈值水涨船高，gold 只命中内容词、被**整条剪掉**：`clause_chinese_12` 在 30% 下候选只剩「前言」1 条；msm 改 10%（单变量探针）gold 立刻回到候选（名次 5/10/6/2/2） |
| ③ keyword 臂（排序） | **有罪（洪泛）** | 前言块 43.4 分居首；gold（第3条）11.1 分列 kw#4；第2条 17.3、第1条 14.4 都压在 gold 上。**只加/删文档全称的单变量对照**：`第12条对数据删除有什么要求？` 无全称时候选=仅 gold（@1 命中）；加 11 字全称后候选=前言/第2条/第1条/第11条，gold 消失 |
| ③ keyword 臂（中文数字） | **有罪（分词）** | `第七`≠`第7`（bigram 不同）→ `clause_chinese_7` 的 gold 连候选都进不去（vector 臂 v#3 明明找到了它）。#2.10 数形归一化的量化证据 |
| ④ RRF/MMR | **有罪（剔除）** | `clause_arabic_3` 的 gold（kw#4/v#6）在 `_mmr_select` 的多样性惩罚下进不了 top5（m1–m5=前言、第1条、第7条、第11条、第13条）——hybrid 上线也送不到 post-process |
| ⑤ post-process | **有罪（三重）** | (a) **doc 帽 2/文档**：gold 排到同文档第 3 位即被剔（`clause_arabic_3` kw#4、`clause_arabic_7` kw#5 均死于 `seen_docs>2`；且计数发生在质量过滤**之前**，被 ov 剔除的块也占槽位）；(b) **终滤阈值**：keyword_only 路径的 raw hit 从不带 `has_keyword` 标记（该键由 `_rrf_fuse` 写）→ 全部按最严的 `ov>=0.15` 终滤；gold 实测 overlap=2/12×0.7=**0.1167**，去帽后仍死在 0.15（hybrid 序里同 chunk 带 kw 标记 → 0.10 阈值放行）；(c) **术语表截断倾斜**：`extract_query_terms` 只取前 12 个 bigram，标题占 8 个，`数据/删除` 被截断出列表（`clause_chinese_12` 的 overlap 分子=0） |

## 3. 单变量探针实录（节选）

- **Q5↔Q8（删/加 11 字文档全称）**：候选数 1→4，gold 从 @1 命中变为被 msm 剪掉。
- **msm 30%→10%**（同查询、同索引，仅此一变量）：5 条探针 query 的 gold 全部回到候选
  （名次 `#5/#10/#6/#2/#2`），但候选集同时膨胀（如 1→11 条）——精度影响未测，仅作定位证据。
- **doc 帽放开**（源码派生变体，唯一差异 = `seen_docs>2` 关闭）：`clause_arabic_3` 变 gold@4
  （PASS），`clause_arabic_7` 仍失败（死于终滤 0.15）——证明「去帽」既非充分也非唯一。
- **强制 hybrid**：`clause_arabic_7` gold 进 MMR 序 m3；去帽后 @3 PASS（说明两条环叠加）。
  但 `clause_arabic_3` 反被 MMR 剔除——**两条 query 的修复环互斥**。

## 4. 反事实变体矩阵（全 28 条，同物理索引；均为模拟，未改产品代码）

| 变体 | ClauseHit@1 | ClauseHit@5 | NearMiss@5 | MRR | 修复 | 副作用 |
|---|---|---|---|---|---|---|
| before（现状） | 80.0% | 84.0% | 12.5% | 0.82 | — | — |
| 仅 doc 帽放开 | 80.0% | **88.0%** | **20.8%** | 0.83 | `clause_arabic_3` | 2 条新 near-miss 进 top5 |
| 仅全量 hybrid | 84.0% | 84.0% | 12.5% | 0.84 | **0 条** | （唯一边动：near-miss 原形翻转 gold@1） |
| 全量 hybrid + 帽放开 | 84.0% | 88.0% | **29.2%** | 0.8533 | `clause_arabic_7` | 4 条新 near-miss 进 top5；`clause_arabic_3` 反被 MMR 挡死 |

**没有任何单一或已测两环组合修好全部 4 条**：Q1 只有「帽放开+保持 keyword_only」能修，Q3 只有
「hybrid+帽放开」能修（二者互斥）；Q2/Q4 还叠加中文数字分词桥接（#2.10）+ 术语表截断。
全修 = 3~4 环同时动 + 数形归一化，且每环都是跨 query 类型的产品行为变更 → **按纪律停下报告**。

## 5. 未定选择（修复决策清单）

1. **doc 帽 2/文档**：去 → 单文档场景可返回 >2 个 chunk（当前是硬上限，本案 4/4 失败都挨它）；
   代价 = NearMiss@5 结构性地 +8.3pt（同文档相邻条必然随行）+ 语义 query 返回构成变化。
2. **minimum_should_match 30% 的口径**：是否按「剔除文档全称后的 token 集」计算（或查询拆分
   docName/clause/content 三组）。直击 Q2/Q4/Q8 的候选剪枝；精度影响未测。
3. **数形归一化（#2.10）范围**：至少 `第七↔第7`、`第十二↔第12`；query 侧归一还是 index 侧双写。
4. **keyword_only 命中是否带 `has_keyword` 语义**（终滤 0.15 vs 0.10 的臂标记缺口）：改动即改
   22 条 keyword_only 的终滤口径。
5. **MMR 对条号直查的适用性**：多样性惩罚与「精确定位」目标相斥（Q1 的 gold 被 MMR 剔除）；
   可选：条号类 query 跳过 MMR。

## 6. 未解释 / 未测量（如实）

1. msm=10% 只做了候选探针；对其余 23 条的精确率/回退影响未测。
2. Q1 的 MMR 数值分解（第7条 m3=0.1604 压过 gold）仅记录轨迹，未逐项分解。
3. 术语表替代方案（去标题 bigram / 不截断）未做变体。
4. 全部结论绑定本轮物理状态（28 chunks、cjk、uuid 记录在工作区证据里）；重 seed 后须整体复测。
5. `clause_9_audit_space` 的 near-miss 机制（第10条正文引用「第 9 条」且审计词面重叠，kw 臂
   20.10 vs gold 19.45）与「引用 vs 定义位」的口径同构，未进一步探。

## 7. 复现

与 Phase 1 同环境（见 `eval-gate-phase1.md` §2）：`python -m evals seed`（一次性）→
`python -m evals run` 出基线；逐阶段探针 = 直调 `HybridRetriever._keyword_hits / _vector_hits /
_rrf_fuse / _mmr_select / _post_process` 与 `QueryComplexityClassifier`，把每个中间列表按
`clauses.clause_definitions` 标注 gold 位次。原始输出（trace/sim/diffs 三份）随 PR 的
实验记录提交（工作区 `_orchestrate/round2/`）。
