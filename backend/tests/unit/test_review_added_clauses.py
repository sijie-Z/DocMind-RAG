"""新增条款检测：revised 有、base 无的条款要能进入核验，而不是被静默跳过。

背景（2026-11-15 实测，docs/review-quality-baseline.md）：
核验只分析固定 4 项清单（payment_term / start_condition / acceptance_invoice / exception），
清单之外的**新增条款**（例如 V2 多了「第10条 违约金」）完全不会出现在核验结果里 ——
而"合同里多了一条不利条款"正是合同核验最主要的风险。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.services.review_analysis_service import (
    Clause,
    _covered_terms,
    detect_added_clauses,
)


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


class TestUnnumberedChunkCoveredByItem:
    """无编号文档：清单项已命中的 chunk 里，新增的其它义务仍要可见。

    现场（2026-11-20）：无编号文档没有条款号可用，`_covered_refs` 退回
    `(chunk_id, None)` 整块排除。于是「这个 chunk 里已经识别到验收/付款义务」
    会把**同 chunk 里新增的违约金条款**一起吞掉 —— 而白纸黑字多出一条不利条款，
    正是核验最该报出来的事。整块排除只是"拿不到更细依据"时的保底，不该是常态。
    """

    COVERED = "甲方应在验收合格后三十日内支付货款。"
    ADDED = "乙方逾期交付的，应按合同总金额的百分之五支付违约金。"
    TERMS = ("验收", "发票")

    def test_new_obligation_in_covered_unnumbered_chunk_is_reported(self) -> None:
        base = [_anon_clause(1, self.COVERED)]
        revised = [_anon_clause(1, self.COVERED + self.ADDED)]

        added = detect_added_clauses(
            base,
            revised,
            exclude_refs={("a1", None)},
            exclude_terms_by_chunk={"a1": self.TERMS},
        )

        texts = " ".join(c.text for c in added)
        assert "违约金" in texts, (
            f"无编号 chunk 里的新增义务被整块排除吞掉: {[c.text for c in added]}"
        )
        assert "验收合格" not in texts, f"已识别的义务不应再报为新增: {texts}"

    def test_without_covered_terms_whole_chunk_exclusion_still_applies(self) -> None:
        base = [_anon_clause(1, self.COVERED)]
        revised = [_anon_clause(1, self.COVERED + self.ADDED)]

        # 拿不到命中词 → 退回整块排除（保守：宁可漏，也不做没有依据的重复报）
        assert detect_added_clauses(base, revised, exclude_refs={("a1", None)}) == []

    def test_punctuation_split_in_covered_unnumbered_chunk_is_not_added(self) -> None:
        # 同一句只改标点（，→。）：拆出的两段都是 base 原文子串，不是新增。
        # 没有子串守卫时，两段相似度都会掉到阈值以下 → 被误报成 2 条新增。
        base = [
            _anon_clause(
                1,
                "乙方应当对本合同项下全部商业秘密承担保密义务，"
                "保密期限为五年，期满后仍应继续承担保密义务。",
            )
        ]
        revised = [
            _anon_clause(
                1,
                "乙方应当对本合同项下全部商业秘密承担保密义务。"
                "保密期限为五年，期满后仍应继续承担保密义务。",
            )
        ]

        added = detect_added_clauses(
            base,
            revised,
            exclude_refs={("a1", None)},
            exclude_terms_by_chunk={"a1": self.TERMS},
        )

        assert added == [], f"仅改标点不应报新增: {[c.text for c in added]}"


class TestCoveredTerms:
    """`_covered_terms` 只收集「无条款号」位置的命中词。

    有条号时行级排除已足够，词表不该被误用（否则会把同块里有条号的新增也按词过滤掉）。
    """

    @staticmethod
    def _item(facts):
        return SimpleNamespace(facts=facts)

    def test_collects_terms_only_for_unnumbered_hits(self) -> None:
        items = [
            self._item({"revised": {"status": "identified", "chunk_id": "a1", "terms": ["验收"]}}),
            # 有条号 → 行级排除负责，不进词表
            self._item(
                {
                    "revised": {
                        "status": "identified",
                        "chunk_id": "c9",
                        "clause_no": [9],
                        "terms": ["保密"],
                    }
                }
            ),
        ]

        assert _covered_terms(items) == {"a1": ("验收",)}

    def test_ignores_items_without_chunk_or_terms(self) -> None:
        items = [
            self._item({"revised": {"status": "not_found"}}),
            self._item({"revised": {"status": "identified", "chunk_id": "a1"}}),
            self._item({"revised": {"status": "identified", "chunk_id": "a2", "terms": []}}),
        ]

        assert _covered_terms(items) == {}


LONG_BASE = (
    "甲方应当在收到乙方开具的增值税专用发票并完成内部审批流程后，"
    "于验收合格之日起三十日内向乙方支付合同价款，付款方式为银行转账，"
    "账户信息以合同附件二为准，如账户变更乙方应提前书面通知甲方。"
)
NEW_SENTENCE = "保密义务：双方对合同内容及履行中知悉的商业秘密承担保密责任，泄露方赔偿全部损失。"
REWORDED = (
    "甲方应在收到乙方开具的增值税专用发票并完成内部审批后，"
    "于验收合格之日起三十日内向乙方支付合同价款，付款方式为银行转账，"
    "账户信息以合同附件二为准，如账户变更乙方需提前书面通知甲方。"
)


class TestSentenceLevelNewContent:
    """并入段落的真正新增内容必须可见（issue #714）。"""

    def test_appended_sentence_inside_same_line_is_reported(self) -> None:
        base = [_multi_line_clause("c1", [LONG_BASE])]
        revised = [_multi_line_clause("c1", [LONG_BASE + NEW_SENTENCE])]

        added = detect_added_clauses(base, revised)

        texts = " ".join(c.text for c in added)
        assert "保密义务" in texts, f"并入段落的新增句被静默吞掉: {[c.text[:20] for c in added]}"
        # 报出文本只含新句子，不应把整段旧内容再贴一遍
        assert LONG_BASE[:12] not in texts, f"不应把旧段落内容当作新增: {texts[:60]}"

    def test_appended_sentence_as_separate_line_is_reported(self) -> None:
        base = [_multi_line_clause("c1", [LONG_BASE])]
        revised = [_multi_line_clause("c1", [LONG_BASE, NEW_SENTENCE])]

        added = detect_added_clauses(base, revised)
        assert any("保密义务" in c.text for c in added)

    def test_pure_rewording_produces_no_added_noise(self) -> None:
        base = [_multi_line_clause("c1", [LONG_BASE])]
        revised = [_multi_line_clause("c1", [REWORDED])]

        assert detect_added_clauses(base, revised) == [], "同义改写不应产生新增噪音"


# ── 编号条款内追加的新增句 ──
# 缺口（2026-11-16 演示实测）：只要该行条号在 base 里已存在就整行 `continue`，
# 句子级检测（#714）根本不会执行 —— 被追加进「第X条」的新义务既不在 4 项清单
# 覆盖范围内，也不作为新增报出，等于对审核人静默隐藏。

NUMBERED_PAYMENT_BASE = "第5条 付款期限：验收合格后30日内支付货款。"
NUMBERED_PAYMENT_APPENDED = (
    "第5条 付款期限：验收合格后30日内支付货款。此外，逾期未对账的视为甲方确认本期货款无误。"
)


class TestNumberedClauseAppendedSentence:
    """条号已存在 ≠ 没有新增：改写顺带追加的新义务必须可见。"""

    def test_sentence_appended_to_numbered_clause_is_reported(self) -> None:
        base = [
            _clause(1, "第1条 标的：甲方向乙方采购办公设备一批。"),
            _clause(5, NUMBERED_PAYMENT_BASE),
        ]
        revised = [
            _clause(1, "第1条 标的：甲方向乙方采购办公设备一批。"),
            _clause(5, NUMBERED_PAYMENT_APPENDED),
        ]

        added = detect_added_clauses(base, revised)

        texts = " ".join(c.text for c in added)
        assert "对账" in texts, f"编号条款内追加的义务被静默吞掉: {[c.text for c in added]}"
        # 只报新句，不能把整条旧条款再贴一遍
        assert "验收合格后30日内支付货款" not in texts, f"不应把旧条款内容当作新增: {texts}"

    def test_appended_sentence_keeps_clause_locator(self) -> None:
        base = [_clause(5, NUMBERED_PAYMENT_BASE)]
        revised = [_clause(5, NUMBERED_PAYMENT_APPENDED)]

        added = detect_added_clauses(base, revised)

        assert len(added) == 1, [c.text for c in added]
        # 引用定位要落在原条款上，审核人才能回溯到原文
        assert added[0].clause_no == [5]
        assert added[0].chunk_id == "c5"

    def test_numbered_clause_value_change_is_still_not_added(self) -> None:
        # 纯数值修改（30日→15日）走清单抽取器判 modified，不能在这里重复报成新增
        base = [_clause(5, "第5条 付款期限：验收合格后30日内支付货款。")]
        revised = [_clause(5, "第5条 付款期限：验收合格后15日内支付货款。")]

        assert detect_added_clauses(base, revised) == []

    def test_punctuation_split_in_numbered_clause_is_not_added(self) -> None:
        # 同一句只改了标点（，→。）：拆出来的两段都是 base 原文的子串，不是新增。
        # 没有子串守卫时这两段相似度都会掉到阈值以下，被误报成 2 条新增。
        base = [
            _clause(
                5,
                "第5条 乙方应当对本合同项下全部商业秘密承担保密义务，"
                "保密期限为五年，期满后仍应继续承担保密义务。",
            )
        ]
        revised = [
            _clause(
                5,
                "第5条 乙方应当对本合同项下全部商业秘密承担保密义务。"
                "保密期限为五年，期满后仍应继续承担保密义务。",
            )
        ]

        assert detect_added_clauses(base, revised) == [], "仅改标点不应报新增"

    def test_identical_numbered_clause_is_not_added(self) -> None:
        base = [_clause(5, NUMBERED_PAYMENT_BASE)]
        assert detect_added_clauses(base, base) == []

    def test_covered_numbered_clause_is_not_reported_twice(self) -> None:
        # 该条款已被清单项识别（exclude_refs 含 (chunk_id, clause_no)）：
        # 追加句由那条清单项判 modified，核验单上不能再多出一条 added
        base = [_clause(5, NUMBERED_PAYMENT_BASE)]
        revised = [_clause(5, NUMBERED_PAYMENT_APPENDED)]

        assert detect_added_clauses(base, revised, exclude_refs={("c5", 5)}) == []

    def test_uncovered_numbered_clause_append_is_reported(self) -> None:
        # 追加义务落在清单未覆盖的条款（第12条 对账）→ 必须报出来，
        # 这正是「清单之外的风险内容」的典型形态
        base = [
            _clause(5, NUMBERED_PAYMENT_BASE),
            _clause(12, "第12条 双方应于每月十日前完成对账。"),
        ]
        revised = [
            _clause(5, NUMBERED_PAYMENT_BASE),
            _clause(
                12,
                "第12条 双方应于每月十日前完成对账。逾期未对账的，视为甲方确认本期货款无误。",
            ),
        ]

        added = detect_added_clauses(base, revised, exclude_refs={("c5", 5)})

        texts = " ".join(c.text for c in added)
        assert "逾期未对账" in texts, [c.text for c in added]
        assert "每月十日前完成对账" not in texts, f"不应把旧条款内容当作新增: {texts}"
