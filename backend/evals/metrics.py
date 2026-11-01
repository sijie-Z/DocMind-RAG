"""指标计算：ClauseHit@k / NearMiss / Empty / MRR / Wilson 区间（纯标准库）。

口径定义（同时是文档口径，见 backend/docs/eval-gate-phase1.md）：

- **ClauseHit@k** = 正例（expect=hit）中，「top-k 里存在某个 chunk，其文件名等于
  gold 文件、且其**定义位**包含 gold 条号」的比例。命中判定只用
  clauses.clause_definitions（行首定义位），不数正文引用。
- **NearMiss@k**（诊断，越低越好）= 声明了 near_miss 的正例中，
  top-k 里出现任一 near_miss 条号定义位的比例。
  「gold 未中且 near-miss 中了」就是「问第七条召回第十二条」的原形。
- **EmptyAcc**（负例正确率）= 负例（expect=empty）中，检索结果为空的比例。
- **MRR** = 正例中首个 gold 命中所处名次的倒数均值（未命中记 0）。
- 小样本一律带 **Wilson 95% 区间**（z=1.96）。

错误语义：单条查询执行失败（异常）记为 status=error，
不计入任何比例的分母（单独计数并报告），绝不静默当成 miss。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .clauses import clause_definitions
from .golden import GoldenItem, Locator, NearMiss


@dataclass
class ItemOutcome:
    """单条 golden 的判定结果。"""

    item_id: str
    item_type: str
    expect: str
    status: str = "ok"  # ok | error
    error: str | None = None
    passed: bool = False
    gold_rank: int | None = None
    matched_gold: list[str] = field(default_factory=list)
    near_miss_rank: int | None = None
    matched_near_miss: list[str] = field(default_factory=list)
    n_results: int = 0
    top1_file: str | None = None
    top1_defs: list[str] = field(default_factory=list)
    strategy: str | None = None
    declares_near_miss: bool = False
    # 条款判定所用文本的依据：original（可验证 O）/ compat（回退 E）/ mixed / none。
    text_basis: str = "none"


# 产品契约：证据正文以 O 为准（text_provenance=parser_original 时才可信）；
# 没有可验证 O 时才回退到 E，并如实记录依据（与 retriever/pipeline 的
# PARSER_ORIGINAL 常量保持一致，evals 不 import app 以保持纯标准库）。
_PARSER_ORIGINAL = "parser_original"


def result_text(result: dict) -> tuple[str, str]:
    """返回（用于条款判定的文本, 依据 original/compat）。

    E（content/prompt_text）含上下文增强前缀，行首定义位可能失真；
    只有带 provenance 标记的 original_text 才能作为条款判定依据。
    """
    original = result.get("original_text")
    if (
        str(result.get("text_provenance") or "") == _PARSER_ORIGINAL
        and isinstance(original, str)
        and original.strip()
    ):
        return original, "original"
    fallback = (
        result.get("prompt_text")
        or result.get("content")
        or result.get("text")
        or result.get("snippet")
        or ""
    )
    return str(fallback), "compat"


def _match_items(
    results: list[dict], locators: tuple[Locator, ...] | tuple[NearMiss, ...]
) -> tuple[int | None, list[str]]:
    """返回（首个命中名次 1-based，该名次命中的定位标签列表）。"""
    if not locators:
        return None, []
    for rank, result in enumerate(results, 1):
        filename = str(result.get("filename") or "")
        defs = clause_definitions(result_text(result)[0])
        matched = [loc.label() for loc in locators if filename == loc.file and loc.clause in defs]
        if matched:
            return rank, matched
    return None, []


def evaluate_item(
    item: GoldenItem,
    results: list[dict],
    *,
    status: str = "ok",
    error: str | None = None,
    strategy: str | None = None,
) -> ItemOutcome:
    """对单条 golden 的检索结果做判定（纯函数，便于单元测试判别）。"""
    bases = {result_text(result)[1] for result in results}
    if not results:
        text_basis = "none"
    elif len(bases) > 1:
        text_basis = "mixed"
    else:
        text_basis = bases.pop()
    outcome = ItemOutcome(
        item_id=item.id,
        item_type=item.type,
        expect=item.expect,
        status=status,
        error=error,
        strategy=strategy,
        n_results=len(results),
        declares_near_miss=bool(item.near_miss),
        text_basis=text_basis,
    )
    if results:
        outcome.top1_file = str(results[0].get("filename") or "") or None
        outcome.top1_defs = sorted(clause_definitions(result_text(results[0])[0]))
    if status == "error":
        return outcome

    if item.expect == "hit":
        outcome.gold_rank, outcome.matched_gold = _match_items(results, item.gold)
        outcome.passed = outcome.gold_rank is not None
    else:  # empty
        outcome.passed = len(results) == 0

    if item.near_miss:
        outcome.near_miss_rank, outcome.matched_near_miss = _match_items(results, item.near_miss)
    return outcome


def wilson_ci(successes: int, total: int, z: float = 1.96) -> tuple[float, float] | None:
    """Wilson 得分区间（95% 默认）；total=0 返回 None。"""
    if total <= 0:
        return None
    p = successes / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return max(0.0, center - margin), min(1.0, center + margin)


def _rate(numer: int, denom: int) -> float | None:
    return (numer / denom) if denom else None


def aggregate(outcomes: list[ItemOutcome], cutoffs: tuple[int, ...] = (1, 3, 5)) -> dict:
    """汇总指标。错误项不进分母；分项计数如实报告。

    ``counts.text_basis`` 只统计有检索结果的条目（original/compat/mixed），
    空结果与错误不参与——它回答的是「本次判定依据了哪种正文」。
    """
    ok = [o for o in outcomes if o.status == "ok"]
    errors = [o for o in outcomes if o.status == "error"]
    positives = [o for o in ok if o.expect == "hit"]
    negatives = [o for o in ok if o.expect == "empty"]
    nm_positives = [o for o in positives if o.declares_near_miss]

    basis_counts: dict[str, int] = {}
    for o in outcomes:
        if o.n_results:
            basis_counts[o.text_basis] = basis_counts.get(o.text_basis, 0) + 1

    agg: dict = {
        "counts": {
            "total": len(outcomes),
            "evaluated": len(ok),
            "errors": len(errors),
            "positives": len(positives),
            "negatives": len(negatives),
            "positives_with_near_miss": len(nm_positives),
            "text_basis": basis_counts,
        },
        "error_items": [o.item_id for o in errors],
    }

    for k in cutoffs:
        hits = sum(1 for o in positives if o.gold_rank is not None and o.gold_rank <= k)
        agg[f"clause_hit@{k}"] = _rate(hits, len(positives))
        nm_hits = sum(
            1 for o in nm_positives if o.near_miss_rank is not None and o.near_miss_rank <= k
        )
        agg[f"near_miss@{k}"] = _rate(nm_hits, len(nm_positives))

    top_k = max(cutoffs)
    top_hits = sum(1 for o in positives if o.gold_rank is not None and o.gold_rank <= top_k)
    ci = wilson_ci(top_hits, len(positives))
    agg["clause_hit_ci95"] = [round(ci[0], 4), round(ci[1], 4)] if ci else None

    mrr = (
        sum(1.0 / o.gold_rank for o in positives if o.gold_rank) / len(positives)
        if positives
        else None
    )
    agg["mrr"] = round(mrr, 4) if mrr is not None else None

    empty_ok = sum(1 for o in negatives if o.passed)
    empty_ci = wilson_ci(empty_ok, len(negatives))
    agg["empty_accuracy"] = _rate(empty_ok, len(negatives))
    agg["empty_accuracy_ci95"] = (
        [round(empty_ci[0], 4), round(empty_ci[1], 4)] if empty_ci else None
    )
    return agg


def aggregate_by_type(
    outcomes: list[ItemOutcome], cutoffs: tuple[int, ...] = (1, 3, 5)
) -> dict[str, dict]:
    """按题型切片汇总（v1.5 per-slice 对比用）；空切片不产出。"""
    types = sorted({o.item_type for o in outcomes})
    return {t: aggregate([o for o in outcomes if o.item_type == t], cutoffs=cutoffs) for t in types}
