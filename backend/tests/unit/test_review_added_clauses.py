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


# ── 真实文档（无「第N条」编号）场景：不能把"改了数字"误报成"新增条款" ──
# 实测（2026-11-15）：markdown 类文档没有条款编号，clause_no 为空，
# 行级比较失去条号依据，凡是文本有差异的行都会被当成新增（真实跑出 3 条假新增，
# 而实际只加了 1 条、改了 1 处数字）。


def _anon_clause(idx: int, text: str) -> Clause:
    return Clause(
        chunk_id=f"a{idx}", chunk_index=idx, text=text, raw_text=text, clause_no=[], section_no=[]
    )


def test_number_change_is_not_an_added_clause() -> None:
    base = [_anon_clause(1, "产品定位：面向中小企业，强调性价比与交付速度。")]
    revised = [_anon_clause(1, "产品定位：面向大型企业，强调性价比与交付速度。")]

    assert detect_added_clauses(base, revised) == []


def test_genuinely_new_paragraph_in_unnumbered_doc_is_added() -> None:
    base = [_anon_clause(1, "产品定位：面向中小企业，强调性价比与交付速度。")]
    revised = [
        _anon_clause(1, "产品定位：面向中小企业，强调性价比与交付速度。"),
        _anon_clause(
            2, "违约责任：逾期交付按日万分之五计收违约金，最高不超过合同总额的百分之二十。"
        ),
    ]

    added = detect_added_clauses(base, revised)
    assert len(added) == 1
    assert "违约金" in added[0].text


# ── 避免重复报同一处：清单项已识别的 chunk，不要再作为"新增条款"重复出现 ──
# 实测（2026-11-15）：配置项 confidentiality 已识别第88条，同时 added_4_1 又报了一遍，
# 审核人会在核验单上看到同一条款两次。


def test_clause_already_covered_by_checklist_item_is_not_reported_again() -> None:
    base = [_anon_clause(1, "标的：采购办公设备一批。")]
    revised = [
        _anon_clause(1, "标的：采购办公设备一批。"),
        _clause(4, "第88条 保密义务：双方对商业秘密承担保密责任，泄露方赔偿全部损失。"),
    ]

    both = detect_added_clauses(base, revised)
    assert len(both) == 1, "未排除时应该报出来"

    covered = {both[0].chunk_id}
    assert detect_added_clauses(base, revised, exclude_chunk_ids=covered) == []


def _multi_line_clause(chunk_id: str, lines: list[str]) -> Clause:
    text = "\n".join(lines)
    return Clause(
        chunk_id=chunk_id,
        chunk_index=0,
        text=text,
        raw_text=text,
        clause_no=[],
        section_no=[],
    )


class TestExclusionGranularity:
    """排除粒度应是「条款行」，不是整个 chunk。

    实测场景（单 chunk 小文档）：chunk 里既有已被清单项识别的第88条（保密），
    又有真正新增的第99条（违约金）。按 chunk 排除会把第99条一并吞掉 → 新增条款漏检。
    """

    def test_new_clause_in_same_chunk_as_covered_clause_is_still_reported(self) -> None:
        base = [_multi_line_clause("c1", ["第1条 标的：甲方向乙方采购办公设备一批。"])]
        revised = [
            _multi_line_clause(
                "c1",
                [
                    "第1条 标的：甲方向乙方采购办公设备一批。",
                    "第88条 保密义务：双方对商业秘密承担保密责任，泄露方赔偿全部损失。",
                    "第99条 违约责任：逾期交付按日万分之五计收违约金。",
                ],
            )
        ]

        # 清单项（confidentiality）已识别 chunk c1 的第88条
        added = detect_added_clauses(base, revised, exclude_refs={("c1", 88)})

        texts = [c.text for c in added]
        assert any("违约金" in t for t in texts), f"同 chunk 的新增条款被误吞: {texts}"
        assert not any("保密义务" in t for t in texts), "已识别的条款不应再报为新增"

    def test_whole_chunk_exclusion_still_supported_for_unnumbered(self) -> None:
        base = [_multi_line_clause("c1", ["标的：采购办公设备一批。"])]
        revised = [
            _multi_line_clause(
                "c1", ["标的：采购办公设备一批。", "违约金：逾期交付按日万分之五计收。"]
            )
        ]

        assert detect_added_clauses(base, revised, exclude_refs={("c1", None)}) == []
