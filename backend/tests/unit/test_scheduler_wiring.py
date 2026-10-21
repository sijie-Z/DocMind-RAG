"""定时任务调度器 lifespan 接线回归（模式与 issue #242 的 ENABLE_MONITORING 一致）。

- ENABLE_SCHEDULER=false → 调度循环不启动；
- ENABLE_SCHEDULER=true（默认）→ 调度循环启动；
- 默认值锚点（钉类默认，防运行时 monkeypatch 用例全绿但部署静默关闭）。
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest


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

    port_file = Path(main_mod.__file__).resolve().parent.parent / ".backend-port"
    saved = port_file.read_text(encoding="utf-8") if port_file.exists() else None
    yield
    if saved is None:
        port_file.unlink(missing_ok=True)
    else:
        port_file.write_text(saved, encoding="utf-8")


def _fake_loop_recorder():
    started: list[bool] = []

    async def fake_loop():
        started.append(True)

    return started, fake_loop


@pytest.mark.asyncio
async def test_lifespan_skips_scheduler_when_disabled(monkeypatch, _lifespan_infra):
    from app import main as main_mod

    started, fake = _fake_loop_recorder()
    monkeypatch.setattr(main_mod, "scheduled_tasks_loop", fake)
    monkeypatch.setattr(main_mod.settings, "ENABLE_SCHEDULER", False)

    async with main_mod.lifespan(main_mod.app):
        await asyncio.sleep(0)
        assert started == [], "ENABLE_SCHEDULER=false 时不应启动定时任务调度器"


@pytest.mark.asyncio
async def test_lifespan_starts_scheduler_when_enabled(monkeypatch, _lifespan_infra):
    from app import main as main_mod

    started, fake = _fake_loop_recorder()
    monkeypatch.setattr(main_mod, "scheduled_tasks_loop", fake)
    monkeypatch.setattr(main_mod.settings, "ENABLE_SCHEDULER", True)

    async with main_mod.lifespan(main_mod.app):
        for _ in range(100):
            if started:
                break
            await asyncio.sleep(0.01)
        assert started == [True], "ENABLE_SCHEDULER=true 时应启动定时任务调度器"


def test_enable_scheduler_default_is_true():
    """默认值锚点：`ENABLE_SCHEDULER` 默认必须是 True（与 ENABLE_MONITORING 同模式）。"""
    from app.core.config.base import BaseAppSettings

    assert BaseAppSettings.model_fields["ENABLE_SCHEDULER"].default is True


def test_scheduler_settings_defaults():
    from app.core.config.base import BaseAppSettings

    assert BaseAppSettings.model_fields["SCHEDULER_POLL_SECONDS"].default == 30
    assert BaseAppSettings.model_fields["SCHEDULER_DEFAULT_TIMEZONE"].default == "UTC"


def test_scheduled_tasks_loop_delegates_to_engine(monkeypatch):
    """main.scheduled_tasks_loop 必须委托给 engine 单例的 run_loop（防接线漂移）。"""
    from app import main as main_mod
    from app.services.scheduler import engine as engine_mod

    called = []

    async def fake_run_loop(self):
        called.append(True)

    monkeypatch.setattr(engine_mod.SchedulerEngine, "run_loop", fake_run_loop)
    asyncio.run(main_mod.scheduled_tasks_loop())
    assert called == [True]
