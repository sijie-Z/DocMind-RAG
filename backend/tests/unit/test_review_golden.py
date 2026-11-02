"""核验 golden set：结构约束 + 基线棘轮（D5）。

- 结构：10 组、每项 change_type 合法、用例文本 >500 字（保证走条款切分且不触发
  embedding）、期望里引用的条款号确实出现在对应文本中。
- 基线：评测结果必须与 `evals/review_golden_baseline.json` 完全一致；提升或回归
  都必须显式 `make review-eval-baseline` 更新基线（评审可见的 PR 行为）。
"""

from __future__ import annotations

import asyncio
import json

from evals import review_golden as golden
from evals.review_golden_cases import CASES

CHANGE_TYPES = {"unchanged", "modified", "added", "removed", "renumbered", "unidentified"}
ITEMS = {"payment_term", "start_condition", "acceptance_invoice", "exception"}


def test_golden_cases_are_well_formed():
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids)), "case id 必须唯一"
    assert len(CASES) >= 10

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
    monkeypatch.setattr(golden.semantic_chunker_module, "embedding_service", golden._NoEmbedding())

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


def test_golden_metrics_match_baseline(monkeypatch):
    """基线棘轮：任何指标变化（回归或未登记提升）都必须显式更新基线。"""
    monkeypatch.setattr(golden.semantic_chunker_module, "embedding_service", golden._NoEmbedding())

    result = asyncio.run(golden.evaluate())
    baseline = json.loads(golden.BASELINE_PATH.read_text(encoding="utf-8"))

    assert result["metrics"] == baseline, (
        "核验 golden 指标与基线不一致；如为有意变化，请运行 "
        "`make review-eval-baseline` 并在 PR 中说明"
    )
