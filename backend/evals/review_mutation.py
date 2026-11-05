"""核验 golden 变异敏感性自检（D12）：受控变异必须让 golden 失败。

这是 harness 的“有牙齿”自检：对核验引擎做已知会破坏行为的受控变异，
逐一跑完整 golden 评测；任何一个变异后指标仍全绿，说明对应守卫缺少
gold 覆盖，脚本以非零码退出。

用法（约 10 秒，离线确定性）：
    cd backend && python -m evals.review_mutation
"""

from __future__ import annotations

import asyncio
import re
import sys
from contextlib import contextmanager
from typing import Any, Iterator

# review_golden 在 import 期设置离线配置默认值（干净 clone 无 .env 也能跑），
# 必须先于 app.* 导入，否则配置加载会以生产模式校验 DATABASE_URL 失败。
from evals import review_golden as golden  # isort: skip
from app.services import review_analysis_service as analysis


@contextmanager
def _patched(target: Any, name: str, value: Any) -> Iterator[None]:
    original = getattr(target, name)
    setattr(target, name, value)
    try:
        yield
    finally:
        setattr(target, name, original)


@contextmanager
def _anchor_lexicon_reverted() -> Iterator[None]:
    """回退付款锚点词表（结清/付清/清偿/缴纳），模拟 D15 新词缺失。"""
    with _patched(analysis, "PAYMENT_ANCHOR_KEYWORDS", analysis.PAYMENT_KEYWORDS):
        yield


@contextmanager
def _payment_lexicon_reverted() -> Iterator[None]:
    """回退 D15 新增的付款动词/名词，模拟词表缺口。"""
    with _patched(
        analysis,
        "_PAYMENT_VERBS",
        ("支付", "付款", "结算"),
    ):
        with _patched(
            analysis,
            "_PAYMENT_NOUNS",
            (
                "付款",
                "货款",
                "价款",
                "账期",
                "结算",
                "款项",
                "尾款",
                "进度款",
                "服务费",
                "预付款",
                "首付款",
                "余款",
            ),
        ):
            yield


def _permissive_colon_replacement(piece: str) -> str | None:
    """旧版过宽上下文：付款名词/起算事件/期限任一命中就保留后缀。"""
    colon = analysis._HEADING_SPLIT_RE.search(piece)
    if colon is None:
        return None
    suffix = piece[colon.end() :]
    if not suffix:
        return None
    if not (
        any(noun in suffix for noun in analysis._PAYMENT_NOUNS)
        or any(event in suffix for event in analysis.START_EVENTS)
        or analysis._contains_duration(suffix)
    ):
        return None
    return analysis._MASK + colon.group(0) + suffix


def _hint_only_heading_end(text: str, heading_hint: str | None = None) -> int:
    """hint 失配时不按当前文本回退的旧行为（期限屏蔽后标题无法剥离）。"""
    if not heading_hint:
        return 0
    index = text.find(heading_hint)
    if index == -1 or index > 20:
        return 0
    end = index + len(heading_hint)
    if text[end : end + 1] in ":：":
        end += 1
    return end


def _uncapped_heading_end(text: str, heading_hint: str | None = None) -> int:
    """忽略标题 hint、全文找冒号的旧行为：正文冒号也会被当标题分隔符。"""
    match = analysis._CLAUSE_NUMBER_PREFIX_RE.match(text)
    start = match.end() if match else 0
    if start < len(text) and text[start] in ":：":
        start += 1
    colon = analysis._HEADING_SPLIT_RE.search(text, start)
    return colon.end() if colon else 0


def _events_without_dedupe(text: str, events: tuple[str, ...]) -> set[str]:
    """去除重叠去重后的行为：只要词出现在文本里就算命中（合同生效之日起 双计）。"""
    return {event for event in events if event in text}


def _permissive_reference_re() -> re.Pattern[str]:
    """把引用短语的标点边界去掉（逗号/冒号可跨越），模拟评审 M4 变异。"""
    return re.compile(
        r"(?:付款期限|支付期限|付款时间|支付时间|付款日期|支付日期|付款节点|支付节点|"
        r"付款安排|支付安排|结算期限|结算周期|结算时间|账期)"
        r"[^。；;]{0,12}?"
        r"(?:详见|参见|见|按照|按|依据|依照|根据|参照|以)"
        r"(?:本合同|本协议)?第[一二三四五六七八九十百零〇0-9]+条"
        r"(?:\s*(?:[、,，和及与]|以及|至)\s*第[一二三四五六七八九十百零〇0-9]+条)*"
    )


def _mutation_specs() -> list[tuple[str, str, Any]]:
    return [
        (
            "start_event_contract_effective_removed",
            "删除起算事件「合同生效」",
            lambda: _patched(
                analysis,
                "START_EVENTS",
                tuple(event for event in analysis.START_EVENTS if event != "合同生效"),
            ),
        ),
        (
            "event_overlap_dedupe_disabled",
            "关闭事件重叠去重（合同生效之日起 双计）",
            lambda: _patched(analysis, "_non_overlapping_events", _events_without_dedupe),
        ),
        (
            "reference_following_disabled",
            "关闭付款条款引用跟随",
            lambda: _patched(
                analysis, "_payment_reference_clauses", lambda clauses, payment: ([], False)
            ),
        ),
        (
            "referenced_action_guard_disabled",
            "关闭被引用期限的动作谓语守卫",
            lambda: _patched(
                analysis, "_referenced_duration_ok", lambda sentence, start, end: True
            ),
        ),
        (
            "reference_punctuation_guard_disabled",
            "允许引用跨逗号/冒号（去掉引用短语标点边界）",
            lambda: _patched(analysis, "_PAYMENT_REF_RE", _permissive_reference_re()),
        ),
        (
            "liability_exclusion_disabled",
            "关闭违约/赔偿条款的起算排除",
            lambda: _patched(analysis, "_is_liability_article", lambda clause: False),
        ),
        (
            "liability_sentence_masking_disabled",
            "关闭责任句屏蔽（违约金句污染起算/哈希）",
            lambda: _patched(analysis, "_mask_liability_sentences", lambda text, **kwargs: text),
        ),
        (
            "liability_conjunction_split_disabled",
            "关闭正向责任连词切分（支付货款并赔偿损失 整片段屏蔽）",
            lambda: _patched(
                analysis,
                "_LIABILITY_CONJUNCTION_RE",
                re.compile(r"(?!x)x"),
            ),
        ),
        (
            "reverse_conjunction_split_disabled",
            "关闭反向语序连词切分（支付违约金并在…付款 整片段屏蔽）",
            lambda: _patched(
                analysis,
                "_PAYMENT_CONJUNCTION_RE",
                re.compile(r"(?!x)x"),
            ),
        ),
        (
            "heading_colon_uncapped",
            "标题冒号不设 40 字上限（正文冒号被当标题）",
            lambda: _patched(analysis, "_heading_end", _uncapped_heading_end),
        ),
        (
            "heading_hint_no_fallback",
            "标题 hint 失配时不按当前文本回退（期限屏蔽后标题不剥离）",
            lambda: _patched(analysis, "_heading_end", _hint_only_heading_end),
        ),
        (
            "liability_colon_split_disabled",
            "关闭责任片段正文冒号切分（违约金：付款期限… 整段屏蔽）",
            lambda: _patched(analysis, "_liability_colon_replacement", lambda piece: None),
        ),
        (
            "liability_colon_context_too_broad",
            "正文冒号后缀上下文过宽（罚则计算/整改/损失被保留）",
            lambda: _patched(
                analysis, "_liability_colon_replacement", _permissive_colon_replacement
            ),
        ),
        (
            "payment_lexicon_reverted",
            "回退新增付款动词/名词（结清/付清/合同总价等）",
            lambda: _payment_lexicon_reverted(),
        ),
        (
            "payment_anchor_lexicon_reverted",
            "回退付款锚点词表（结清/付清/清偿/缴纳）",
            lambda: _anchor_lexicon_reverted(),
        ),
        (
            "object_exclusion_veto_disabled",
            "关闭税费/社保/账目类片段级否决（同义词改写回退）",
            lambda: _patched(analysis, "_object_exclusion_present", lambda fragment: False),
        ),
        (
            "object_exclusion_neutralization_disabled",
            "关闭排除词的付款宾语中性化（增值税专用发票/含税价款回退）",
            lambda: _patched(
                analysis,
                "_object_exclusion_is_modifier",
                lambda fragment, term, start, end: False,
            ),
        ),
        (
            "weak_anchor_object_guard_disabled",
            "关闭弱支付动词宾语守卫（结清关系/缴纳相关税费被吸成付款）",
            lambda: _patched(analysis, "_weak_anchor_object_ok", lambda fragment, anchor_end: True),
        ),
        (
            "penalty_colon_scope_disabled",
            "关闭责任头冒号否决（违约金：相关费用缴纳 回退成付款）",
            lambda: _patched(analysis, "_penalty_colon_scope", lambda fragment: False),
        ),
        (
            "reverse_subject_preservation_disabled",
            "反向语序付款主体不保留（甲方→乙方 假 unchanged）",
            lambda: _patched(analysis, "_leading_subject", lambda prefix: ""),
        ),
        (
            "liability_suffix_nonpayment_object_disabled",
            "关闭责任句后缀非付款宾语守卫（缴纳社保费用泄漏起算）",
            lambda: _patched(analysis, "_has_nonpayment_object", lambda text: False),
        ),
    ]


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")

    original_embedding = golden.semantic_chunker_module.embedding_service
    golden.semantic_chunker_module.embedding_service = golden._NoEmbedding()
    try:
        golden._NoEmbedding.calls = 0
        baseline = asyncio.run(golden.evaluate())
        if baseline["failures"]:
            print("基线本身存在失败，先修好再跑变异自检：")
            for failure in baseline["failures"]:
                print("  -", failure)
            return 1
        print(f"baseline: {baseline['metrics']['cases']} 组，失败 0")

        undetected = 0
        for name, description, factory in _mutation_specs():
            with factory():
                result = asyncio.run(golden.evaluate())
            failures = result["failures"]
            if failures:
                print(f"[DETECTED] {name}: {description}（{len(failures)} 条失败）")
                for failure in failures[:3]:
                    print("    -", failure)
            else:
                undetected += 1
                print(f"[MISSED] {name}: {description} —— 变异后 golden 仍全绿，需补 gold/度量")

        if undetected:
            print(f"\n{undetected} 个变异未被 golden 捕获")
            return 1
        print("\n所有变异均被 golden 捕获")
        return 0
    finally:
        golden.semantic_chunker_module.embedding_service = original_embedding


if __name__ == "__main__":
    raise SystemExit(main())
