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


def _item_with_facts(key: str, change_type: str | None, facts: dict | None) -> ReviewItem:
    return ReviewItem(
        id=key, case_id="c1", item_key=key, ordinal=1, change_type=change_type, facts=facts
    )


def test_summary_lists_reasons_for_undetermined_items() -> None:
    """「如实呈现」要带原因：审核人得知道为什么判不了。"""
    facts = {
        "base": {"status": "ambiguous", "reason": "unit_unclear"},
        "revised": {"status": "not_found"},
    }
    items = [
        _item_with_facts("payment_term", "unidentified", facts),
        _item_with_facts("confidentiality", None, None),
        _item_with_facts("exception", "modified", {"base": {"status": "identified"}}),
    ]

    summary = summarize_items(items)

    reasons = {entry["item_key"]: entry for entry in summary["undetermined_items"]}
    assert set(reasons) == {"payment_term", "confidentiality"}
    assert reasons["payment_term"]["reasons"] == [
        "base:ambiguous:unit_unclear",
        "revised:not_found",
    ]
    assert reasons["confidentiality"]["reasons"] == ["not_analyzed"]


def test_summary_undetermined_items_empty_when_all_identified() -> None:
    items = [_item_with_facts("exception", "modified", {"base": {"status": "identified"}})]
    assert summarize_items(items)["undetermined_items"] == []


def test_unchanged_is_not_counted_as_undetermined() -> None:
    """`unchanged` 是明确的判定结果，不是"无法判定"。

    实测（2026-11-15 真栈导出）：5 项里 2 项 change_type='unchanged'，
    summary 却报 undetermined_count=2、has_undetermined=True，
    界面因此对"其实已经判清楚了"的案件显示「需人工复核 / N 项无法判定」——反向误报。
    """
    items = [
        _item("payment_term", "modified"),
        _item("start_condition", "unchanged"),
        _item("acceptance_invoice", "unchanged"),
        _item("added_9_1", "added"),
    ]

    summary = summarize_items(items)

    assert summary["unchanged_count"] == 2
    assert summary["undetermined_count"] == 0
    assert summary["has_undetermined"] is False
    assert summary["undetermined_items"] == []


def test_all_unchanged_means_no_difference_confirmed() -> None:
    items = [_item("payment_term", "unchanged"), _item("exception", "unchanged")]
    summary = summarize_items(items)
    assert summary["has_undetermined"] is False
    assert summary["changed_count"] == 0
    assert summary["undetermined_items"] == []
