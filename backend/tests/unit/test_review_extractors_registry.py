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
