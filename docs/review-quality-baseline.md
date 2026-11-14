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
- 真正的缺口是**覆盖面**，不是准确率：
  1. 新增条款检测（revised 有、base 无的条款 → 生成 `change_type=added` 的临时项）
  2. 清单可配置（不同合同类型需要不同清单，现在写死在代码里）

## 复现方式

```bash
# 服务就绪：docker 6 容器 + backend :8010 + worker
# 用同一份文本构造 policy/base/revised，按上述流程调用即可
python -c "..."   # 见本文件"方法"一节的四步调用
```

注：分析链路在 DeepSeek Key 失效时可用「用户级 Provider → 本地 Ollama」跑通
（详见 `docs/llm-provider-setup.md`），但本地小模型的结论质量不能代表线上。
