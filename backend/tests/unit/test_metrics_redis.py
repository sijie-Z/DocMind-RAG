"""Unit tests for MetricsCollector Redis snapshot persistence."""

import pytest

from app.core.config import settings
from app.core.middleware import MetricsCollector


class _FakeRedis:
    def __init__(self):
        self.lists: dict[str, list[str]] = {}
        self.data: dict[str, str] = {}

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

    async def get(self, key: str) -> str | None:
        return self.data.get(key)

    async def setex(self, key: str, _ttl: int, value: str) -> None:
        self.data[key] = value


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


@pytest.mark.asyncio
async def test_live_counters_restore_from_redis(monkeypatch):
    monkeypatch.setattr(settings, "METRICS_LIVE_PERSIST_SECONDS", 0)
    fake = _FakeRedis()
    monkeypatch.setattr("app.core.redis.redis_client", fake)

    collector = MetricsCollector()
    await collector.record_request(duration=0.1, status_code=200, method="GET", path="/health")
    await collector.record_request(
        duration=0.2, is_error=True, status_code=500, method="POST", path="/chat"
    )

    restored = MetricsCollector()
    stats = await restored.get_stats()

    assert stats["request_count"] == 2
    assert stats["error_count"] == 1
    assert "500" in restored.status_counts
