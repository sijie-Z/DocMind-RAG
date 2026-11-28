"""迁移 047：工件分享访问统计 schema 契约（真实 alembic 升级 + SQLite 读回）。

判别点：

- 047 接在 046 之后、全链单头；revision id ≤ 32（MySQL VARCHAR(32)）；
- ``agent_artifact_share`` 追加 ``access_count`` / ``last_accessed_at`` 两列，
  均可空、无 server_default（NULL 由服务层视为 0）；
- 历史行保持 NULL（本迁移不回填）；
- upgrade 按列存在性幂等（半迁移重复执行不报错）；downgrade 只删两列，
  不删表、不触碰 046 的既有对象；
- 迁移列与 ORM 模型完全一致（列级漂移即红）。
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
HEAD = "047_artifact_share_analytics"
PREVIOUS = "046_org_skill"
TABLE = "agent_artifact_share"
NEW_COLUMNS = ("access_count", "last_accessed_at")


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


def _columns(db_path: Path) -> dict[str, tuple]:
    with sqlite3.connect(str(db_path)) as conn:
        return {row[1]: row for row in conn.execute(f"PRAGMA table_info({TABLE})")}


def test_047_revision_chain():
    script = ScriptDirectory.from_config(_cfg())
    rev = script.get_revision(HEAD)
    assert rev.down_revision == PREVIOUS
    assert len(HEAD) <= 32, "MySQL alembic_version.version_num 是 VARCHAR(32)"
    assert set(script.get_heads()) == {"050_public_kb_chat"}, "050 必须是当前唯一 head"


def test_047_migration_source_guard():
    """upgrade 必须按列存在性幂等；downgrade 只 drop_column，不删表。"""
    migration = (BACKEND_ROOT / "alembic" / "versions" / f"{HEAD}.py").read_text(encoding="utf-8")
    assert "server_default=" not in migration, "新列默认值只能由 ORM/服务层提供"
    assert "mysql.DATETIME(fsp=6)" in migration, "last_accessed_at 必须与 ORM 的 fsp=6 变体一致"
    assert "op.add_column(" in migration
    downgrade = migration.split("def downgrade() -> None:", 1)[1]
    assert "op.drop_column(" in downgrade
    assert "op.drop_table(" not in downgrade
    assert "op.drop_index(" not in downgrade


def test_047_schema_roundtrip_and_legacy_rows_stay_null(tmp_path, monkeypatch):
    """升级读回列；历史行保持 NULL；回滚只删两列且 046 对象仍在。"""
    db_path = tmp_path / "artifact-share-047.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = _cfg()

    command.upgrade(cfg, PREVIOUS)
    # 047 之前插入的历史分享行：升级后两列必须保持 NULL（不回填）。
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO agent_artifact_share "
            "(id, organization_id, user_id, artifact_id, token_hash, created_by, created_at) "
            "VALUES ('legacy-share', 1, 1, 'legacy-artifact', ?, 1, '2026-01-01 00:00:00')",
            ("a" * 64,),
        )
        conn.commit()

    command.upgrade(cfg, HEAD)
    assert _revision(db_path) == {HEAD}
    cols = _columns(db_path)
    assert set(cols) == {c.name for c in Base.metadata.tables[TABLE].columns}, (
        "agent_artifact_share 迁移列与 ORM 模型漂移"
    )
    for name in NEW_COLUMNS:
        assert cols[name][3] == 0, f"{name} 必须可空"
        assert cols[name][4] is None, f"{name} 不得有 server_default"

    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT access_count, last_accessed_at FROM agent_artifact_share WHERE id='legacy-share'"
        ).fetchone()
        assert row == (None, None), "历史行必须保持 NULL，由服务层按 0 处理"

    command.downgrade(cfg, PREVIOUS)
    assert _revision(db_path) == {PREVIOUS}
    cols = _columns(db_path)
    assert not (set(NEW_COLUMNS) & set(cols)), "downgrade 必须删除本迁移追加的两列"
    # 046 的表与本表（及既有列）不能被误删。
    assert {"org_skill", "org_skill_version", TABLE} <= _tables(db_path)
    assert {"id", "token_hash", "expires_at", "revoked_at"} <= set(cols)


def test_047_upgrade_is_idempotent(tmp_path, monkeypatch):
    """半迁移（列已存在、版本仍停在 046）重复 upgrade 必须跳过建列。"""
    db_path = tmp_path / "artifact-share-047-idempotent.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = _cfg()
    command.upgrade(cfg, HEAD)
    # 模拟半迁移：版本回退到 046，但两列已经存在；再次 upgrade 时存在性守卫生效。
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("UPDATE alembic_version SET version_num = '046_org_skill'")
        conn.commit()
    command.upgrade(cfg, HEAD)
    assert _revision(db_path) == {HEAD}
    assert set(NEW_COLUMNS) <= set(_columns(db_path))


def test_047_upgrade_handles_only_one_column_already_present(tmp_path, monkeypatch):
    """半迁移只建了第一列时，upgrade 必须只补第二列。"""
    db_path = tmp_path / "artifact-share-047-partial.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = _cfg()
    command.upgrade(cfg, PREVIOUS)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(f"ALTER TABLE {TABLE} ADD COLUMN access_count INTEGER")
        conn.commit()
    command.upgrade(cfg, HEAD)
    assert _revision(db_path) == {HEAD}
    cols = _columns(db_path)
    assert set(NEW_COLUMNS) <= set(cols)
    assert cols["last_accessed_at"][3] == 0
