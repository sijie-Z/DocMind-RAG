"""校验数据库 schema 是否与 Alembic 迁移、以及 ORM 模型一致。

CI 的 schema 门禁用它在真实数据库上做断言：
  1. ``alembic_version`` 的内容与迁移脚本的 head **集合相等**（不多、不少、不分叉）；
  2. 所有 revision id <= 32 字符（MySQL 的 version_num 是 VARCHAR(32)，超长报 1406）；
  3. 迁移建出来的表与 ``Base.metadata`` 声明的表完全一致；
  4. MySQL 上所有表使用统一的 canonical collation。

用法::

    python scripts/check_schema.py                  # Alembic 路径：含 revision 校验
    python scripts/check_schema.py --create-all     # create_all 路径：跳过 revision 校验
    python scripts/check_schema.py --report-columns # 额外打印列级漂移报告（不阻断）

退出码 0 = 通过，1 = 失败。数据库地址取 ``DATABASE_URL``（与 alembic 同源）。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from alembic.config import Config  # noqa: E402
from alembic.script import ScriptDirectory  # noqa: E402
from sqlalchemy import create_engine, inspect, text  # noqa: E402

import app.models  # noqa: E402, F401  # 必须导入以填充 Base.metadata
from app.core.config import settings  # noqa: E402
from app.core.database import Base  # noqa: E402

ALEMBIC_VERSION_TABLE = "alembic_version"
# MySQL 下 alembic_version.version_num 是 VARCHAR(32)，更长的 revision id 会在
# 写入版本号时报 1406 Data too long。
ALEMBIC_VERSION_NUM_MAX = 32
# 项目约定的 MySQL collation。docker-compose 用 --collation-server 设定它，
# SETUP.md 的 CREATE DATABASE 也用同一个值。见 CHANGELOG「canonical collation」。
CANONICAL_MYSQL_COLLATION = "utf8mb4_unicode_ci"


def _sync_url(url: str) -> str:
    """把异步驱动 URL 换成对应的同步驱动，供 create_engine 使用。"""
    return url.replace("+aiomysql", "+pymysql").replace("+aiosqlite", "")


def _script_directory() -> ScriptDirectory:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return ScriptDirectory.from_config(cfg)


def check_alembic_revision(engine) -> list[str]:
    """确认迁移脚本无分叉、id 不过长，且数据库停在 head。返回错误信息列表。"""
    script = _script_directory()
    heads = set(script.get_heads())

    if len(heads) > 1:
        return [f"迁移出现分叉，存在多个 head: {sorted(heads)}（需要先 merge）"]

    too_long = [
        rev.revision for rev in script.walk_revisions()
        if len(rev.revision) > ALEMBIC_VERSION_NUM_MAX
    ]
    if too_long:
        return [
            f"revision id 超过 {ALEMBIC_VERSION_NUM_MAX} 字符，MySQL 上会报 1406: {too_long}"
        ]

    with engine.connect() as conn:
        exists = conn.execute(
            text(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_name = :t"
            )
            if engine.dialect.name == "mysql"
            else text(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name = :t"
            ),
            {"t": ALEMBIC_VERSION_TABLE},
        ).scalar()

        if not exists:
            return [f"数据库里没有 {ALEMBIC_VERSION_TABLE} 表——迁移从未在这个库上跑过"]

        # 取全部行而不是 scalar()：alembic_version 以 version_num 为主键，
        # 迁移分叉时会有多行，只取首行会漏掉多出来的 revision。
        current = {
            row[0]
            for row in conn.execute(
                text(f"SELECT version_num FROM {ALEMBIC_VERSION_TABLE}")  # noqa: S608
            )
        }

    if current != heads:
        return [
            f"数据库 revision {sorted(current)} 与迁移 head {sorted(heads)} 不一致"
            "（迁移未跑完，或库里残留了已删除的 revision）"
        ]
    return []


def check_tables_match(engine) -> list[str]:
    """对比迁移建出的表与 ORM 模型声明的表。返回错误信息列表。"""
    actual = set(inspect(engine).get_table_names()) - {ALEMBIC_VERSION_TABLE}
    expected = set(Base.metadata.tables)

    errors = []
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)

    if missing:
        errors.append(f"模型声明了但迁移没建出来的表 ({len(missing)}): {missing}")
    if extra:
        errors.append(f"迁移建了但模型里没有的表 ({len(extra)}): {extra}")
    return errors


def check_collation(engine) -> list[str]:
    """MySQL 上：所有业务表必须使用统一的 canonical collation。

    外键要求两端 collation 完全一致；collation 还决定唯一约束的语义
    （如 tags.name 的尾随空格处理），所以它属于业务 schema 的一部分，
    不能跟着数据库默认值漂移。SQLite 无 collation 概念，跳过。
    """
    if engine.dialect.name != "mysql":
        return []

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT table_name, table_collation FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_name != :v"
            ),
            {"v": ALEMBIC_VERSION_TABLE},
        ).all()

    bad = sorted(
        f"{name}({coll})" for name, coll in rows if coll != CANONICAL_MYSQL_COLLATION
    )
    if bad:
        return [
            f"以下表的 collation 不是 {CANONICAL_MYSQL_COLLATION}，"
            f"与项目约定的 canonical collation 不一致 ({len(bad)}): {bad}",
            "  修复方式：CREATE DATABASE ... COLLATE "
            f"{CANONICAL_MYSQL_COLLATION}（见 SETUP.md / docker-compose.yml）",
        ]
    return []


def report_column_drift(engine) -> None:
    """打印模型与数据库的列级差异。只报告，不影响退出码。

    用于评估「模型与迁移在列层面漂移了多少」，据此再决定是否升级为门禁。
    """
    inspector = inspect(engine)
    db_tables = set(inspector.get_table_names()) - {ALEMBIC_VERSION_TABLE}
    model_tables = set(Base.metadata.tables)

    print("\n── 列级漂移报告（仅报告，不阻断）──")
    total = 0
    for table in sorted(db_tables & model_tables):
        db_cols = {c["name"] for c in inspector.get_columns(table)}
        model_cols = set(Base.metadata.tables[table].columns.keys())
        missing, extra = sorted(model_cols - db_cols), sorted(db_cols - model_cols)
        if missing or extra:
            total += 1
            print(f"  {table}:")
            if missing:
                print(f"    模型有但库里没有: {missing}")
            if extra:
                print(f"    库里有但模型没有: {extra}")
    if total == 0:
        print("  无列级漂移")
    else:
        print(f"  共 {total} 张表存在列级差异")


def main() -> int:
    url = os.getenv("DATABASE_URL") or settings.DATABASE_URL
    display_url = url.split("@")[-1] if "@" in url else url
    # create_all 建的库没有 alembic_version 表，跳过 revision 相关校验。
    create_all_mode = "--create-all" in sys.argv
    mode = "create_all" if create_all_mode else "alembic"
    print(f"检查数据库 schema [{mode}]: {display_url} (dialect={url.split('://')[0]})")

    engine = create_engine(_sync_url(url), pool_pre_ping=True)
    try:
        errors = check_tables_match(engine) + check_collation(engine)
        if not create_all_mode:
            errors = check_alembic_revision(engine) + errors
        tables = inspect(engine).get_table_names()
        if "--report-columns" in sys.argv:
            report_column_drift(engine)
    finally:
        engine.dispose()

    if errors:
        print("\nSchema 校验失败:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    suffix = "" if create_all_mode else "，alembic 停在 head"
    app_tables = [t for t in tables if t != ALEMBIC_VERSION_TABLE]
    print(f"Schema 校验通过：{len(app_tables)} 张表，与模型声明一致{suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
