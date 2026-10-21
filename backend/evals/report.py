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
from datetime import UTC, datetime
from pathlib import Path

from .clauses import clause_definitions
from .golden import GoldenSet
from .harness import ItemRun
from .metrics import aggregate

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
    es_version: str | None = None,
    corpus_chunks: dict[str, int] | None = None,
) -> dict:
    """构建报告 dict（可直接 json.dump 到文件）。"""
    return {
        "meta": {
            "phase": 1,
            "phase_note": (
                "测量口径见 backend/docs/eval-gate-phase1.md；embedding_mode=fake 为 CI 门禁模式"
                "（确定性伪嵌入 + 专用索引先删后建），live 为本地真嵌入工具模式"
            ),
            "dataset_id": golden.dataset_id,
            "dataset_version": golden.version,
            "golden_path": str(golden.path),
            "corpus_files": list(golden.corpus_files),
            "corpus_chunks": dict(corpus_chunks or {}),
            "index": index,
            "org_id": org_id,
            "top_k": top_k,
            "cutoffs": list(cutoffs),
            "retriever": retriever_label,
            "embedding_mode": embedding_mode,
            "es_version": es_version,
            "llm_assist": False,
            "generated_at": generated_at or datetime.now(UTC).isoformat(timespec="seconds"),
        },
        "aggregates": aggregate([run.outcome for run in runs], cutoffs=cutoffs),
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
