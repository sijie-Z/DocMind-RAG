"""迁移 041：Rubric 评测 schema 契约（真实 alembic 升级 + SQLite 读回）。

判别点：
- 041 接在 040 之后、全链单头；revision id ≤ 32（MySQL VARCHAR(32)）；
- 三张表的列/索引/唯一约束/外键与 ORM 模型完全一致；
- ``lock_version`` 无 server_default（由服务层/ORM 提供）；
- 运行表 aggregate/results 可空（失败运行保留机器码但不产生分数）；
- downgrade 回滚到 040 后三张表消失，040 的对象不受影响（round-trip 不残留）；
- downgrade 源码不得逐条 drop_index（MySQL 1553 先例）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

import app.models  # noqa: F401
from alembic import command
from app.core.config import settings
from app.core.database import Base

BACKEND_ROOT = Path(__file__).resolve().parents[2]
HEAD = "042_rubric_eval"
PREVIOUS = "041_agent_artifact"
RUBRIC = "eval_rubric"
VERSION = "eval_rubric_version"
RUN = "eval_rubric_run"


def _cfg() -> Config:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return cfg


def _revision(db_path: Path) -> set[str]:
    with sqlite3.connect(str(db_path)) as conn:
        return {row[0] for row in conn.execute("SELECT version_num FROM alembic_version")}


def _tables(db_path: Path) -> set[str]:
    with sqlite3.connect(str(db_path)) as conn:
        return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def test_041_revision_chain():
    script = ScriptDirectory.from_config(_cfg())
    rev = script.get_revision(HEAD)
    assert rev.down_revision == PREVIOUS
    assert len(HEAD) <= 32, "MySQL alembic_version.version_num 是 VARCHAR(32)"
    assert set(script.get_heads()) == {HEAD}


def test_041_downgrade_does_not_drop_indexes_before_tables():
    """downgrade 不得逐条 drop_index（MySQL 1553：drop_table 会随表删除索引）。"""
    migration = (BACKEND_ROOT / "alembic" / "versions" / "042_rubric_eval.py").read_text(
        encoding="utf-8"
    )
    downgrade = migration.split("def downgrade() -> None:", 1)[1]
    assert "op.drop_table(RUN)" in downgrade
    assert "op.drop_table(VERSION)" in downgrade
    assert "op.drop_table(RUBRIC)" in downgrade
    assert "op.drop_index(" not in downgrade


def test_041_schema_and_downgrade_roundtrip(tmp_path, monkeypatch):
    """升级到 041 读回列/FK/索引/唯一约束；回滚到 040 后三张表全部消失。"""
    db_path = tmp_path / "rubric-eval-041.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = _cfg()

    command.upgrade(cfg, HEAD)
    assert _revision(db_path) == {HEAD}
    tables = _tables(db_path)
    assert {RUBRIC, VERSION, RUN} <= tables
    # 040 的对象不能被 041 的 upgrade 误删。
    assert {"review_redline_submission", "review_redline_row"} <= tables

    with sqlite3.connect(str(db_path)) as conn:
        for table in (RUBRIC, VERSION, RUN):
            cols = {row[1]: row for row in conn.execute(f"PRAGMA table_info({table})")}
            assert set(cols) == {c.name for c in Base.metadata.tables[table].columns}, (
                f"{table} 迁移列与 ORM 模型漂移"
            )
            assert cols["created_at"][3] == 1, f"{table}.created_at NOT NULL"

        # lock_version 只能由 ORM/服务层提供默认值，不能落到 DDL 的 server_default。
        assert {row[1]: row for row in conn.execute(f"PRAGMA table_info({RUBRIC})")}[
            "lock_version"
        ][4] is None
        # 失败运行必须能落库：aggregate/results 可空。
        run_cols = {row[1]: row for row in conn.execute(f"PRAGMA table_info({RUN})")}
        assert run_cols["aggregate"][3] == 0
        assert run_cols["results"][3] == 0
        assert run_cols["error_code"][3] == 0
        assert run_cols["completed_at"][3] == 0

        rubric_fks = {
            (row[2], row[3]) for row in conn.execute(f"PRAGMA foreign_key_list({RUBRIC})")
        }
        assert ("organizations", "organization_id") in rubric_fks
        assert ("users", "created_by") in rubric_fks

        version_fks = {
            (row[2], row[3]) for row in conn.execute(f"PRAGMA foreign_key_list({VERSION})")
        }
        assert ("eval_rubric", "rubric_id") in version_fks
        assert ("organizations", "organization_id") in version_fks
        assert ("users", "created_by") in version_fks

        run_fks = {(row[2], row[3]) for row in conn.execute(f"PRAGMA foreign_key_list({RUN})")}
        assert ("organizations", "organization_id") in run_fks
        assert ("eval_rubric", "rubric_id") in run_fks
        assert ("users", "created_by") in run_fks

        rubric_indexes = {row[1] for row in conn.execute(f"PRAGMA index_list({RUBRIC})")}
        assert {"ix_eval_rubric_org_created", "ix_eval_rubric_org_status"} <= rubric_indexes
        version_indexes = {row[1] for row in conn.execute(f"PRAGMA index_list({VERSION})")}
        assert {
            "ix_eval_rubric_version_rubric_id",
            "ix_eval_rubric_version_organization_id",
            "ix_eval_rubric_version_rubric_created",
        } <= version_indexes
        run_indexes = {row[1] for row in conn.execute(f"PRAGMA index_list({RUN})")}
        assert {
            "ix_eval_rubric_run_org_created",
            "ix_eval_rubric_run_rubric_created",
        } <= run_indexes

        # SQLite 把命名 UniqueConstraint 渲染成 sqlite_autoindex_*：按列+唯一性反查。
        unique_cols = set()
        for index_row in conn.execute(f"PRAGMA index_list({RUBRIC})"):
            if index_row[2]:
                cols = tuple(
                    info[2]
                    for info in conn.execute(f"PRAGMA index_xinfo({index_row[1]})")
                    if info[2] is not None
                )
                unique_cols.add(cols)
        assert ("organization_id", "name") in unique_cols
        version_unique = set()
        for index_row in conn.execute(f"PRAGMA index_list({VERSION})"):
            if index_row[2]:
                cols = tuple(
                    info[2]
                    for info in conn.execute(f"PRAGMA index_xinfo({index_row[1]})")
                    if info[2] is not None
                )
                version_unique.add(cols)
        assert ("rubric_id", "version_no") in version_unique

    command.downgrade(cfg, PREVIOUS)
    assert _revision(db_path) == {PREVIOUS}
    tables = _tables(db_path)
    assert RUBRIC not in tables
    assert VERSION not in tables
    assert RUN not in tables
    # 040 的表不能被 downgrade 误删。
    assert {"review_redline_submission", "review_redline_row"} <= tables
