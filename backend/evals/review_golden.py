"""核验 golden set 评测（D5）：三张表 + 基线棘轮，离线、确定性。

用法：
    cd backend && python -m evals.review_golden            # 打印三张表并对比基线
    cd backend && python -m evals.review_golden --update-baseline
    make review-eval / make review-eval-baseline

路径与生产一致：semantic_chunker.split_text → Clause（normalize + clause 字段）
→ extract_* → align_item。用例保持“每条 <5 句”，分块器不会调用 embedding；
评测器对 embedding 调用计数，一旦触发（分块器会吞异常回退单 chunk）立即硬失败，
避免悄悄偏离生产路径。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# 评测只需要 app 的纯函数；在干净 clone 里 import 链会碰配置加载，先给离线默认值
# （setdefault：绝不覆盖真实环境）。仅本 eval CLI/测试导入本模块时生效。
os.environ.setdefault("SECRET_KEY", "review-golden-not-a-secret")
os.environ.setdefault("JWT_SECRET_KEY", "review-golden-not-a-secret")
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
# embedding_service 在 import 期构造客户端；评测不会真正调用（由拒绝桩替换）。
os.environ.setdefault("EMBEDDING_API_KEY", "review-golden-not-a-secret")

from app.services import review_analysis_service as analysis  # noqa: E402
from app.services import semantic_chunker as semantic_chunker_module  # noqa: E402
from app.services.semantic_chunker import semantic_chunker  # noqa: E402
from evals.review_golden_cases import CASES  # noqa: E402

ITEMS = ("payment_term", "start_condition", "acceptance_invoice", "exception")
BASELINE_PATH = Path(__file__).with_name("review_golden_baseline.json")


class _NoEmbedding:
    """返回占位向量但计数；由 `_clauses` 在 split 后检查并硬失败。

    分块器内部对 embedding 异常是 `except Exception` 回退整段单 chunk——若这里直接
    抛异常会被吞掉并静默改变切分路径，因此必须“先返回、事后计数校验”。
    """

    calls = 0

    async def get_embeddings(self, texts):  # noqa: ANN001 - 仅占位
        type(self).calls += 1
        return [[0.0] for _ in texts]


async def _clauses(text: str) -> list[analysis.Clause]:
    before = _NoEmbedding.calls
    chunks = await semantic_chunker.split_text(text)
    if _NoEmbedding.calls != before:
        raise RuntimeError(
            "review_golden 用例触发了 embedding：分块器会静默回退单 chunk，"
            "评测将偏离生产路径。请保持每条 < 5 句。"
        )
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


def _evidence_nums(side_facts: dict, *, primary: bool) -> list[int]:
    key = "clause_no" if primary else "clause_no_union"
    nums = side_facts.get(key)
    if nums is None:
        nums = side_facts.get("clause_no") or []
    return list(nums)


async def evaluate(cases=None) -> dict:
    cases = CASES if cases is None else cases
    per_item = {
        item: {"extraction_ok": 0, "extraction_n": 0, "change_ok": 0, "change_n": 0}
        for item in ITEMS
    }
    evidence_union_ok = evidence_primary_ok = evidence_n = 0
    failures: list[str] = []
    details: list[dict] = []

    _NoEmbedding.calls = 0
    for case in cases:
        base = await _clauses(case["base"])
        revised = await _clauses(case["revised"])
        for item in ITEMS:
            gold = case["items"][item]
            base_facts = analysis.EXTRACTORS[item](base)
            revised_facts = analysis.EXTRACTORS[item](revised)
            change_type = analysis.align_item(item, base_facts, revised_facts)
            # 与 analyze_case 相同的差异感知主证据修正（生产路径保真）。
            analysis.refine_primary_locator(
                item,
                base_facts,
                revised_facts,
                change_type,
                base_clauses=base,
                revised_clauses=revised,
            )
            side_facts = {"base": base_facts, "revised": revised_facts}

            stats = per_item[item]
            stats["change_n"] += 1
            change_ok = change_type == gold["change_type"]
            stats["change_ok"] += int(change_ok)
            if not change_ok:
                failures.append(f"{case['id']}/{item}: change {gold['change_type']}->{change_type}")

            extraction_ok = None
            if gold.get("facts") is not None:
                side = (gold.get("evidence") or ("revised", 0))[0]
                actual = _norm_facts(item, side_facts[side])
                expected = _norm_facts(item, gold["facts"])
                extraction_ok = actual == expected
                stats["extraction_n"] += 1
                stats["extraction_ok"] += int(extraction_ok)
                if not extraction_ok:
                    failures.append(f"{case['id']}/{item}: extraction {expected}->{actual}")

            union_hit = primary_hit = None
            if gold.get("evidence") is not None:
                side, clause_no = gold["evidence"]
                union_hit = clause_no in _evidence_nums(side_facts[side], primary=False)
                primary_hit = clause_no in _evidence_nums(side_facts[side], primary=True)
                evidence_n += 1
                evidence_union_ok += int(union_hit)
                evidence_primary_ok += int(primary_hit)
                if not union_hit:
                    failures.append(f"{case['id']}/{item}: evidence union miss {side}:{clause_no}")
                if not primary_hit:
                    failures.append(
                        f"{case['id']}/{item}: evidence primary miss {side}:{clause_no}"
                    )

            if (
                not change_ok
                or extraction_ok is False
                or union_hit is False
                or primary_hit is False
            ):
                details.append(
                    {
                        "case": case["id"],
                        "item": item,
                        "expected_change": gold["change_type"],
                        "actual_change": change_type,
                        "extraction_ok": extraction_ok,
                        "evidence_union_hit": union_hit,
                        "evidence_primary_hit": primary_hit,
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
        "evidence_hit_rate": _ratio(evidence_union_ok, evidence_n),
        "primary_evidence_hit_rate": _ratio(evidence_primary_ok, evidence_n),
        "overall_change_type": _ratio(
            sum(s["change_ok"] for s in per_item.values()),
            sum(s["change_n"] for s in per_item.values()),
        ),
    }
    return {"metrics": metrics, "failures": sorted(failures), "details": details}


def _ratio(ok: int, n: int) -> float | None:
    return round(ok / n, 4) if n else None


def _table(metrics: dict) -> str:
    lines = [
        f"核验 golden set（{metrics['cases']} 组合成中文采购合同，确定性规则链路）",
        "",
        "| 核验项 | 识别准确率 | change_type 准确率 |",
        "|---|---|---|",
    ]
    for item in ITEMS:
        row = metrics["per_item"][item]
        lines.append(f"| {item} | {_fmt(row['extraction'])} | {_fmt(row['change_type'])} |")
    lines += [
        "",
        f"证据命中率（主定位器）: {_fmt(metrics['primary_evidence_hit_rate'])}",
        f"证据命中率（union 口径）: {_fmt(metrics['evidence_hit_rate'])}",
        f"change_type 总体准确率: {_fmt(metrics['overall_change_type'])}",
    ]
    return "\n".join(lines)


def _fmt(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _load_baseline() -> dict | None:
    if not BASELINE_PATH.exists():
        return None
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


def compare_to_baseline(result: dict, baseline: dict) -> list[str]:
    """基线棘轮：指标或失败集合任何变化都必须显式更新基线（提升亦然）。"""
    problems: list[str] = []
    metrics, before = result["metrics"], baseline["metrics"]

    if metrics["cases"] != before["cases"]:
        problems.append(f"cases: {metrics['cases']} != 基线 {before['cases']}")
    for item in ITEMS:
        for metric in ("extraction", "change_type"):
            now = metrics["per_item"][item][metric]
            old = before["per_item"][item][metric]
            if now != old:
                problems.append(f"{item}.{metric}: {now} != 基线 {old}")
    for metric in ("evidence_hit_rate", "primary_evidence_hit_rate", "overall_change_type"):
        if metrics[metric] != before[metric]:
            problems.append(f"{metric}: {metrics[metric]} != 基线 {before[metric]}")

    if result["failures"] != baseline["failures"]:
        problems.append("失败集合与基线不一致（'修好一例、弄坏一例'也会被捕获）")
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
        for detail in result["details"]:
            print(
                f"  - {detail['case']}/{detail['item']}: change 期望"
                f" {detail['expected_change']} 实际 {detail['actual_change']}"
                f"; extraction_ok={detail['extraction_ok']}"
                f"; evidence primary/union={detail['evidence_primary_hit']}"
                f"/{detail['evidence_union_hit']}"
            )

    if args.update_baseline:
        BASELINE_PATH.write_text(
            json.dumps(
                {"metrics": result["metrics"], "failures": result["failures"]},
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"\n基线已更新: {BASELINE_PATH}")
        return 0

    baseline = _load_baseline()
    if baseline is None:
        print("\n未找到基线；先运行一次 --update-baseline 并随 PR 提交。")
        return 2
    problems = compare_to_baseline(result, baseline)
    if problems:
        print("\n与基线不一致:")
        for problem in problems:
            print("  -", problem)
        return 1
    print("\n与基线一致（无回归、无未登记变化）")
    return 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="核验 golden set 评测（离线性）")
    parser.add_argument("--update-baseline", action="store_true", help="显式更新基线")
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
