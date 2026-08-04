"""Unit tests for MetricsCollector Redis snapshot persistence."""

import pytest

from app.core.middleware import MetricsCollector


class _FakeRedis:
    def __init__(self):
        self.lists: dict[str, list[str]] = {}

    def __bool__(self):
        return True

    async def lpush(self, key: str, value: str) -> None:
        self.lists.setdefault(key, []).insert(0, value)

    async def ltrim(self, key: str, start: int, end: int) -> None:
        items = self.lists.get(key, [])
        self.lists[key] = items[start : end + 1 if end >= 0 else None]

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        items = self.lists.get(key, [])
        return items[start : end + 1 if end >= 0 else None]


@pytest.mark.asyncio
async def test_snapshot_persists_and_loads_from_redis(monkeypatch):
    fake = _FakeRedis()
    monkeypatch.setattr("app.core.redis.redis_client", fake)

    collector = MetricsCollector()
    await collector.take_snapshot()

    assert len(fake.lists["metrics:snapshots"]) == 1

    restored = MetricsCollector()
    history = await restored.get_history()

    assert len(history) == 1
    assert history[0]["request_count"] == 0
