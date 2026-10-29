"""Guarded 启动的数据库所有权：真空库只能由 Alembic 建 schema。

全部用例使用 pytest ``tmp_path`` 下的独立文件型 SQLite，不使用内存共享连接，
也不连接真实外部服务。 lifespan 在数据库初始化与 guard 之后、Redis 初始化前
用一个 BaseException 停止，避免启动其余基础设施。
"""

import asyncio
import sqlite3
from contextlib import closing
from functools import partial
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app import bootstrap
from app.core import database as database_module
from app.core.config import settings


class _StopStartup(BaseException):
    """在 Redis 初始化处截断 lifespan；不会被 ``except Exception`` 吞掉。"""


_DEFAULT_GUARD = object()


async def _stop_before_infrastructure() -> None:
    raise _StopStartup


def _table_names(db_path) -> set[str]:
    """通过新的同步连接读回表名，排除 SQLite 自己的内部表。"""
    with closing(sqlite3.connect(db_path)) as conn:
        return {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }


def _revision_rows(db_path) -> set[str] | None:
    with closing(sqlite3.connect(db_path)) as conn:
        exists = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'alembic_version'"
        ).fetchone()[0]
        if not exists:
            return None
        return {row[0] for row in conn.execute("SELECT version_num FROM alembic_version")}


def _business_tables(db_path) -> set[str]:
    return _table_names(db_path) - {bootstrap.ALEMBIC_VERSION_TABLE}


def _legacy_note_rows(db_path) -> list[tuple[int, str]]:
    with closing(sqlite3.connect(db_path)) as conn:
        return conn.execute("SELECT id, note FROM legacy_notes ORDER BY id").fetchall()


@pytest.fixture
async def sqlite_db(tmp_path, monkeypatch):
    """把真实 init_db、guard 与 Alembic 同指向一个全新的文件型 SQLite。"""
    db_path = tmp_path / "guarded-bootstrap.db"
    url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    engine = create_async_engine(url)

    monkeypatch.setattr(settings, "DATABASE_URL", url)
    monkeypatch.setattr(settings, "SCHEMA_GUARD", True)
    monkeypatch.setattr(database_module, "engine", engine)
    monkeypatch.delenv("USE_ALEMBIC", raising=False)

    # 不让 lifespan 的端口文件写出本次 pytest scratch；也不允许测试进程真的退出。
    port_file = tmp_path / ".backend-port"
    monkeypatch.setattr(
        bootstrap,
        "open",
        lambda _path, mode: port_file.open(mode),
        raising=False,
    )

    def unexpected_exit(code):
        raise AssertionError(f"此路径不应调用 os._exit({code})")

    monkeypatch.setattr(bootstrap.os, "_exit", unexpected_exit)
    try:
        yield db_path, engine
    finally:
        await engine.dispose()


def _lifespan_deps(engine, *, auto_migrate: bool, schema_guard=_DEFAULT_GUARD):
    if schema_guard is _DEFAULT_GUARD:
        schema_guard = partial(
            bootstrap.enforce_schema_guard,
            engine=engine,
            auto_migrate=auto_migrate,
        )
    return bootstrap.LifespanDeps(
        init_db=database_module.init_db,
        init_redis=_stop_before_infrastructure,
        init_elasticsearch=AsyncMock(),
        close_redis=AsyncMock(),
        close_db=AsyncMock(),
        kafka_producer=MagicMock(),
        es_client=None,
        resolve_backend_port=lambda: 8000,
        metrics_snapshot_task=lambda: None,
        scheduled_tasks_loop=lambda: None,
        schema_guard=schema_guard,
    )


async def _enter_until_database_ready(
    engine, *, auto_migrate: bool, schema_guard=_DEFAULT_GUARD
) -> None:
    lifespan = bootstrap.build_lifespan(
        _lifespan_deps(engine, auto_migrate=auto_migrate, schema_guard=schema_guard)
    )
    with pytest.raises(_StopStartup):
        async with lifespan(MagicMock()):
            pass


def _capture_schema_exit(monkeypatch) -> tuple[list[int], list[bootstrap.SchemaOutdatedError]]:
    """保留真实 exit-2 路径，同时把进程退出替换成测试控制流异常。"""
    codes: list[int] = []
    errors: list[bootstrap.SchemaOutdatedError] = []
    real_exit = bootstrap._exit_for_schema_outdated

    def capture_error(exc):
        errors.append(exc)
        real_exit(exc)

    def fake_exit(code):
        codes.append(code)
        raise _StopStartup

    monkeypatch.setattr(bootstrap, "_exit_for_schema_outdated", capture_error)
    monkeypatch.setattr(bootstrap.os, "_exit", fake_exit)
    return codes, errors


def _spy_create_all(monkeypatch) -> MagicMock:
    """保留真实 create_all 行为，同时记录 guarded 路径是否越权调用它。"""
    spy = MagicMock(side_effect=database_module.Base.metadata.create_all)
    monkeypatch.setattr(database_module.Base.metadata, "create_all", spy)
    return spy


def _spy_alembic_upgrade(monkeypatch) -> MagicMock:
    """保留真实迁移行为；修复后 legacy 路径必须一次也不调用它。"""
    spy = MagicMock(side_effect=bootstrap._run_alembic_upgrade)
    monkeypatch.setattr(bootstrap, "_run_alembic_upgrade", spy)
    return spy


@pytest.mark.asyncio
async def test_guarded_fresh_db_auto_migrate_is_owned_by_alembic(sqlite_db, monkeypatch):
    """空库 + AUTO_MIGRATE=true：真实 upgrade 到唯一 head，create_all 零调用。"""
    db_path, engine = sqlite_db
    heads = bootstrap.get_alembic_heads()
    create_all = _spy_create_all(monkeypatch)

    await _enter_until_database_ready(engine, auto_migrate=True)

    assert create_all.call_count == 0, "guarded 空库不得先由 create_all 建表"
    assert len(heads) == 1
    assert _revision_rows(db_path) == heads
    assert {"organizations", "users", "agent_tasks"} <= _table_names(db_path)


@pytest.mark.asyncio
async def test_guarded_fresh_db_without_auto_migrate_writes_no_business_table(
    sqlite_db, monkeypatch
):
    """空库 + AUTO_MIGRATE=false：保留 current/head 指引，exit 2 前业务表仍为零。"""
    db_path, engine = sqlite_db
    heads = bootstrap.get_alembic_heads()
    create_all = _spy_create_all(monkeypatch)
    exit_codes, errors = _capture_schema_exit(monkeypatch)

    await _enter_until_database_ready(engine, auto_migrate=False)

    assert exit_codes == [2]
    assert len(errors) == 1
    message = str(errors[0])
    assert "current=(none)" in message
    assert f"head={','.join(sorted(heads))}" in message
    assert "alembic upgrade head" in message
    assert _business_tables(db_path) == set(), "guard 拒绝前不得写任何业务表"
    assert create_all.call_count == 0


@pytest.mark.asyncio
async def test_guarded_database_at_head_is_not_rebuilt_or_remigrated(sqlite_db, monkeypatch):
    """已在 head 的库：不重复 upgrade，也不调用 create_all，schema 与 revision 不变。"""
    db_path, engine = sqlite_db
    await asyncio.to_thread(bootstrap._run_alembic_upgrade)
    tables_before = _table_names(db_path)
    revisions_before = _revision_rows(db_path)
    upgrade = MagicMock()
    monkeypatch.setattr(bootstrap, "_run_alembic_upgrade", upgrade)
    create_all = _spy_create_all(monkeypatch)

    await _enter_until_database_ready(engine, auto_migrate=True)

    assert upgrade.call_count == 0
    assert create_all.call_count == 0
    assert _table_names(db_path) == tables_before
    assert _revision_rows(db_path) == revisions_before == bootstrap.get_alembic_heads()


@pytest.mark.asyncio
@pytest.mark.parametrize("auto_migrate", [False, True], ids=["auto_false", "auto_true"])
async def test_guarded_legacy_database_still_requires_explicit_adoption(
    sqlite_db, monkeypatch, auto_migrate
):
    """38 表 create_all/legacy 库在两种策略下都不得被自动收养或改写。"""
    db_path, engine = sqlite_db
    async with engine.begin() as conn:
        await conn.run_sync(database_module.Base.metadata.create_all)
    tables_before = _table_names(db_path)
    assert len(tables_before) == 38
    assert _revision_rows(db_path) is None

    create_all = _spy_create_all(monkeypatch)
    upgrade = _spy_alembic_upgrade(monkeypatch)
    exit_codes, errors = _capture_schema_exit(monkeypatch)

    await _enter_until_database_ready(engine, auto_migrate=auto_migrate)

    assert exit_codes == [2]
    assert len(errors) == 1
    assert "alembic stamp" in str(errors[0])
    assert create_all.call_count == 0
    assert upgrade.call_count == 0
    assert _table_names(db_path) == tables_before
    assert _revision_rows(db_path) is None


@pytest.mark.asyncio
async def test_guarded_single_table_legacy_auto_migrate_fails_before_any_ddl(
    sqlite_db, monkeypatch
):
    """一张无冲突表也不得被 AUTO_MIGRATE 收养；原行与无 revision 状态不变。"""
    db_path, engine = sqlite_db
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE legacy_notes (id INTEGER PRIMARY KEY, note TEXT)"))
        await conn.execute(
            text("INSERT INTO legacy_notes (id, note) VALUES (:id, :note)"),
            {"id": 7, "note": "preserve-me"},
        )
    tables_before = _table_names(db_path)

    create_all = _spy_create_all(monkeypatch)
    upgrade = _spy_alembic_upgrade(monkeypatch)
    exit_codes, errors = _capture_schema_exit(monkeypatch)

    await _enter_until_database_ready(engine, auto_migrate=True)

    assert exit_codes == [2]
    assert len(errors) == 1
    assert "current=(none)" in str(errors[0])
    assert "alembic stamp" in str(errors[0])
    assert create_all.call_count == 0
    assert upgrade.call_count == 0
    assert _table_names(db_path) == tables_before
    assert _legacy_note_rows(db_path) == [(7, "preserve-me")]
    assert _revision_rows(db_path) is None


@pytest.mark.asyncio
async def test_unguarded_empty_database_keeps_create_all(sqlite_db, monkeypatch):
    """SCHEMA_GUARD=false 的真实 guard 逃生门仍可由 create_all 引导空库。"""
    db_path, engine = sqlite_db
    monkeypatch.setattr(settings, "SCHEMA_GUARD", False)
    create_all = _spy_create_all(monkeypatch)

    await _enter_until_database_ready(engine, auto_migrate=False)

    assert create_all.call_count == 1
    assert "organizations" in _business_tables(db_path)
    assert _revision_rows(db_path) is None


@pytest.mark.asyncio
async def test_main_asgi_lifespan_empty_database_keeps_create_all(sqlite_db, monkeypatch):
    """直接走 app.main 的真实 ASGI lifespan，兼容入口不绑定 guard 且可建空库。"""
    db_path, _engine = sqlite_db
    from app.core import logging as logging_module

    # 避免兼容壳 import 时初始化文件日志；端口文件已由 fixture 重定向到 scratch。
    monkeypatch.setattr(logging_module, "setup_logging", lambda: None)
    from app import main as main_module

    monkeypatch.setattr(main_module, "init_db", database_module.init_db)
    monkeypatch.setattr(main_module, "init_redis", _stop_before_infrastructure)
    monkeypatch.setattr(main_module, "resolve_backend_port", lambda: 8000)
    create_all = _spy_create_all(monkeypatch)

    with pytest.raises(_StopStartup):
        async with main_module.app.router.lifespan_context(main_module.app):
            pass

    assert create_all.call_count == 1
    assert "organizations" in _business_tables(db_path)
    assert _revision_rows(db_path) is None
