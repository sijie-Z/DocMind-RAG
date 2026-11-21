# 核验质量基线（2026-11-15）

> 目的：在真实链路上跑一次"已知答案"的核验，看它能不能准确指出差异。
> 在此之前，核验功能只有 E2E 证明"按钮能点、流程能走"，**没有任何证据说明结论准不准**。

## 方法

构造一份三件套（制度 / 合同 V1 / 合同 V2），在 V2 里埋 **3 处已知改动**：

| 编号 | 改动 | 期望 |
|---|---|---|
| e1 | 第5条 付款期限 `30日 → 15日` | `modified` |
| e2 | 新增 第10条 违约金 | `added` |
| e3 | 删除 第8条 例外 | `removed` |

流程：注册账号 → 上传 3 份文档（.md）→ 等索引 → 注册版本元数据
（`PUT /documents/{id}/review-meta`）→ 建案（`POST /reviews`）→ 分析（`POST /reviews/{id}/analyze`）
→ 读 `GET /reviews/{id}` 的 `items`。

关键前置（第一次跑就踩到）：**不注册 review-meta 时建案直接 blocked**，
`blocking_reasons=['policy:missing_version_meta', 'base:missing_version_meta',
'revised:missing_version_meta']`，分析返回 `409 REVIEW_CASE_NOT_READY`。
这个行为是对的（拒绝在身份不全时产出结论），但**界面上是否说清楚了**值得单独核。

## 实测结果

案件状态 `ready`，分析返回 200，产出 4 个核验项：

```
payment_term        change=modified   status=pending  evidence=3
start_condition     change=modified   status=pending  evidence=3
acceptance_invoice  change=modified   status=pending  evidence=3
exception           change=removed    status=pending  evidence=2
```

对照 ground truth：

| 改动 | 结果 |
|---|---|
| e1 付款期限 30→15 | ✅ 命中（`payment_term=modified`，并定位到 base/revised 证据） |
| e3 删除第8条例外 | ✅ 命中（`exception=removed`） |
| **e2 新增第10条违约金** | ❌ **完全未出现** |

## 根因（代码级）

`backend/app/services/review_analysis_service.py`：

```python
EXTRACTORS = {            # 只有 4 个键
    "payment_term", "start_condition", "acceptance_invoice", "exception",
}
...
for item in items:                      # items 来自固定清单
    extractor = EXTRACTORS.get(item.item_key)
    if extractor is None:
        continue                        # 清单之外的条款：直接跳过
```

**核验的覆盖范围 = 固定 4 项清单**。清单之外的新增条款（本例的违约金）永远不会被检查，
而"合同里悄悄多了一条对己方不利的条款"恰恰是合同核验最主要的风险场景。

## 结论与建议

- 确定性抽取本身**工作正常**：3 处改动里 2 处被准确识别并带证据，且不依赖 LLM（facts 里是
  `events` / `terms` / `chunk_id` 这类结构化字段）。
- 真正的缺口是**覆盖面**，不是准确率。

### 更新（2026-11-15）：新增条款检测已修复

PR #693 在清单循环之后追加了一步：`detect_added_clauses(base, revised)` 找出「revised 有、
base 无」的条款，生成 `change_type=added` 的核验项并挂 revised 侧证据，进入人工决策。
判定是**行级**的（按「第N条」拆行比较）—— 因为小文档常被切成 1 个 chunk，整篇是一个
Clause，按块比较必然"文本不同 + 条号交集"，会把新增误判成修改。

修复后同一套 V1/V2 的实测输出：

```text
改动前: items=4  payment_term / start_condition / acceptance_invoice / exception
改动后: items=5  …同上 4 项完全不变…  + added_0_1  change=added  evidence=1
```

即：e2（新增第10条 违约金）现在会被识别，且**既有 4 项行为不变**。

### 更新（2026-11-15 晚）：清单可配置 + 不确定可判定

**① 清单可配置**（`app/services/review_checklist.py`）：

```bash
export REVIEW_CHECKLIST_JSON='[{"key":"confidentiality","label":"保密义务","keywords":["保密","泄露"]}]'
```

默认 4 项不变；配置项追加在后。配置错误（坏 JSON / 非法 key / 空 keywords / 重复）
直接 `ValueError`，不静默忽略。真实验证：

```text
带环境变量: 建案 items(5) = payment_term, start_condition, acceptance_invoice, exception, confidentiality
不带环境变量: 建案 items(4) = 默认 4 项（与改动前一致）
```

配置项没有专用抽取器，分析后为 `undetermined`（如实呈现，不臆造）。

**② 不确定可在接口层判定**：`GET /reviews/{id}` 新增 `summary`：

```json
{"item_count":5,"changed_count":0,"added_count":0,"removed_count":0,
 "unidentified_count":4,"undetermined_count":1,"has_undetermined":true}
```

`has_undetermined=true` 时界面/导出**不得**呈现为"没有差异"。

### 更新（2026-11-15 深夜）：配置项真正参与判定 + 前端消费 summary

**① 抽取器可注册，配置项不再永远 undetermined**（`review_analysis_service`）：

```python
register_extractor(item_key, *, replace=False)   # 注册专用抽取器
make_keyword_extractor(keywords)                 # 通用关键词抽取器
resolve_extractor(item_key, *, keywords=None)    # 专用优先，否则按关键词兜底
```

默认 4 项仍是各自专用抽取器（有断言 `resolve_extractor("payment_term") is EXTRACTORS["payment_term"]`）。
真实验证（真实 md 构造 V1/V2，revised 追加一条保密条款）：

```text
confidentiality  change=modified  facts={'base': {'terms': ['泄露'], 'status': 'identified', 'chunk_id': ...}}
summary: {'item_count': 6, 'undetermined_count': 0, 'has_undetermined': True}
```

**② 同一条款不再重复报**：清单项（如 confidentiality）已识别的 chunk，不再被
`detect_added_clauses` 二次报成 `added_*`（实测曾出现同一条款两个核验项）。

**③ 前端消费 summary**：详情页把 `items.unidentified + summary.undetermined_count`
合并计数；有未判定项时显示「需人工复核」+「N 项无法判定」，不再落到"待决策"。
`has_undetermined=true` 时界面**不呈现为无差异**。

### 更新（2026-11-16 凌晨）：导出呈现已核、两条"疑似缺陷"被证伪、锁加 lease 上限

**① 导出呈现已核实**（守卫测试固定）：核验单**逐项**列出 `unchanged`（标签「无差异」），
不静默省略；实测导出文本含「未识别」「需人工」，**不含**「无差异」误用。
→ 原"导出如何呈现无法判定尚未验证"这条**已关闭**。

**② `payment_term` 的 `unit_unclear` 不是缺陷**（原判断有误，此处更正）：

```
30日  -> status=ambiguous reason=unit_unclear candidate={value:30, unit:None, unit_raw:"日"}
30天  -> identified unit=day
30个工作日 -> identified unit=workday
align(30日, 15日) = modified      ← 变更检测照常工作
```

- 「日」被刻意保持 `unit: None`，代码注释写明是**有意**的（不擅自断言"自然日 vs 工作日"，
  避免掩盖歧义）；
- 而且变更检测走的是 `candidates` 比对，**30日 → 15日 依然判 modified**，
  不会因为 unit 不明就漏掉真实改动；
- 只有"一侧识别不到"时才落 `unidentified`（`前后只有一侧有条款`），这是保守但正确的取舍。

**③ worker 锁加绝对 lease 上限**（#708 / #709）：`_LOCK_MAX_HOLD_SECONDS = 1800`。
死锁可由同伴接管（存活键消失），而「活着却卡死」的持有者此前会一直续期、把整个分区
无限堵住；现在 lease 到顶即停止续期 → 在下一个 checkpoint 中止 → 同伴按
`reason=lease_expired` 接管继续推进。

### 更新（2026-11-16）：配置项数值纳入语义键，「通用抽取器不做数值对比」已解决

上一条「通用关键词抽取器只能判 identified/not_found」**已收口**（#716）：

```text
「赔偿上限 10%」 vs 「赔偿上限 20%」        -> modified   ✅ 金额变了必须报
「应赔偿」      vs 「应当赔偿」（同 10%）    -> unchanged  ✅ 纯措辞调整不再误报
「百分之十」    vs 「百分之二十」           -> modified   ✅ 中文百分比也能比
```

- 新增 `_extract_numbers`：阿拉伯数字/百分比 + 中文百分比（「百分之十/二十/三十」「十五」）
- 配置项语义键 = `(terms, numbers)`；`align_item` 在「键同 sha 异」时对配置项判 `unchanged`
- 默认 4 项路径不变（181 条 analysis 用例全绿）

### 更新（2026-11-16 下午）：编号条款内追加的新义务不再被静默隐藏

演示实测发现第二种漏检形态（#722）：

```text
base     第5条 付款期限：验收合格后30日内支付货款。
revised  第5条 付款期限：验收合格后30日内支付货款。此外，逾期未对账的视为甲方确认本期货款无误。
                                                   ↑ 这条义务既不在 4 项清单内，也不作为 added 报出
```

根因：`detect_added_clauses` 一旦发现该行条号在 base 里已存在就整行 `continue`
（原注释：「属于修改既有条款，交给清单抽取器，不重复报」）。这条规则对**纯改写**是对的
（否则 `30日→15日` 会同时被报成 modified 和 added），但它把「改写顺带追加」的新义务
一起吞掉了 —— 而那部分恰恰是固定清单最不可能覆盖的内容。

修法：条号已存在的行不再整行跳过，改为对该条号的 base 句子池做句子级比对，
只报**句子级**新内容；并加子串守卫 —— 只改标点（`，→。`）时拆出的两段都是 base 原文
的子串，不是新增（单靠相似度阈值拦不住：句子被拆短后相似度会掉到 0.75 以下）。

```text
B  30日 → 15日（纯数值修改）                      -> added 0 条  ✅ 不重复报
G  「…分两期支付。」 + 「此外，逾期未对账…」       -> added 1 条  ✅ 只报新增句，保留条号 [3]
   同一句只改标点（，→。）                        -> added 0 条  ✅ 子串守卫
```

- 新增 `_fresh_sentences_in_existing_clause`；`detect_added_clauses` 主循环改为先算出
  「要报出的文本」（整行 / 句子级），再统一走同一条 append 路径（消除原来的两段重复构造）
- `test_review_added_clauses.py` 14 → 19 条（5 条新用例：追加句可见、条号保留、
  数值修改不重复报、标点拆分不误报、完全一致不报）

### 更新（2026-11-18）：新增条款项在核验单上可读，且证据不再误报「原文已变更」

上一轮把「新增条款**检测**」补全后，端到端跑真实 API 才发现：**检测到了，但审核人看不出来**。

```text
核验单上的 added 项：
  facts.revised = {status, chunk_id, source}      ← 没有 text、没有 clause_no
  差异条渲染                     = 「仅修订版有:  — → 无」
  证据 permalink 的徽标          = 「原文已变更」(chunk_text_changed)   ← 恒为真
```

三个原因（同一条链上的三处自相矛盾）：

1. `facts.revised` 只有 `status/chunk_id/source`，没有新增原文与条号 → 界面 `factText()`
   取不到任何可用字段，只能回落到 `factsNone`（「无」），`factLocator()` 也拿不到定位。
2. added 项快照的 `quote` 是**行级片段**（`_split_clause_segments` 的某一行）或**句级拼接**
   （#714/#722 的新句），却标了 `text_provenance = parser_original` —— 拼接句并不是原文。
3. `ReviewEvidence.char_start/char_end` 取自**整个 chunk**，与片段 quote 自相矛盾；
   而 `evidence_detail` 用 `sha256(chunk.original_text) == quote_sha256` 判定是否漂移，
   片段永远对不上 → 每一处新增条款的证据都恒定显示「原文已变更」。

修法（让三者对齐到同一个口径：**证据 = chunk 级 parser 原文，精确内容 = facts**）：

- `facts.revised` 补 `text`（精确到条/句的新增原文）、`clause_no`、`section_no`、`page_number`
- added 项的证据 `quote` 改为**所在 chunk 的 parser 原文**（用 `chunk_id → raw_text` 映射），
  `char_start/char_end` 与该原文一致
- `_split_clause_segments` 的行级条号改为复用 `extract_clause_fields`（#188「条号解析单一来源」）：
  原先自带的 `第\s*(\d+)\s*条` 只认阿拉伯数字，「第九条」匹配不到就继承整个 chunk 的条号
  （实测得到 `[1, 9]`），定位回不到原文；顺带也不再把握手式引用（「依据第7条」）当定义位

```text
真实 API 实测（tests/api/test_review_case_api.py）：
  修复前  AssertionError: 新增项没有可读文本：{'status': 'identified', 'chunk_id': ..., 'source': 'unlisted_clause'}
  修复前  facts.revised.clause_no = [1, 9]      ← 中文条号「第九条」丢失，继承了整块条号
  修复后  text = 「第九条 乙方逾期交付的…违约金」 / clause_no = [9] / quote = 整块原文
          live_chunk.matches_snapshot = True, replay.warnings = []
```

端到端用例 `test_added_item_is_readable_and_its_evidence_is_truthful`（先红后绿）；
前端 `reviewWorkbench.test.ts` 新增一条（撤掉 fix 后实测红：
`expected '仅修订版有:  — → 无' to contain '违约金'`）。

**已知限制**：本次修复前已经分析过的案件，其 added 项快照仍是片段 quote、facts 也没有 `text`。
`analyze` 对已存在的 `item_key` 是跳过而非覆盖，因此这些历史案件不会被自动回填 ——
需要重新建案分析。不自动改写既有证据快照是刻意的（快照一旦落库就不该被悄悄替换）。

### 更新（2026-11-20）：无编号文档的「整块排除」下沉到句子级 + 一条旧结论被证伪

**修复（#748）**：无编号文档没有条款号可用，`_covered_refs` 只能退回 `(chunk_id, None)`
**整块排除**。于是「这个 chunk 里已经识别到验收/付款义务」会把同 chunk 里**新增的违约金条款**
一起吞掉 —— 而多出一条不利条款正是核验最该报出来的事。整块排除只是"拿不到更细依据"时的保底，
不该是常态。

修法：把清单项在该块的**命中词**（`facts.terms`）传进 `detect_added_clauses`，排除下沉到句子级 ——
只有含命中词的句子算已识别，其余句子若 base 里完全没有，就是真正的新增。拿不到命中词时
仍退回整块排除（保守方向：宁可漏，也不做没有依据的重复报）。

```text
base     甲方应在验收合格且收到发票后三十日内支付货款。
revised  甲方应在验收合格且收到发票后三十日内支付货款。乙方逾期交付的，应按合同总金额的百分之五支付违约金。
         验收项已在该 chunk 命中（无条款号）

修复前 added = []                                              ← 新增义务被整块排除吞掉
修复后 added = ['乙方逾期交付的，应按合同总金额的百分之五支付违约金']   ← 只报新增句
```

守卫：只改标点（`，→。`）拆出的两段都是 base 原文子串，不是新增 —— 加 `sentence not in base_corpus`
过滤（拆短后相似度会掉到 0.75 以下，单靠阈值拦不住）。

**证伪（同日 probe）**：本文件原先列的「无编号文档的附加句可能漏检（被 chunker 并入相邻 chunk）」
**不再复现**。三个真实分块形态实测全部能检出：

```text
A 新增句并入相邻段落（同 chunk，无换行）  -> 检出
B 新增句独立成段（新 chunk）              -> 检出
C 新增句并入前一段落（前一个 chunk）      -> 检出
```

但同一个 probe 暴露了**相反方向的缺陷**：A / C 报出的是**整段原文**（含全部旧内容），
而不是新增的那一句 —— 因为 `_is_variant_of_existing` 的 0.75 相似度阈值在
"长段落 + 句子被稀释"时会失守。审核人看到 `— → 整段旧文+新句`，等于把旧条款当新增。
这是**过度报出**（噪音），不是漏检。已登记为后续项，不在 #748 范围内。

### 更新（2026-11-21）：并入段落的新句只报新句，不再把整段旧文当新增

上一条 probe 暴露的**过度报出**已收口（#756）：

```text
base     …段落甲…（本制度适用于……流程。）
revised  …段落甲… + 保密义务：双方对合同内容及履行中知悉的商业秘密承担保密责任，泄露方赔偿全部损失。
                            ↑ 新增句被 chunker 并入相邻段落

修复前  added = ['本制度适用于……流程。保密义务：……']   ← 整段旧文被当新增贴上
修复后  added = ['保密义务：双方对合同内容及履行中知悉的商业秘密承担保密责任，泄露方赔偿全部损失。']
```

根因：整段相似度被新增句稀释后跌破 0.75，`_is_variant_of_existing` 失守 → 走「整条新增」分支。

修法（两步，都靠**去标点的句式键** `_sentence_key`）：

1. **分支之前**：片段去标点后与 base 某段/某句完全相同 → 只是排版差异（`，→。` 拆句），直接跳过。
   必须放在分支之前 —— 这类片段会被判成"高度相似"而走句级分支，拆出的短句相似度
   跌破阈值后会被误报成新增（实测：`期满仍应继续承担保密义务` 被报出来）。
2. **整条新增之前**：片段里只要有一句是 base 已有的原句，就说明是「旧内容 + 追加」，
   只报追加的句子；一句都不匹配才算整条新增（独立新段落仍整条报出，不过度切碎）。

```text
A 并入相邻段落（同 chunk）  -> 只报新句            ✅
C 并入前一段落（前一个 chunk）-> 只报新句          ✅
   同一段只改标点（，→。）    -> added 0 条        ✅
   独立成段的新条款          -> 整条报出（不切碎）  ✅
```

- 新增 `_sentence_key`（去标点/空白）+ `base_sent_keys` / `base_seg_keys`
- `test_review_added_clauses.py` 21 → 30 条（本轮新增 4 条：两种并入形态、标点重排、
  独立新段落仍整条报出）

### 仍待解决

- ~~新句并入段落时会整段报出~~ → **已修**（#756）：改为「片段里有一句与 base 相同 → 只报追加的句子」，
  纯标点重排（`，→。`）按去标点句式键直接跳过（见上一条更新）
- ~~无编号文档的附加句可能漏检（被 chunker 并入相邻 chunk）~~ → **已证伪**（2026-11-20）：
  A/B/C 三种并入形态均能检出；真正的问题是上面的"整段报出"。
- ~~编号条款内追加的新义务不可见~~ → **已修**（#722）：原先条号已存在就整行跳过，
  追加句永远不报；现在改为条号内句子级比对 + 子串守卫（见上一条更新）
- ~~`detect_added_clauses` 的排除粒度是 chunk 级~~ → **已修**（#711 / #712 / #748）：
  排除改为条款行级 `(chunk_id, clause_no)`；无条款号时用命中词下沉到**句子级**（#748），
  只有拿不到命中词才退回整块排除；

  只有无条款号的文档才退回整块排除（保底，且已在 `_covered_refs` 注释里写明）





## 复现方式

```bash
# 服务就绪：docker 6 容器 + backend :8010 + worker
# 用同一份文本构造 policy/base/revised，按上述流程调用即可
python -c "..."   # 见本文件"方法"一节的四步调用
```

注：分析链路在 DeepSeek Key 失效时可用「用户级 Provider → 本地 Ollama」跑通
（详见 `docs/llm-provider-setup.md`），但本地小模型的结论质量不能代表线上。
