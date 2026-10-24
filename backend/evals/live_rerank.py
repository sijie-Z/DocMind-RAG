"""Live rerank 评估（issue #512，v1.5）：检索 → rerank → before/after 指标。

CI 门禁始终用 fake 嵌入 + 不经过 rerank；本脚本是**单独的 live 工具**：

```bash
cd backend
export ELASTICSEARCH_HOSTS='["http://localhost:9200"]'
export ELASTICSEARCH_INDEX_NAME=docmind_knowledge_eval_v1
export EMBEDDING_API_URL=http://localhost:11434/v1 EMBEDDING_API_KEY=ollama
export EMBEDDING_MODEL=qwen3-embedding:0.6b VECTOR_DIMENSION=1024
# 二选一：本地 cross-encoder（RERANK_USE_LOCAL=true）
# 或 Zhipu：export RERANK_API_KEY=... RERANK_MODEL=rerank
python -m evals.live_rerank --out evals/reports/v1.5/live_rerank.json
```

纪律：**绝不伪造数字**。任何 rerank 路径不可用（degraded/disabled/empty）时，
脚本只写 status=unavailable 的占位报告（含 mode/date/语料 commit/原因），
不写任何指标，退出码 2。只有全部条目 rerank status=ok 才写 before/after 数字。
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from .golden import GoldenSet, GoldenSetError, load_golden_set
from .metrics import aggregate, evaluate_item
from .report import write_json, write_markdown
from .runner import (
    DEFAULT_GOLDEN,
    DEFAULT_INDEX,
    DEFAULT_ORG_ID,
    _apply_env,
    _close_clients,
    _corpus_digest,
    _git_commit,
    _require_embedding_service,
)


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(pct * (len(ordered) - 1))))
    return round(ordered[idx], 1)


def _latency_summary(values: list[float]) -> dict:
    return {
        "n": len(values),
        "p50": _percentile(values, 0.5),
        "p95": _percentile(values, 0.95),
        "mean": round(sum(values) / len(values), 1) if values else None,
    }


def _placeholder(path: Path, golden: GoldenSet, reason: str, *, out: Path) -> None:
    payload = {
        "meta": {
            "report_type": "live_rerank",
            "mode": "live",
            "status": "unavailable",
            "reason": reason,
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "dataset_id": golden.dataset_id,
            "dataset_version": golden.version,
            "corpus_digest": _corpus_digest(golden),
            "git_commit": _git_commit(),
            "index": os.environ.get("ELASTICSEARCH_INDEX_NAME", DEFAULT_INDEX),
            "org_id": DEFAULT_ORG_ID,
            "note": (
                "占位报告：真实 embedding/rerank provider 不可用，未产生任何指标数字。"
                "配置真实服务后运行 python -m evals.live_rerank 生成正式报告。"
            ),
        },
        "aggregates_before": None,
        "aggregates_after": None,
        "items": [],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    write_json(payload, path)
    md = "\n".join(
        [
            "# Live rerank 报告（占位）",
            "",
            "- mode：`live`　status：`unavailable`",
            f"- 日期：{payload['meta']['generated_at']}",
            f"- 语料 commit：`{payload['meta']['git_commit'] or 'n/a'}`",
            f"- 语料指纹：`{payload['meta']['corpus_digest']}`",
            f"- 原因：{reason}",
            "",
            "本文件**不含任何指标数字**。真实 provider 可用后运行：",
            "",
            "```bash",
            "cd backend && python -m evals.live_rerank --out evals/reports/v1.5/live_rerank.json",
            "```",
            "",
        ]
    )
    path.with_suffix(".md").write_text(md, encoding="utf-8")
    print(f"[live-rerank] 不可用：{reason}", file=sys.stderr)
    print(f"[live-rerank] 占位报告：{path}", file=sys.stderr)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m evals.live_rerank")
    parser.add_argument("--golden", default=str(DEFAULT_GOLDEN))
    parser.add_argument("--index", default=DEFAULT_INDEX)
    parser.add_argument("--es-hosts", default=None)
    parser.add_argument("--org-id", type=int, default=DEFAULT_ORG_ID)
    parser.add_argument("--top-k", type=int, default=None)
    parser.add_argument("--candidate-k", type=int, default=20, help="送 rerank 的候选数")
    parser.add_argument("--out", default="evals/reports/v1.5/live_rerank.json")
    return parser


async def _run(args: argparse.Namespace, golden: GoldenSet) -> int:
    from app.core.config import settings
    from app.core.elasticsearch import get_elasticsearch, get_index_error, init_elasticsearch
    from app.rag.reranker import rerank
    from app.rag.retriever import HybridRetriever

    out = Path(args.out)
    embed = _require_embedding_service("live")
    if embed is None:
        _placeholder(
            out, golden, "embedding service 未配置（EMBEDDING_API_KEY/EMBEDDING_API_URL）", out=out
        )
        return 2

    await init_elasticsearch()
    err = get_index_error()
    if err:
        _placeholder(out, golden, f"ES 不可用：{err}", out=out)
        return 2

    top_k = args.top_k or golden.top_k
    candidate_k = max(top_k, args.candidate_k)
    retriever = HybridRetriever(openai_client=None, embedding_provider=embed)
    before_outcomes = []
    after_outcomes = []
    items: list[dict] = []
    retrieval_ms: list[float] = []
    rerank_ms: list[float] = []
    paths: set[str] = set()
    models: set[str] = set()

    try:
        client = await get_elasticsearch()
        count = await client.count(
            index=settings.ELASTICSEARCH_INDEX_NAME,
            query={"term": {"organization_id": str(args.org_id)}},
        )
        if int(count.get("count", 0)) == 0:
            _placeholder(out, golden, "评估索引为空（先运行 python -m evals seed）", out=out)
            return 2

        import time

        for item in golden.items:
            start = time.perf_counter()
            docs, _qv, _debug, _degraded = await retriever.retrieve(
                item.query, args.org_id, candidate_k, debug=True
            )
            retrieval_ms.append((time.perf_counter() - start) * 1000)

            before = evaluate_item(item, list(docs or [])[:top_k], strategy="hybrid")
            before_outcomes.append(before)
            start = time.perf_counter()
            ordered, info = await rerank(item.query, list(docs or []), None, None)
            rerank_ms.append((time.perf_counter() - start) * 1000)
            if info.get("status") != "ok":
                _placeholder(
                    out,
                    golden,
                    "rerank provider 不可用："
                    f"status={info.get('status')} path={info.get('path')} "
                    f"attempts={info.get('attempts')}",
                    out=out,
                )
                return 2
            paths.add(str(info.get("path")))
            if info.get("model"):
                models.add(str(info.get("model")))
            after = evaluate_item(item, list(ordered or [])[:top_k], strategy="hybrid")
            after_outcomes.append(after)
            items.append(
                {
                    "id": item.id,
                    "type": item.type,
                    "expect": item.expect,
                    "query": item.query,
                    "before": {
                        "passed": before.passed,
                        "gold_rank": before.gold_rank,
                        "near_miss_rank": before.near_miss_rank,
                    },
                    "after": {
                        "passed": after.passed,
                        "gold_rank": after.gold_rank,
                        "near_miss_rank": after.near_miss_rank,
                    },
                    "rerank": info,
                }
            )
    finally:
        await _close_clients(embed)

    meta = {
        "report_type": "live_rerank",
        "mode": "live",
        "status": "ok",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "dataset_id": golden.dataset_id,
        "dataset_version": golden.version,
        "corpus_digest": _corpus_digest(golden),
        "git_commit": _git_commit(),
        "index": settings.ELASTICSEARCH_INDEX_NAME,
        "org_id": args.org_id,
        "top_k": top_k,
        "candidate_k": candidate_k,
        "embedding_mode": "live",
        "llm_assist": False,
        "rerank": {
            "path": sorted(paths),
            "model": sorted(models),
            "threshold": None,
            "note": "reranker 未配置分数阈值；排序后取 top_k 评估",
        },
        "latency_ms": {
            "retrieval": _latency_summary(retrieval_ms),
            "rerank": _latency_summary(rerank_ms),
        },
    }
    payload = {
        "meta": meta,
        "aggregates_before": aggregate(before_outcomes),
        "aggregates_after": aggregate(after_outcomes),
        "items": items,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    write_json(payload, out)
    write_markdown(payload, out.with_suffix(".md"))
    print(
        f"[live-rerank] ok：ClauseHit@5 before="
        f"{payload['aggregates_before']['clause_hit@5']} after="
        f"{payload['aggregates_after']['clause_hit@5']}　报告：{out}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:  # noqa: BLE001
        pass
    args = build_parser().parse_args(argv)
    _apply_env(args)
    try:
        golden = load_golden_set(args.golden)
    except GoldenSetError as e:
        print(f"[live-rerank] golden set 加载失败: {e}", file=sys.stderr)
        return 2
    # 没配置真实嵌入时直接写占位报告，不 import app（避免长栈）。
    if not os.environ.get("EMBEDDING_API_KEY") and not os.environ.get("DEEPSEEK_API_KEY"):
        _placeholder(
            Path(args.out), golden, "未配置 EMBEDDING_API_KEY/DEEPSEEK_API_KEY", out=Path(args.out)
        )
        return 2
    return asyncio.run(_run(args, golden))


if __name__ == "__main__":
    raise SystemExit(main())
