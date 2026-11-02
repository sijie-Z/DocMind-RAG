# 核验 golden set（D5）

量化产品主张「逐条告诉你改了什么、证据在哪一句、且不猜」的确定性评测。
评测走与生产相同的路径：`semantic_chunker.split_text` → `Clause`（normalize +
clause 字段）→ `extract_*` → `align_item`，不调 LLM、不联网、不依赖 DB/ES。

```bash
make review-eval           # 打三张表并与基线比对（CI 同款）
make review-eval-baseline  # 显式更新基线（评审可见的 PR 行为）
```

## 数据集

`evals/review_golden_cases.py`：10 组合成中文采购合同 V1/V2。gold **只依据合同
文本人工判定**，不从引擎输出反推。用例保持「每条 <5 句、整篇 >500 字」：
生产分块器走条款切分且不触发 embedding（评测器注入拒绝桩，写法漂移会立即报错）。

覆盖：付款天数变化、裸「日」不猜（ambiguous→unidentified）、两版都缺失、
例外新增/删除、起算事件变化、验收术语变化、仅改条款编号（renumbered）、
完全不变、验收天数变化。

## 指标（当前基线）

| 指标 | 付款期限 | 起算条件 | 验收/发票 | 例外 | 总体 |
|---|---|---|---|---|---|
| 抽取准确率 | 100% | 100% | 100% | 100% | — |
| change_type 准确率 | 80% | 80% | 80% | 100% | **85%** |
| 证据命中率 | — | — | — | — | **100%** |

- 抽取口径：payment 比对 value/unit 或 ambiguous/not_found；start 比对 events
  集合；acceptance/exception 比对 status（术语子串重叠会让“集合相等”变成
  引擎口径，v1 不做）。
- 证据口径：gold 标注的证据条款号是否出现在对应侧 facts 的
  `clause_no_union` 中。

## 已知失败类型（next batch 的输入）

当前 6 个失败全部来自同一类：**共享条款文本变动会污染其它核验项**。
`align_item` 在语义词元相同但整段条款文本变化时判 modified（否定/主体/限定语
保护），但同一句里既有起算事件又有付款天数时，改天数会让起算条件也报
modified；同理新增例外条款（含「付款期限相应顺延」）会让付款项报 modified。
产品语义上这些项并未变化——需要把文本哈希保护收窄到「该项自己的证据片段」，
或引入否定/限定语检测而不是整句哈希。作为独立批次处理，本批只建立度量与棘轮。

## 政策

- 指标任何变化（回归或提升）都必须显式更新基线并在 PR 说明；CI（pytest
  `tests/unit/test_review_golden.py`）会拦截未登记变化。
- 第二批替换为设计伙伴的真实脱敏合同（格式不变，直接换 corpus）；
  引用任何数字时注明样本量（当前 10 组）。
