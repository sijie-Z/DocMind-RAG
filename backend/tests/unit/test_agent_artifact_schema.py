"""迁移 041：Agent 工件 schema 契约（真实 alembic 升级 + SQLite 读回）。

判别点：
- 041 接在 040 之后且单头；
- ``agent_artifact`` 的列/索引/外键/唯一约束与 ORM 模型完全一致；
- ``conversation_id`` / ``task_id`` **无外键**（生命周期独立于会话/任务）；
- ``version_no`` / ``status`` 无 server_default（由 ORM/服务层提供，防批次五式
  迁移库↔create_all 库列级漂移）；
- downgrade 回滚到 040 后表消失，040 的表不受影响（round-trip 不残留）。
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
from app.models.artifact import ARTIFACT_KINDS, ARTIFACT_STATUSES, AgentArtifact

BACKEND_ROOT = Path(__file__).resolve().parents[2]
HEAD = "041_agent_artifact"
CURRENT_HEAD = "042_rubric_eval"
LATEST_HEAD = "043_session_tree"
FINAL_HEAD = "044_answer_quality_eval"
PREVIOUS = "040_inbound_redline_ledger"
TABLE = "agent_artifact"

ORG_INDEX = "ix_agent_artifact_organization_id"


def _cfg() -> Config:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return cfg


def _revision(db_path: Path) -> set[str]:
    with sqlite3.connect(str(db_path)) as conn:
        return {row[0] for row in conn.execute("SELECT version_num FROM alembic_version")}


def test_041_org_index_declared_in_orm():
    """ORM 侧也必须声明单列 organization_id 索引，否则 create_all 库与迁移库漂移。"""
    indexes = {idx.name for idx in Base.metadata.tables[TABLE].indexes}
    assert ORG_INDEX in indexes
    assert "ix_agent_artifact_org_sha256" in indexes
    assert "ix_agent_artifact_task_id" in indexes


def test_041_migration_source_guard():
    """041 downgrade 不得逐条 drop_index（MySQL 1553：drop_table 会随表删除索引）。"""
    migration = (BACKEND_ROOT / "alembic" / "versions" / "041_agent_artifact.py").read_text(
        encoding="utf-8"
    )
    downgrade = migration.split("def downgrade() -> None:", 1)[1]
    assert "op.drop_table(TABLE)" in downgrade
    assert "op.drop_index(" not in downgrade


def test_041_revision_chain():
    script = ScriptDirectory.from_config(_cfg())
    rev = script.get_revision(HEAD)
    assert rev.down_revision == PREVIOUS, "041 必须接在 040 之后"
    assert len(HEAD) <= 32, "MySQL alembic_version.version_num 是 VARCHAR(32)"
    assert script.get_revision(CURRENT_HEAD).down_revision == HEAD
    assert script.get_revision(LATEST_HEAD).down_revision == CURRENT_HEAD
    assert script.get_revision(FINAL_HEAD).down_revision == LATEST_HEAD
    assert set(script.get_heads()) == {FINAL_HEAD}, "044 必须是当前唯一 head"


def test_artifact_constants_are_closed_sets():
    """kind 集合与后端媒体映射同源；status 闭集只有 ready（v1 无异步写入态）。"""
    from app.services.artifact_service import KIND_MEDIA

    assert ARTIFACT_KINDS == ("text", "markdown", "json", "diff", "code", "html", "svg")
    assert set(KIND_MEDIA) == set(ARTIFACT_KINDS)
    assert ARTIFACT_STATUSES == ("ready",)


def test_041_schema_and_downgrade_roundtrip(tmp_path, monkeypatch):
    db_path = tmp_path / "agent-artifact-041.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = _cfg()

    command.upgrade(cfg, HEAD)
    with sqlite3.connect(str(db_path)) as conn:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert TABLE in tables
        # 040 的两张表不能被 041 的 upgrade 误删。
        assert {"review_redline_submission", "review_redline_row"} <= tables

        cols = {row[1]: row for row in conn.execute(f"PRAGMA table_info({TABLE})")}
        assert set(cols) == {c.name for c in Base.metadata.tables[TABLE].columns}, (
            "agent_artifact 迁移列与 ORM 模型漂移"
        )
        assert cols["created_at"][3] == 1, "created_at NOT NULL"
        # 默认值只能由 ORM/服务层提供，不能落到 DDL 的 server_default。
        assert cols["status"][4] is None
        assert cols["version_no"][4] is None
        assert cols["kind"][4] is None

        fks = {(row[2], row[3]) for row in conn.execute(f"PRAGMA foreign_key_list({TABLE})")}
        assert ("organizations", "organization_id") in fks
        assert ("users", "user_id") in fks
        assert (TABLE, "replaces_artifact_id") in fks
        # 会话/任务是溯源信息，生命周期独立 → 刻意不建外键。
        assert not any(local == "conversation_id" for _, local in fks)
        assert not any(local == "task_id" for _, local in fks)

        indexes = {row[1] for row in conn.execute(f"PRAGMA index_list({TABLE})")}
        assert {
            ORG_INDEX,
            "ix_agent_artifact_user_id",
            "ix_agent_artifact_conversation_id",
            "ix_agent_artifact_task_id",
            "ix_agent_artifact_replaces_artifact_id",
            "ix_agent_artifact_org_created",
            "ix_agent_artifact_org_sha256",
        } <= indexes
        org_index_cols = [
            info[2]
            for info in conn.execute(f"PRAGMA index_xinfo({ORG_INDEX})")
            if info[2] is not None
        ]
        assert org_index_cols == ["organization_id"]

        # 唯一约束 (task_id, source_call_id)：SQLite 渲染成 sqlite_autoindex_*。
        unique_cols = set()
        for index_row in conn.execute(f"PRAGMA index_list({TABLE})"):
            if index_row[2]:
                uniq = tuple(
                    info[2]
                    for info in conn.execute(f"PRAGMA index_xinfo({index_row[1]})")
                    if info[2] is not None
                )
                unique_cols.add(uniq)
        assert ("task_id", "source_call_id") in unique_cols

        defaults = Base.metadata.tables[TABLE]
        assert defaults.c.status.default.arg == "ready"
        assert defaults.c.version_no.default.arg == 1

    assert _revision(db_path) == {HEAD}

    command.downgrade(cfg, PREVIOUS)
    with sqlite3.connect(str(db_path)) as conn:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert TABLE not in tables
        assert {"review_redline_submission", "review_redline_row"} <= tables
    assert _revision(db_path) == {PREVIOUS}


def test_artifact_model_is_registered():
    """模型必须在 app.models 注册（create_all / check_schema 依赖它）。"""
    import app.models as models

    assert models.AgentArtifact is AgentArtifact
    assert "AgentArtifact" in models.__all__
