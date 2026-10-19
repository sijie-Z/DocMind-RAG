"""评估 CLI：`python -m evals {check,seed,run,compare}`（#208 Phase 1 + 第⑤步 CI 门禁）。

两种模式：

**live 模式**（本地工具，需要真实 ES + 嵌入服务）——先 seed 再 run：

```bash
cd backend
export ELASTICSEARCH_HOSTS='["http://localhost:9200"]'
export ELASTICSEARCH_INDEX_NAME=docmind_knowledge_eval_v1
export EMBEDDING_API_URL=http://localhost:11434/v1 EMBEDDING_API_KEY=ollama
export EMBEDDING_MODEL=qwen3-embedding:0.6b VECTOR_DIMENSION=1024
python -m evals seed   # 把 corpus/ 固定语料写进评估索引（可反复执行）
python -m evals run    # 跑全套 golden set，出 JSON + Markdown 报告
```

**CI 模式**（`--embedding fake`，门禁本体）——确定性伪嵌入 + 专用索引先删后建：

```bash
python -m evals seed --recreate --embedding fake --index docmind_knowledge_eval_ci_v1
python -m evals run  --embedding fake --index docmind_knowledge_eval_ci_v1 --out evals/reports/ci
python -m evals compare --report evals/reports/ci/latest.json \
    --baseline evals/baselines/ci_baseline.json   # 非零退出码 = 门禁红
```

（一键入口 `make eval-ci`；协议与维护纪律见 backend/docs/eval-gate-phase1.md 的
「CI 门禁」一节。物理状态纪律：门禁每次从零重建专用索引、refresh 到 visible、
回读 count 断言 == 写入块数——不同 seed 物理状态间的分数摆动因此被消除。）

接线纪律：`--index/--org-id/--es-hosts` 等参数先写进环境变量，**再** import app ——
settings 在导入时读环境，这是唯一正确的顺序（沿 #279 执行者的 env 法）。

检索层口径（有意收窄）：直接调用 `HybridRetriever.retrieve`
（关键词+向量+RRF/MMR+后处理），**不经过** rerank / 语义缓存 / LLM 环节，
且 `openai_client=None`（无 LLM 改写 / HyDE）——保证可复现。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from .chunking import split_corpus_file
from .deterministic_embedding import (
    CI_EMBEDDING_DIM,
    CI_EMBEDDING_ID,
    DeterministicEmbeddingClient,
)
from .golden import GoldenSet, GoldenSetError, load_golden_set
from .harness import RetrievalObservation, run_eval
from .report import build_report, write_json, write_markdown

DEFAULT_GOLDEN = Path(__file__).resolve().parent / "golden_set" / "clause_set_v1.json"
DEFAULT_INDEX = "docmind_knowledge_eval_v1"
DEFAULT_CI_INDEX = "docmind_knowledge_eval_ci_v1"
DEFAULT_ORG_ID = 9901
DEFAULT_OUT_DIR = "evals/reports"
DEFAULT_CI_REPORT = "evals/reports/ci/latest.json"
DEFAULT_CI_BASELINE = Path(__file__).resolve().parent / "baselines" / "ci_baseline.json"
EMBED_BATCH = 8

# --recreate 的护栏：只允许重建「专用评估索引」（名字含 eval）。
_RECREATE_INDEX_MARKER = "eval"

# CI 门禁的固定指标 schema（顺序即输出顺序）。kind：
#   floor = 实测必须 ≥ 基线（越高越好）；cap = 实测必须 ≤ 基线（越低越好）。
# 容差 = 0：固定协议（伪嵌入 + 先删后建 seed + refresh）下逐位确定性，
# 基线值 = 首次 stamp 时观测值；任何下跌即红、任何提升都放行。
CI_METRIC_SPECS: tuple[tuple[str, str, str], ...] = (
    ("clause_hit@1", "floor", "aggregates"),
    ("clause_hit@3", "floor", "aggregates"),
    ("clause_hit@5", "floor", "aggregates"),
    ("near_miss@5", "cap", "aggregates"),
    ("mrr", "floor", "aggregates"),
    ("empty_accuracy", "floor", "aggregates"),
    ("errors", "cap", "counts"),
)

# 报告 meta 与基线 protocol 必须逐键一致的协议字段（防拿 apples 对 oranges）。
PROTOCOL_KEYS: tuple[str, ...] = ("embedding_mode", "top_k", "dataset_id", "dataset_version")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m evals",
        description="DocMind RAG 检索评估（#208 评估门禁；CI 模式 = --embedding fake + seed --recreate）",
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

    def add_embedding(sp: argparse.ArgumentParser) -> None:
        sp.add_argument(
            "--embedding",
            choices=("live", "fake"),
            default="live",
            help=(
                "嵌入后端：live=真实服务（本地真嵌入模式）；fake=确定性伪嵌入"
                f"（CI 模式；{CI_EMBEDDING_ID}，dim 固定 {CI_EMBEDDING_DIM}，无网络）"
            ),
        )

    sp_check = sub.add_parser("check", help="校验 golden set（不联网、不碰 ES）")
    add_common(sp_check)
    sp_check.add_argument("--verbose", action="store_true", help="逐条列出条目")

    sp_seed = sub.add_parser("seed", help="把固定语料分块/向量化/写入评估索引")
    add_common(sp_seed)
    add_embedding(sp_seed)
    sp_seed.add_argument("--dry-run", action="store_true", help="只展示分块结果，不写 ES")
    sp_seed.add_argument(
        "--recreate",
        action="store_true",
        help=(
            "CI 协议：先删后建专用评估索引（要求索引名含 'eval'），把索引物理状态"
            "从零固定——消除不同 seed 状态间的分数摆动"
        ),
    )

    sp_run = sub.add_parser("run", help="对全套 golden set 跑分并出报告")
    add_common(sp_run)
    add_embedding(sp_run)
    sp_run.add_argument("--top-k", type=int, default=None, help="覆盖数据集 top_k")
    sp_run.add_argument("--out", default=DEFAULT_OUT_DIR, help="报告输出目录")
    sp_run.add_argument(
        "--allow-empty",
        action="store_true",
        help="索引里没有该组织的数据时也继续跑（默认拒绝，防止产出误导性的全 miss 报告）",
    )

    sp_cmp = sub.add_parser(
        "compare", help="把报告与 CI 基线比对（门禁：非零退出码 = 红；fail-closed）"
    )
    sp_cmp.add_argument("--report", default=DEFAULT_CI_REPORT, help="run 产出的报告 JSON")
    sp_cmp.add_argument(
        "--baseline", default=str(DEFAULT_CI_BASELINE), help="基线 JSON（入库工件）"
    )
    sp_cmp.add_argument(
        "--update-baseline",
        action="store_true",
        help=(
            "用本次报告重盖基线文件（有意识的显式操作：改基线=显式 PR 行为、评审可见）；"
            "报告含 errors 时拒绝"
        ),
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
    # CI 模式（--embedding fake）：VECTOR_DIMENSION 写死为伪嵌入维度并覆盖环境——
    # 索引 mapping 的 dense_vector dims 与向量必须同源，本机残留的
    # VECTOR_DIMENSION（如 live 模式的 1024）绝不能改变桶宽、进而在基线里摆动。
    if getattr(args, "embedding", "live") == "fake":
        prev = os.environ.get("VECTOR_DIMENSION")
        os.environ["VECTOR_DIMENSION"] = str(CI_EMBEDDING_DIM)
        if prev and prev != str(CI_EMBEDDING_DIM):
            print(
                f"[evals] fake 嵌入模式：VECTOR_DIMENSION 固定为 {CI_EMBEDDING_DIM}"
                f"（原环境值 {prev} 已覆盖）"
            )
        # 伪嵌入不需要在线 key，但 embedding_service 单例在模块导入时构造
        # （#196 唯一构造点）；补占位 key 以通过构造，绝不会发起真实调用。
        os.environ.setdefault("EMBEDDING_API_KEY", "eval-fake-placeholder")
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


def _require_embedding_service(mode: str):
    """获取评估用 embedding provider（索引/检索共用同一契约，issue #196）。

    - fake：确定性伪嵌入（固定 dim，无网络），包装成生产同款
      ``EmbeddingService``，维度校验/调用面与 live 完全一致；
    - live：与生产索引/检索共用 ``app.services.embedding_service`` 单例
      （模型/维度由配置唯一解析）。
    """
    try:
        from app.services.embedding_service import (
            EmbeddingConfig,
            EmbeddingService,
            embedding_service,
        )
    except RuntimeError as e:
        # 单例在模块导入时构造（唯一构造点）；缺 key 时会抛 RuntimeError。
        print(
            f"[evals] embedding 配置不可用：{e}\n"
            "  需要 EMBEDDING_API_KEY（或 DEEPSEEK_API_KEY）与 EMBEDDING_API_URL。"
            "示例（本地 Ollama）：\n"
            "  export EMBEDDING_API_URL=http://localhost:11434/v1 EMBEDDING_API_KEY=ollama\n"
            "  export EMBEDDING_MODEL=qwen3-embedding:0.6b VECTOR_DIMENSION=1024\n"
            "（或在 CI 模式用 --embedding fake，无需任何嵌入服务）",
            file=sys.stderr,
        )
        return None

    if mode == "fake":
        print(
            f"[evals] 嵌入模式：fake（{CI_EMBEDDING_ID}，固定 dim={CI_EMBEDDING_DIM}）"
            "——确定性伪嵌入，无网络、无 Ollama"
        )
        return EmbeddingService(
            config=EmbeddingConfig(
                client=DeterministicEmbeddingClient(),
                model=CI_EMBEDDING_ID,
                dimension=CI_EMBEDDING_DIM,
                source="fake",
            )
        )
    # live：唯一真源（与生产索引路径同一实例/同一模型/同一维度契约）。
    return embedding_service


def _recreate_guard(index_name: str) -> str | None:
    """`--recreate` 的护栏：只允许重建名字含 'eval' 的专用评估索引。

    先删后建是破坏性操作；runner 的默认索引与 CI 索引都含 'eval'，
    这道护栏防的是「误把 --recreate 指向生产知识库索引」这一类不可逆事故。
    """
    if _RECREATE_INDEX_MARKER in index_name.lower():
        return None
    return (
        f"--recreate 会先删除再重建索引 {index_name!r}；为防误删生产数据，"
        f"仅允许索引名包含 {_RECREATE_INDEX_MARKER!r} 的专用评估索引。"
        f"请改用评估索引名（如 {DEFAULT_CI_INDEX}），或去掉 --recreate。"
    )


def _seed_readback_error(expected: int, actual: int) -> str | None:
    """seed 的 doc 数断言（CI 协议的一部分）：回读 count 必须等于写入块数。"""
    if actual == expected:
        return None
    return f"回读不一致（期望 {expected}，得到 {actual}）——先不要跑 run，检查 ES。"


async def _embed_texts(embed_service, texts: list[str]) -> list[list[float]]:
    """批量取向量（走统一 EmbeddingService：同 client/model/维度校验）。

    批量失败时降级为逐条——本地 Ollama 对批量支持不一致。
    """
    vectors: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH):
        batch = [t.replace("\n", " ") for t in texts[start : start + EMBED_BATCH]]
        try:
            vectors.extend(await embed_service.get_embeddings(batch))
        except Exception as batch_err:  # noqa: BLE001 — 降级逐条重试
            if len(batch) == 1:
                raise RuntimeError(f"embedding 调用失败: {batch_err}") from batch_err
            for text in batch:
                vectors.extend(await embed_service.get_embeddings([text]))
    return vectors


async def _close_clients(embed) -> None:
    """关掉 ES 与 embedding 客户端（含异常路径）——防 aiohttp 会话泄漏告警。"""
    from app.core.elasticsearch import close_elasticsearch

    try:
        await close_elasticsearch()
    except Exception:  # noqa: BLE001 — 关停失败不应再抛
        pass
    # embed 可能是 EmbeddingService（统一契约）或裸客户端；都尝试关底层 client。
    client = getattr(embed, "client", embed)
    close = getattr(client, "close", None)
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
    if args.recreate:
        # CI 协议（先删后建）：物理状态从零固定——实测不同 seed 状态间 BM25
        # 统计会因 tombstone/merge 时机摆动（评审 Minor-6），任何基线比对都必须
        # 在同一物理状态下做；每次重建 + 无删除 = 该状态可复现。
        guard = _recreate_guard(index)
        if guard:
            print(f"[evals] {guard}", file=sys.stderr)
            return 2
        from app.core.elasticsearch import recreate_knowledge_index

        try:
            ok = await recreate_knowledge_index(confirm=True)
        except RuntimeError as e:  # get_elasticsearch() 在连接失败时抛
            print(f"[evals] ES 不可用，无法重建索引: {e}", file=sys.stderr)
            return 2
        if not ok:
            print(f"[evals] 索引重建失败（{index}）——详情见日志。", file=sys.stderr)
            return 2
        print(f"[seed] 先删后建：索引 {index} 已重建（mapping/settings 按当前配置）")
    err = get_index_error()
    if err:
        print(f"[evals] ES 索引不可用: {err}", file=sys.stderr)
        return 2
    embed = _require_embedding_service(getattr(args, "embedding", "live"))
    if embed is None:
        return 2
    try:
        client = await get_elasticsearch()

        if args.recreate:
            # 协议断言：重建后索引必须为空（先删后建真的生效了）。
            c0 = await client.count(index=index)
            n0 = int(c0.get("count", 0))
            if n0 != 0:
                print(
                    f"[evals] 重建后索引应空（count=0），实际 {n0} —— 中止（协议断言失败）。",
                    file=sys.stderr,
                )
                return 3
            print("[seed] 重建后 count=0 断言通过")

        actions: list[dict] = []
        dim = settings.VECTOR_DIMENSION
        for fname in golden.corpus_files:
            text = (golden.corpus_dir / fname).read_text(encoding="utf-8")
            chunks = split_corpus_file(text)
            document_id = f"eval-{Path(fname).stem}"
            vectors = await _embed_texts(embed, chunks)
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
        readback_err = _seed_readback_error(expected, n)
        if readback_err:
            print(f"[evals] {readback_err}", file=sys.stderr)
            return 3
        # doc 数断言（CI 协议的另一半）：组织内 distinct document_id 数 == 语料文件数
        docs_res = await client.search(
            index=index,
            size=0,
            query={"term": {"organization_id": org}},
            aggs={"docs": {"cardinality": {"field": "document_id"}}},
        )
        n_docs = int(docs_res["aggregations"]["docs"]["value"])
        expected_docs = len(golden.corpus_files)
        print(f"[seed] doc 数断言：distinct document_id={n_docs}（期望 {expected_docs}）")
        if n_docs != expected_docs:
            print(
                f"[evals] doc 数断言失败（期望 {expected_docs}，得到 {n_docs}）——中止。",
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
    mode = getattr(args, "embedding", "live")
    embed = _require_embedding_service(mode)
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

        try:
            info = await client.info()
            es_version = str((info.get("version") or {}).get("number") or "") or None
        except Exception:  # noqa: BLE001 — 版本只是 provenance，拿不到不阻断
            es_version = None

        top_k = args.top_k or golden.top_k
        retriever = HybridRetriever(openai_client=None, embedding_provider=embed)

        async def retrieve(query: str, k: int) -> RetrievalObservation:
            docs, _qv, debug, degraded = await retriever.retrieve(query, args.org_id, k, debug=True)
            strategy = debug.get("strategy") if isinstance(debug, dict) else None
            return RetrievalObservation(
                results=list(docs or []), strategy=strategy, degraded=list(degraded or [])
            )

        print(
            f"[evals] 跑分中：{len(golden.items)} 条 × top_k={top_k} "
            f"（index={settings.ELASTICSEARCH_INDEX_NAME} org={args.org_id} "
            f"org 文档数={doc_count}，无 LLM 辅助，嵌入={mode}）"
        )
        runs = await run_eval(golden.items, retrieve, top_k)
    finally:
        await _close_clients(embed)

    # 分块数（协议记录：CI 基线的 seed 回读断言正是按这些数校验的）
    corpus_chunks = {
        fname: len(split_corpus_file((golden.corpus_dir / fname).read_text(encoding="utf-8")))
        for fname in golden.corpus_files
    }
    if mode == "fake":
        retriever_label = (
            f"HybridRetriever.retrieve (CI 确定性伪嵌入 {CI_EMBEDDING_ID}, LLM off)"
        )
    else:
        retriever_label = "HybridRetriever.retrieve (live, LLM off)"
    report = build_report(
        golden,
        runs,
        index=settings.ELASTICSEARCH_INDEX_NAME,
        org_id=args.org_id,
        top_k=top_k,
        retriever_label=retriever_label,
        embedding_mode=mode,
        es_version=es_version,
        corpus_chunks=corpus_chunks,
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
# compare（CI 门禁本体；fail-closed）
# ---------------------------------------------------------------------------


class CompareError(ValueError):
    """报告/基线结构或协议不一致 —— 一律按失败处理（fail-closed），绝不静默放行。"""


def _load_json(path: str | Path, what: str) -> object:
    p = Path(path)
    if not p.is_file():
        raise CompareError(f"{what} 不存在: {p}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise CompareError(f"{what} 不是合法 JSON: {p}: {e}") from e


def validate_baseline(baseline: object) -> dict:
    """基线文件的 schema 校验（宁严勿松：缺键/多键/类型不对一律拒绝）。"""
    if not isinstance(baseline, dict):
        raise CompareError("基线顶层必须是对象")
    protocol = baseline.get("protocol")
    if not isinstance(protocol, dict):
        raise CompareError("基线缺少 protocol 对象")
    for key in PROTOCOL_KEYS:
        if key not in protocol:
            raise CompareError(f"基线 protocol 缺少 {key}")
    if not isinstance(protocol.get("corpus_chunks"), dict):
        raise CompareError("基线 protocol 缺少 corpus_chunks（分块协议记录）")
    metrics = baseline.get("metrics")
    if not isinstance(metrics, dict):
        raise CompareError("基线缺少 metrics 对象")
    want = {name for name, _, _ in CI_METRIC_SPECS}
    missing = want - set(metrics)
    extra = set(metrics) - want
    if missing or extra:
        raise CompareError(
            f"基线 metrics 与固定 schema 不符：缺少 {sorted(missing)} / 多余 {sorted(extra)}"
        )
    for name, kind, _section in CI_METRIC_SPECS:
        entry = metrics[name]
        if not isinstance(entry, dict) or entry.get("kind") != kind:
            raise CompareError(f"基线 metrics[{name}] 必须为 {{kind: {kind!r}, value: ...}}")
        value = entry.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise CompareError(f"基线 metrics[{name}].value 必须是数值，得到 {value!r}")
    return baseline


def _metric_actual(report: dict, name: str, section: str):
    aggregates = report.get("aggregates")
    if not isinstance(aggregates, dict):
        raise CompareError("报告缺少 aggregates 对象")
    if section == "counts":
        counts = aggregates.get("counts")
        if not isinstance(counts, dict) or "errors" not in counts:
            raise CompareError("报告 aggregates.counts.errors 缺失")
        return counts["errors"]
    if name not in aggregates:
        raise CompareError(f"报告 aggregates 缺少指标 {name}")
    return aggregates[name]


def _protocol_mismatches(report: dict, baseline: dict) -> list[str]:
    meta = report.get("meta")
    if not isinstance(meta, dict):
        raise CompareError("报告缺少 meta 对象")
    protocol = baseline["protocol"]
    out: list[str] = []
    for key in PROTOCOL_KEYS:
        reported, expected = meta.get(key), protocol.get(key)
        if reported != expected:
            out.append(f"{key}: 报告={reported!r} 基线={expected!r}")
    if meta.get("corpus_chunks") != protocol.get("corpus_chunks"):
        out.append(
            f"corpus_chunks: 报告={meta.get('corpus_chunks')!r} "
            f"基线={protocol.get('corpus_chunks')!r}"
        )
    return out


def compare_report(report: dict, baseline: dict) -> list[dict]:
    """逐项对比（纯函数）。结构/协议问题抛 CompareError；返回逐项判定行。

    容差 = 0：floor 用 ``实测 >= 基线``、cap 用 ``实测 <= 基线``——基线值就是
    固定协议下的观测值，相等即通过（任何下跌即红、任何提升都放行）。
    指标为 None（分母为 0 / 全部错误）按失败处理。
    """
    validate_baseline(baseline)
    mismatches = _protocol_mismatches(report, baseline)
    if mismatches:
        raise CompareError("报告与基线协议不一致：" + "；".join(mismatches))
    rows: list[dict] = []
    for name, kind, section in CI_METRIC_SPECS:
        expected = baseline["metrics"][name]["value"]
        actual = _metric_actual(report, name, section)
        if actual is None:
            rows.append(
                {
                    "name": name,
                    "kind": kind,
                    "expected": expected,
                    "actual": None,
                    "verdict": "fail",
                    "note": "指标为 None（分母为 0 / 全部错误）",
                }
            )
            continue
        if isinstance(actual, bool) or not isinstance(actual, (int, float)):
            raise CompareError(f"报告指标 {name} 类型异常: {actual!r}")
        ok = actual >= expected if kind == "floor" else actual <= expected
        note = ""
        if not ok:
            delta = actual - expected
            note = f"越界 {delta:+.4f}（{'应为 ≥' if kind == 'floor' else '应为 ≤'} {expected}）"
        rows.append(
            {
                "name": name,
                "kind": kind,
                "expected": expected,
                "actual": actual,
                "verdict": "pass" if ok else "fail",
                "note": note,
            }
        )
    return rows


def stamp_baseline(report: dict) -> dict:
    """从报告生成基线 dict（floors/caps = 本次观测值；容差 0 的口径即在此固化）。"""
    meta = report.get("meta")
    if not isinstance(meta, dict):
        raise CompareError("报告缺少 meta 对象")
    for key in PROTOCOL_KEYS:
        if key not in meta:
            raise CompareError(f"报告 meta 缺少协议字段 {key}（旧版报告不能盖基线）")
    if not isinstance(meta.get("corpus_chunks"), dict) or not meta["corpus_chunks"]:
        raise CompareError("报告 meta 缺少 corpus_chunks")
    metrics: dict = {}
    for name, kind, section in CI_METRIC_SPECS:
        actual = _metric_actual(report, name, section)
        if actual is None:
            raise CompareError(f"指标 {name} 为 None，拒绝盖基线")
        if name == "errors" and int(actual) != 0:
            raise CompareError(f"报告含 errors={actual}，拒绝盖基线（先修回归）")
        metrics[name] = {"kind": kind, "value": actual}
    return {
        "baseline_id": "ci-gate-v1",
        "description": (
            "CI 评估门禁基线（#208 第⑤步）。运行协议 = 专用评估索引先删后建 + "
            f"确定性伪嵌入 {CI_EMBEDDING_ID}(dim={CI_EMBEDDING_DIM}) + 无 LLM；此文件是门禁的"
            "唯一真相——更新它等于接受一次行为变化，属显式 PR 行为（评审可见）。"
        ),
        "tolerance": "0（固定协议下确定性：floor 实测≥基线、cap 实测≤基线，相等即通过）",
        "protocol": {
            "embedding_mode": meta["embedding_mode"],
            "embedding_impl": f"{CI_EMBEDDING_ID} (dim={CI_EMBEDDING_DIM})",
            "dataset_id": meta["dataset_id"],
            "dataset_version": meta["dataset_version"],
            "top_k": meta["top_k"],
            "index": meta.get("index"),
            "es_version": meta.get("es_version"),
            "corpus_chunks": meta["corpus_chunks"],
            "seed": "recreate(先删后建) → bulk → refresh → 回读 count==写入块数 且 distinct doc==语料文件数",
        },
        "stamped_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "stamped_note": "由 python -m evals compare --update-baseline 生成",
        "metrics": metrics,
    }


def _cmd_compare(args: argparse.Namespace) -> int:
    in_ci = os.environ.get("GITHUB_ACTIONS") == "true"
    try:
        report = _load_json(args.report, "报告")
    except CompareError as e:
        print(f"[evals] 门禁比对前置检查失败（fail-closed）：{e}", file=sys.stderr)
        return 2
    if not isinstance(report, dict):
        print("[evals] 门禁比对前置检查失败（fail-closed）：报告顶层必须是对象", file=sys.stderr)
        return 2

    if args.update_baseline:
        old: dict | None = None
        try:
            loaded = _load_json(args.baseline, "基线")
            old = validate_baseline(loaded)
        except CompareError as e:
            print(f"[evals] 旧基线不可用（将新建）：{e}")
        try:
            new = stamp_baseline(report)
        except CompareError as e:
            print(f"[evals] 拒绝盖基线（fail-closed）：{e}", file=sys.stderr)
            return 2
        print(
            f"[evals] 盖基线（有意识操作）：{args.baseline}"
            "——改基线=显式 PR 行为，请把本文件的 diff 放进 PR 供评审。"
        )
        if old is not None:
            for name, _kind, _section in CI_METRIC_SPECS:
                old_v = old["metrics"].get(name, {}).get("value")
                new_v = new["metrics"][name]["value"]
                mark = "" if old_v == new_v else "   ← 变化"
                print(f"  {name:18s} {old_v} → {new_v}{mark}")
        p = Path(args.baseline)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(new, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[evals] 基线已写入：{p}（stamped_at={new['stamped_at']}）")
        return 0

    try:
        baseline = validate_baseline(_load_json(args.baseline, "基线"))
        rows = compare_report(report, baseline)
    except CompareError as e:
        print(f"[evals] 门禁比对前置检查失败（fail-closed）：{e}", file=sys.stderr)
        if in_ci:
            print(f"::error::Eval Gate 前置检查失败：{e}")
        return 2

    meta = report["meta"]
    print(
        f"[evals] CI 门禁比对：报告={args.report}（{meta.get('embedding_mode')} 模式，"
        f"生成于 {meta.get('generated_at')}）"
    )
    print(f"[evals] 基线={args.baseline}（stamped_at={baseline.get('stamped_at')}，容差=0）")
    print(f"  {'指标':<18s} {'要求':<8s} {'基线':>10s} {'实测':>10s}  判定")
    breaches: list[dict] = []
    for row in rows:
        requirement = "≥" if row["kind"] == "floor" else "≤"
        if row["actual"] is None:
            actual_str = "None"
        elif isinstance(row["actual"], float):
            actual_str = f"{row['actual']:.4f}"
        else:
            actual_str = f"{row['actual']}"
        expected_str = (
            f"{row['expected']:.4f}" if isinstance(row["expected"], float) else f"{row['expected']}"
        )
        verdict = "PASS" if row["verdict"] == "pass" else "FAIL"
        line = (
            f"  {row['name']:<18s} {requirement:<8s} {expected_str:>10s} {actual_str:>10s}"
            f"  {verdict}{('  ' + row['note']) if row['note'] else ''}"
        )
        print(line)
        if row["verdict"] != "pass":
            breaches.append(row)

    if breaches:
        for row in breaches:
            msg = (
                f"Eval Gate 越界：{row['name']} 实测={row['actual']} "
                f"{'<' if row['kind'] == 'floor' else '>'} 基线={row['expected']}"
                f"{'（' + row['note'] + '）' if row['note'] else ''}"
            )
            print(f"[evals] 门禁失败：{msg}", file=sys.stderr)
            if in_ci:
                print(f"::error::{msg}")
        print(
            f"[evals] CI 门禁失败：{len(breaches)}/{len(rows)} 项越界。"
            "若这是有意的行为变化：更新基线放到同一个 PR（python -m evals compare "
            "--update-baseline，或 make eval-ci-baseline），基线 diff 供评审核对；"
            "若是回归：修代码而不是改基线。",
            file=sys.stderr,
        )
        return 1
    print(f"[evals] CI 门禁通过：{len(rows)}/{len(rows)} 项达标（容差 0）")
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
    if args.command == "compare":
        return _cmd_compare(args)
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
