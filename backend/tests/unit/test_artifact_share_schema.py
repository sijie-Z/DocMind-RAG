"""迁移 045：工件分享 schema 契约（真实 alembic 升级 + SQLite 读回）。

判别点：
- 045 接在 044 之后、全链单头；revision id ≤ 32（MySQL VARCHAR(32)）；
- ``agent_artifact_share`` 的列/索引/唯一键/外键与 ORM 模型完全一致；
- 分享绑定**具体 artifact 版本行**（artifact_id 外键指向 agent_artifact）；
- ``expires_at`` / ``revoked_at`` 可空（不过期/未撤销）；其余列 NOT NULL；
- 除 ``created_at`` 外无 server_default（默认值由服务层提供）；
- downgrade 回滚到 044 后表消失，044 的表不受影响；源码不得逐条 drop_index。
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
HEAD = "045_artifact_share"
PREVIOUS = "044_answer_quality_eval"
TABLE = "agent_artifact_share"

ORG_INDEX = "ix_agent_artifact_share_organization_id"
USER_INDEX = "ix_agent_artifact_share_user_id"
ARTIFACT_UNIQUE = "uq_agent_artifact_share_artifact"
TOKEN_UNIQUE = "uq_agent_artifact_share_token_hash"


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


def test_045_revision_chain():
    script = ScriptDirectory.from_config(_cfg())
    rev = script.get_revision(HEAD)
    assert rev.down_revision == PREVIOUS
    assert len(HEAD) <= 32, "MySQL alembic_version.version_num 是 VARCHAR(32)"
    assert set(script.get_heads()) == {"048_session_share_expiry"}, "批次十二后唯一 head 是 048"


def test_045_downgrade_does_not_drop_indexes_before_tables():
    """downgrade 不得逐条 drop_index（MySQL 1553：drop_table 会随表删除索引）。"""
    migration = (BACKEND_ROOT / "alembic" / "versions" / f"{HEAD}.py").read_text(encoding="utf-8")
    downgrade = migration.split("def downgrade() -> None:", 1)[1]
    assert "op.drop_table(TABLE)" in downgrade
    assert "op.drop_index(" not in downgrade


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


def test_045_schema_and_downgrade_roundtrip(tmp_path, monkeypatch):
    """升级到 045 读回列/FK/索引/唯一键；回滚到 044 后表消失且 044 对象仍在。"""
    db_path = tmp_path / "artifact-share-045.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = _cfg()

    command.upgrade(cfg, HEAD)
    assert _revision(db_path) == {HEAD}
    tables = _tables(db_path)
    assert TABLE in tables
    # 被引用的工件表与 044 的评测表不能被本迁移误删。
    assert {"agent_artifact", "answer_quality_eval_run"} <= tables

    with sqlite3.connect(str(db_path)) as conn:
        cols = {row[1]: row for row in conn.execute(f"PRAGMA table_info({TABLE})")}
        # 045 只建本表当时的列；047 起 ORM 又追加了访问统计两列。这里比较
        # 「045 建出的列 == ORM 列 - 后续迁移追加列」，保持列级漂移护栏有效。
        later_columns = {"access_count", "last_accessed_at"}
        assert set(cols) == {c.name for c in Base.metadata.tables[TABLE].columns} - later_columns, (
            "agent_artifact_share 迁移列与 ORM 模型漂移"
        )
        for name in ("id", "organization_id", "user_id", "artifact_id", "token_hash", "created_by"):
            assert cols[name][3] == 1, f"{name} NOT NULL"
        assert cols["expires_at"][3] == 0, "expires_at 必须可空（空 = 不过期）"
        assert cols["revoked_at"][3] == 0, "revoked_at 必须可空（空 = 未撤销）"
        assert cols["created_at"][3] == 1 and cols["created_at"][4] is not None
        # 其余列不得偷偷带 DDL 默认值（由服务层/ORM 提供）。
        for name in ("token_hash", "expires_at", "revoked_at", "created_by"):
            assert cols[name][4] is None, f"{name} 不应有 server_default"

        fks = {(row[2], row[3]) for row in conn.execute(f"PRAGMA foreign_key_list({TABLE})")}
        assert ("organizations", "organization_id") in fks
        assert ("users", "user_id") in fks
        assert ("users", "created_by") in fks
        # 版本绑定：外键指向具体工件行，而不是任何「链头」概念列。
        assert ("agent_artifact", "artifact_id") in fks

        indexes = {row[1] for row in conn.execute(f"PRAGMA index_list({TABLE})")}
        assert {ORG_INDEX, USER_INDEX} <= indexes
        unique_cols = _unique_column_sets(conn, TABLE)
        assert ("artifact_id",) in unique_cols, "一版本至多一条分享记录"
        assert ("token_hash",) in unique_cols, "token 摘要必须唯一"

    command.downgrade(cfg, PREVIOUS)
    assert _revision(db_path) == {PREVIOUS}
    tables = _tables(db_path)
    assert TABLE not in tables
    assert {"agent_artifact", "answer_quality_eval_run"} <= tables


def test_045_upgrade_is_idempotent(tmp_path, monkeypatch):
    """重复 upgrade 到同一 head 不报错、不重复建表（DDL 存在性守卫）。"""
    db_path = tmp_path / "artifact-share-045-idempotent.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = _cfg()
    command.upgrade(cfg, HEAD)
    # 模拟半迁移：表已存在但 alembic 版本回退到 044；再次 upgrade 时
    # 045 的 DDL 存在性守卫必须跳过建表（直接二次 upgrade 是 no-op，测不到守卫）。
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("UPDATE alembic_version SET version_num = '044_answer_quality_eval'")
        conn.commit()
    command.upgrade(cfg, HEAD)
    assert _revision(db_path) == {HEAD}
    assert TABLE in _tables(db_path)


def test_045_model_is_registered():
    """模型必须在 app.models 注册（create_all / schema guard 依赖它）。"""
    import app.models as models
    from app.models.artifact_share import AgentArtifactShare

    assert models.AgentArtifactShare is AgentArtifactShare
    assert "AgentArtifactShare" in models.__all__
