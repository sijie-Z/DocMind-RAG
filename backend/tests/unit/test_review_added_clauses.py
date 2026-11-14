"""新增条款检测：revised 有、base 无的条款要能进入核验，而不是被静默跳过。

背景（2026-11-15 实测，docs/review-quality-baseline.md）：
核验只分析固定 4 项清单（payment_term / start_condition / acceptance_invoice / exception），
清单之外的**新增条款**（例如 V2 多了「第10条 违约金」）完全不会出现在核验结果里 ——
而"合同里多了一条不利条款"正是合同核验最主要的风险。
"""

from __future__ import annotations

from app.services.review_analysis_service import Clause, detect_added_clauses


def _clause(idx: int, text: str) -> Clause:
    return Clause(
        chunk_id=f"c{idx}",
        chunk_index=idx,
        text=text,
        raw_text=text,
        clause_no=[idx],
        section_no=[],
    )


BASE = [
    _clause(1, "第1条 标的：甲方向乙方采购办公设备一批。"),
    _clause(2, "第5条 付款期限：验收合格后30日内支付货款。"),
    _clause(3, "第9条 归档：合同与验收单保存不少于五年。"),
]

REVISED = [
    _clause(1, "第1条 标的：甲方向乙方采购办公设备一批。"),
    _clause(2, "第5条 付款期限：验收合格后15日内支付货款。"),
    _clause(3, "第9条 归档：合同与验收单保存不少于五年。"),
    _clause(4, "第10条 违约金：逾期付款按日万分之五计收违约金。"),
]


def test_detects_clause_present_only_in_revised() -> None:
    added = detect_added_clauses(BASE, REVISED)

    assert len(added) == 1
    assert "违约金" in added[0].text
    assert added[0].chunk_id == "c4"


def test_modified_clause_is_not_reported_as_added() -> None:
    # 付款期限 30→15 是"修改"，不是"新增"：它会在 base 里有近似条款（同一条号）
    added = detect_added_clauses(BASE, REVISED)
    assert all("付款期限" not in c.text for c in added)


def test_identical_documents_yield_nothing() -> None:
    assert detect_added_clauses(BASE, BASE) == []


def test_short_and_duplicate_clauses_are_ignored() -> None:
    revised = [
        *REVISED,
        _clause(5, "附"),  # 太短
        _clause(6, "第10条 违约金：逾期付款按日万分之五计收违约金。"),  # 重复
    ]
    added = detect_added_clauses(BASE, revised)
    assert len(added) == 1


def test_limit_caps_items() -> None:
    revised = [
        *REVISED,
        *[_clause(10 + i, f"第{20 + i}条 额外约定内容第{i}项，需人工确认。") for i in range(6)],
    ]
    assert len(detect_added_clauses(BASE, revised, limit=3)) == 3

# ── 真实链路补充：小文档常被切成 1 个 chunk，整篇文本落在一个 Clause 里 ──
# 此时按「条号交集」判定会把整块都当成"修改"而漏掉新增条款，
# 必须能下沉到行级（第X条）比较。

BASE_ONE_CHUNK = [
    _clause(
        1,
        "采购合同（V1）\n第1条 标的：甲方向乙方采购办公设备一批。\n"
        "第5条 付款期限：验收合格后30日内支付货款。\n"
        "第9条 归档：合同与验收单保存不少于五年。",
    )
]

REVISED_ONE_CHUNK = [
    _clause(
        1,
        "采购合同（V2）\n第1条 标的：甲方向乙方采购办公设备一批。\n"
        "第5条 付款期限：验收合格后15日内支付货款。\n"
        "第9条 归档：合同与验收单保存不少于五年。\n"
        "第10条 违约金：逾期付款按日万分之五计收违约金。",
    )
]


def test_detects_added_clause_even_when_whole_doc_is_one_chunk() -> None:
    added = detect_added_clauses(BASE_ONE_CHUNK, REVISED_ONE_CHUNK)

    texts = [c.text for c in added]
    assert len(added) == 1, f"期望 1 条新增，实际 {texts}"
    assert "违约金" in texts[0]
