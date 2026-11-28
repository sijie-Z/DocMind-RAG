"""迁移 046：组织技能库 schema 契约（真实 alembic 升级 + SQLite 读回）。

判别点：
- 046 接在 045 之后、全链单头；revision id ≤ 32（MySQL VARCHAR(32)）；
- ``org_skill`` / ``org_skill_version`` 的列/索引/唯一键/外键与 ORM 模型完全一致；
- 除 ``created_at`` 外无 server_default（默认值由服务层/ORM 提供）；
- 版本行唯一 ``(skill_id, version_no)``；``(organization_id, review_status)`` 索引存在；
- downgrade 回滚到 045 后两表消失，045 的对象不受影响；源码不得逐条 drop_index。
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
HEAD = "046_org_skill"
PREVIOUS = "045_artifact_share"
SKILL = "org_skill"
VERSION = "org_skill_version"
ORG_REVIEW_INDEX = "ix_org_skill_version_org_review"


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


def _unique_column_sets(conn: sqlite3.Connection, table: str) -> set[tuple[str, ...]]:
    return {
        tuple(
            info[2]
            for info in conn.execute(f"PRAGMA index_xinfo({index_row[1]})")
            if info[2] is not None
        )
        for index_row in conn.execute(f"PRAGMA index_list({table})")
        if index_row[2]
    }


def test_046_revision_chain():
    script = ScriptDirectory.from_config(_cfg())
    rev = script.get_revision(HEAD)
    assert rev.down_revision == PREVIOUS
    assert len(HEAD) <= 32, "MySQL alembic_version.version_num 是 VARCHAR(32)"
    assert set(script.get_heads()) == {"050_public_kb_chat"}, (
        "批次十七后唯一 head 是 050（046 本身仍是本用例的被测迁移）"
    )


def test_046_downgrade_does_not_drop_indexes_before_tables():
    """downgrade 不得逐条 drop_index（MySQL 1553：drop_table 会随表删除索引）。"""
    migration = (BACKEND_ROOT / "alembic" / "versions" / f"{HEAD}.py").read_text(encoding="utf-8")
    downgrade = migration.split("def downgrade() -> None:", 1)[1]
    assert "op.drop_table(VERSION)" in downgrade
    assert "op.drop_table(SKILL)" in downgrade
    assert "op.drop_index(" not in downgrade


def test_046_schema_and_downgrade_roundtrip(tmp_path, monkeypatch):
    """升级到 046 读回列/FK/索引/唯一键；回滚到 045 后两表消失且 045 对象仍在。"""
    db_path = tmp_path / "org-skill-046.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = _cfg()

    command.upgrade(cfg, HEAD)
    assert _revision(db_path) == {HEAD}
    tables = _tables(db_path)
    assert {SKILL, VERSION} <= tables
    # 被引用的既有对象不能被本迁移误删。
    assert {"agent_artifact_share", "organizations", "users"} <= tables

    with sqlite3.connect(str(db_path)) as conn:
        for table in (SKILL, VERSION):
            cols = {row[1]: row for row in conn.execute(f"PRAGMA table_info({table})")}
            assert set(cols) == {c.name for c in Base.metadata.tables[table].columns}, (
                f"{table} 迁移列与 ORM 模型漂移"
            )
            assert cols["created_at"][3] == 1 and cols["created_at"][4] is not None
            # 其余列不得偷偷带 DDL 默认值（由服务层/ORM 提供）。
            for name, row in cols.items():
                if name != "created_at":
                    assert row[4] is None, f"{table}.{name} 不应有 server_default"

        skill_cols = {row[1]: row for row in conn.execute(f"PRAGMA table_info({SKILL})")}
        for name in (
            "id",
            "organization_id",
            "name",
            "status",
            "latest_version",
            "created_by",
            "lock_version",
        ):
            assert skill_cols[name][3] == 1, f"org_skill.{name} NOT NULL"
        for name in (
            "description",
            "enabled_version_no",
            "updated_by",
            "archived_at",
            "archived_by",
            "updated_at",
        ):
            assert skill_cols[name][3] == 0, f"org_skill.{name} 必须可空"

        version_cols = {row[1]: row for row in conn.execute(f"PRAGMA table_info({VERSION})")}
        for name in (
            "id",
            "skill_id",
            "organization_id",
            "version_no",
            "payload_json",
            "payload_sha256",
            "source",
            "review_status",
            "created_by",
        ):
            assert version_cols[name][3] == 1, f"org_skill_version.{name} NOT NULL"
        for name in ("provenance_json", "reviewed_by", "reviewed_at", "review_note"):
            assert version_cols[name][3] == 0, f"org_skill_version.{name} 必须可空"

        skill_fks = {(row[2], row[3]) for row in conn.execute(f"PRAGMA foreign_key_list({SKILL})")}
        assert ("organizations", "organization_id") in skill_fks
        assert ("users", "created_by") in skill_fks
        version_fks = {
            (row[2], row[3]) for row in conn.execute(f"PRAGMA foreign_key_list({VERSION})")
        }
        assert ("org_skill", "skill_id") in version_fks
        assert ("organizations", "organization_id") in version_fks
        assert ("users", "created_by") in version_fks

        assert ("organization_id", "name") in _unique_column_sets(conn, SKILL)
        assert ("skill_id", "version_no") in _unique_column_sets(conn, VERSION)
        version_indexes = {row[1] for row in conn.execute(f"PRAGMA index_list({VERSION})")}
        assert ORG_REVIEW_INDEX in version_indexes

    command.downgrade(cfg, PREVIOUS)
    assert _revision(db_path) == {PREVIOUS}
    tables = _tables(db_path)
    assert SKILL not in tables and VERSION not in tables
    assert {"agent_artifact_share", "organizations", "users"} <= tables
