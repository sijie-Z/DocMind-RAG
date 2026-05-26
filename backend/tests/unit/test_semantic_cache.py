"""SemanticCache 纯函数测试（不依赖 Redis）。"""
import pytest

from app.rag.cache import SemanticCache


@pytest.fixture
def cache():
    return SemanticCache()


class TestCosineSimilarity:
    def test_identical_vectors(self, cache: SemanticCache):
        v = [1.0, 2.0, 3.0]
        assert cache._cosine_similarity(v, v) == pytest.approx(1.0)

    def test_opposite_vectors(self, cache: SemanticCache):
        v1 = [1.0, 0.0]
        v2 = [-1.0, 0.0]
        assert cache._cosine_similarity(v1, v2) == pytest.approx(-1.0)

    def test_orthogonal_vectors(self, cache: SemanticCache):
        v1 = [1.0, 0.0]
        v2 = [0.0, 1.0]
        assert cache._cosine_similarity(v1, v2) == pytest.approx(0.0)

    def test_zero_vector(self, cache: SemanticCache):
        v1 = [0.0, 0.0]
        v2 = [1.0, 2.0]
        assert cache._cosine_similarity(v1, v2) == 0.0

    def test_both_zero_vectors(self, cache: SemanticCache):
        assert cache._cosine_similarity([0.0], [0.0]) == 0.0

    def test_similar_vectors_high_score(self, cache: SemanticCache):
        v1 = [1.0, 1.0, 0.0]
        v2 = [1.0, 0.9, 0.1]
        score = cache._cosine_similarity(v1, v2)
        assert score > 0.95

    def test_large_dimension_vectors(self, cache: SemanticCache):
        """Verify with higher-dimensional vectors."""
        import random
        random.seed(42)
        v1 = [random.random() for _ in range(128)]
        v2 = [random.random() for _ in range(128)]
        score = cache._cosine_similarity(v1, v2)
        assert -1.0 <= score <= 1.0
