"""Reranker — local cross-encoder model with API/LLM fallbacks.

Primary: local cross-encoder (BAAI/bge-reranker-base, free, no API cost)
Fallback 1: Zhipu rerank API (paid, requires RERANK_API_KEY)
Fallback 2: LLM-based reranking (paid, uses DeepSeek/configured chat model)

issue #190/#185/#189 契约（本模块的对外承诺）：
- ``rerank(query, hits)`` 返回 ``(hits, info)``。``info`` 结构：
  ``{status: ok|degraded|disabled|empty, path: local|zhipu|llm|noop,
  reason: str|None, attempts: [{path, ok, reason}], scores_attached: bool,
  model: str|None}``。``degraded`` 表示所有配置路径都失败、按原始顺序返回——
  不再静默（修复前全链失败零信号）。
- 打分路径（local/zhipu）在命中上写 ``rerank_score``（绝对分，不归一化）；
  LLM 路径只产出顺序、没有可信分数，不伪造 ``rerank_score``。
- 配置单一真源：``RERANK_MODEL``/``RERANK_API_KEY``/``RERANK_API_URL`` 优先，
  ``RAG_RERANK_*`` 为 legacy 别名；local 模型用 ``RERANK_LOCAL_MODEL``；
  送审 passage 统一带 ``文件:<filename>`` 出处前缀。
"""
import asyncio
import logging
import re
import time
from functools import lru_cache

from app.core.config import settings

logger = logging.getLogger(__name__)

# 送审片段上限（字符）。issue #190：本地 CE 此前截 512、zhipu 截 1000——
# 统一到同一口径，避免同一候选在不同后备路径看到不同正文长度。
_RERANK_PASSAGE_MAX_CHARS = 1000
_UNKNOWN_FILENAME = "未知"


# ── 配置解析（单一真源；legacy RAG_RERANK_* 别名兜底） ───────────

def _resolve_api_key() -> str | None:
    return getattr(settings, "RERANK_API_KEY", None) or getattr(settings, "RAG_RERANK_API_KEY", None)


def _resolve_api_url() -> str | None:
    return getattr(settings, "RERANK_API_URL", None) or getattr(settings, "RAG_RERANK_API_URL", None)


def _resolve_api_model() -> str:
    # issue #190：此前请求体写死 "rerank"，覆盖配置不生效。
    return (
        getattr(settings, "RERANK_MODEL", None)
        or getattr(settings, "RAG_RERANK_MODEL", None)
        or "rerank"
    )


def _resolve_local_model() -> str:
    return getattr(settings, "RERANK_LOCAL_MODEL", None) or "BAAI/bge-reranker-base"


def _resolve_llm_model() -> str:
    """LLM 兜底走 chat completions，模型必须是对话模型（不是 /rerank 的模型名）。"""
    return settings.LOCAL_LLM_MODEL if settings.ENABLE_LOCAL_LLM else settings.DEEPSEEK_MODEL


# ── 命中形态兼容 ─────────────────────────────────────────────────

def _hit_text(hit: dict) -> str:
    """提取命中片段的正文文本。

    修复（R1）：检索后处理结果（HybridRetriever._post_process）以 content/text
    键携带正文，不含 _source；原始 ES 命中则以 _source.content/chunk_text 携带。
    两种形态都要兼容，否则重排序器收到空文本、排序退化为任意顺序。
    """
    text = hit.get("content") or hit.get("chunk_text") or hit.get("text")
    if text:
        return text
    src = hit.get("_source") or {}
    return src.get("content") or src.get("chunk_text") or ""


def _hit_filename(hit: dict) -> str:
    """提取命中片段的文件名（兼容后处理结果与原始 ES 命中两种形态）。"""
    text = hit.get("filename")
    if text:
        return text
    src = hit.get("_source") or {}
    return src.get("filename") or _UNKNOWN_FILENAME


def _hit_passage(hit: dict) -> str:
    """送审片段：出处前缀 + 截断正文（issue #190：本地/智谱输入此前无出处）。"""
    return f"文件:{_hit_filename(hit)}\n{_hit_text(hit)[:_RERANK_PASSAGE_MAX_CHARS]}"


# ── Local cross-encoder (lazy singleton) ─────────────────────────

@lru_cache(maxsize=1)
def _get_cross_encoder(model_name: str | None = None):
    """Load the cross-encoder model once and cache it for the process lifetime.

    缓存键含模型名；调用方显式传入 ``_resolve_local_model()``——配置变更会
    落到新的缓存键（超出 maxsize=1 时旧模型被逐出），而不是永远复用首个实例。
    Returns the model instance, or None if loading fails.
    """
    model_name = model_name or _resolve_local_model()
    try:
        import torch
        from sentence_transformers import CrossEncoder

        device = "cuda" if torch.cuda.is_available() else "cpu"
        logger.info("Loading local cross-encoder reranker: %s (device=%s)", model_name, device)
        model = CrossEncoder(model_name, device=device)
        logger.info("Local reranker loaded successfully")
        return model
    except Exception as e:
        logger.warning("Failed to load local cross-encoder reranker '%s': %s", model_name, e)
        return None


async def _rerank_local(
    query: str, candidates: list[dict], top_n: int
) -> tuple[list[dict] | None, str | None]:
    """Rerank using local cross-encoder model. Returns (ordered, reason).

    reason 为 None 表示成功；否则是机器可读的失败原因（写入 rerank info）。
    """
    model = _get_cross_encoder(_resolve_local_model())
    if model is None:
        return None, "local_model_unavailable"

    if not candidates:
        return [], None

    pairs = [[query, _hit_passage(h)] for h in candidates]

    try:
        loop = asyncio.get_running_loop()
        scores = await loop.run_in_executor(
            None,
            lambda: model.predict(pairs, show_progress_bar=False).tolist(),
        )
    except Exception as e:
        logger.warning("Local reranker predict failed: %s", e)
        return None, f"local_predict_failed: {e}"

    # Pair scores back to candidates & sort descending.
    # issue #190：把绝对分写回命中（rerank_score）——此前分数只用于排序后被丢弃。
    scored = list(zip(candidates, scores, strict=False))
    scored.sort(key=lambda x: x[1], reverse=True)

    result: list[dict] = []
    for h, score in scored[:top_n]:
        h["rerank_score"] = float(score)
        result.append(h)
    # Append any remaining candidates beyond top_n
    remaining = [h for h in candidates if h not in result]
    return result + remaining, None


# ── Zhipu rerank API (paid) ──────────────────────────────────────

async def _rerank_zhipu(
    query: str, candidates: list[dict], top_n: int
) -> tuple[list[dict] | None, str | None]:
    """Rerank using Zhipu rerank API. Returns (ordered, reason)."""
    api_key = _resolve_api_key()
    if not api_key:
        return None, "no_api_key"
    api_url = _resolve_api_url()
    if not api_url:
        return None, "no_api_url"

    try:
        import httpx

        documents = [_hit_passage(h) for h in candidates]
        async with httpx.AsyncClient() as client:
            url = f"{api_url.rstrip('/')}/rerank"
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    # issue #190：配置驱动，不再写死 "rerank"。
                    "model": _resolve_api_model(),
                    "query": query,
                    "documents": documents,
                    "top_n": top_n,
                },
                timeout=settings.RAG_RERANK_TIMEOUT_SECONDS,
            )
            if resp.status_code != 200:
                logger.warning("Zhipu rerank failed (%s)", resp.status_code)
                return None, f"zhipu_http_{resp.status_code}"

            results = resp.json().get("results", [])
            ordered: list[dict] = []
            used: set[int] = set()
            for res in results:
                idx = res.get("index")
                if idx is not None and 0 <= idx < len(candidates):
                    hit = candidates[idx]
                    # issue #190：relevance_score 不再丢弃，作为绝对分落盘。
                    # 单条分数异常（非数值）只丢该条分数，不让整条 zhipu 路径降级。
                    score = res.get("relevance_score")
                    if score is not None:
                        try:
                            hit["rerank_score"] = float(score)
                        except (TypeError, ValueError):
                            logger.warning(
                                "Zhipu rerank returned non-numeric relevance_score "
                                "for index %s: %r", idx, score,
                            )
                    ordered.append(hit)
                    used.add(idx)
            for i, h in enumerate(candidates):
                if i not in used:
                    ordered.append(h)
            return ordered, None
    except Exception as e:
        logger.warning("Zhipu rerank error: %s", e)
        return None, f"zhipu_error: {e}"


# ── LLM-based reranker (paid fallback) ────────────────────────────

async def _rerank_llm(
    query: str, candidates: list[dict], top_n: int, llm_client
) -> tuple[list[dict] | None, str | None]:
    """Rerank using LLM (expensive fallback). Returns (ordered, reason)."""
    if not llm_client:
        return None, "no_llm_client"

    model_name = _resolve_llm_model()

    brief = []
    for idx, h in enumerate(candidates, 1):
        text = _hit_text(h)[:500]
        brief.append(f"[{idx}] 文件:{_hit_filename(h)}\n内容:{text}")

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
        # LLM 只产出顺序，无可信数值分——不写 rerank_score（不伪造）。
        return ordered, None
    except Exception as e:
        logger.warning("LLM reranker failed: %s", e)
        return None, f"llm_error: {e}"


# ── Public API ────────────────────────────────────────────────────

def _record_rerank_metric(path: str, outcome: str) -> None:
    from app.core.prometheus import RAG_RERANK_TOTAL

    RAG_RERANK_TOTAL.labels(path=path, outcome=outcome).inc()


def _rerank_info(status: str, path: str, reason: str | None = None) -> dict:
    return {
        "status": status,
        "path": path,
        "reason": reason,
        "attempts": [],
        "scores_attached": False,
        "model": None,
    }


async def rerank(
    query: str, hits: list[dict], rerank_client=None
) -> tuple[list[dict], dict]:
    """Rerank hits using local cross-encoder (free), with API/LLM fallbacks.

    Strategy priority:
      1. Local cross-encoder (if RERANK_USE_LOCAL = True, default)
      2. Zhipu rerank API (if RERANK_API_KEY is set)
      3. LLM-based reranking (if rerank_client is provided, most expensive)
      4. Degraded no-op (original order) — 显式 status="degraded" + reason/attempts

    Returns ``(hits, info)``；info 契约见模块 docstring（issue #189）。
    """
    if not hits:
        return hits, _rerank_info("empty", "noop", "no_candidates")

    if not settings.RAG_ENABLE_RERANKER:
        _record_rerank_metric("noop", "disabled")
        logger.info("Reranker disabled by configuration (RAG_ENABLE_RERANKER=False)")
        return hits, _rerank_info("disabled", "noop", "RAG_ENABLE_RERANKER=False")

    from app.core.prometheus import RAG_RERANK_LATENCY
    start = time.perf_counter()

    top_n = max(1, int(settings.RAG_RERANK_TOP_N or 20))
    candidates = hits[:top_n]
    info = _rerank_info("ok", "noop")
    attempts: list[dict] = []

    try:
        ordered: list[dict] | None = None

        # Strategy 1: local cross-encoder (free)
        if getattr(settings, "RERANK_USE_LOCAL", True):
            ordered, reason = await _rerank_local(query, candidates, top_n)
        else:
            reason = "disabled_by_config"
        attempts.append({"path": "local", "ok": ordered is not None, "reason": reason})

        # Strategy 2: Zhipu API (paid)
        if ordered is None:
            ordered, reason = await _rerank_zhipu(query, candidates, top_n)
            attempts.append({"path": "zhipu", "ok": ordered is not None, "reason": reason})

        # Strategy 3: LLM-based (paid)
        if ordered is None:
            ordered, reason = await _rerank_llm(query, candidates, top_n, rerank_client)
            attempts.append({"path": "llm", "ok": ordered is not None, "reason": reason})

        info["attempts"] = attempts

        # Strategy 4: degraded no-op —— 全链失败绝不静默（#189）。
        if ordered is None:
            info.update(status="degraded", path="noop", reason="all_rerank_paths_failed")
            _record_rerank_metric("noop", "degraded")
            logger.warning(
                "Reranker degraded to original order (all paths failed): %s",
                "; ".join(f"{a['path']}={a['reason']}" for a in attempts),
            )
            return hits, info

        path = next(a["path"] for a in attempts if a["ok"])
        info.update(
            status="ok",
            path=path,
            reason=None,
            scores_attached=any("rerank_score" in h for h in ordered[:top_n]),
            model={
                "local": _resolve_local_model(),
                "zhipu": _resolve_api_model(),
                "llm": _resolve_llm_model(),
            }.get(path),
        )
        _record_rerank_metric(path, "ok")
        return ordered + hits[top_n:], info

    except Exception as e:
        info.update(
            status="degraded",
            path="noop",
            reason=f"reranker_exception: {e}",
            attempts=attempts,
        )
        _record_rerank_metric("noop", "degraded")
        logger.warning("Reranker failed, fallback to original: %s", e)
        return hits, info
    finally:
        RAG_RERANK_LATENCY.observe(time.perf_counter() - start)
