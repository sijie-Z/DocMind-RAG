"""核验 golden set：结构约束 + 评测器敏感性 + 基线快照（D5，评审 A/B 加固）。

- 结构：12 组、每项 change_type 合法、用例文本 >500 字、期望证据条款号真实存在。
- 评测器不再“自证”：故意写错 gold 必须让指标下降（canary），比较器被削弱时变红。
- “每条 <5 句”用 embedding 调用计数钉住；触发即硬失败（分块器会吞异常回退）。
- 基线：指标 + 失败集合必须与 `evals/review_golden_baseline.json` 完全一致；
  任何变化（含提升）都必须显式 `make review-eval-baseline` 更新。
"""

from __future__ import annotations

import asyncio
import copy
import json

from evals import review_golden as golden
from evals.review_golden_cases import CASES

CHANGE_TYPES = {"unchanged", "modified", "added", "removed", "renumbered", "unidentified"}
ITEMS = {"payment_term", "start_condition", "acceptance_invoice", "exception"}


def _with_stub(monkeypatch):
    monkeypatch.setattr(golden.semantic_chunker_module, "embedding_service", golden._NoEmbedding())


def test_golden_cases_are_well_formed():
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids)), "case id 必须唯一"
    assert len(CASES) >= 12

    for case in CASES:
        assert len(case["base"]) > 500, f"{case['id']} base <500 字会退化为单 chunk"
        assert len(case["revised"]) > 500, f"{case['id']} revised <500 字会退化为单 chunk"
        assert set(case["items"]) == ITEMS
        for item, spec in case["items"].items():
            assert spec["change_type"] in CHANGE_TYPES, (case["id"], item)
            if spec.get("evidence"):
                side, clause_no = spec["evidence"]
                assert side in ("base", "revised")
                assert isinstance(clause_no, int)
            assert "facts" in spec


def test_golden_evidence_clause_numbers_exist_in_text(monkeypatch):
    """gold 引用的条款号必须在对应一侧的分块里出现过（防止手误）。"""
    _with_stub(monkeypatch)

    async def _run():
        for case in CASES:
            numbers = {}
            for side in ("base", "revised"):
                nums: set[int] = set()
                for clause in await golden._clauses(case[side]):
                    nums.update(clause.clause_no)
                numbers[side] = nums
            for item, spec in case["items"].items():
                if spec.get("evidence"):
                    side, clause_no = spec["evidence"]
                    assert clause_no in numbers[side], (
                        f"{case['id']}/{item}: 第{clause_no}条不在 {side} 文本的条款号里"
                    )

    asyncio.run(_run())


def test_cases_do_not_trigger_embedding(monkeypatch):
    """用例必须保持“每条 <5 句”：分块器若调用 embedding，会静默回退单 chunk。"""
    _with_stub(monkeypatch)
    asyncio.run(golden.evaluate())
    assert golden._NoEmbedding.calls == 0, "有用例触发了 embedding（分块路径已偏离生产）"


def test_embedding_trigger_is_hard_failure(monkeypatch):
    """≥5 句的长段落必须让评测硬失败，而不是静默回退。"""
    _with_stub(monkeypatch)
    long_text = "本合同条款如下。" + "甲方应在约定期限内履行相应义务并承担违约责任。" * 24
    assert len(long_text) > 500
    bad_case = {
        "id": "embedding_trigger",
        "base": long_text,
        "revised": long_text,
        "items": copy.deepcopy(CASES[0]["items"]),
    }
    try:
        asyncio.run(golden.evaluate([bad_case]))
    except RuntimeError as exc:
        assert "embedding" in str(exc)
    else:
        raise AssertionError("触发 embedding 的用例未被硬失败")


def test_evaluator_detects_broken_gold(monkeypatch):
    """敏感性 canary：每个核验项各写错一条 gold 都必须降指标（防比较器恒真）。"""
    _with_stub(monkeypatch)
    mutations = {
        "payment_term": {"status": "identified", "value": 999, "unit": "day"},
        "start_condition": {"status": "identified", "events": ["__missing__"]},
        "acceptance_invoice": {"status": "not_found"},
        "exception": {"status": "not_found"},
    }
    # 选 facts 为 identified 的用例；exception 用 exception_added（gold/实际均 identified）
    case_index = {"payment_term": 0, "start_condition": 0, "acceptance_invoice": 0, "exception": 4}
    for item, bad_facts in mutations.items():
        broken = copy.deepcopy(CASES)
        broken[case_index[item]]["items"][item]["facts"] = bad_facts
        result = asyncio.run(golden.evaluate(broken))
        assert result["metrics"]["per_item"][item]["extraction"] < 1.0, item
        assert any(d["item"] == item for d in result["details"]), item

    broken_change = copy.deepcopy(CASES)
    broken_change[0]["items"]["payment_term"]["change_type"] = "unidentified"
    result2 = asyncio.run(golden.evaluate(broken_change))
    assert result2["metrics"]["per_item"]["payment_term"]["change_type"] < 1.0


def test_golden_snapshot_matches_baseline(monkeypatch):
    """基线棘轮：指标与失败集合都必须与基线完全一致（任何变化=显式更新基线）。"""
    _with_stub(monkeypatch)
    result = asyncio.run(golden.evaluate())
    baseline = json.loads(golden.BASELINE_PATH.read_text(encoding="utf-8"))

    assert result["metrics"] == baseline["metrics"], (
        "核验 golden 指标与基线不一致；如为有意变化，请运行 "
        "`make review-eval-baseline` 并在 PR 中说明"
    )
    assert result["failures"] == baseline["failures"], (
        "失败集合与基线不一致（修好一例、弄坏一例会在这里暴露）"
    )
