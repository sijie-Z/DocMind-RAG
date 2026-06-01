"""Reranker — local cross-encoder model with API/LLM fallbacks.

Primary: local cross-encoder (BAAI/bge-reranker-base, free, no API cost)
Fallback 1: Zhipu rerank API (paid, requires RERANK_API_KEY)
Fallback 2: LLM-based reranking (paid, uses DeepSeek/configured model)
"""
import asyncio
import logging
import re
import time
from functools import lru_cache

from app.core.config import settings

logger = logging.getLogger(__name__)


# ── Local cross-encoder (lazy singleton) ─────────────────────────

@lru_cache(maxsize=1)
def _get_cross_encoder(model_name: str | None = None):
    """Load the cross-encoder model once and cache it for the process lifetime.

    Returns the model instance, or None if loading fails.
    """
    model_name = model_name or getattr(settings, "RERANK_LOCAL_MODEL", "BAAI/bge-reranker-base")
    try:
        from sentence_transformers import CrossEncoder
        import torch

        device = "cuda" if torch.cuda.is_available() else "cpu"
        logger.info("Loading local cross-encoder reranker: %s (device=%s)", model_name, device)
        model = CrossEncoder(model_name, device=device)
        logger.info("Local reranker loaded successfully")
        return model
    except Exception as e:
        logger.warning("Failed to load local cross-encoder reranker '%s': %s", model_name, e)
        return None


async def _rerank_local(query: str, candidates: list[dict], top_n: int) -> list[dict] | None:
    """Rerank using local cross-encoder model. Returns None if unavailable."""
    model = _get_cross_encoder()
    if model is None:
        return None

    if not candidates:
        return []

    pairs = []
    for h in candidates:
        src = h.get("_source") or {}
        passage = (src.get("content") or src.get("chunk_text") or "")[:512]
        pairs.append([query, passage])

    try:
        loop = asyncio.get_running_loop()
        scores = await loop.run_in_executor(
            None,
            lambda: model.predict(pairs, show_progress_bar=False).tolist(),
        )
    except Exception as e:
        logger.warning("Local reranker predict failed: %s", e)
        return None

    # Pair scores back to candidates & sort descending
    scored = list(zip(candidates, scores))
    scored.sort(key=lambda x: x[1], reverse=True)

    result = [h for h, _ in scored[:top_n]]
    # Append any remaining candidates beyond top_n
    remaining = [h for h in candidates if h not in result]
    return result + remaining


# ── Zhipu rerank API (paid) ──────────────────────────────────────

async def _rerank_zhipu(query: str, candidates: list[dict], top_n: int) -> list[dict] | None:
    """Rerank using Zhipu rerank API. Returns None on failure."""
    if not hasattr(settings, "RERANK_API_KEY") or not settings.RERANK_API_KEY:
        return None

    try:
        import httpx

        documents = [
            (h.get("_source") or {}).get("chunk_text", "")[:1000]
            for h in candidates
        ]
        async with httpx.AsyncClient() as client:
            url = f"{settings.RERANK_API_URL.rstrip('/')}/rerank"
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {settings.RERANK_API_KEY}"},
                json={"model": "rerank", "query": query, "documents": documents, "top_n": top_n},
                timeout=settings.RAG_RERANK_TIMEOUT_SECONDS,
            )
            if resp.status_code != 200:
                logger.warning("Zhipu rerank failed (%s)", resp.status_code)
                return None

            results = resp.json().get("results", [])
            ordered = []
            used: set[int] = set()
            for res in results:
                idx = res.get("index")
                if idx is not None and 0 <= idx < len(candidates):
                    ordered.append(candidates[idx])
                    used.add(idx)
            for i, h in enumerate(candidates):
                if i not in used:
                    ordered.append(h)
            return ordered
    except Exception as e:
        logger.warning("Zhipu rerank error: %s", e)
        return None


# ── LLM-based reranker (paid fallback) ────────────────────────────

async def _rerank_llm(query: str, candidates: list[dict], top_n: int, llm_client) -> list[dict] | None:
    """Rerank using LLM (expensive fallback). Returns None if no client."""
    if not llm_client:
        return None

    model_name = settings.RERANK_MODEL or settings.RAG_RERANK_MODEL or settings.DEEPSEEK_MODEL

    brief = []
    for idx, h in enumerate(candidates, 1):
        src = h.get("_source") or {}
        text = (src.get("content") or "")[:500]
        brief.append(f"[{idx}] 文件:{src.get('filename', '未知')}\n内容:{text}")

    prompt = (
        "你是检索重排器。请根据用户问题判断候选片段相关性，"
        "只返回 JSON 数组，元素是候选编号，按相关性从高到低排序。\n\n"
        f"用户问题: {query}\n\n候选片段:\n" + "\n\n".join(brief) + "\n\n输出示例: [3,1,2]"
    )
    try:
        rsp = await llm_client.chat.completions.create(
            model=model_name,
            messages=[
                {"role": "system", "content": "你是严格输出JSON数组的重排器。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
            timeout=settings.RAG_RERANK_TIMEOUT_SECONDS,
        )
        content = (rsp.choices[0].message.content or "").strip()
        nums = [int(x) for x in re.findall(r"\d+", content)]
        ordered, used = [], set()
        for n in nums:
            if 1 <= n <= len(candidates) and n not in used:
                ordered.append(candidates[n - 1])
                used.add(n)
        for i, h in enumerate(candidates, 1):
            if i not in used:
                ordered.append(h)
        return ordered
    except Exception as e:
        logger.warning("LLM reranker failed: %s", e)
        return None


# ── Public API ────────────────────────────────────────────────────

async def rerank(query: str, hits: list[dict], rerank_client=None) -> list[dict]:
    """Rerank hits using local cross-encoder (free), with API/LLM fallbacks.

    Strategy priority:
      1. Local cross-encoder (if RERANK_USE_LOCAL = True, default)
      2. Zhipu rerank API (if RERANK_API_KEY is set)
      3. LLM-based reranking (if rerank_client is provided, most expensive)
      4. No-op (return original order)
    """
    if not hits or not settings.RAG_ENABLE_RERANKER:
        return hits

    from app.core.prometheus import RAG_RERANK_LATENCY, RAG_RERANK_TOTAL
    RAG_RERANK_TOTAL.inc()
    start = time.perf_counter()

    top_n = max(1, int(settings.RAG_RERANK_TOP_N or 20))
    candidates = hits[:top_n]

    try:
        ordered: list[dict] | None = None

        # Strategy 1: local cross-encoder (free)
        use_local = getattr(settings, "RERANK_USE_LOCAL", True)
        if use_local:
            ordered = await _rerank_local(query, candidates, top_n)

        # Strategy 2: Zhipu API (paid)
        if ordered is None:
            ordered = await _rerank_zhipu(query, candidates, top_n)

        # Strategy 3: LLM-based (paid)
        if ordered is None:
            ordered = await _rerank_llm(query, candidates, top_n, rerank_client)

        # Strategy 4: no-op
        if ordered is None:
            return hits

        return ordered + hits[top_n:]

    except Exception as e:
        logger.warning("Reranker failed, fallback to original: %s", e)
        return hits
    finally:
        RAG_RERANK_LATENCY.observe(time.perf_counter() - start)
