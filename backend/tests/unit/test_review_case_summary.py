"""案件详情要能一眼看出「有几项已识别差异、几项无法判定」。

为什么：`case_detail` 之前只返回 items，计数只在列表接口 `_case_summary` 里有。
详情页要判断"能不能说没有差异"，必须自己遍历 items 重算；一旦前端算漏，
就会出现「明明有 1 项未识别，界面却显示无差异」——正是 baseline 文档里担心的
"把不确定说成没差异"。
"""

from __future__ import annotations

from app.models.review import ReviewItem
from app.services.review_case_service import summarize_items


def _item(key: str, change_type: str | None) -> ReviewItem:
    return ReviewItem(id=key, case_id="c1", item_key=key, ordinal=1, change_type=change_type)


def test_counts_each_change_type() -> None:
    items = [
        _item("payment_term", "modified"),
        _item("start_condition", "modified"),
        _item("acceptance_invoice", "removed"),
        _item("added_1_1", "added"),
        _item("exception", "unidentified"),
    ]

    summary = summarize_items(items)

    assert summary["item_count"] == 5
    assert summary["changed_count"] == 2
    assert summary["removed_count"] == 1
    assert summary["added_count"] == 1
    assert summary["unidentified_count"] == 1
    assert summary["undetermined_count"] == 0
    # 有未识别项 → 不得宣称"无差异"
    assert summary["has_undetermined"] is True


def test_unanalyzed_items_also_block_no_difference_claim() -> None:
    items = [_item("payment_term", None), _item("exception", "modified")]

    summary = summarize_items(items)

    assert summary["undetermined_count"] == 1
    assert summary["has_undetermined"] is True


def test_all_identified_and_no_changes_allows_clean_claim() -> None:
    items = [_item("payment_term", "unidentified")]  # 未识别仍算不确定
    assert summarize_items(items)["has_undetermined"] is True

    identified = [_item("payment_term", "modified")]
    summary = summarize_items(identified)
    assert summary["has_undetermined"] is False
    assert summary["changed_count"] == 1
