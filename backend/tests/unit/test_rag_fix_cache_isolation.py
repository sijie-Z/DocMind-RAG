"""R4/R5 回归测试：缓存命名空间隔离与共享实例失效。

R4：检索缓存（answer="") 与问答缓存共用命名空间时，用户中断流式回答后留下的
answer="" 条目会被下一个相似问题命中，返回空助手消息。
R5：doc_processor/knowledge_service/knowledge.py 以 `RetrievalCache()` 新建实例调用
clear_for_org，清的是自己的空 _memory，而 pipeline 单例的内存缓存不受影响。
"""
import fnmatch

import pytest

from app.rag.cache import RetrievalCache, SemanticCache


class _FakeRedis:
    """最小内存 Redis 替身（覆盖语义缓存与精确缓存的 Redis 分支）。"""

    def __init__(self):
        self.values: dict[str, str] = {}
        self.members: dict[str, dict[str, int]] = {}

    def pipeline(self):
        return self

    def setex(self, key: str, _ttl: int, value: str) -> None:
        self.values[key] = value

    def zadd(self, key: str, mapping: dict[str, int]) -> None:
        self.members.setdefault(key, {}).update(mapping)

    async def execute(self) -> list:
        return []

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def zrangebyscore(self, key: str, min_score: int, max_score: int) -> list[str]:
        return [
            member
            for member, score in self.members.get(key, {}).items()
            if min_score <= score <= max_score
        ]

    async def zrem(self, key: str, member: str) -> None:
        self.members.get(key, {}).pop(member, None)

    async def scan_iter(self, match: str = "*", count: int = 100):
        for key in list(self.values):
            if fnmatch.fnmatch(key, match):
                yield key

    async def delete(self, key: str) -> None:
        self.values.pop(key, None)
        self.members.pop(key, None)


class TestSemanticCacheNamespaceIsolation:
    @pytest.mark.asyncio
    async def test_retrieval_entry_with_empty_answer_not_visible_to_answer_cache(self, monkeypatch):
        fake = _FakeRedis()
        monkeypatch.setattr("app.rag.cache._get_redis", lambda: fake)
        # issue #195：语义缓存默认关闭，本用例验证的是命名空间隔离——显式开启。
        monkeypatch.setattr("app.rag.cache.settings.SEMANTIC_CACHE_RETRIEVAL_ENABLED", True)
        monkeypatch.setattr("app.rag.cache.settings.SEMANTIC_CACHE_ANSWER_ENABLED", True)
        embedding = [0.1, 0.2, 0.3, 0.4] * 8

        retrieval_cache = SemanticCache.for_retrieval()
        answer_cache = SemanticCache()

        # 检索阶段写入 answer="" 的条目（模拟 pipeline.search_knowledge_base）
        await retrieval_cache.set("查询", embedding, "", [{"text": "资料"}], organization_id=3)

        # 问答链路绝不能命中 answer 为空的条目（修复前这里会返回空回答）
        assert await answer_cache.get(embedding, organization_id=3, query="查询") is None
        # 检索链路仍能命中自己的缓存（缓存收益保留）
        hit = await retrieval_cache.get(embedding, organization_id=3, query="查询")
        assert hit is not None
        assert hit["sources"] == [{"text": "资料"}]

        # 反向：问答缓存的完整回答也不会流入检索缓存
        await answer_cache.set("查询", embedding, "完整回答", [{"text": "资料"}], organization_id=3)
        retrieval_hit = await retrieval_cache.get(embedding, organization_id=3, query="查询")
        assert retrieval_hit is not None
        assert retrieval_hit["answer"] == ""
        assert (await answer_cache.get(embedding, organization_id=3, query="查询"))["answer"] == "完整回答"

    def test_pipeline_uses_retrieval_namespace(self):
        from app.rag.pipeline import RAGPipeline

        pipeline = RAGPipeline()
        assert pipeline.semantic_cache.prefix == SemanticCache.RETRIEVAL_PREFIX
        assert pipeline.semantic_cache.prefix != SemanticCache().prefix
        assert pipeline.semantic_cache.index_key != SemanticCache().index_key

    @pytest.mark.asyncio
    async def test_clear_for_org_clears_retrieval_namespace_too(self, monkeypatch):
        """调用点仍以默认实例清理，检索命名空间也必须被清掉，否则文档更新后命中旧内容。"""
        fake = _FakeRedis()
        monkeypatch.setattr("app.rag.cache._get_redis", lambda: fake)
        # issue #195：语义缓存默认关闭，清空语义条目需先显式开启写入。
        monkeypatch.setattr("app.rag.cache.settings.SEMANTIC_CACHE_RETRIEVAL_ENABLED", True)
        embedding = [0.5] * 32

        retrieval_cache = SemanticCache.for_retrieval()
        await retrieval_cache.set("查询", embedding, "", [{"text": "旧资料"}], organization_id=5)
        assert await retrieval_cache.get(embedding, organization_id=5, query="查询") is not None

        await SemanticCache().clear_for_org(5)

        assert await retrieval_cache.get(embedding, organization_id=5, query="查询") is None
        assert not any("org:5:" in key for key in fake.values)


class TestSharedRetrievalCacheInvalidation:
    @pytest.mark.asyncio
    async def test_clear_for_org_from_new_instance_invalidates_shared_memory(self, monkeypatch):
        """Redis 不可用时，新建实例的 clear_for_org 必须清理到 pipeline 单例实际读写的内存缓存。"""
        monkeypatch.setattr("app.rag.cache._get_redis", lambda: None)

        serving = RetrievalCache()  # 模拟 pipeline 单例持有的实例
        await serving.set("缓存查询", 9, 5, [{"text": "旧内容"}])
        assert await serving.get("缓存查询", 9, 5) == [{"text": "旧内容"}]

        await RetrievalCache().clear_for_org(9)  # 调用点行为：每次新建实例

        assert await serving.get("缓存查询", 9, 5) is None

    @pytest.mark.asyncio
    async def test_clear_for_org_redis_branch_unchanged(self, monkeypatch):
        """Redis 可用时仍按 SCAN 前缀匹配删除（行为不变）。"""
        fake = _FakeRedis()
        monkeypatch.setattr("app.rag.cache._get_redis", lambda: fake)

        serving = RetrievalCache()
        await serving.set("缓存查询2", 9, 5, [{"text": "旧内容"}])
        assert "rag:cache:org=9|topk=5|q=缓存查询2" in fake.values

        await RetrievalCache().clear_for_org(9)

        assert not any(key.startswith("rag:cache:org=9|") for key in fake.values)
