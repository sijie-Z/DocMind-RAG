"""核验 golden set 评测（D5）：三张表 + 基线棘轮，离线、确定性。

用法：
    cd backend && python -m evals.review_golden            # 打印三张表并对比基线
    cd backend && python -m evals.review_golden --update-baseline
    make review-eval / make review-eval-baseline

路径与生产一致：semantic_chunker.split_text → Clause（normalize + clause 字段）
→ extract_* → align_item。用例保持“每条 <5 句”，分块器不会调用 embedding；
评测器仍显式注入拒绝桩，一旦用例写法漂移触发 embedding 会立即报错。
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from app.services import review_analysis_service as analysis
from app.services import semantic_chunker as semantic_chunker_module
from app.services.semantic_chunker import semantic_chunker
from evals.review_golden_cases import CASES

ITEMS = ("payment_term", "start_condition", "acceptance_invoice", "exception")
BASELINE_PATH = Path(__file__).with_name("review_golden_baseline.json")


class _NoEmbedding:
    async def get_embeddings(self, texts):  # noqa: ANN001 - 仅用于拒绝
        raise RuntimeError(
            "review_golden 用例触发了 embedding：请保持每条 < 5 句，评测必须离线且确定性"
        )


async def _clauses(text: str) -> list[analysis.Clause]:
    chunks = await semantic_chunker.split_text(text)
    clauses: list[analysis.Clause] = []
    for i, ch in enumerate(chunks):
        raw = ch["text"]
        fields = analysis.extract_clause_fields(raw)
        clauses.append(
            analysis.Clause(
                chunk_id=f"golden-{i}",
                chunk_index=i,
                text=analysis.normalize_clause_text(raw),
                raw_text=raw,
                clause_no=fields["clause_no"],
                section_no=fields["section_no"],
            )
        )
    return clauses


def _norm_facts(item: str, facts: dict) -> dict:
    status = facts.get("status")
    if status != "identified":
        return {"status": status}
    if item == "payment_term":
        return {"status": "identified", "value": facts.get("value"), "unit": facts.get("unit")}
    if item == "start_condition":
        return {"status": "identified", "events": sorted(facts.get("events") or [])}
    return {"status": "identified"}


def _evidence_hit(side_facts: dict, clause_no: int) -> bool:
    nums = side_facts.get("clause_no_union") or side_facts.get("clause_no") or []
    return clause_no in nums


async def evaluate(cases=None) -> dict:
    cases = CASES if cases is None else cases
    per_item = {
        item: {"extraction_ok": 0, "extraction_n": 0, "change_ok": 0, "change_n": 0}
        for item in ITEMS
    }
    evidence_ok = evidence_n = 0
    details: list[dict] = []

    for case in cases:
        base = await _clauses(case["base"])
        revised = await _clauses(case["revised"])
        case_facts: dict[str, dict] = {}
        for item in ITEMS:
            gold = case["items"][item]
            base_facts = analysis.EXTRACTORS[item](base)
            revised_facts = analysis.EXTRACTORS[item](revised)
            change_type = analysis.align_item(item, base_facts, revised_facts)
            case_facts[item] = {
                "base": base_facts,
                "revised": revised_facts,
                "change_type": change_type,
            }

            stats = per_item[item]
            stats["change_n"] += 1
            change_ok = change_type == gold["change_type"]
            stats["change_ok"] += int(change_ok)

            extraction_ok = None
            if gold.get("facts") is not None:
                side = (gold.get("evidence") or ("revised", 0))[0]
                actual = _norm_facts(item, case_facts[item][side])
                extraction_ok = actual == _norm_facts(item, gold["facts"])
                stats["extraction_n"] += 1
                stats["extraction_ok"] += int(extraction_ok)

            evidence_hit = None
            if gold.get("evidence") is not None:
                side, clause_no = gold["evidence"]
                evidence_hit = _evidence_hit(case_facts[item][side], clause_no)
                evidence_n += 1
                evidence_ok += int(evidence_hit)

            if not change_ok or extraction_ok is False or evidence_hit is False:
                details.append(
                    {
                        "case": case["id"],
                        "item": item,
                        "expected_change": gold["change_type"],
                        "actual_change": change_type,
                        "extraction_ok": extraction_ok,
                        "evidence_hit": evidence_hit,
                    }
                )

    metrics = {
        "cases": len(cases),
        "per_item": {
            item: {
                "extraction": _ratio(stats["extraction_ok"], stats["extraction_n"]),
                "change_type": _ratio(stats["change_ok"], stats["change_n"]),
            }
            for item, stats in per_item.items()
        },
        "evidence_hit_rate": _ratio(evidence_ok, evidence_n),
        "overall_change_type": _ratio(
            sum(s["change_ok"] for s in per_item.values()),
            sum(s["change_n"] for s in per_item.values()),
        ),
    }
    return {"metrics": metrics, "details": details}


def _ratio(ok: int, n: int) -> float | None:
    return round(ok / n, 4) if n else None


def _table(metrics: dict) -> str:
    lines = [
        "核验 golden set（10 组合成中文采购合同，确定性规则链路）",
        "",
        "| 核验项 | 抽取准确率 | change_type 准确率 |",
        "|---|---|---|",
    ]
    for item in ITEMS:
        row = metrics["per_item"][item]
        lines.append(f"| {item} | {_fmt(row['extraction'])} | {_fmt(row['change_type'])} |")
    lines += [
        "",
        f"证据命中率: {_fmt(metrics['evidence_hit_rate'])}",
        f"change_type 总体准确率: {_fmt(metrics['overall_change_type'])}",
    ]
    return "\n".join(lines)


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _load_baseline() -> dict | None:
    if not BASELINE_PATH.exists():
        return None
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def compare_to_baseline(metrics: dict, baseline: dict) -> list[str]:
    """基线棘轮：任一项低于基线即失败；高于基线要求显式更新基线。"""
    problems: list[str] = []
    for item in ITEMS:
        for metric in ("extraction", "change_type"):
            before = baseline["per_item"][item][metric]
            now = metrics["per_item"][item][metric]
            if now is not None and before is not None and now < before:
                problems.append(f"{item}.{metric}: {now} < 基线 {before}")
            if now is not None and before is not None and now > before:
                problems.append(
                    f"{item}.{metric}: {now} > 基线 {before}（提升需显式 --update-baseline）"
                )
    for metric in ("evidence_hit_rate", "overall_change_type"):
        before = baseline[metric]
        now = metrics[metric]
        if now is not None and before is not None and now < before:
            problems.append(f"{metric}: {now} < 基线 {before}")
        if now is not None and before is not None and now > before:
            problems.append(f"{metric}: {now} > 基线 {before}（提升需显式 --update-baseline）")
    return problems


async def main_async(args) -> int:
    original = semantic_chunker_module.embedding_service
    semantic_chunker_module.embedding_service = _NoEmbedding()
    try:
        result = await evaluate()
    finally:
        semantic_chunker_module.embedding_service = original

    print(_table(result["metrics"]))
    if result["details"]:
        print("\n失败明细:")
        for d in result["details"]:
            print(
                f"  - {d['case']}/{d['item']}: change 期望 {d['expected_change']}"
                f" 实际 {d['actual_change']}"
                + (
                    f"; extraction_ok={d['extraction_ok']}"
                    if d["extraction_ok"] is not None
                    else ""
                )
                + (f"; evidence_hit={d['evidence_hit']}" if d["evidence_hit"] is not None else "")
            )

    if args.update_baseline:
        BASELINE_PATH.write_text(
            json.dumps(result["metrics"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"\n基线已更新: {BASELINE_PATH}")
        return 0

    baseline = _load_baseline()
    if baseline is None:
        print("\n未找到基线；先运行一次 --update-baseline 并随 PR 提交。")
        return 2
    problems = compare_to_baseline(result["metrics"], baseline)
    if problems:
        print("\n与基线不一致:")
        for p in problems:
            print("  -", p)
        return 1
    print("\n与基线一致（无回归、无未登记提升）")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="核验 golden set 评测（离线性）")
    parser.add_argument("--update-baseline", action="store_true", help="显式更新基线")
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
