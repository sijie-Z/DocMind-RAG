"""两条建库路径（Alembic 迁移 vs create_all）schema 一致性的集成回归（issue #150）。

分别建两个一次性 MySQL 库：一个跑 ``alembic upgrade head``，一个跑
``Base.metadata.create_all``，然后走 information_schema 逐列比对
``COLUMN_TYPE / IS_NULLABLE / COLUMN_DEFAULT / EXTRA``，必须零差异
（``alembic_version`` 表属迁移基础设施，不参与比对）。

这是 issue #150 的长期回归网：迁移或模型任一侧再引入 schema 漂移，本测试都会变红。
需要真实 MySQL（``RUN_INTEGRATION=1``；本机会话与 CI 均用 root 连测试库）。

取舍说明：建两个库 + 跑完整迁移链约数秒，放进 integration 套件（而非 PR 快测）
是因为它依赖真实 MySQL 且比现有单库 smoke（check_schema.py）多覆盖「两条路径
逐列相等」这一 issue #150 的核心不变式；integration 套件本身已经是「真实服务」
层，代价可接受。
"""

import os
from pathlib import Path

import pytest
from alembic.config import Config as AlembicConfig
from sqlalchemy import create_engine, make_url, text

import app.models  # noqa: F401  # 必须导入以填充 Base.metadata
from alembic import command
from app.core.config import settings
from app.core.database import Base

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION") != "1",
    reason="需要真实 MySQL（设置 RUN_INTEGRATION=1）",
)

BACKEND_DIR = Path(__file__).resolve().parents[2]
_MIG_DB = "docmind_schema_parity_mig"
_CT_DB = "docmind_schema_parity_ct"

_COLUMN_SQL = (
    "SELECT table_name, column_name, column_type, is_nullable, "
    "IFNULL(column_default, '<无>'), extra "
    "FROM information_schema.columns WHERE table_schema = :schema "
    "ORDER BY table_name, column_name"
)


def _sync_url(url):
    """把异步驱动 URL 换成同步驱动（pymysql），供 create_engine 使用。"""
    return url.set(drivername=url.drivername.replace("+aiomysql", "+pymysql"))


def _fetch_columns(engine, schema: str) -> dict:
    with engine.connect() as conn:
        rows = conn.execute(text(_COLUMN_SQL), {"schema": schema}).all()
    columns = {}
    for table, column, ctype, nullable, default, extra in rows:
        if table == "alembic_version":
            continue
        columns[(table, column)] = (ctype, nullable, default, extra)
    return columns


def test_alembic_and_create_all_build_identical_schema(monkeypatch):
    url = make_url(os.getenv("DATABASE_URL") or settings.DATABASE_URL)
    assert url.get_backend_name() == "mysql", f"本测试只支持 MySQL，当前: {url.drivername}"
    assert "+aiomysql" in url.drivername, "alembic 路径需要异步 MySQL 驱动 URL（+aiomysql）"

    mig_url = url.set(database=_MIG_DB)
    ct_url = url.set(database=_CT_DB)
    admin = create_engine(_sync_url(url.set(database=None)), isolation_level="AUTOCOMMIT")

    try:
        with admin.connect() as conn:
            for name in (_MIG_DB, _CT_DB):
                conn.execute(text(f"DROP DATABASE IF EXISTS {name}"))
                conn.execute(text(f"CREATE DATABASE {name} COLLATE utf8mb4_unicode_ci"))

        # ── 路径 1：alembic upgrade head（env.py 从 settings.DATABASE_URL 取库地址）──
        # 注意：URL 的 __str__ 会把口令打码成 ***，必须用 render_as_string
        # 取回真实连接串，否则会以口令 "***" 连库（1045）。
        cfg = AlembicConfig(str(BACKEND_DIR / "alembic.ini"))
        cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
        with monkeypatch.context() as m:
            m.setattr(settings, "DATABASE_URL", mig_url.render_as_string(hide_password=False))
            command.upgrade(cfg, "head")

        # ── 路径 2：Base.metadata.create_all ──
        ct_engine = create_engine(_sync_url(ct_url))
        mig_engine = create_engine(_sync_url(mig_url))
        try:
            Base.metadata.create_all(ct_engine)
            mig_cols = _fetch_columns(mig_engine, _MIG_DB)
            ct_cols = _fetch_columns(ct_engine, _CT_DB)
        finally:
            ct_engine.dispose()
            mig_engine.dispose()

        # 防「空比对假绿」：两条路径都应建出全量 schema（当前为 240 列）。
        assert len(mig_cols) > 200 and len(ct_cols) > 200, (
            f"比对的列数异常偏少（迁移={len(mig_cols)}, create_all={len(ct_cols)}），"
            "检查两条路径是否真的建出了全量 schema"
        )

        only_mig = sorted(set(mig_cols) - set(ct_cols))
        only_ct = sorted(set(ct_cols) - set(mig_cols))
        diff = [
            (key, mig_cols[key], ct_cols[key])
            for key in sorted(set(mig_cols) & set(ct_cols))
            if mig_cols[key] != ct_cols[key]
        ]
        assert not only_mig and not only_ct and not diff, (
            "两条建库路径的 schema 不一致（issue #150 的漂移回归）：\n"
            f"  只在迁移库存在: {only_mig}\n"
            f"  只在 create_all 库存在: {only_ct}\n"
            + "\n".join(
                f"  {table}.{column}: 迁移={mig} vs create_all={ct}"
                for (table, column), mig, ct in diff
            )
        )
    finally:
        with admin.connect() as conn:
            for name in (_MIG_DB, _CT_DB):
                conn.execute(text(f"DROP DATABASE IF EXISTS {name}"))
        admin.dispose()
