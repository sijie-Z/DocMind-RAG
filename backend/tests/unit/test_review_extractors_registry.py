"""抽取器注册机制：默认 4 项走专用抽取器，配置项走通用关键词抽取器。

背景：`REVIEW_CHECKLIST_JSON` 追加的项（如 confidentiality）此前永远 `undetermined` ——
因为分析循环只认写死的 4 个专用抽取器，配置项没有抽取器就直接跳过。
用户加了一个项，却永远得到"无法判定"，等于配了个摆设。
"""

from __future__ import annotations

import pytest

from app.services.review_analysis_service import (
    EXTRACTORS,
    Clause,
    make_keyword_extractor,
    register_extractor,
    resolve_extractor,
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


class TestResolveExtractor:
    def test_default_items_keep_their_specialized_extractor(self) -> None:
        for key in ("payment_term", "start_condition", "acceptance_invoice", "exception"):
            resolved = resolve_extractor(key)
            assert resolved is EXTRACTORS[key], f"{key} 的默认抽取器被改变了"

    def test_configured_item_gets_generic_keyword_extractor(self) -> None:
        extractor = resolve_extractor("confidentiality", keywords=("保密", "泄露"))
        assert extractor is not None

        facts = extractor([_clause(1, "双方对商业秘密承担保密义务，泄露方承担责任。")])
        assert facts["status"] == "identified"
        assert facts.get("chunk_id") == "c1"

    def test_generic_extractor_reports_not_found_without_match(self) -> None:
        extractor = resolve_extractor("confidentiality", keywords=("保密", "泄露"))
        assert extractor is not None
        facts = extractor([_clause(1, "标的：采购办公设备一批。")])
        assert facts["status"] == "not_found"

    def test_unknown_item_without_keywords_resolves_to_none(self) -> None:
        assert resolve_extractor("no_such_item") is None


class TestRegisterExtractor:
    def test_registered_extractor_is_used(self) -> None:
        def _fake(clauses: list[Clause]) -> dict:
            return {"status": "identified", "chunk_id": "c9", "terms": ["自定义"]}

        register_extractor("custom_item")(_fake)
        assert resolve_extractor("custom_item") is _fake

        # 清理，避免污染其它用例
        EXTRACTORS.pop("custom_item", None)

    def test_duplicate_registration_rejected_by_default(self) -> None:
        with pytest.raises(ValueError):
            register_extractor("payment_term")(lambda clauses: {"status": "not_found"})


class TestGenericExtractorDoesNotInheritExceptionFilter:
    """通用抽取器不该套用支付项的"例外条款"过滤器。

    `_collect_terms` 原有过滤器是为 payment_term/start_condition/acceptance_invoice 设计的：
    "例外：…验收合格…" 不应被当成主验收条款。但配置项（如 confidentiality）继承它以后，
    只要命中文字出现在标题含「例外/暂缓/除外」的条款里，就会被报成 not_found —— 假阴性。
    """

    def test_configured_item_finds_term_inside_exception_clause(self) -> None:
        extractor = make_keyword_extractor(("保密",))
        clause = _clause(8, "第8条 例外：下列情形不适用保密义务，包括已公开信息。")

        facts = extractor([clause])

        assert facts["status"] == "identified", f"例外条款里的保密字样被漏掉: {facts}"
        assert "保密" in (facts.get("terms") or [])

    def test_payment_item_still_skips_exception_clause(self) -> None:
        # 行为保持：默认项仍跳过例外条款（避免把"例外"当成主付款条款）
        from app.services.review_analysis_service import EXTRACTORS

        clause = _clause(8, "第8条 例外：验收合格且发票齐全时另行付款。")
        facts = EXTRACTORS["payment_term"]([clause])
        assert facts["status"] == "not_found"
