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

### 仍待解决

- **通用关键词抽取器只能判 identified/not_found**，不做数值/单位对比；
  需要"改数字"级别的判定时仍得写专用抽取器（可用 `register_extractor` 注册）
- **无编号文档的附加句可能漏检**：实测在 markdown 里追加一行"保密义务：……"，
  如果没有独立成段（被 chunker 并入相邻段落），行级比较看不到它 → 不产生 added 项
- **`detect_added_clauses` 的排除粒度是 chunk 级**：某 chunk 里若既有清单项已识别的条款、
  又有真正的新增条款，后者会被一并排除（单 chunk 小文档最容易命中）




## 复现方式

```bash
# 服务就绪：docker 6 容器 + backend :8010 + worker
# 用同一份文本构造 policy/base/revised，按上述流程调用即可
python -c "..."   # 见本文件"方法"一节的四步调用
```

注：分析链路在 DeepSeek Key 失效时可用「用户级 Provider → 本地 Ollama」跑通
（详见 `docs/llm-provider-setup.md`），但本地小模型的结论质量不能代表线上。
