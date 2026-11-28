"""迁移 048：会话分享过期 schema 契约（真实 alembic 升级 + SQLite 读回）。

判别点：

- 048 接在 047 之后、全链单头；revision id ≤ 32（MySQL VARCHAR(32)）；
- ``chat_sessions`` 追加 ``share_expires_at`` 一个可空列，无 server_default
  （NULL 由 ORM/服务层表达「永不过期」）；
- 047 时已存在的分享行升级后 ``share_expires_at`` 保持 NULL（旧链接永久有效，
  本迁移不回填、不强制过期）；
- upgrade 按列存在性幂等（半迁移重复执行不报错）；downgrade 只删本列，
  不删表、不触碰既有列/索引/数据；
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
HEAD = "048_session_share_expiry"
PREVIOUS = "047_artifact_share_analytics"
TABLE = "chat_sessions"
NEW_COLUMN = "share_expires_at"


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


def test_048_revision_chain():
    script = ScriptDirectory.from_config(_cfg())
    rev = script.get_revision(HEAD)
    assert rev.down_revision == PREVIOUS
    assert len(HEAD) <= 32, "MySQL alembic_version.version_num 是 VARCHAR(32)"
    assert set(script.get_heads()) == {"050_public_kb_chat"}, "050 必须是当前唯一 head"


def test_048_migration_source_guard():
    """upgrade 必须按列存在性幂等；downgrade 只 drop_column，不删表/索引。"""
    migration = (BACKEND_ROOT / "alembic" / "versions" / f"{HEAD}.py").read_text(encoding="utf-8")
    assert "server_default=" not in migration, "新列默认值只能由 ORM/服务层提供"
    assert "mysql.DATETIME(fsp=6)" in migration, "share_expires_at 必须与 ORM 的 fsp=6 变体一致"
    assert "op.add_column(" in migration
    downgrade = migration.split("def downgrade() -> None:", 1)[1]
    assert "op.drop_column(" in downgrade
    assert "op.drop_table(" not in downgrade
    assert "op.drop_index(" not in downgrade


def test_048_schema_roundtrip_and_legacy_share_stays_permanent(tmp_path, monkeypatch):
    """升级读回列；历史分享行过期时间为 NULL；回滚只删本列且 047 对象仍在。"""
    db_path = tmp_path / "session-share-048.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = _cfg()

    command.upgrade(cfg, PREVIOUS)
    # 048 之前插入的历史分享行（已有 token/created，无 expires）：升级后必须
    # 保持 share_expires_at IS NULL——旧链接不因本迁移而失效。
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO chat_sessions "
            "(id, user_id, organization_id, title, status, message_count, "
            " next_message_seq, source, created_at, share_token_hash, share_created_at) "
            "VALUES ('legacy-share', 7, NULL, '旧分享会话', 'ACTIVE', 0, 0, 'user', "
            "        '2026-01-01 00:00:00', ?, '2026-01-02 00:00:00')",
            ("b" * 64,),
        )
        conn.commit()

    command.upgrade(cfg, HEAD)
    assert _revision(db_path) == {HEAD}
    cols = _columns(db_path)
    assert set(cols) == {c.name for c in Base.metadata.tables[TABLE].columns}, (
        "chat_sessions 迁移列与 ORM 模型漂移"
    )
    assert cols[NEW_COLUMN][3] == 0, f"{NEW_COLUMN} 必须可空"
    assert cols[NEW_COLUMN][4] is None, f"{NEW_COLUMN} 不得有 server_default"

    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT share_token_hash, share_created_at, share_expires_at "
            "FROM chat_sessions WHERE id='legacy-share'"
        ).fetchone()
        assert row == ("b" * 64, "2026-01-02 00:00:00", None), (
            "历史分享行的过期时间必须保持 NULL（永不过期）"
        )

    command.downgrade(cfg, PREVIOUS)
    assert _revision(db_path) == {PREVIOUS}
    cols = _columns(db_path)
    assert NEW_COLUMN not in cols, "downgrade 必须删除本迁移追加的列"
    # 047 的表、既有列与数据不能被误删/误改。
    assert {"agent_artifact_share", TABLE} <= _tables(db_path)
    assert {"id", "share_token_hash", "share_created_at"} <= set(cols)
    with sqlite3.connect(str(db_path)) as conn:
        assert conn.execute(
            "SELECT share_token_hash FROM chat_sessions WHERE id='legacy-share'"
        ).fetchone() == ("b" * 64,)


def test_048_upgrade_is_idempotent(tmp_path, monkeypatch):
    """半迁移（列已存在、版本仍停在 047）重复 upgrade 必须跳过建列。"""
    db_path = tmp_path / "session-share-048-idempotent.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = _cfg()
    command.upgrade(cfg, HEAD)
    # 模拟半迁移：版本回退到 047，但列已经存在；再次 upgrade 时存在性守卫生效。
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("UPDATE alembic_version SET version_num = '047_artifact_share_analytics'")
        conn.commit()
    command.upgrade(cfg, HEAD)
    assert _revision(db_path) == {HEAD}
    assert NEW_COLUMN in _columns(db_path)


def test_share_expires_at_uses_mysql_fsp6():
    """ORM 类型必须保留 DATETIME(fsp=6) 变体；删掉变体本用例红。"""
    from sqlalchemy.dialects import mysql

    from app.models.chat import ChatSession

    column = ChatSession.__table__.c.share_expires_at
    compiled = str(column.type.compile(dialect=mysql.dialect())).upper()
    assert "DATETIME(6)" in compiled, compiled
