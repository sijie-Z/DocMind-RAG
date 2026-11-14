"""核验清单定义（可配置，默认 4 项）。

为什么单独成模块：清单原先写死在 `review_case_service.ITEM_KEYWORDS` 与
`models.review.REVIEW_ITEM_KEYS` 里 —— 采购合同要看的项和服务合同完全不同，
用户无法增删，这是覆盖面缺口（见 docs/review-quality-baseline.md）。

配置方式：环境变量 `REVIEW_CHECKLIST_JSON`，值是数组，追加在默认 4 项之后：

```bash
export REVIEW_CHECKLIST_JSON='[
  {"key": "confidentiality", "label": "保密义务", "keywords": ["保密", "泄露"]}
]'
```

约束（宁可直接报错，也不静默忽略配置）：
- 必须是合法 JSON 数组；
- `key` 需匹配 `^[a-z][a-z0-9_]{1,31}$`（与 DB 列 `String(32)` 对齐）；
- `keywords` 必须是非空字符串数组（候选证据抓取依赖它）；
- 不允许与默认项重复。
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

ENV_VAR = "REVIEW_CHECKLIST_JSON"

KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,31}$")

DEFAULT_ITEM_KEYWORDS: dict[str, tuple[str, ...]] = {
    "payment_term": ("付款", "支付", "账期", "价款", "货款", "结算"),
    "start_condition": ("起算", "起计", "自收到", "收到发票", "对账单", "验收合格"),
    "acceptance_invoice": ("验收", "发票", "对账"),
    "exception": ("例外", "暂缓", "除外", "另行约定", "补充协议", "不可抗力"),
}


@dataclass(frozen=True)
class ChecklistItem:
    """一个核验项：key 落库，keywords 用于抓候选证据，label 给人看。"""

    key: str
    keywords: tuple[str, ...]
    label: str = ""
    configurable: bool = False
    """True = 来自配置的附加项（没有专用抽取器，分析后通常为 unidentified）。"""


DEFAULT_ITEMS: tuple[ChecklistItem, ...] = tuple(
    ChecklistItem(key=key, keywords=keywords) for key, keywords in DEFAULT_ITEM_KEYWORDS.items()
)

DEFAULT_ITEM_KEYS: tuple[str, ...] = tuple(item.key for item in DEFAULT_ITEMS)


def _parse_extras(raw: str) -> list[ChecklistItem]:
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{ENV_VAR} 不是合法 JSON: {exc}") from exc
    if not isinstance(data, list):
        raise ValueError(f"{ENV_VAR} 必须是数组（收到 {type(data).__name__}）")

    extras: list[ChecklistItem] = []
    for entry in data:
        if not isinstance(entry, dict):
            raise ValueError(f"{ENV_VAR} 的每一项都必须是对象，收到 {type(entry).__name__}")
        key = str(entry.get("key") or "").strip()
        if not KEY_PATTERN.match(key):
            raise ValueError(
                f"{ENV_VAR} 中存在非法清单项 key: {key!r}（需匹配 {KEY_PATTERN.pattern}）"
            )
        raw_keywords = entry.get("keywords") or []
        if not isinstance(raw_keywords, list) or not raw_keywords:
            raise ValueError(f"{ENV_VAR} 中 {key!r} 的 keywords 必须是非空数组")
        keywords = tuple(str(word).strip() for word in raw_keywords if str(word).strip())
        if not keywords:
            raise ValueError(f"{ENV_VAR} 中 {key!r} 的 keywords 全是空串")
        extras.append(
            ChecklistItem(
                key=key,
                keywords=keywords,
                label=str(entry.get("label") or ""),
                configurable=True,
            )
        )
    return extras


def load_checklist(raw: str | None = None) -> tuple[ChecklistItem, ...]:
    """默认 4 项 + 配置追加项。`raw` 为空则读环境变量。"""
    extras_raw = raw if raw is not None else os.environ.get(ENV_VAR, "")
    items = list(DEFAULT_ITEMS)
    if extras_raw.strip():
        seen = {item.key for item in items}
        for extra in _parse_extras(extras_raw):
            if extra.key in seen:
                raise ValueError(f"{ENV_VAR} 中存在重复的清单项 key: {extra.key!r}")
            seen.add(extra.key)
            items.append(extra)
    return tuple(items)


def keyword_map(items: tuple[ChecklistItem, ...] | None = None) -> dict[str, tuple[str, ...]]:
    """item_key → keywords，供候选证据抓取使用。"""
    source = items if items is not None else load_checklist()
    return {item.key: item.keywords for item in source}
