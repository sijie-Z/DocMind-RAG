"""Tests for RAG cache modules (RetrievalCache, SemanticCache)."""
import pytest

from app.rag.cache import RetrievalCache, SemanticCache


class TestRetrievalCache:
    @pytest.mark.asyncio
    async def test_get_miss_returns_none(self):
        cache = RetrievalCache()
        result = await cache.get("nonexistent query", 1, 5)
        assert result is None

    @pytest.mark.asyncio
    async def test_set_and_get(self):
        cache = RetrievalCache()
        data = [{"_id": "doc1", "_score": 1.0, "_source": {"content": "test"}}]
        await cache.set("test query", 1, 5, data)
        result = await cache.get("test query", 1, 5)
        assert result is not None
        assert len(result) == 1
        assert result[0]["_id"] == "doc1"

    @pytest.mark.asyncio
    async def test_different_params_miss(self):
        cache = RetrievalCache()
        data = [{"_id": "doc1"}]
        await cache.set("query", 1, 5, data)
        result = await cache.get("query", 1, 10)  # different top_k
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_result_cached(self):
        cache = RetrievalCache()
        await cache.set("empty query", 1, 5, [])
        result = await cache.get("empty query", 1, 5)
        assert result is not None
        assert result == []


class TestSemanticCacheQuantize:
    def test_quantize_returns_int(self):
        cache = SemanticCache()
        embedding = [0.1, 0.2, 0.3, -0.5, 0.9] * 20  # 100 dims
        result = cache._quantize(embedding)
        assert isinstance(result, int)

    def test_quantize_deterministic(self):
        cache = SemanticCache()
        embedding = [0.1, 0.2, 0.3] * 10
        a = cache._quantize(embedding)
        b = cache._quantize(embedding)
        assert a == b

    def test_quantize_empty_returns_zero(self):
        cache = SemanticCache()
        result = cache._quantize([])
        assert result == 0

    def test_quantize_positive_vs_negative(self):
        cache = SemanticCache()
        all_positive = [0.1, 0.2, 0.3, 0.4, 0.5]
        all_negative = [-0.1, -0.2, -0.3, -0.4, -0.5]
        pos_score = cache._quantize(all_positive)
        neg_score = cache._quantize(all_negative)
        assert pos_score != neg_score

    def test_cosine_similarity_identical(self):
        cache = SemanticCache()
        v = [1.0, 2.0, 3.0]
        assert abs(cache._cosine_similarity(v, v) - 1.0) < 1e-6

    def test_cosine_similarity_orthogonal(self):
        cache = SemanticCache()
        assert abs(cache._cosine_similarity([1, 0], [0, 1])) < 1e-6

    def test_cosine_similarity_empty(self):
        cache = SemanticCache()
        assert cache._cosine_similarity([], [1, 2]) == 0.0
        assert cache._cosine_similarity([1, 2], []) == 0.0
