"""进程内跑迁移不得静默宿主进程的既有 logger（issue #161 的守护测试）。

背景（issue #160 实测）：`alembic/env.py` 原先调用 `fileConfig(config.config_file_name)`
用的是默认 `disable_existing_loggers=True`，会把配置里没提到的既有 logger
（`app.core.database`、`sqlalchemy` 根等）置 `disabled=True`，且**全进程残留、不会自己恢复**。
从此该进程里 `app.*` 的日志全被静默——caplog 与自挂 handler 都收不到，
因为 `disabled` 在 `isEnabledFor()` 处直接短路。

本测试把这条不变式钉死：
1. 迁移跑完后，既有 logger 不得出现**新增**的 disabled（相对迁移前的快照取差集）；
2. `app.core.database` 仍启用，且迁移后挂到 root 的 fresh handler 能收到它的 WARNING
   （#160 的实况观测路径——宿主/pytest 正是在迁移之后才重新挂 handler 的，不依赖 caplog）；
3. 迁移自身的日志能力不得被削弱（验收②）：`alembic` 的 logger 仍按 alembic.ini
   配置生效（level=INFO），且 `alembic.runtime.migration` 的 INFO 记录能传播到 root handler。

放在 integration 层是因为它需要真实 MySQL 才能跑完整迁移（`RUN_INTEGRATION=1`）。
实现要点：`fileConfig` 本身会替换 root 的 handler（与 `disable_existing_loggers` 取值无关），
所以收集器一律挂在**迁移之后**；本测试只钉「disabled 短路」这一持久性污染，
不还原 fileConfig 替换 root handler 的其余副作用（同目录 `test_schema_path_parity`
已在做同样的进程内迁移，且 pytest 的捕获 handler 按测试相位重挂，见 #160 分析）。
"""

import logging
import os
from pathlib import Path

import pytest
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine, make_url, text

import app.core.database  # noqa: F401  # 确保既有 logger "app.core.database" 在迁移前就已存在
from alembic import command
from app.core.config import settings

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION") != "1",
    reason="需要真实 MySQL（设置 RUN_INTEGRATION=1）",
)

BACKEND_DIR = Path(__file__).resolve().parents[2]
_GUARD_DB = "docmind_env_logging_guard"


def _sync_url(url):
    """把异步驱动 URL 换成同步驱动（pymysql），供 create_engine 使用。"""
    return url.set(drivername=url.drivername.replace("+aiomysql", "+pymysql"))


def _disabled_loggers() -> set[str]:
    """当前被置 disabled 的既有 logger 名单（只统计真 Logger，排除 PlaceHolder）。"""
    return {
        name
        for name, obj in logging.root.manager.loggerDict.items()
        if isinstance(obj, logging.Logger) and obj.disabled
    }


class _Collector(logging.Handler):
    def __init__(self):
        super().__init__(level=0)
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)


def _capture_via_root(logger: logging.Logger, level: int, message: str) -> list[logging.LogRecord]:
    """在 root 上临时挂 fresh handler，向 logger 发一条记录，返回收到的记录。"""
    collector = _Collector()
    logging.getLogger().addHandler(collector)
    try:
        logger.log(level, message)
        return list(collector.records)
    finally:
        logging.getLogger().removeHandler(collector)


def test_inprocess_migration_keeps_existing_loggers_enabled(monkeypatch):
    url = make_url(os.getenv("DATABASE_URL") or settings.DATABASE_URL)
    assert url.get_backend_name() == "mysql", f"本测试只支持 MySQL，当前: {url.drivername}"
    assert "+aiomysql" in url.drivername, "alembic 路径需要异步 MySQL 驱动 URL（+aiomysql）"

    guard_url = url.set(database=_GUARD_DB)
    admin = create_engine(_sync_url(url.set(database=None)), isolation_level="AUTOCOMMIT")

    try:
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {_GUARD_DB}"))
            conn.execute(text(f"CREATE DATABASE {_GUARD_DB} COLLATE utf8mb4_unicode_ci"))

        before_disabled = _disabled_loggers()

        cfg = AlembicConfig(str(BACKEND_DIR / "alembic.ini"))
        cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
        with monkeypatch.context() as m:
            m.setattr(settings, "DATABASE_URL", guard_url.render_as_string(hide_password=False))
            command.upgrade(cfg, "head")

        # ── 不变式 1：迁移不得新增任何 disabled 的既有 logger（issue #161 核心）──
        newly_disabled = _disabled_loggers() - before_disabled
        assert not newly_disabled, (
            "进程内跑过迁移后，这些既有 logger 被静默（disabled=True）："
            f"{sorted(newly_disabled)}——env.py 的 fileConfig 是否又用了默认 "
            "disable_existing_loggers=True？（issue #161）"
        )

        # ── 不变式 2：app.core.database 仍启用，且记录能到达 root 的 fresh handler ──
        # 注意：disabled 按真值判断即可——fileConfig 给配置里的 logger 置的是整数 0
        # （CPython logging.config 源码 `logger.disabled = 0`），`is False` 会误伤。
        app_logger = logging.getLogger("app.core.database")
        assert not app_logger.disabled, (
            "迁移后 app.core.database 被置 disabled=True——宿主日志被静默（issue #161）"
        )
        assert app_logger.isEnabledFor(logging.WARNING)
        received = _capture_via_root(app_logger, logging.WARNING, "guard-probe-app")
        assert any(r.getMessage() == "guard-probe-app" for r in received), (
            "迁移后 app.core.database 的 WARNING 到不了 root 的 handler——日志被静默了"
        )

        # ── 不变式 3（验收②）：迁移自身的日志仍按 alembic.ini 生效 ──
        alembic_logger = logging.getLogger("alembic")
        assert not alembic_logger.disabled
        assert alembic_logger.level == logging.INFO, (
            "alembic.ini [logger_alembic] 的 level=INFO 没生效——迁移自身的日志配置被弄坏了"
        )
        alembic_child = logging.getLogger("alembic.runtime.migration")
        assert not alembic_child.disabled
        received = _capture_via_root(alembic_child, logging.INFO, "guard-probe-alembic")
        assert any(r.getMessage() == "guard-probe-alembic" for r in received), (
            "alembic 自己的 INFO 记录到不了 root handler——迁移日志被弄坏了"
        )
    finally:
        with admin.connect() as conn:
            conn.execute(text(f"DROP DATABASE IF EXISTS {_GUARD_DB}"))
        admin.dispose()
