"""核验清单可配置：默认 4 项不变，可通过配置追加项目（不同合同类型需要不同清单）。

背景：清单写死在代码里（ITEM_KEYWORDS / EXTRACTORS / REVIEW_ITEM_KEYS），
采购合同要看的项和服务合同完全不同，用户无法增删 —— 这是覆盖面缺口。
"""

from __future__ import annotations

import json

import pytest

from app.services.review_checklist import (
    DEFAULT_ITEM_KEYS,
    load_checklist,
)


def test_default_checklist_keeps_four_known_items() -> None:
    items = load_checklist()
    assert tuple(i.key for i in items) == DEFAULT_ITEM_KEYS
    assert len(items) == 4
    # 关键词随项一起带出（候选证据抓取依赖它）
    assert items[0].keywords


def test_extra_items_are_appended_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    extra = [{"key": "confidentiality", "label": "保密义务", "keywords": ["保密", "泄露"]}]
    monkeypatch.setenv("REVIEW_CHECKLIST_JSON", json.dumps(extra))

    items = load_checklist()

    assert len(items) == 5
    assert items[-1].key == "confidentiality"
    assert "保密" in items[-1].keywords


def test_invalid_key_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "REVIEW_CHECKLIST_JSON", json.dumps([{"key": "Bad-Key!", "keywords": ["x"]}])
    )
    with pytest.raises(ValueError):
        load_checklist()


def test_duplicate_key_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "REVIEW_CHECKLIST_JSON", json.dumps([{"key": "payment_term", "keywords": ["付款"]}])
    )
    with pytest.raises(ValueError) as err:
        load_checklist()
    assert "payment_term" in str(err.value)


def test_malformed_json_fails_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REVIEW_CHECKLIST_JSON", "{not json")
    with pytest.raises(ValueError):
        load_checklist()
