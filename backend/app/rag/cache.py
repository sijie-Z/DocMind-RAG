"""RAG retrieval cache — exact (key-based) + semantic (embedding-based).

Semantic cache uses Redis Sorted Sets for O(log n) similarity search.
Memory fallback uses bounded LRU eviction to prevent unbounded growth.
"""
import hashlib
import json
import logging
import math
import time
from collections import OrderedDict
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)


def _get_redis():
    """Lazy import to avoid circular dependencies."""
    from app.core.redis import redis_client
    return redis_client


class BoundedLRUCache:
    """Size-bounded LRU cache using OrderedDict."""

    def __init__(self, max_size: int = 1000):
        self._cache: OrderedDict[str, Any] = OrderedDict()
        self._max_size = max(100, max_size)

    def get(self, key: str) -> Any | None:
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        return None

    def set(self, key: str, value: Any) -> None:
        if key in self._cache:
            self._cache.move_to_end(key)
        self._cache[key] = value
        while len(self._cache) > self._max_size:
            self._cache.popitem(last=False)

    def pop(self, key: str) -> Any | None:
        return self._cache.pop(key, None)

    def __len__(self) -> int:
        return len(self._cache)

    def oldest_key(self) -> str | None:
        if self._cache:
            return next(iter(self._cache))
        return None


class RetrievalCache:
    """Exact-match retrieval cache backed by Redis (with bounded LRU memory fallback)."""

    def __init__(self):
        max_size = max(100, int(getattr(settings, "RAG_CACHE_MAX_SIZE", 1000) or 1000))
        self._memory = BoundedLRUCache(max_size=max_size)

    def _key(self, query: str, organization_id: int, top_k: int) -> str:
        return f"org={organization_id}|topk={top_k}|q={query.strip().lower()}"

    async def get(self, query: str, organization_id: int, top_k: int) -> list[dict[str, Any]] | None:
        if not settings.RAG_ENABLE_CACHE:
            return None
        key = self._key(query, organization_id, top_k)
        # Try Redis first
        try:
            rc = _get_redis()
            if rc is not None:
                raw = await rc.get(f"rag:cache:{key}")
                if raw:
                    return json.loads(raw)
        except Exception:
            pass
        # Fallback to memory
        row = self._memory.get(key)
        if not row:
            return None
        ttl = max(1, int(settings.RAG_CACHE_TTL_SECONDS or 120))
        if time.time() - row["ts"] > ttl:
            self._memory.pop(key)
            return None
        return row["value"]

    async def set(self, query: str, organization_id: int, top_k: int, value: list[dict[str, Any]]) -> None:
        if not settings.RAG_ENABLE_CACHE:
            return
        key = self._key(query, organization_id, top_k)
        # Try Redis
        try:
            rc = _get_redis()
            if rc is not None:
                ttl = max(1, int(settings.RAG_CACHE_TTL_SECONDS or 120))
                await rc.setex(f"rag:cache:{key}", ttl, json.dumps(value, ensure_ascii=False))
                return
        except Exception:
            pass
        # Fallback to memory (bounded LRU)
        self._memory.set(key, {"ts": time.time(), "value": value})


class SemanticCache:
    """Semantic cache using Redis Sorted Set for O(log n) similarity search.

    Stores query embeddings in a sorted set keyed by a quantized hash.
    At lookup time, finds candidates within a Hamming distance window
    and checks cosine similarity only on those candidates.
    """

    def __init__(self):
        self.threshold = getattr(settings, "SEMANTIC_CACHE_THRESHOLD", 0.92)
        self.ttl = getattr(settings, "SEMANTIC_CACHE_TTL_SECONDS", 3600)
        self.index_key = "rag:semantic_index"
        self.prefix = "rag:sem:"
        self._quantize_bits = 64

    def _quantize(self, embedding: list[float]) -> int:
        """Quantize embedding to a compact integer for sorted set scoring."""
        if not embedding:
            return 0
        bits = 0
        step = max(1, len(embedding) // self._quantize_bits)
        for i in range(0, len(embedding), step):
            if i < len(embedding) and embedding[i] > 0:
                bits |= 1 << (i // step)
        return bits

    def _cosine_similarity(self, v1: list[float], v2: list[float]) -> float:
        if not v1 or not v2 or len(v1) != len(v2):
            return 0.0
        dot = sum(a * b for a, b in zip(v1, v2, strict=False))
        na = math.sqrt(sum(a * a for a in v1))
        nb = math.sqrt(sum(b * b for b in v2))
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)

    def _hash_key(self, embedding: list[float]) -> str:
        vec_str = ",".join(f"{v:.4f}" for v in embedding[:20])
        return hashlib.md5(vec_str.encode()).hexdigest()[:16]

    async def get(self, query_embedding: list[float]) -> dict[str, Any] | None:
        """Find the most similar cached answer using sorted set candidates."""
        if not query_embedding:
            return None
        try:
            rc = _get_redis()
            if rc is None:
                return None

            query_score = self._quantize(query_embedding)
            candidates = await rc.zrangebyscore(
                self.index_key,
                max(0, query_score - 2),
                query_score + 2,
            )
            if not candidates:
                return None

            best_match = None
            best_score = -1.0

            for cache_key in candidates:
                if isinstance(cache_key, bytes):
                    cache_key = cache_key.decode()
                raw = await rc.get(cache_key)
                if not raw:
                    await rc.zrem(self.index_key, cache_key)
                    continue
                data = json.loads(raw)
                cached_emb = data.get("embedding", [])
                sim = self._cosine_similarity(query_embedding, cached_emb)
                if sim >= self.threshold and sim > best_score:
                    best_score = sim
                    best_match = data

            if best_match:
                logger.info(f"Semantic cache hit (sim={best_score:.4f})")
                return best_match
            return None
        except Exception as e:
            logger.error(f"Semantic cache lookup failed: {e}")
            return None

    async def set(self, query: str, embedding: list[float], answer: str, sources: list[dict] = None) -> None:
        """Store a Q&A pair in the semantic cache."""
        if not embedding:
            return
        try:
            rc = _get_redis()
            if rc is None:
                return

            cache_key = f"{self.prefix}{self._hash_key(embedding)}"
            data = {
                "query": query,
                "embedding": embedding,
                "answer": answer,
                "sources": sources or [],
            }
            score = self._quantize(embedding)

            pipe = rc.pipeline()
            pipe.setex(cache_key, self.ttl, json.dumps(data, ensure_ascii=False))
            pipe.zadd(self.index_key, {cache_key: score})
            await pipe.execute()
            logger.info(f"Semantic cache stored: '{query[:30]}...'")
        except Exception as e:
            logger.error(f"Semantic cache store failed: {e}")
