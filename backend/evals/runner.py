"""评估 CLI：`python -m evals {check,seed,run}`（#208 Phase 1）。

用法（本地一键跑分；先 seed 再 run）：

```bash
cd backend
export ELASTICSEARCH_HOSTS='["http://localhost:9200"]'
export ELASTICSEARCH_INDEX_NAME=docmind_knowledge_eval_v1
export EMBEDDING_API_URL=http://localhost:11434/v1 EMBEDDING_API_KEY=ollama
export EMBEDDING_MODEL=qwen3-embedding:0.6b VECTOR_DIMENSION=1024
python -m evals seed   # 把 corpus/ 固定语料写进评估索引（可反复执行）
python -m evals run    # 跑全套 golden set，出 JSON + Markdown 报告
python -m evals check  # 只校验 golden set 自洽（不碰 ES）
```

接线纪律：`--index/--org-id/--es-hosts` 等参数先写进环境变量，**再** import app ——
settings 在导入时读环境，这是唯一正确的顺序（沿 #279 执行者的 env 法）。

检索层口径（Phase 1 有意收窄）：直接调用 `HybridRetriever.retrieve`
（关键词+向量+RRF+MMR+后处理），**不经过** rerank / 语义缓存 / LLM 环节，
且 `openai_client=None`（无 LLM 改写 / HyDE）——保证可复现。
「rerank 前/后」两组指标与缓存链路属于 Phase 2 的逐阶段 trace 实验。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from .chunking import split_corpus_file
from .golden import GoldenSet, GoldenSetError, load_golden_set
from .harness import RetrievalObservation, run_eval
from .report import build_report, write_json, write_markdown

DEFAULT_GOLDEN = Path(__file__).resolve().parent / "golden_set" / "clause_set_v1.json"
DEFAULT_INDEX = "docmind_knowledge_eval_v1"
DEFAULT_ORG_ID = 9901
DEFAULT_OUT_DIR = "evals/reports"
EMBED_BATCH = 8


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evals",
        description="DocMind RAG 检索评估（#208 评估门禁 Phase 1 起步版）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--golden", default=str(DEFAULT_GOLDEN), help="golden set JSON 路径")
        sp.add_argument("--index", default=DEFAULT_INDEX, help="ES 索引名（经环境变量生效）")
        sp.add_argument(
            "--es-hosts",
            default=None,
            help="ES 地址；JSON 数组字符串或逗号分隔，如 '[\"http://localhost:9200\"]'",
        )
        sp.add_argument("--org-id", type=int, default=DEFAULT_ORG_ID, help="评估用组织 id")

    sp_check = sub.add_parser("check", help="校验 golden set（不联网、不碰 ES）")
    add_common(sp_check)
    sp_check.add_argument("--verbose", action="store_true", help="逐条列出条目")

    sp_seed = sub.add_parser("seed", help="把固定语料分块/向量化/写入评估索引")
    add_common(sp_seed)
    sp_seed.add_argument("--dry-run", action="store_true", help="只展示分块结果，不写 ES")

    sp_run = sub.add_parser("run", help="对全套 golden set 跑分并出报告")
    add_common(sp_run)
    sp_run.add_argument("--top-k", type=int, default=None, help="覆盖数据集 top_k")
    sp_run.add_argument("--out", default=DEFAULT_OUT_DIR, help="报告输出目录")
    sp_run.add_argument(
        "--allow-empty",
        action="store_true",
        help="索引里没有该组织的数据时也继续跑（默认拒绝，防止产出误导性的全 miss 报告）",
    )
    return parser


def _apply_env(args: argparse.Namespace) -> None:
    if getattr(args, "index", None):
        os.environ["ELASTICSEARCH_INDEX_NAME"] = str(args.index)
    hosts = getattr(args, "es_hosts", None)
    if hosts:
        raw = hosts.strip()
        value = (
            raw
            if raw.startswith("[")
            else json.dumps([h.strip() for h in raw.split(",") if h.strip()])
        )
        os.environ["ELASTICSEARCH_HOSTS"] = value
    # 评估链不碰 DB / JWT，但 Settings 在生产模式下要求这三键非空 ——
    # 缺省时补纯占位值（只过校验，不会有任何连接或签发发生）。
    os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    os.environ.setdefault("SECRET_KEY", "evals-placeholder-not-for-production-0000")
    os.environ.setdefault("JWT_SECRET_KEY", "evals-placeholder-not-for-production-0000")


def _load_golden(args: argparse.Namespace) -> GoldenSet | None:
    try:
        return load_golden_set(args.golden)
    except GoldenSetError as e:
        print(f"[evals] golden set 加载失败: {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


def _cmd_check(args: argparse.Namespace, golden: GoldenSet) -> int:
    positives = golden.positive_items()
    negatives = golden.negative_items()
    nm_count = sum(1 for it in positives if it.near_miss)
    by_type: dict[str, int] = {}
    for it in golden.items:
        by_type[it.type] = by_type.get(it.type, 0) + 1
    print(f"[evals] golden set OK: {golden.dataset_id} v{golden.version}")
    print(f"  条目: {len(golden.items)}（正例 {len(positives)} + 负例 {len(negatives)}）")
    print(f"  声明 near-miss 的正例: {nm_count}")
    print(f"  类型分布: {json.dumps(by_type, ensure_ascii=False)}")
    print(f"  语料: {len(golden.corpus_files)} 个文件 @ {golden.corpus_dir}")
    if args.verbose:
        for it in golden.items:
            gold = " ".join(loc.label() for loc in it.gold) or "-"
            print(f"    [{it.expect:5s}] {it.id:36s} gold={gold}")
    return 0


# ---------------------------------------------------------------------------
# seed（live）
# ---------------------------------------------------------------------------


def _require_embedding_client():
    from app.dependencies import _build_embedding_client

    client = _build_embedding_client()
    if client is None:
        print(
            "[evals] 未配置 embedding 客户端：需要 EMBEDDING_API_KEY（或 DEEPSEEK_API_KEY）"
            " 与 EMBEDDING_API_URL。示例（本地 Ollama）：\n"
            "  export EMBEDDING_API_URL=http://localhost:11434/v1 EMBEDDING_API_KEY=ollama\n"
            "  export EMBEDDING_MODEL=qwen3-embedding:0.6b VECTOR_DIMENSION=1024",
            file=sys.stderr,
        )
    return client


async def _embed_texts(client, texts: list[str], model: str) -> list[list[float]]:
    """批量取向量；批量失败时降级为逐条（本地模型对批量支持不一致）。"""
    vectors: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH):
        batch = [t.replace("\n", " ") for t in texts[start : start + EMBED_BATCH]]
        try:
            resp = await client.embeddings.create(input=batch, model=model)
            vectors.extend([list(d.embedding) for d in resp.data])
        except Exception as batch_err:  # noqa: BLE001 — 降级逐条重试
            if len(batch) == 1:
                raise RuntimeError(f"embedding 调用失败: {batch_err}") from batch_err
            for text in batch:
                resp = await client.embeddings.create(input=text, model=model)
                vectors.append(list(resp.data[0].embedding))
    return vectors


async def _close_clients(embed) -> None:
    """关掉 ES 与 embedding 客户端（含异常路径）——防 aiohttp 会话泄漏告警。"""
    from app.core.elasticsearch import close_elasticsearch

    try:
        await close_elasticsearch()
    except Exception:  # noqa: BLE001 — 关停失败不应再抛
        pass
    close = getattr(embed, "close", None)
    if close is not None:
        try:
            await close()
        except Exception:  # noqa: BLE001
            pass


async def _cmd_seed_async(args: argparse.Namespace, golden: GoldenSet) -> int:
    from app.core.config import settings
    from app.core.elasticsearch import (
        ElasticsearchTools,
        get_elasticsearch,
        get_index_error,
        init_elasticsearch,
    )

    index = settings.ELASTICSEARCH_INDEX_NAME
    org = str(args.org_id)

    if args.dry_run:
        for fname in golden.corpus_files:
            text = (golden.corpus_dir / fname).read_text(encoding="utf-8")
            chunks = split_corpus_file(text)
            print(f"[dry-run] {fname}: {len(chunks)} chunks")
            for i, chunk in enumerate(chunks):
                print(f"  c{i:02d}: {chunk.splitlines()[0][:60]}")
        return 0

    await init_elasticsearch()
    err = get_index_error()
    if err:
        print(f"[evals] ES 索引不可用: {err}", file=sys.stderr)
        return 2
    embed = _require_embedding_client()
    if embed is None:
        return 2
    try:
        client = await get_elasticsearch()

        actions: list[dict] = []
        dim = settings.VECTOR_DIMENSION
        for fname in golden.corpus_files:
            text = (golden.corpus_dir / fname).read_text(encoding="utf-8")
            chunks = split_corpus_file(text)
            document_id = f"eval-{Path(fname).stem}"
            vectors = await _embed_texts(embed, chunks, settings.EMBEDDING_MODEL)
            for i, (chunk, vec) in enumerate(zip(chunks, vectors, strict=True)):
                if len(vec) != dim:
                    print(
                        f"[evals] 向量维度不符：{fname} chunk {i} 得到 {len(vec)}，"
                        f"VECTOR_DIMENSION={dim}。请对齐配置后重试（不改动已有数据）。",
                        file=sys.stderr,
                    )
                    return 2
                actions.append(
                    {
                        "_index": index,
                        "_id": f"{document_id}:c{i:02d}",
                        "_source": {
                            "content": chunk,
                            "chunk_text": chunk,
                            "embedding": vec,
                            "organization_id": org,
                            "document_id": document_id,
                            "filename": fname,
                            "file_type": "md",
                            "file_size": len(chunk.encode("utf-8")),
                            "upload_time": "2026-10-09T00:00:00+00:00",
                            "user_id": "eval-seed",
                            "metadata": {"chunk_index": i, "source": "evals/corpus"},
                        },
                    }
                )
            # 幂等：先清同文档旧数据再写入（同 worker 的 delete_by_query 惯例）
            await client.delete_by_query(
                index=index, query={"term": {"document_id": document_id}}, refresh=True
            )
            print(f"[seed] {fname}: {len(chunks)} chunks → document_id={document_id}")

        await ElasticsearchTools.bulk_index_documents(actions)
        # bulk 默认不刷新：先 refresh 再回读，否则 count 会看到陈旧段（冒烟实测过）
        await client.indices.refresh(index=index)
        count = await client.count(index=index, query={"term": {"organization_id": org}})
        n = int(count.get("count", 0))
        expected = len(actions)
        print(f"[seed] 完成：写入 {len(actions)} chunks，回读 count(org={org})={n}")
        if n != expected:
            print(
                f"[evals] 回读不一致（期望 {expected}，得到 {n}）——先不要跑 run，检查 ES。",
                file=sys.stderr,
            )
            return 3
        return 0
    finally:
        await _close_clients(embed)


# ---------------------------------------------------------------------------
# run（live）
# ---------------------------------------------------------------------------


def _cmd_run_preflight(client_count: int, args: argparse.Namespace) -> int | None:
    if client_count > 0 or args.allow_empty:
        return None
    print(
        f"[evals] 索引里组织 {args.org_id} 的文档数为 0 —— 拒绝跑分（会产出误导性的全 miss 报告）。\n"
        f"  请先 `python -m evals seed`（或换 --index/--org-id）；确要空跑加 --allow-empty。",
        file=sys.stderr,
    )
    return 2


async def _cmd_run_async(args: argparse.Namespace, golden: GoldenSet) -> int:
    from app.core.config import settings
    from app.core.elasticsearch import (
        get_elasticsearch,
        get_index_error,
        init_elasticsearch,
    )
    from app.rag.retriever import HybridRetriever

    await init_elasticsearch()
    err = get_index_error()
    if err:
        print(f"[evals] ES 索引不可用: {err}", file=sys.stderr)
        return 2
    embed = _require_embedding_client()
    if embed is None:
        return 2
    try:
        client = await get_elasticsearch()

        count = await client.count(
            index=settings.ELASTICSEARCH_INDEX_NAME,
            query={"term": {"organization_id": str(args.org_id)}},
        )
        doc_count = int(count.get("count", 0))
        rc = _cmd_run_preflight(doc_count, args)
        if rc is not None:
            return rc

        top_k = args.top_k or golden.top_k
        retriever = HybridRetriever(openai_client=None, embedding_client=embed)

        async def retrieve(query: str, k: int) -> RetrievalObservation:
            docs, _qv, debug, degraded = await retriever.retrieve(query, args.org_id, k, debug=True)
            strategy = debug.get("strategy") if isinstance(debug, dict) else None
            return RetrievalObservation(
                results=list(docs or []), strategy=strategy, degraded=list(degraded or [])
            )

        print(
            f"[evals] 跑分中：{len(golden.items)} 条 × top_k={top_k} "
            f"（index={settings.ELASTICSEARCH_INDEX_NAME} org={args.org_id} "
            f"org 文档数={doc_count}，无 LLM 辅助）"
        )
        runs = await run_eval(golden.items, retrieve, top_k)
    finally:
        await _close_clients(embed)

    report = build_report(
        golden,
        runs,
        index=settings.ELASTICSEARCH_INDEX_NAME,
        org_id=args.org_id,
        top_k=top_k,
        retriever_label="HybridRetriever.retrieve (live, LLM off)",
    )
    out_dir = Path(args.out)
    json_path = write_json(report, out_dir / "latest.json")
    md_path = write_markdown(report, out_dir / "latest.md")

    agg = report["aggregates"]
    counts = agg["counts"]

    def pct(v):
        return "n/a" if v is None else f"{v:.1%}"

    print("─" * 60)
    print(f"ClauseHit@1 = {pct(agg['clause_hit@1'])}")
    print(f"ClauseHit@3 = {pct(agg['clause_hit@3'])}")
    print(
        f"ClauseHit@{top_k} = {pct(agg[f'clause_hit@{top_k}'])}  "
        f"95%CI={agg['clause_hit_ci95']}  (正例 {counts['positives']})"
    )
    print(
        f"NearMiss@{top_k} = {pct(agg[f'near_miss@{top_k}'])}  "
        f"(声明 {counts['positives_with_near_miss']}，越低越好)"
    )
    print(
        f"MRR = {agg['mrr']}  EmptyAcc = {pct(agg['empty_accuracy'])} (负例 {counts['negatives']})"
    )
    if counts["errors"]:
        print(f"错误 {counts['errors']} 条: {agg['error_items']}")
    print(f"报告: {json_path} / {md_path}")
    return 0


# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001 — 非交互环境无 reconfigure 也不该挂
        pass
    args = build_parser().parse_args(argv)
    _apply_env(args)
    golden = _load_golden(args)
    if golden is None:
        return 2
    if args.command == "check":
        return _cmd_check(args, golden)
    if args.command == "seed":
        return asyncio.run(_cmd_seed_async(args, golden))
    if args.command == "run":
        return asyncio.run(_cmd_run_async(args, golden))
    return 2
