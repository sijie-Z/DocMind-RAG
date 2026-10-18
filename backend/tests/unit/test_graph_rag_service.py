"""Unit tests for GraphRAG Redis persistence."""

from types import SimpleNamespace

import pytest

from app.services.graph_rag_service import GraphRAGService


class _FakeRedis:
    def __init__(self):
        self.data: dict[str, str] = {}

    def __bool__(self):
        return True

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def setex(self, key: str, _ttl: int, value: str) -> None:
        self.data[key] = value


@pytest.mark.asyncio
async def test_graph_persists_and_loads_from_redis(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr("app.core.redis.redis_client", fake)

    service = GraphRAGService()
    await service.build_graph_from_entities(
        [
            {"entity": "DocMind", "type": "PRODUCT", "description": "RAG system", "relations": []}
        ],
        organization_id=1,
    )

    # 组织分区后 key 带 org 后缀（显式 org 1）
    assert "rag:graph:1" in fake.data

    restored = GraphRAGService()
    await restored.load(1)

    entity_key = restored._normalize_entity("DocMind")
    org1_graph = restored._graph_for(1)
    assert org1_graph[entity_key]["entity_name"] == "DocMind"
    assert org1_graph[entity_key]["entity_type"] == "PRODUCT"

    # 组织隔离：org 5 不应看到 org 1 的图谱
    await restored.load(5)
    assert restored._graph_for(5) == {}
    assert entity_key not in restored._graph_for(5)


@pytest.mark.asyncio
async def test_search_graph_injects_real_llm_client(monkeypatch):
    """缺口收口：search_graph 此前不传 llm_client → 查询实体识别恒走规则回退。

    变异点：删掉 search_graph 里的 rag_service.openai_client 注入 → 本用例红。
    """
    fake = _FakeRedis()
    monkeypatch.setattr("app.core.redis.redis_client", fake)

    service = GraphRAGService()
    await service.build_graph_from_entities(
        [
            {"entity": "DocMind", "type": "PRODUCT", "description": "RAG system", "relations": []}
        ],
        organization_id=1,
    )

    captured = {}
    sentinel = object()

    async def fake_extract(text, llm_client=None):
        captured["client"] = llm_client
        return [{"entity": "DocMind", "type": "PRODUCT"}]

    monkeypatch.setattr(service, "extract_entities_with_llm", fake_extract)
    monkeypatch.setattr(
        "app.services.rag_service.rag_service",
        SimpleNamespace(openai_client=sentinel),
    )

    results = await service.search_graph("DocMind 是什么", 1)

    assert captured["client"] is sentinel, "必须注入真实 LLM 客户端"
    assert results and results[0]["entity"] == "DocMind"


@pytest.mark.asyncio
async def test_search_graph_degrades_when_llm_unavailable(monkeypatch):
    """llm_client 拿不到（无 key）时不得崩溃：降级为 None → 规则回退。"""
    fake = _FakeRedis()
    monkeypatch.setattr("app.core.redis.redis_client", fake)

    service = GraphRAGService()
    captured = {}

    async def fake_extract(text, llm_client=None):
        captured["client"] = llm_client
        return []

    monkeypatch.setattr(service, "extract_entities_with_llm", fake_extract)
    monkeypatch.setattr(
        "app.services.rag_service.rag_service",
        SimpleNamespace(openai_client=None),
    )

    assert await service.search_graph("任意查询", 1) == []
    assert captured["client"] is None
