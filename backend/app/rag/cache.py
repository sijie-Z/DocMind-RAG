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


# 修复（R5）：内存回退缓存必须是进程级共享注册表。
# 此前每次 clear_for_org 调用都 `RetrievalCache()` 新建实例，清空的是刚创建的空
# _memory，而真正服务读写的实例是 pipeline 单例持有的那个（dependencies.py），
# 导致 Redis 不可用时文档更新后旧内容仍命中缓存。
_SHARED_MEMORY: "BoundedLRUCache | None" = None


def _get_shared_memory() -> "BoundedLRUCache":
    """获取进程级共享的内存回退缓存（容量取 RAG_CACHE_MAX_SIZE）。"""
    global _SHARED_MEMORY
    if _SHARED_MEMORY is None:
        max_size = max(100, int(getattr(settings, "RAG_CACHE_MAX_SIZE", 1000) or 1000))
        _SHARED_MEMORY = BoundedLRUCache(max_size=max_size)
    return _SHARED_MEMORY


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
        if len(self._cache) > self._max_size:
            # 指标补埋（issue #242 第 10 条）：内存回退缓存的淘汰此前从未计数。
            from app.core.prometheus import RAG_CACHE_EVICTIONS
            while len(self._cache) > self._max_size:
                self._cache.popitem(last=False)
                RAG_CACHE_EVICTIONS.labels(cache_type="memory").inc()

    def pop(self, key: str) -> Any | None:
        return self._cache.pop(key, None)

    def __len__(self) -> int:
        return len(self._cache)

    def oldest_key(self) -> str | None:
        if self._cache:
            return next(iter(self._cache))
        return None

    def clear_matching(self, prefix: str) -> None:
        """Remove all entries whose key starts with prefix (org-scoped invalidation)."""
        keys = [k for k in list(self._cache.keys()) if k.startswith(prefix)]
        for k in keys:
            self._cache.pop(k, None)


class RetrievalCache:
    """Exact-match retrieval cache backed by Redis (with bounded LRU memory fallback)."""

    def __init__(self):
        # 修复（R5）：所有实例共享同一内存回退缓存，保证任意实例上的
        # clear_for_org 都能清理到实际服务读写的 pipeline 单例缓存数据。
        self._memory = _get_shared_memory()

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

    async def clear_for_org(self, organization_id: int) -> None:
        """Invalidate all exact-match cache entries for an organization."""
        # 内存 LRU 中清理该组织的匹配项
        self._memory.clear_matching(f"org={organization_id}|")
        # Redis 中按 SCAN 匹配删除（无 Redis 时静默跳过）
        try:
            rc = _get_redis()
            if rc is not None:
                pattern = f"rag:cache:org={organization_id}|*"
                async for key in rc.scan_iter(match=pattern, count=100):
                    await rc.delete(key)
        except Exception:
            pass


class SemanticCache:
    """Semantic cache using Redis Sorted Set for O(log n) similarity search.

    Stores query embeddings in a sorted set keyed by a quantized hash.
    At lookup time, finds candidates within a Hamming distance window
    and checks cosine similarity only on those candidates.

    修复（R4）：问答缓存（chat_service 缓存完整 answer）与检索缓存
    （RAGPipeline 只缓存 sources、answer 为空）使用两个独立命名空间，
    否则检索阶段写入的空 answer 条目会被问答链路按相似度命中，
    在用户中断流式回答后返回空消息。
    """

    # 检索专用命名空间（RAGPipeline 使用，见 for_retrieval）
    RETRIEVAL_PREFIX = "rag:sem:retrieval:"
    RETRIEVAL_INDEX_KEY = "rag:semantic_index:retrieval"

    def __init__(self, prefix: str = "rag:sem:", index_key: str = "rag:semantic_index"):
        # issue #259：字段已补进 Settings（此前 getattr 兜底因字段缺失而 .env 无效）
        self.threshold = settings.SEMANTIC_CACHE_THRESHOLD
        self.ttl = settings.SEMANTIC_CACHE_TTL_SECONDS
        self.index_key = index_key
        self.prefix = prefix
        self._quantize_bits = 64

    @classmethod
    def for_retrieval(cls) -> "SemanticCache":
        """构建检索专用实例（独立 key 空间，不与问答缓存互相命中）。"""
        return cls(prefix=cls.RETRIEVAL_PREFIX, index_key=cls.RETRIEVAL_INDEX_KEY)

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
        return hashlib.md5(vec_str.encode(), usedforsecurity=False).hexdigest()[:16]

    async def get(self, query_embedding: list[float], organization_id: int = 0) -> dict[str, Any] | None:
        """Find the most similar cached answer using sorted set candidates."""
        if not query_embedding:
            return None
        try:
            rc = _get_redis()
            if rc is None:
                return None

            index_key = f"{self.index_key}:{organization_id}"
            org_prefix = f"org:{organization_id}:"
            query_score = self._quantize(query_embedding)
            candidates = await rc.zrangebyscore(
                index_key,
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
                if org_prefix not in cache_key:
                    continue
                raw = await rc.get(cache_key)
                if not raw:
                    await rc.zrem(index_key, cache_key)
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

    async def set(self, query: str, embedding: list[float], answer: str, sources: list[dict] = None, organization_id: int = 0) -> None:
        """Store a Q&A pair in the semantic cache."""
        if not embedding:
            return
        try:
            rc = _get_redis()
            if rc is None:
                return

            index_key = f"{self.index_key}:{organization_id}"
            org_prefix = f"org:{organization_id}:"
            cache_key = f"{self.prefix}{org_prefix}{self._hash_key(embedding)}"
            data = {
                "query": query,
                "embedding": embedding,
                "answer": answer,
                "sources": sources or [],
            }
            score = self._quantize(embedding)

            pipe = rc.pipeline()
            pipe.setex(cache_key, self.ttl, json.dumps(data, ensure_ascii=False))
            pipe.zadd(index_key, {cache_key: score})
            await pipe.execute()
            logger.info(f"Semantic cache stored: '{query[:30]}...'")
        except Exception as e:
            logger.error(f"Semantic cache store failed: {e}")

    async def clear_for_org(self, organization_id: int) -> None:
        """Invalidate all semantic cache entries for an organization.

        修复（R4）：问答与检索两个命名空间都要清理（调用点仍以
        `SemanticCache()` 默认实例调用），否则文档更新后检索缓存仍会命中旧内容。
        """
        try:
            rc = _get_redis()
            if rc is None:
                return
            namespaces = {
                self.prefix: self.index_key,
                self.RETRIEVAL_PREFIX: self.RETRIEVAL_INDEX_KEY,
            }
            for prefix, index_key in namespaces.items():
                cache_prefix = f"{prefix}org:{organization_id}:"
                async for key in rc.scan_iter(match=f"{cache_prefix}*", count=100):
                    await rc.delete(key)
                await rc.delete(f"{index_key}:{organization_id}")
        except Exception as e:
            logger.error(f"Semantic cache clear failed: {e}")
