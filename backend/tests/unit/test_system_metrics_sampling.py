"""系统指标采样器测试（round2/sampling）。

覆盖：
- 受控时钟 + 注入采集函数的确定性采样写入（timestamp 秒级 int）；
- 内存环形缓冲有界保留（超限淘汰最旧）；
- Redis 侧持久化：键名、JSON 内容、LTRIM 有界；持久化失败不丢内存样本；
- 读路径三态：有数据 / 空（无 Redis）/ 键缺失；另加 Redis 回填与顺序断言。

变异判别（交付时人工执行并留原始输出，见 _orchestrate/round2/_sampling_mut_*.txt）：
- 移除 main.py 的 ENABLE_MONITORING 门控 → 门控用例红；
- deque(maxlen=history_size) → deque() → 有界保留用例红；
- 删除 _persist_sample 的 ltrim → Redis 有界用例红；
- _load_samples 的 reversed() 还原 → 回填顺序用例红。
"""

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.system_metrics import (
    SYSTEM_METRICS_REDIS_KEY,
    SystemMetricsSampler,
)


def _fixed_collect(cpu: float, memory: float):
    return lambda: {"cpu_percent": cpu, "memory_percent": memory}


class _RecordingRedis:
    """记录 lpush/ltrim 调用的 fake Redis（写侧）。"""

    def __init__(self) -> None:
        self.pushed: list[tuple[str, str]] = []
        self.trims: list[tuple[str, int, int]] = []

    def __bool__(self) -> bool:
        return True

    async def lpush(self, key: str, value: str) -> None:
        self.pushed.append((key, value))

    async def ltrim(self, key: str, start: int, end: int) -> None:
        self.trims.append((key, start, end))


class _ListRedis:
    """给定列表内容的 fake Redis（读侧；newest-first，模拟 lpush 语义）。"""

    def __init__(self, items: list[str]) -> None:
        self._items = items

    def __bool__(self) -> bool:
        return True

    async def lrange(self, key: str, start: int, end: int) -> list[str]:
        assert key == SYSTEM_METRICS_REDIS_KEY
        return list(self._items)


# ── 采样写入（注入采集 + 受控时钟）─────────────────────────────────


@pytest.mark.asyncio
async def test_record_sample_uses_injected_collect_and_clock(monkeypatch):
    """确定性值断言：cpu/memory 取注入采集值；timestamp 为注入时钟的 int 秒。"""
    monkeypatch.setattr("app.core.redis.redis_client", None)
    collect = iter([
        {"cpu_percent": 7.5, "memory_percent": 63.25},
        {"cpu_percent": 8.0, "memory_percent": 64.0},
    ])
    clock = iter([100.9, 160.1])
    sampler = SystemMetricsSampler(
        history_size=10, collect_fn=lambda: next(collect), now_fn=lambda: next(clock)
    )

    first = await sampler.record_sample()
    second = await sampler.record_sample()

    assert first == {"cpu_percent": 7.5, "memory_percent": 63.25, "timestamp": 100}
    assert second == {"cpu_percent": 8.0, "memory_percent": 64.0, "timestamp": 160}
    # 零真实 sleep：两次采样之间无需等待，时钟完全由注入驱动。


@pytest.mark.asyncio
async def test_record_sample_returns_and_appends_in_order(monkeypatch):
    """get_history 旧→新返回，且返回副本（外部改动不污染内部缓冲）。"""
    monkeypatch.setattr("app.core.redis.redis_client", None)
    clock = iter([1000, 1060, 1120])
    sampler = SystemMetricsSampler(
        collect_fn=_fixed_collect(5.0, 50.0), now_fn=lambda: next(clock)
    )
    for _ in range(3):
        await sampler.record_sample()

    history = await sampler.get_history()
    assert [point["timestamp"] for point in history] == [1000, 1060, 1120]

    history.clear()
    assert len(await sampler.get_history()) == 3


# ── 有界保留（内存环形 + Redis LTRIM）──────────────────────────────


@pytest.mark.asyncio
async def test_memory_ring_drops_oldest_beyond_bound(monkeypatch):
    """上界生效：超过 history_size 后淘汰最旧，保留最近 N 条。"""
    monkeypatch.setattr("app.core.redis.redis_client", None)
    clock = iter(range(1000, 1100))
    sampler = SystemMetricsSampler(
        history_size=3, collect_fn=_fixed_collect(1.0, 2.0), now_fn=lambda: next(clock)
    )
    for _ in range(5):
        await sampler.record_sample()

    history = await sampler.get_history()
    assert len(history) == 3, "内存环形缓冲未生效：超限未淘汰最旧样本"
    assert [point["timestamp"] for point in history] == [1002, 1003, 1004]


@pytest.mark.asyncio
async def test_redis_persist_json_and_bounded_ltrim(monkeypatch):
    """写侧契约：键名对齐 metrics:snapshots 风格；JSON 内容与样本一致；LTRIM 保留 N 条。"""
    fake = _RecordingRedis()
    monkeypatch.setattr("app.core.redis.redis_client", fake)
    sampler = SystemMetricsSampler(
        history_size=3, collect_fn=_fixed_collect(11.0, 22.0), now_fn=lambda: 42.9
    )

    sample = await sampler.record_sample()

    assert fake.pushed, "样本未写入 Redis（lpush 未被调用）"
    key, raw = fake.pushed[0]
    assert key == "metrics:system:snapshots"  # 键名风格随仓内既有监控键
    assert json.loads(raw) == sample
    assert fake.trims == [("metrics:system:snapshots", 0, 2)], (
        "Redis 侧未做有界保留：LTRIM 应保留最近 history_size 条"
    )


@pytest.mark.asyncio
async def test_persist_failure_keeps_memory_sample(monkeypatch):
    """Redis 故障降级：持久化失败不抛出，内存样本仍在。"""

    class _DownRedis:
        def __bool__(self) -> bool:
            return True

        async def lpush(self, key: str, value: str) -> None:
            raise ConnectionError("redis down")

        async def ltrim(self, key: str, start: int, end: int) -> None:  # pragma: no cover
            raise AssertionError("lpush 已失败，不应到达 ltrim")

    monkeypatch.setattr("app.core.redis.redis_client", _DownRedis())
    sampler = SystemMetricsSampler(
        collect_fn=_fixed_collect(9.0, 90.0), now_fn=lambda: 7.0
    )

    sample = await sampler.record_sample()
    assert await sampler.get_history() == [sample]


# ── 读路径三态：有数据 / 空 / 键缺失 ────────────────────────────────


@pytest.mark.asyncio
async def test_get_history_empty_without_samples(monkeypatch):
    """态 2（空）：无内存样本且无 Redis → 空列表（诚实空态）。"""
    monkeypatch.setattr("app.core.redis.redis_client", None)
    sampler = SystemMetricsSampler(
        collect_fn=_fixed_collect(1.0, 1.0), now_fn=lambda: 0.0
    )
    assert await sampler.get_history() == []


@pytest.mark.asyncio
async def test_get_history_key_missing_returns_empty(monkeypatch):
    """态 3（键缺失）：Redis 在但键不存在（lrange 空）→ 空列表，不抛错。"""
    monkeypatch.setattr("app.core.redis.redis_client", _ListRedis([]))
    sampler = SystemMetricsSampler(
        collect_fn=_fixed_collect(1.0, 1.0), now_fn=lambda: 0.0
    )
    assert await sampler.get_history() == []


@pytest.mark.asyncio
async def test_get_history_backfills_from_redis_oldest_first(monkeypatch):
    """内存为空时从 Redis 回填（跨 worker/重启可见）；顺序反转为旧→新。"""
    newest_first = [
        json.dumps({"cpu_percent": 2.0, "memory_percent": 20.0, "timestamp": 200}),
        json.dumps({"cpu_percent": 1.0, "memory_percent": 10.0, "timestamp": 100}),
    ]
    monkeypatch.setattr("app.core.redis.redis_client", _ListRedis(newest_first))
    sampler = SystemMetricsSampler(
        collect_fn=_fixed_collect(1.0, 1.0), now_fn=lambda: 0.0
    )

    history = await sampler.get_history()
    assert [point["timestamp"] for point in history] == [100, 200]


@pytest.mark.asyncio
async def test_get_history_skips_corrupt_redis_entries(monkeypatch):
    """损坏条目跳过（与 middleware._load_snapshots 同语义），合法条目保留。"""
    items = [
        "not-json",
        json.dumps({"cpu_percent": 3.0, "memory_percent": 30.0, "timestamp": 300}),
    ]
    monkeypatch.setattr("app.core.redis.redis_client", _ListRedis(items))
    sampler = SystemMetricsSampler(
        collect_fn=_fixed_collect(1.0, 1.0), now_fn=lambda: 0.0
    )

    history = await sampler.get_history()
    assert [point["timestamp"] for point in history] == [300]


# ── 门控：ENABLE_MONITORING 关 → 采样不启动（复用既有 lifespan 门控语义）──


@pytest.fixture
def _lifespan_infra(monkeypatch):
    """把 lifespan 的外部依赖全部打桩，只保留被测的开关与采样接线；保护 .backend-port。

    与 test_issue242_settings_wiring._lifespan_infra 同款（#242 门控先例）。
    """
    from app import main as main_mod

    for name in ("init_db", "init_redis", "init_elasticsearch", "close_redis", "close_db"):
        monkeypatch.setattr(main_mod, name, AsyncMock())
    monkeypatch.setattr(main_mod.kafka_producer, "start", AsyncMock())
    monkeypatch.setattr(main_mod.kafka_producer, "stop", AsyncMock())
    monkeypatch.setattr(main_mod, "es_client", None)
    monkeypatch.setattr(
        "app.services.permission_service.permission_service"
        ".initialize_default_permissions_and_roles",
        AsyncMock(),
    )
    monkeypatch.setattr("app.dependencies.wire_memory_embedding_provider", AsyncMock())
    monkeypatch.setattr(main_mod.settings, "ENABLE_DEMO_ACCOUNT", False)

    port_file = Path(main_mod.__file__).resolve().parent.parent / ".backend-port"
    saved = port_file.read_text(encoding="utf-8") if port_file.exists() else None
    yield
    if saved is None:
        port_file.unlink(missing_ok=True)
    else:
        port_file.write_text(saved, encoding="utf-8")


def _patch_sampling_recorder(monkeypatch):
    """把应用快照与系统采样都替换为记录器；返回系统采样调用列表。"""
    from app import main as main_mod

    calls: list[bool] = []

    async def fake_record_sample():
        calls.append(True)

    monkeypatch.setattr(main_mod.metrics_collector, "take_snapshot", AsyncMock())
    monkeypatch.setattr(main_mod.system_metrics_sampler, "record_sample", fake_record_sample)
    return calls


@pytest.mark.asyncio
async def test_lifespan_skips_system_sampling_when_gate_off(monkeypatch, _lifespan_infra):
    """ENABLE_MONITORING=false → 系统指标采样不运行（门控失效则本用例红）。"""
    from app import main as main_mod

    calls = _patch_sampling_recorder(monkeypatch)
    monkeypatch.setattr(main_mod.settings, "ENABLE_MONITORING", False)

    async with main_mod.lifespan(main_mod.app):
        await asyncio.sleep(0.05)
        assert calls == [], "ENABLE_MONITORING=false 时系统指标采样不应运行"

    assert calls == []


@pytest.mark.asyncio
async def test_lifespan_runs_system_sampling_when_gate_on(monkeypatch, _lifespan_infra):
    """ENABLE_MONITORING=true → 快照任务启动且周期内执行系统采样。"""
    from app import main as main_mod

    calls = _patch_sampling_recorder(monkeypatch)
    monkeypatch.setattr(main_mod.settings, "ENABLE_MONITORING", True)

    async with main_mod.lifespan(main_mod.app):
        for _ in range(100):
            if calls:
                break
            await asyncio.sleep(0.01)
        assert calls == [True], "ENABLE_MONITORING=true 时系统指标采样应随快照任务启动"

    # lifespan 退出已 cancel 任务；让事件循环把取消落定，避免 pending-task 噪音
    await asyncio.sleep(0)
    await asyncio.sleep(0)


# ── 读路径接线：/monitoring/metrics/system 与 dashboard trends.system ──


def _sample(ts: int) -> dict:
    return {"timestamp": ts, "cpu_percent": 12.5, "memory_percent": 71.0}


class _StubSampler:
    """以固定历史替身 sampling 单例，断言端点的读取与信封。"""

    def __init__(self, history: list[dict]) -> None:
        self._history = history

    async def get_history(self) -> list[dict]:
        return list(self._history)


@pytest.mark.asyncio
async def test_metrics_system_returns_data_envelope(monkeypatch):
    """/metrics/system → {"data": [...]}，元素为采样点（与 K 支线信封对齐）。"""
    from app.api.v1.endpoints import monitoring as monitoring_mod

    monkeypatch.setattr(monitoring_mod, "system_metrics_sampler", _StubSampler([_sample(100)]))

    result = await monitoring_mod.get_metrics("system", "1h", None)
    assert result == {"data": [_sample(100)]}


@pytest.mark.asyncio
async def test_metrics_system_empty_stays_honest(monkeypatch):
    """无样本 → {"data": []}（诚实空态；不造假、不造伪点）。"""
    from app.api.v1.endpoints import monitoring as monitoring_mod

    monkeypatch.setattr(monitoring_mod, "system_metrics_sampler", _StubSampler([]))

    result = await monitoring_mod.get_metrics("system", "1h", None)
    assert result == {"data": []}


@pytest.mark.asyncio
async def test_metrics_application_branch_unchanged(monkeypatch):
    """回归：application 分支仍走 metrics_collector 历史与 <2 防伪门槛。"""
    from app.api.v1.endpoints import monitoring as monitoring_mod

    monkeypatch.setattr(
        monitoring_mod.metrics_collector, "get_history", AsyncMock(return_value=[_sample(1)])
    )
    assert await monitoring_mod.get_metrics("application", "1h", None) == {"data": []}

    monkeypatch.setattr(
        monitoring_mod.metrics_collector,
        "get_history",
        AsyncMock(return_value=[_sample(1), _sample(2)]),
    )
    assert await monitoring_mod.get_metrics("application", "1h", None) == {
        "data": [_sample(1), _sample(2)]
    }


@pytest.mark.asyncio
async def test_dashboard_trends_system_reads_sampler_history(monkeypatch):
    """dashboard.trends.system 为采样器历史（前端 CPU/内存图消费点）。"""
    from app.api.v1.endpoints import monitoring as monitoring_mod

    monkeypatch.setattr(
        monitoring_mod, "system_metrics_sampler", _StubSampler([_sample(50), _sample(110)])
    )

    db = MagicMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar=MagicMock(return_value=0)))
    es = MagicMock()
    es.cluster.health = AsyncMock(return_value={"status": "green"})
    es.indices.stats = AsyncMock(return_value={})
    monkeypatch.setattr(monitoring_mod, "get_elasticsearch", AsyncMock(return_value=es))

    body = await monitoring_mod.get_monitoring_dashboard(db=db)

    assert body["trends"]["system"] == [_sample(50), _sample(110)]
    # 其余趋势维持既有形态（不在本支线范围内）
    assert body["trends"]["application"] == []
    assert body["trends"]["knowledge_base"] == []
