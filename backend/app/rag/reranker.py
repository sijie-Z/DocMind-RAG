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
  LLM 路径在模型按契约返回 ``[{"index","score"}]`` 时同样落 ``rerank_score``
  （自报 0–10 分，同一次调用内产出、不增加成本）；模型只返回编号时仍只给序，
  不伪造分数（issue #209）。
- 配置单一真源：``RERANK_MODEL``/``RERANK_API_KEY``/``RERANK_API_URL`` 优先，
  ``RAG_RERANK_*`` 为 legacy 别名；local 模型用 ``RERANK_LOCAL_MODEL``；
  送审 passage 统一带 ``文件:<filename>`` 出处前缀。
"""

import asyncio
import json
import logging
import math
import re
import time
from functools import lru_cache

from app.core.config import settings
from app.core.llm_failfast import LLMUnreachableError, create_chat_completion

logger = logging.getLogger(__name__)

# 送审片段上限（字符）。issue #190：本地 CE 此前截 512、zhipu 截 1000——
# 统一到同一口径，避免同一候选在不同后备路径看到不同正文长度。
_RERANK_PASSAGE_MAX_CHARS = 1000
_UNKNOWN_FILENAME = "未知"


# ── 配置解析（单一真源；legacy RAG_RERANK_* 别名兜底） ───────────


def _resolve_api_key() -> str | None:
    return getattr(settings, "RERANK_API_KEY", None) or getattr(
        settings, "RAG_RERANK_API_KEY", None
    )


def _resolve_api_url() -> str | None:
    return getattr(settings, "RERANK_API_URL", None) or getattr(
        settings, "RAG_RERANK_API_URL", None
    )


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
                                "for index %s: %r",
                                idx,
                                score,
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

# 非贪婪匹配单个方括号片段：贪婪版本会把正文里的其它方括号一并吞掉，
# 导致 JSON 解析失败后落到「数字大杂烩」的兜底路径。
_JSON_ARRAY_RE = re.compile(r"\[[^\[\]]*\]", re.S)
# JSON 不合法（单引号、尾逗号等）但字段形态可辨认时的兜底抽取。
_INDEX_SCORE_PAIR_RE = re.compile(
    r"""['"]?index['"]?\s*:\s*['"]?(?P<idx>-?\d+(?:\.\d+)?)['"]?"""
    r"""[^}]*?['"]?score['"]?\s*:\s*['"]?(?P<score>-?\d+(?:\.\d+)?)['"]?""",
    re.S,
)


def _coerce_llm_score(value) -> float | None:
    """LLM 自报分 → float；布尔/非数值/非有限值一律视为无分数（不伪造）。"""
    if isinstance(value, bool):
        return None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    # "NaN"/"Infinity" 是合法 JSON 字符串但会污染 sources.relevanceScore
    # （json.dumps 会输出非法 JSON 的 NaN），按无分数处理。
    return score if math.isfinite(score) else None


def _coerce_llm_index(value) -> int | None:
    """候选编号 → int；只接受整数语义（2 / 2.0 / "2"），其余视为无效条目。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
        return int(number) if number.is_integer() else None
    return None


def _parse_llm_rerank(content: str) -> list[tuple[int, float | None]]:
    """解析 LLM 重排输出为 ``[(候选编号 1-based, 自报分|None), ...]``。

    issue #209：首选 JSON 数组——既接受契约的 ``[{"index","score"}]``，也兼容
    只返回编号的 ``[3,1,2]``（不守格式的模型）。JSON 不合法（单引号/尾逗号等）
    时按 ``index``/``score`` 字段正则抽取，最后才退化为裸编号抓取——避免把
    「分数数字」当成候选编号排序。
    """
    for match in _JSON_ARRAY_RE.finditer(content):
        try:
            data = json.loads(match.group(0))
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(data, list):
            continue
        parsed: list[tuple[int, float | None]] = []
        for item in data:
            if isinstance(item, dict):
                idx = _coerce_llm_index(item.get("index"))
                if idx is not None:
                    parsed.append((idx, _coerce_llm_score(item.get("score"))))
            else:
                idx = _coerce_llm_index(item)
                if idx is not None:
                    parsed.append((idx, None))
        if parsed:
            return parsed
    pairs: list[tuple[int, float]] = []
    for m in _INDEX_SCORE_PAIR_RE.finditer(content):
        score = _coerce_llm_score(m.group("score"))
        if score is not None:
            pairs.append((int(float(m.group("idx"))), score))
    if pairs:
        return pairs
    return [(int(x), None) for x in re.findall(r"\d+", content)]


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
        "你是检索重排器。请根据用户问题判断候选片段相关性，按相关性从高到低排序，"
        '只返回 JSON 数组，元素为 {"index": 候选编号, "score": 相关性分}；'
        "score 为 0-10 的整数（10=最相关，0=完全无关）。\n\n"
        f"用户问题: {query}\n\n候选片段:\n" + "\n\n".join(brief) + "\n\n"
        '输出示例: [{"index": 3, "score": 9}, {"index": 1, "score": 4}, '
        '{"index": 2, "score": 0}]'
    )
    try:
        rsp = await create_chat_completion(
            llm_client,
            # issue #467：重排是检索链的可选增强——端点不可达时立即降级
            # （不再等满 RAG_RERANK_TIMEOUT_SECONDS）；连接阶段另有独立短超时。
            read_timeout=settings.RAG_RERANK_TIMEOUT_SECONDS,
            model=model_name,
            messages=[
                {"role": "system", "content": "你是严格输出JSON数组的重排器。"},
                {"role": "user", "content": prompt},
            ],
            temperature=0,
        )
        content = (rsp.choices[0].message.content or "").strip()
        ordered, used = [], set()
        for n, score in _parse_llm_rerank(content):
            if 1 <= n <= len(candidates) and n not in used:
                hit = candidates[n - 1]
                if score is not None:
                    # issue #209：同一次调用内让模型自报 0–10 分（不增成本）；
                    # 口径与 local/zhipu 不同，闸门阈值必须按实际重排器校准。
                    hit["rerank_score"] = score
                ordered.append(hit)
                used.add(n)
        for i, h in enumerate(candidates, 1):
            if i not in used:
                ordered.append(h)
        # 只返回编号（无分数）的输出仍只给序，不伪造 rerank_score。
        return ordered, None
    except LLMUnreachableError as e:
        # issue #467：连接级不可达——零重试，立即降级并给出可判别原因。
        logger.warning("LLM reranker unreachable (fast-fail): %s", e)
        return None, "llm_unreachable"
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
    query: str, hits: list[dict], rerank_client=None, llm_client=None
) -> tuple[list[dict], dict]:
    """Rerank hits using local cross-encoder (free), with API/LLM fallbacks.

    Strategy priority:
      1. Local cross-encoder (if RERANK_USE_LOCAL = True, default)
      2. Zhipu rerank API (if RERANK_API_KEY is set)
      3. LLM-based reranking (if an LLM client is provided — llm_client 优先，
         回退 rerank_client；most expensive)
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
            ordered, reason = await _rerank_llm(
                query, candidates, top_n, llm_client or rerank_client
            )
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
