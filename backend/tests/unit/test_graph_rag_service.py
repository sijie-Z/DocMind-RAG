"""Unit tests for GraphRAG Redis persistence."""

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
    await service.build_graph_from_entities([
        {"entity": "DocMind", "type": "PRODUCT", "description": "RAG system", "relations": []}
    ])

    # 组织分区后 key 带 org 后缀（默认 org 1）
    assert "rag:graph:1" in fake.data

    restored = GraphRAGService()
    await restored.load()

    entity_key = restored._normalize_entity("DocMind")
    assert restored.graph[entity_key]["entity_name"] == "DocMind"
    assert restored.graph[entity_key]["entity_type"] == "PRODUCT"

    # 组织隔离：org 5 不应看到 org 1 的图谱
    await restored.load(5)
    assert restored._graph_for(5) == {}
    assert entity_key not in restored._graph_for(5)
