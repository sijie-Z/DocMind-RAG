"""报告输出：机器可读 JSON + 人类可读 Markdown（纯标准库）。

报告是可回归的基线载体（CI 门禁 `python -m evals compare` 基于同一 JSON 做
floor/cap 比对），所以字段口径在这里固定：
- meta：数据集/索引/组织/参数/时间/嵌入模式——复现一次跑分所需的一切；
  `embedding_mode`/`es_version`/`corpus_chunks` 同时是门禁的协议字段
  （compare 与基线逐键校验，防 apples 对 oranges）；
- aggregates：metrics.aggregate 的输出原样放入；
- items：逐条判定 + top-k 结果摘要（只保留诊断需要的字段）。
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from .clauses import clause_definitions, clause_mentions
from .golden import GoldenSet
from .harness import ItemRun
from .metrics import aggregate, aggregate_by_type

_SNIPPET_LEN = 120


def _content_of(result: dict) -> str:
    return str(result.get("content") or result.get("text") or result.get("snippet") or "")


def _trim_results(results: list[dict]) -> list[dict]:
    trimmed = []
    for rank, r in enumerate(results, 1):
        text = _content_of(r)
        trimmed.append(
            {
                "rank": rank,
                "filename": str(r.get("filename") or ""),
                "score": r.get("score"),
                "defs": sorted(clause_definitions(text)),
                "mentions": sorted(clause_mentions(text)),
                "snippet": text[:_SNIPPET_LEN],
            }
        )
    return trimmed


def build_report(
    golden: GoldenSet,
    runs: list[ItemRun],
    *,
    index: str,
    org_id: int,
    top_k: int,
    retriever_label: str,
    cutoffs: tuple[int, ...] = (1, 3, 5),
    generated_at: str | None = None,
    embedding_mode: str = "live",
    retriever_mode: str = "hybrid",
    es_version: str | None = None,
    corpus_chunks: dict[str, int] | None = None,
    corpus_defs: int | None = None,
    corpus_digest: str | None = None,
    git_commit: str | None = None,
) -> dict:
    """构建报告 dict（可直接 json.dump 到文件）。"""
    outcomes = [run.outcome for run in runs]
    return {
        "meta": {
            "phase": 1.5,
            "phase_note": (
                "测量口径见 backend/docs/eval-gate-phase1.md 与 evals/REPORT.md；"
                "embedding_mode=fake 为 CI/公开复现模式（确定性伪嵌入 + 专用索引先删后建），"
                "live 为本地真嵌入工具模式"
            ),
            "dataset_id": golden.dataset_id,
            "dataset_version": golden.version,
            "golden_path": str(golden.path),
            "corpus_files": list(golden.corpus_files),
            "corpus_chunks": dict(corpus_chunks or {}),
            "corpus_defs": corpus_defs,
            "corpus_digest": corpus_digest,
            "git_commit": git_commit,
            "index": index,
            "org_id": org_id,
            "top_k": top_k,
            "cutoffs": list(cutoffs),
            "retriever": retriever_label,
            "retriever_mode": retriever_mode,
            "embedding_mode": embedding_mode,
            "es_version": es_version,
            "llm_assist": False,
            "generated_at": generated_at or datetime.now(UTC).isoformat(timespec="seconds"),
        },
        "aggregates": aggregate(outcomes, cutoffs=cutoffs),
        "aggregates_by_type": aggregate_by_type(outcomes, cutoffs=cutoffs),
        "items": [
            {
                "id": run.item.id,
                "type": run.item.type,
                "expect": run.item.expect,
                "query": run.item.query,
                "status": run.outcome.status,
                "error": run.outcome.error,
                "passed": run.outcome.passed,
                "gold_rank": run.outcome.gold_rank,
                "matched_gold": run.outcome.matched_gold,
                "near_miss_rank": run.outcome.near_miss_rank,
                "matched_near_miss": run.outcome.matched_near_miss,
                "declares_near_miss": run.outcome.declares_near_miss,
                "n_results": run.outcome.n_results,
                "strategy": run.outcome.strategy,
                "elapsed_ms": round(run.elapsed_ms, 1),
                "degraded": run.degraded,
                "critical": run.item.critical,
                "notes": run.item.notes,
                "gold": [loc.label() for loc in run.item.gold],
                "near_miss": [nm.label() for nm in run.item.near_miss],
                "results": _trim_results(run.results),
            }
            for run in runs
        ],
    }


def write_json(report: dict, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def _fmt_rate(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def _fmt_ci(value: list[float] | None) -> str:
    if not value:
        return "n/a"
    return f"[{value[0]:.1%}, {value[1]:.1%}]"


def _fmt_rank(rank: int | None) -> str:
    return "-" if rank is None else f"@{rank}"


def render_markdown(report: dict) -> str:
    """把报告 dict 渲染成 Markdown（表格 + 失败明细）。"""
    meta = report["meta"]
    agg = report["aggregates"]
    counts = agg["counts"]
    top_k = meta["top_k"]
    lines: list[str] = []
    lines.append(f"# RAG 检索评估报告（{meta['dataset_id']} v{meta['dataset_version']}）")
    lines.append("")
    lines.append(f"- 索引：`{meta['index']}`　组织：`{meta['org_id']}`　top_k：{top_k}")
    lines.append(
        f"- 条目：{counts['total']}（正例 {counts['positives']} + 负例 {counts['negatives']}"
        f"{'，错误 ' + str(counts['errors']) if counts['errors'] else ''}）"
    )
    lines.append(f"- 生成时间：{meta['generated_at']}　检索器：{meta['retriever']}")
    lines.append(
        f"- 嵌入模式：{meta.get('embedding_mode', 'live')}　说明：{meta['phase_note']}；LLM 辅助（改写/HyDE）关闭以保证可复现"
    )
    lines.append("")
    lines.append("## 汇总")
    lines.append("")
    lines.append("| 指标 | 值 | 95% CI (Wilson) | 说明 |")
    lines.append("|---|---|---|---|")
    lines.append(f"| ClauseHit@1 | {_fmt_rate(agg['clause_hit@1'])} | - | 条号定义位命中（首位） |")
    lines.append(f"| ClauseHit@3 | {_fmt_rate(agg['clause_hit@3'])} | - | 同上（前三） |")
    lines.append(
        f"| **ClauseHit@{top_k}** | **{_fmt_rate(agg[f'clause_hit@{top_k}'])}** |"
        f" {_fmt_ci(agg['clause_hit_ci95'])} | 主指标（正例 {counts['positives']} 条） |"
    )
    lines.append(
        f"| NearMiss@{top_k} | {_fmt_rate(agg[f'near_miss@{top_k}'])} | - |"
        f" 误召相邻条比例（{counts['positives_with_near_miss']} 条声明，越低越好） |"
    )
    lines.append(
        f"| MRR | {agg['mrr'] if agg['mrr'] is not None else 'n/a'} | - | 首个 gold 名次倒数均值 |"
    )
    lines.append(
        f"| EmptyAcc | {_fmt_rate(agg['empty_accuracy'])} | {_fmt_ci(agg['empty_accuracy_ci95'])} |"
        f" 负例返回空的比例（n={counts['negatives']}） |"
    )
    lines.append("")
    lines.append("## 逐条结果")
    lines.append("")
    lines.append("| # | id | 类型 | 期望 | 结果 | gold 名次 | near-miss 名次 | 首条返回 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for i, item in enumerate(report["items"], 1):
        if item["status"] == "error":
            verdict = "ERROR"
        else:
            verdict = "PASS" if item["passed"] else "FAIL"
        first = item["results"][0] if item["results"] else None
        first_label = f"{first['filename']} {first['defs']}" if first else "（空）"
        lines.append(
            f"| {i} | `{item['id']}` | {item['type']} | {item['expect']} | {verdict} |"
            f" {_fmt_rank(item['gold_rank'])} | {_fmt_rank(item['near_miss_rank'])} | {first_label} |"
        )
    failed = [it for it in report["items"] if it["status"] == "ok" and not it["passed"]]
    errors = [it for it in report["items"] if it["status"] == "error"]
    if failed or errors:
        lines.append("")
        lines.append("## 失败明细")
        for item in failed:
            lines.append("")
            lines.append(f"### {'FAIL'} `{item['id']}`（{item['type']}）")
            lines.append(f"- 查询：{item['query']}")
            lines.append(
                f"- gold：{', '.join(item['gold'])}　near-miss：{', '.join(item['near_miss']) or '-'}"
            )
            if item["matched_near_miss"] and item["gold_rank"] is None:
                lines.append(
                    f"- ⚠️ gold 未命中但误召 near-miss（{item['near_miss_rank']}）："
                    f"{', '.join(item['matched_near_miss'])}"
                )
            for r in item["results"][:3]:
                lines.append(
                    f"  - @{r['rank']} {r['filename']} defs={r['defs']} score={r['score']}：{r['snippet']}"
                )
        for item in errors:
            lines.append("")
            lines.append(f"### ERROR `{item['id']}`")
            lines.append(f"- {item['error']}")
    lines.append("")
    return "\n".join(lines)


def write_markdown(report: dict, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_markdown(report), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# v1.5 汇总报告：per-slice 对比 + 失败分类（issue #512）
# ---------------------------------------------------------------------------

FAILURE_CLASSES: tuple[tuple[str, str], ...] = (
    ("title_token_flooding", "title-token 淹没"),
    ("numeral_normalization", "中文/阿拉伯数字不归一"),
    ("chunk_boundary", "chunk 边界"),
    ("near_miss_recall", "near-miss 误召"),
    ("negative_false_recall", "负例误召"),
)

_CN_CLAUSE_QUERY_RE = re.compile(r"第[ \t]*[〇零一二三四五六七八九十百千两]+[ \t]*条")


def _is_failed(item: dict) -> bool:
    return item.get("status") == "ok" and not item.get("passed")


def _gold_pairs(item: dict) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for label in item.get("gold") or []:
        file, _, clause = str(label).partition(":")
        pairs.append((file, clause))
    return pairs


def _example(report: dict, source: str, item: dict) -> dict:
    results = item.get("results") or []
    top = []
    for r in results[:3]:
        top.append(
            {
                "rank": r.get("rank"),
                "filename": r.get("filename"),
                "defs": r.get("defs"),
                "score": r.get("score"),
                "snippet": r.get("snippet"),
            }
        )
    return {
        "source": source,
        "id": item.get("id"),
        "type": item.get("type"),
        "query": item.get("query"),
        "gold": item.get("gold"),
        "gold_rank": item.get("gold_rank"),
        "near_miss": item.get("near_miss"),
        "near_miss_rank": item.get("near_miss_rank"),
        "n_results": item.get("n_results"),
        "notes": item.get("notes"),
        "top": top,
    }


def classify_failures(report: dict, source: str = "hybrid") -> dict[str, list[dict]]:
    """把失败样本按五类归档（启发式、可解释；不改变判定）。

    每个失败可能落进多类（例如带《》的条号直查既可能 title-token 淹没，
    也可能数字不归一）；报告展示时按类去重列出。
    """
    out: dict[str, list[dict]] = {key: [] for key, _ in FAILURE_CLASSES}
    for item in report.get("items") or []:
        if not _is_failed(item):
            continue
        query = str(item.get("query") or "")
        notes = str(item.get("notes") or "")
        gold_pairs = _gold_pairs(item)

        if "《" in query:
            out["title_token_flooding"].append(_example(report, source, item))

        if _CN_CLAUSE_QUERY_RE.search(query) or "数字" in notes or "中文数字" in query:
            out["numeral_normalization"].append(_example(report, source, item))

        boundary = False
        gold_files = {f for f, _ in gold_pairs}
        gold_clauses = {c for _, c in gold_pairs}
        for r in item.get("results") or []:
            if str(r.get("filename") or "") not in gold_files:
                continue
            defs = set(r.get("defs") or [])
            mentions = set(r.get("mentions") or [])
            if (mentions & gold_clauses) - defs:
                boundary = True
                break
        if boundary:
            out["chunk_boundary"].append(_example(report, source, item))

        if item.get("near_miss_rank") is not None or item.get("matched_near_miss"):
            out["near_miss_recall"].append(_example(report, source, item))

        if item.get("expect") == "empty" and int(item.get("n_results") or 0) > 0:
            out["negative_false_recall"].append(_example(report, source, item))
    return out


def _pct(value) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.1%}"


def _num(value) -> str:
    if value is None:
        return "n/a"
    return f"{float(value):.4f}"


def _ci(value) -> str:
    if not value:
        return "n/a"
    return f"[{value[0]:.1%}, {value[1]:.1%}]"


def _report_title(report: dict) -> str:
    meta = report.get("meta") or {}
    return str(meta.get("retriever_mode") or "hybrid")


def _overall_row(report: dict) -> dict:
    return report.get("aggregates") or {}


def _slice_names(*reports: dict) -> list[str]:
    names: list[str] = []
    for report in reports:
        for key in report.get("aggregates_by_type") or {}:
            if key not in names:
                names.append(key)
    order = [
        "clause_lookup",
        "semantic",
        "numerical",
        "cross_doc",
        "conflict",
        "unanswerable",
    ]
    return [n for n in order if n in names] + [n for n in names if n not in order]


def _failure_examples(hybrid: dict, dense: dict | None) -> dict[str, list[dict]]:
    """每类最多 2 个例子：优先 hybrid，不足则用 dense 补（明示来源）。"""
    h = classify_failures(hybrid, source="hybrid")
    d = classify_failures(dense, source="dense") if dense else {}
    out: dict[str, list[dict]] = {}
    for key, _label in FAILURE_CLASSES:
        merged = list(h.get(key, []))
        seen = {e["id"] for e in merged}
        for e in d.get(key, []):
            if e["id"] not in seen:
                merged.append(e)
                seen.add(e["id"])
        out[key] = merged[:2]
    return out


def _render_example(example: dict) -> list[str]:
    lines = [
        f"- `{example['id']}`（{example['source']}，{example['type']}）　query：{example['query']}",
        f"  - gold：{', '.join(example['gold']) or '-'}　gold_rank：{example['gold_rank']}",
    ]
    if example.get("near_miss"):
        lines.append(
            f"  - near-miss：{', '.join(example['near_miss'])}"
            f"　near_miss_rank：{example['near_miss_rank']}"
        )
    for r in example["top"]:
        lines.append(
            f"  - top@{r['rank']} `{r['filename']}` defs={r['defs']} "
            f"score={r['score']}：{r['snippet']}"
        )
    return lines


def render_v15_report(hybrid: dict, dense: dict | None = None) -> str:
    """v1.5 汇总 Markdown：总体指标 + per-slice 对比 + 失败分类与例子。"""
    meta = hybrid.get("meta") or {}
    reports = [r for r in (hybrid, dense) if r]
    lines: list[str] = []
    lines.append("# DocMind RAG 检索评估报告 v1.5（issue #512）")
    lines.append("")
    lines.append(
        "- 数据集：`{}` v{}（{} 条 = 正例 {} + 负例 {}）".format(
            meta.get("dataset_id"),
            meta.get("dataset_version"),
            (hybrid.get("aggregates") or {}).get("counts", {}).get("total"),
            (hybrid.get("aggregates") or {}).get("counts", {}).get("positives"),
            (hybrid.get("aggregates") or {}).get("counts", {}).get("negatives"),
        )
    )
    chunks = meta.get("corpus_chunks") or {}
    lines.append(
        f"- 语料：{len(meta.get('corpus_files') or [])} 个文件 / "
        f"{sum(chunks.values())} 个 chunk / {meta.get('corpus_defs') or 'n/a'} 条 clause-level 定义"
        f"（确定性分块，一条款一 chunk）"
    )
    lines.append(
        f"- 运行模式：`{meta.get('embedding_mode')}`（确定性伪嵌入，无 LLM/rerank）"
        f"　检索器：`{meta.get('retriever_mode')}`"
    )
    lines.append(
        f"- 日期：{meta.get('generated_at')}　索引：`{meta.get('index')}`"
        f"　ES：{meta.get('es_version') or 'unknown'}"
    )
    lines.append(
        f"- 语料指纹（sha256）：`{meta.get('corpus_digest') or 'n/a'}`"
        f"　代码/语料 commit：`{meta.get('git_commit') or 'n/a'}`"
    )
    if dense:
        dmeta = dense.get("meta") or {}
        lines.append(
            f"- dense 报告：日期 {dmeta.get('generated_at')}　"
            f"语料指纹 `{dmeta.get('corpus_digest') or 'n/a'}`"
        )
    lines.append("")
    lines.append(
        "> 口径：ClauseHit@k = top-k 中存在「文件名匹配 + 条号位于 chunk 行首定义位」的块；"
    )
    lines.append(
        "> NearMiss@k = 声明 near-miss 的正例中 top-k 出现相邻条定义位的比例（越低越好）；"
        "Wilson 95% CI；不引入 bootstrap。"
    )
    lines.append("")

    lines.append("## 总体指标（含 Wilson 95% CI）")
    lines.append("")
    header = "| 指标 | " + " | ".join(_report_title(r) for r in reports) + " |"
    lines.append(header)
    lines.append("|---|" + "---|" * len(reports))
    rows = [
        ("ClauseHit@1", "clause_hit@1"),
        ("ClauseHit@3", "clause_hit@3"),
        ("ClauseHit@5", "clause_hit@5"),
        ("NearMiss@5", "near_miss@5"),
        ("MRR", "mrr"),
        ("EmptyAcc", "empty_accuracy"),
    ]
    for label, key in rows:
        cells = []
        for report in reports:
            agg = _overall_row(report)
            val = agg.get(key)
            cells.append(_num(val) if key == "mrr" else _pct(val))
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    cells = []
    for report in reports:
        agg = _overall_row(report)
        cells.append(_ci(agg.get("clause_hit_ci95")))
    lines.append("| ClauseHit@5 95% CI | " + " | ".join(cells) + " |")
    cells = []
    for report in reports:
        agg = _overall_row(report)
        cells.append(_ci(agg.get("empty_accuracy_ci95")))
    lines.append("| EmptyAcc 95% CI | " + " | ".join(cells) + " |")
    cells = []
    for report in reports:
        cells.append(str((_overall_row(report).get("counts") or {}).get("errors", "n/a")))
    lines.append("| errors | " + " | ".join(cells) + " |")
    lines.append("")

    lines.append("## Per-slice 对比（按题型）")
    lines.append("")
    lines.append(
        "| 题型 | 正例 n | 负例 n | "
        + " | ".join(f"{_report_title(r)} ClauseHit@5" for r in reports)
        + " | "
        + " | ".join(f"{_report_title(r)} MRR" for r in reports)
        + " | "
        + " | ".join(f"{_report_title(r)} EmptyAcc" for r in reports)
        + " |"
    )
    lines.append("|---|---|---|" + "---|" * (3 * len(reports)))
    for slice_name in _slice_names(*reports):
        first = (hybrid.get("aggregates_by_type") or {}).get(slice_name) or {}
        counts = first.get("counts") or {}
        row = [slice_name, str(counts.get("positives", "-")), str(counts.get("negatives", "-"))]
        for report in reports:
            agg = (report.get("aggregates_by_type") or {}).get(slice_name) or {}
            row.append(_pct(agg.get("clause_hit@5")))
        for report in reports:
            agg = (report.get("aggregates_by_type") or {}).get(slice_name) or {}
            row.append(_num(agg.get("mrr")))
        for report in reports:
            agg = (report.get("aggregates_by_type") or {}).get(slice_name) or {}
            row.append(_pct(agg.get("empty_accuracy")))
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")

    lines.append("## 失败分类与例子（每类最多 2 个，来自 hybrid / dense 实际结果）")
    lines.append("")
    examples = _failure_examples(hybrid, dense)
    for key, label in FAILURE_CLASSES:
        items = examples.get(key, [])
        lines.append(f"### {label}（{len(items)} 例展示）")
        lines.append("")
        if not items:
            lines.append("- 本 run 未出现此类失败（0 例）——不伪造例子。")
        else:
            for ex in items:
                lines.extend(_render_example(ex))
        lines.append("")

    lines.append("## 复现")
    lines.append("")
    lines.append("```bash")
    lines.append("# 仅需 Docker ES 8.11，无需 Ollama / 网络")
    lines.append("cd backend")
    lines.append("python -m evals check")
    lines.append("make eval-public       # seed --recreate → run(hybrid) → run(dense) → report")
    lines.append("make eval-ci           # CI 门禁（hybrid，7 项指标 + 基线比对）")
    lines.append("```")
    lines.append("")
    lines.append("## 限制")
    lines.append("")
    lines.append(
        "- fake 模式的数字只反映「确定性伪嵌入 + 检索管道」的行为面，**不可**与 live "
        "（真实 embedding）数字混比；对外引用必须标注 `mode=fake/live`、日期、语料 commit。"
    )
    lines.append(
        "- 本报告不经过 rerank / 语义缓存 / LLM 改写；live rerank 报告由 "
        "`python -m evals.live_rerank` 生成（缺少真实 provider 时只提供占位报告，绝不伪造数字）。"
    )
    lines.append("- Wilson 区间按二项比例计算；未引入 bootstrap（重采样单位与收益未论证）。")
    lines.append("")
    return "\n".join(lines)
