"""issue #242 第 6 条：三个「零消费」settings 字段的接线回归。

背景：`ENABLE_MONITORING` / `CHUNK_SIZE` / `TOP_K_RESULTS` 都被 .env 样例、README
或 CI 当作有效开关使用过，但代码里零读取——置 false / 改值会被静默忽略。
本文件锁住三条接线（默认值行为保持；改值 / 关闭必须真实生效）：

- `ENABLE_MONITORING=false` → `main.lifespan` 不启动指标快照任务（此前无条件启动）；
- `CHUNK_SIZE` → `FileUploadService.chunk_size`（此前硬编码 5MB）；
- `TOP_K_RESULTS` → `KnowledgeSearchRequest.top_k` 的默认值（此前硬编码 10）。

变异验证（交付时人工执行并留原始输出）：
1. `main.py` 的 `if settings.ENABLE_MONITORING:` 还原成无条件
   `metrics_task = asyncio.create_task(metrics_snapshot_task())` → disabled 用例红；
2. `file_service.py` 的 `self.chunk_size = settings.CHUNK_SIZE` 还原成
   `5 * 1024 * 1024` → chunk_size 用例对 settings 改值无感、红；
3. `schemas/knowledge.py` 的 `default_factory` 还原成 `Field(10, ...)` → top_k 用例红。
"""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.core.config import settings

# ── ENABLE_MONITORING → lifespan 快照任务 ─────────────────────────────


@pytest.fixture
def _lifespan_infra(monkeypatch):
    """把 lifespan 的外部依赖全部打桩，只保留被测的开关逻辑；并保护 .backend-port。"""
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

    # lifespan 启动时会把「实际端口」写进 backend/.backend-port（前端代理读的契约
    # 文件）——用例结束后恢复原值，不给开发者本地留下副作用（同 test_backend_port_file）。
    port_file = Path(main_mod.__file__).resolve().parent.parent / ".backend-port"
    saved = port_file.read_text(encoding="utf-8") if port_file.exists() else None
    yield
    if saved is None:
        port_file.unlink(missing_ok=True)
    else:
        port_file.write_text(saved, encoding="utf-8")


def _fake_snapshot_recorder():
    started: list[bool] = []

    async def fake_snapshot_task():
        started.append(True)

    return started, fake_snapshot_task


@pytest.mark.asyncio
async def test_lifespan_skips_snapshot_task_when_disabled(monkeypatch, _lifespan_infra):
    """ENABLE_MONITORING=false → 指标快照任务不启动（接线前无条件启动，用例红）。"""
    from app import main as main_mod

    started, fake = _fake_snapshot_recorder()
    monkeypatch.setattr(main_mod, "metrics_snapshot_task", fake)
    monkeypatch.setattr(main_mod.settings, "ENABLE_MONITORING", False)

    async with main_mod.lifespan(main_mod.app):
        await asyncio.sleep(0)
        assert started == [], "ENABLE_MONITORING=false 时不应启动指标快照任务（issue #242）"


@pytest.mark.asyncio
async def test_lifespan_starts_snapshot_task_when_enabled(monkeypatch, _lifespan_infra):
    """ENABLE_MONITORING=true（默认）→ 指标快照任务启动，行为与接线前一致。"""
    from app import main as main_mod

    started, fake = _fake_snapshot_recorder()
    monkeypatch.setattr(main_mod, "metrics_snapshot_task", fake)
    monkeypatch.setattr(main_mod.settings, "ENABLE_MONITORING", True)

    async with main_mod.lifespan(main_mod.app):
        for _ in range(100):
            if started:
                break
            await asyncio.sleep(0.01)
        assert started == [True], "ENABLE_MONITORING=true 时应启动指标快照任务"


def test_enable_monitoring_default_is_true():
    """默认值锚点（review_cfg242 minor-1）：`ENABLE_MONITORING` 默认必须是 True。

    上面两条用例都在运行时 monkeypatch settings，钉不到**类默认值**——
    变异默认值 True→False 时它们全绿（review_cfg242 MUT-A：6 passed），
    而未设 env 的部署会静默关闭监控快照。本用例把默认值本身钉住。
    """
    from app.core.config.base import BaseAppSettings

    assert BaseAppSettings.model_fields["ENABLE_MONITORING"].default is True


# ── CHUNK_SIZE → FileUploadService ───────────────────────────────────


class TestChunkSizeWiring:
    def test_service_reads_settings_value(self, monkeypatch):
        """settings.CHUNK_SIZE 改值 → 分块上限跟随（接线前恒为硬编码 5MB）。"""
        from app.services.file_service import FileUploadService

        monkeypatch.setattr(settings, "CHUNK_SIZE", 1024 * 1024)
        assert FileUploadService().chunk_size == 1024 * 1024, (
            "CHUNK_SIZE 未接线：服务仍用硬编码分块大小（issue #242）"
        )

    def test_default_value_unchanged(self):
        """默认值保持 5MB（行为保持的锚点）。"""
        from app.core.config.database import DatabaseSettings

        assert DatabaseSettings.model_fields["CHUNK_SIZE"].default == 5 * 1024 * 1024


# ── TOP_K_RESULTS → 搜索请求默认值 ────────────────────────────────────


class TestTopKResultsWiring:
    def test_request_default_follows_settings(self, monkeypatch):
        """settings.TOP_K_RESULTS 改值 → 搜索请求默认 top_k 跟随（接线前恒为硬编码 10）。"""
        from app.schemas.knowledge import KnowledgeSearchRequest

        monkeypatch.setattr(settings, "TOP_K_RESULTS", 17)
        req = KnowledgeSearchRequest(query="q", organization_id=1)
        assert req.top_k == 17, (
            "TOP_K_RESULTS 未接线：搜索请求默认 top_k 仍是硬编码 10（issue #242）"
        )

    def test_default_value_unchanged(self):
        """默认值保持 10（行为保持的锚点；chat 链路默认 5 不归本字段管）。"""
        from app.core.config.ai import AISettings
        from app.schemas.knowledge import KnowledgeSearchRequest

        assert AISettings.model_fields["TOP_K_RESULTS"].default == 10
        assert KnowledgeSearchRequest(query="q", organization_id=1).top_k == settings.TOP_K_RESULTS
