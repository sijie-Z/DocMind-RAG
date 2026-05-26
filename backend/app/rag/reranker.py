"""Reranker — cross-encoder or LLM-based document reranking."""
import logging
import re
import time

from app.core.config import settings

logger = logging.getLogger(__name__)


async def rerank(query: str, hits: list[dict], rerank_client=None) -> list[dict]:
    """Rerank hits using available reranker (Zhipu rerank API or LLM fallback)."""
    if not hits or not settings.RAG_ENABLE_RERANKER:
        return hits

    from app.core.prometheus import RAG_RERANK_LATENCY, RAG_RERANK_TOTAL
    RAG_RERANK_TOTAL.inc()
    start = time.perf_counter()

    top_n = max(1, int(settings.RAG_RERANK_TOP_N or 20))
    candidates = hits[:top_n]
    model_name = settings.RERANK_MODEL or settings.RAG_RERANK_MODEL or "rerank"

    try:
        # Try Zhipu rerank API
        if model_name == "rerank" and hasattr(settings, "RERANK_API_KEY") and settings.RERANK_API_KEY:
            import httpx
            async with httpx.AsyncClient() as client:
                documents = [h.get("_source", {}).get("chunk_text", "")[:1000] for h in candidates]
                url = f"{settings.RERANK_API_URL.rstrip('/')}/rerank"
                resp = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {settings.RERANK_API_KEY}"},
                    json={"model": "rerank", "query": query, "documents": documents, "top_n": top_n},
                    timeout=settings.RAG_RERANK_TIMEOUT_SECONDS,
                )
                if resp.status_code == 200:
                    results = resp.json().get("results", [])
                    ordered = []
                    used = set()
                    for res in results:
                        idx = res.get("index")
                        if idx is not None and 0 <= idx < len(candidates):
                            ordered.append(candidates[idx])
                            used.add(idx)
                    for i, h in enumerate(candidates):
                        if i not in used:
                            ordered.append(h)
                    return ordered + hits[top_n:]
                logger.warning(f"Zhipu rerank failed ({resp.status_code})")

        # LLM fallback
        if not rerank_client:
            return hits

        brief = []
        for idx, h in enumerate(candidates, 1):
            src = h.get("_source", {})
            text = (src.get("content") or "")[:500]
            brief.append(f"[{idx}] 文件:{src.get('filename', '未知')}\n内容:{text}")

        prompt = (
            "你是检索重排器。请根据用户问题判断候选片段相关性，"
            "只返回 JSON 数组，元素是候选编号，按相关性从高到低排序。\n\n"
            f"用户问题: {query}\n\n候选片段:\n" + "\n\n".join(brief) + "\n\n输出示例: [3,1,2]"
        )
        rsp = await rerank_client.chat.completions.create(
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
        return ordered + hits[top_n:]

    except Exception as e:
        logger.warning(f"Reranker failed, fallback to original: {e}")
        return hits
    finally:
        RAG_RERANK_LATENCY.observe(time.perf_counter() - start)
