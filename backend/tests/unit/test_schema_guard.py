"""启动期 schema guard（issue #495）单测：落后 / 一致 / 连不上 DB 三条路径。

判据与 scripts/check_schema.py 同口径：数据库 alembic_version 的 revision 集合
必须与迁移脚本的 head 集合相等。这里用 SQLite（aiosqlite）真实读写版本表，
不依赖 MySQL；AUTO_MIGRATE 的升级动作以 monkeypatch 注入（真实 alembic 升级
由 CI 的 migration-mysql job 覆盖）。
"""

import logging
import sqlite3
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app import bootstrap

CURRENT_REVISION = "011_organization_settings"
HEAD_REVISION = "014_api_keys"
HEADS = {HEAD_REVISION}

ALEMBIC_VERSION_DDL = (
    "CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
)


@pytest.fixture
async def sqlite_engine(tmp_path):
    """文件型 SQLite 异步引擎（AUTO_MIGRATE 用例需要在工作线程里用同步连接改写）。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'schema_guard.db'}")
    yield engine
    await engine.dispose()


async def _seed_revision(engine, revision: str) -> None:
    async with engine.begin() as conn:
        await conn.execute(text(ALEMBIC_VERSION_DDL))
        await conn.execute(text("DELETE FROM alembic_version"))
        await conn.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:r)"), {"r": revision}
        )


@pytest.fixture
def guard_logs():
    """收集 ``app.main`` logger 的记录（不依赖 caplog / root handler 链）。

    与 test_init_db_create_all_gating 同样的理由：全量跑套件时 root handler
    可能被 setup_logging()/alembic fileConfig 换掉或 disable，直接在目标
    logger 上挂 handler 并保存/恢复 level 与 disabled，观测结果与本进程其它
    套件的日志污染无关。
    """
    logger = logging.getLogger("app.main")
    records: list[logging.LogRecord] = []

    class _Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Collector(level=logging.DEBUG)
    prev_level, prev_disabled = logger.level, logger.disabled
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.disabled = False
    try:
        yield records
    finally:
        logger.disabled = prev_disabled
        logger.setLevel(prev_level)
        logger.removeHandler(handler)


# ── 路径 1：库里版本落后 → 需要迁移，明确错误结束 ──────────────────────────


@pytest.mark.asyncio
async def test_outdated_schema_raises_with_actionable_message(
    sqlite_engine, monkeypatch, guard_logs
):
    """current=011 / head=014：必须抛 SchemaOutdatedError，且不触发 upgrade。"""
    await _seed_revision(sqlite_engine, CURRENT_REVISION)
    monkeypatch.setattr(bootstrap, "get_alembic_heads", lambda: set(HEADS))
    upgrade = MagicMock()
    monkeypatch.setattr(bootstrap, "_run_alembic_upgrade", upgrade)

    with pytest.raises(bootstrap.SchemaOutdatedError) as excinfo:
        await bootstrap.enforce_schema_guard(engine=sqlite_engine, auto_migrate=False)

    assert upgrade.call_count == 0, "AUTO_MIGRATE=false 时绝不能在启动期迁移"
    assert excinfo.value.current == {CURRENT_REVISION}
    assert excinfo.value.heads == HEADS

    message = str(excinfo.value)
    assert f"current={CURRENT_REVISION}" in message
    assert f"head={HEAD_REVISION}" in message
    assert "alembic upgrade head" in message

    critical = [r for r in guard_logs if r.levelno == logging.CRITICAL]
    assert critical, "schema 落后必须写 critical 日志"
    assert any(
        f"current={CURRENT_REVISION}" in r.getMessage()
        and f"head={HEAD_REVISION}" in r.getMessage()
        and "alembic upgrade head" in r.getMessage()
        for r in critical
    ), "critical 日志必须含 current / head / 修复命令"


# ── 路径 2：current == head → 通过、不触发 upgrade ────────────────────────


@pytest.mark.asyncio
async def test_schema_at_head_passes_without_upgrade(sqlite_engine, monkeypatch, guard_logs):
    await _seed_revision(sqlite_engine, HEAD_REVISION)
    monkeypatch.setattr(bootstrap, "get_alembic_heads", lambda: set(HEADS))
    upgrade = MagicMock()
    monkeypatch.setattr(bootstrap, "_run_alembic_upgrade", upgrade)

    await bootstrap.enforce_schema_guard(engine=sqlite_engine, auto_migrate=False)

    assert upgrade.call_count == 0
    assert any("处于迁移 head" in r.getMessage() for r in guard_logs)
    assert not [r for r in guard_logs if r.levelno == logging.CRITICAL]


# ── 路径 3：连不上 DB → fail-closed（异常上抛，不迁移）────────────────────


@pytest.mark.asyncio
async def test_db_unreachable_fails_closed(monkeypatch):
    engine = MagicMock()
    engine.connect.side_effect = ConnectionError("connection refused")
    monkeypatch.setattr(bootstrap, "get_alembic_heads", lambda: set(HEADS))
    upgrade = MagicMock()
    monkeypatch.setattr(bootstrap, "_run_alembic_upgrade", upgrade)

    with pytest.raises(ConnectionError):
        await bootstrap.enforce_schema_guard(engine=engine, auto_migrate=False)

    assert upgrade.call_count == 0, "连不上库时不得尝试迁移（fail-closed）"


# ── 补充：迁移从未跑过（无 alembic_version 表）同样是 fail-closed ──────────


@pytest.mark.asyncio
async def test_missing_alembic_version_is_fail_closed(sqlite_engine, monkeypatch):
    monkeypatch.setattr(bootstrap, "get_alembic_heads", lambda: set(HEADS))

    with pytest.raises(bootstrap.SchemaOutdatedError) as excinfo:
        await bootstrap.enforce_schema_guard(engine=sqlite_engine, auto_migrate=False)

    assert excinfo.value.current is None
    assert "current=(none)" in str(excinfo.value)
    assert "alembic stamp" in str(excinfo.value), "非迁移建库需要给出 stamp 接管提示"


@pytest.mark.asyncio
async def test_empty_alembic_version_table_gets_adopt_hint(sqlite_engine, monkeypatch):
    """issue #154 的真实形态：表在但 0 行（create_all 残留）也必须提示 stamp。"""
    async with sqlite_engine.begin() as conn:
        await conn.execute(text(ALEMBIC_VERSION_DDL))
    monkeypatch.setattr(bootstrap, "get_alembic_heads", lambda: set(HEADS))

    with pytest.raises(bootstrap.SchemaOutdatedError) as excinfo:
        await bootstrap.enforce_schema_guard(engine=sqlite_engine, auto_migrate=False)

    assert excinfo.value.current == set()
    assert "current=(none)" in str(excinfo.value)
    assert "alembic stamp" in str(excinfo.value), "空版本表同样需要 stamp 接管提示"


# ── AUTO_MIGRATE 兜底：升级后复检通过 / 仍落后则失败 ──────────────────────


@pytest.mark.asyncio
async def test_auto_migrate_runs_upgrade_then_rechecks(sqlite_engine, monkeypatch, guard_logs):
    await _seed_revision(sqlite_engine, CURRENT_REVISION)
    monkeypatch.setattr(bootstrap, "get_alembic_heads", lambda: set(HEADS))
    db_file = sqlite_engine.url.database

    calls: list[int] = []

    def fake_upgrade() -> None:
        # 模拟 alembic upgrade head 把版本表推进到 head（工作线程里用同步连接）。
        calls.append(1)
        conn = sqlite3.connect(db_file)
        try:
            conn.execute("UPDATE alembic_version SET version_num = ?", (HEAD_REVISION,))
            conn.commit()
        finally:
            conn.close()

    monkeypatch.setattr(bootstrap, "_run_alembic_upgrade", fake_upgrade)

    await bootstrap.enforce_schema_guard(engine=sqlite_engine, auto_migrate=True)

    assert calls == [1]
    assert any("AUTO_MIGRATE 完成" in r.getMessage() for r in guard_logs)


@pytest.mark.asyncio
async def test_auto_migrate_still_behind_raises(sqlite_engine, monkeypatch):
    await _seed_revision(sqlite_engine, CURRENT_REVISION)
    monkeypatch.setattr(bootstrap, "get_alembic_heads", lambda: set(HEADS))
    upgrade = MagicMock()
    monkeypatch.setattr(bootstrap, "_run_alembic_upgrade", upgrade)

    with pytest.raises(bootstrap.SchemaOutdatedError) as excinfo:
        await bootstrap.enforce_schema_guard(engine=sqlite_engine, auto_migrate=True)

    assert upgrade.call_count == 1, "AUTO_MIGRATE=true 必须尝试过 upgrade 后才失败"
    assert excinfo.value.current == {CURRENT_REVISION}


# ── SCHEMA_GUARD=false：显式逃生门（连 heads 都不读）──────────────────────


@pytest.mark.asyncio
async def test_schema_guard_disabled_skips_everything(sqlite_engine, monkeypatch):
    await _seed_revision(sqlite_engine, CURRENT_REVISION)
    monkeypatch.setattr(bootstrap.settings, "SCHEMA_GUARD", False)

    def _must_not_read_heads() -> set[str]:
        raise AssertionError("SCHEMA_GUARD=false 时不应读取迁移 heads")

    monkeypatch.setattr(bootstrap, "get_alembic_heads", _must_not_read_heads)
    upgrade = MagicMock()
    monkeypatch.setattr(bootstrap, "_run_alembic_upgrade", upgrade)

    await bootstrap.enforce_schema_guard(engine=sqlite_engine, auto_migrate=False)

    assert upgrade.call_count == 0


# ── 退出码 2（uvicorn 会把启动异常折叠成 exit 3，必须 os._exit(2)）─────────


def test_exit_for_schema_outdated_uses_code_2(monkeypatch, guard_logs):
    recorded: list[int] = []

    def fake_exit(code: int) -> None:
        recorded.append(code)

    monkeypatch.setattr(bootstrap.os, "_exit", fake_exit)
    exc = bootstrap.SchemaOutdatedError({CURRENT_REVISION}, set(HEADS))

    bootstrap._exit_for_schema_outdated(exc)

    assert recorded == [2]
    assert any(
        r.levelno == logging.CRITICAL and f"current={CURRENT_REVISION}" in r.getMessage()
        for r in guard_logs
    ), "未被 guard 记录过的异常，退出前必须补一条 critical"


def test_exit_does_not_duplicate_logged_message(monkeypatch, guard_logs):
    recorded: list[int] = []
    monkeypatch.setattr(bootstrap.os, "_exit", lambda code: recorded.append(code))
    exc = bootstrap.SchemaOutdatedError({CURRENT_REVISION}, set(HEADS))
    exc.logged = True

    bootstrap._exit_for_schema_outdated(exc)

    assert recorded == [2]
    assert not [r for r in guard_logs if r.levelno == logging.CRITICAL]


@pytest.mark.asyncio
async def test_lifespan_converts_outdated_schema_to_exit_2(monkeypatch):
    """启动路径接线：lifespan 里 guard 抛错时必须走到 exit 2（不是 uvicorn 的 3）。"""

    class _StopStartup(Exception):
        pass

    recorded: list[int] = []

    def fake_exit(code: int) -> None:
        recorded.append(code)
        raise _StopStartup

    monkeypatch.setattr(bootstrap.os, "_exit", fake_exit)
    deps = bootstrap.LifespanDeps(
        init_db=AsyncMock(),
        init_redis=AsyncMock(),
        init_elasticsearch=AsyncMock(),
        close_redis=AsyncMock(),
        close_db=AsyncMock(),
        kafka_producer=MagicMock(),
        es_client=None,
        resolve_backend_port=lambda: 8000,
        metrics_snapshot_task=lambda: None,
        scheduled_tasks_loop=lambda: None,
        schema_guard=AsyncMock(
            side_effect=bootstrap.SchemaOutdatedError({CURRENT_REVISION}, set(HEADS))
        ),
    )
    lifespan = bootstrap.build_lifespan(deps)

    with pytest.raises(_StopStartup):
        async with lifespan(MagicMock()):
            pass

    assert recorded == [2]


@pytest.mark.asyncio
async def test_lifespan_guard_db_error_keeps_database_stage_log(monkeypatch, guard_logs, tmp_path):
    """guard 前移后，普通 DB 异常仍带初始化阶段日志，且不误走 exit 2。"""
    port_file = tmp_path / ".backend-port"
    monkeypatch.setattr(
        bootstrap,
        "open",
        lambda _path, mode: port_file.open(mode),
        raising=False,
    )
    init_db = AsyncMock()
    process_exit = MagicMock()
    monkeypatch.setattr(bootstrap.os, "_exit", process_exit)
    deps = bootstrap.LifespanDeps(
        init_db=init_db,
        init_redis=AsyncMock(),
        init_elasticsearch=AsyncMock(),
        close_redis=AsyncMock(),
        close_db=AsyncMock(),
        kafka_producer=MagicMock(),
        es_client=None,
        resolve_backend_port=lambda: 8000,
        metrics_snapshot_task=lambda: None,
        scheduled_tasks_loop=lambda: None,
        schema_guard=AsyncMock(side_effect=ConnectionError("connection refused")),
    )
    lifespan = bootstrap.build_lifespan(deps)

    with pytest.raises(ConnectionError, match="connection refused"):
        async with lifespan(MagicMock()):
            pass

    assert init_db.await_count == 0
    assert process_exit.call_count == 0
    assert any(
        r.levelno == logging.ERROR and "数据库初始化失败" in r.getMessage() for r in guard_logs
    )


# ── 接线与仓库自检 ────────────────────────────────────────────────────────


def test_default_lifespan_deps_binds_schema_guard():
    """生产统一入口必须绑定 guard —— 防止某次重构把接线删掉后没人发现。"""
    assert bootstrap.DEFAULT_LIFESPAN_DEPS.schema_guard is bootstrap.enforce_schema_guard


def test_repo_migrations_have_exactly_one_head():
    """仓库迁移不得分叉（多 head 时 guard 永远无法 pass），且至少有一个 head。"""
    heads = bootstrap.get_alembic_heads()
    assert heads
    assert len(heads) == 1, f"迁移出现分叉：{sorted(heads)}"


def test_run_alembic_upgrade_disables_alembic_fileconfig(monkeypatch):
    """评审判定 F1：不能让 env.py 的 fileConfig 重置宿主日志管线（审计日志被吞）。

    config_file_name=None 时 alembic/env.py 跳过 fileConfig；升级命令本身与
    script_location 仍须正确。
    """
    import alembic.command

    captured: dict = {}

    def fake_upgrade(cfg, revision) -> None:
        captured["cfg"] = cfg
        captured["revision"] = revision

    monkeypatch.setattr(alembic.command, "upgrade", fake_upgrade)

    bootstrap._run_alembic_upgrade()

    assert captured["revision"] == "head"
    assert captured["cfg"].config_file_name is None
    assert str(captured["cfg"].get_main_option("script_location")) == str(
        bootstrap.ALEMBIC_SCRIPT_DIR
    )
