"""迁移 049/050：公开知识库发布与聊天 schema 契约（真实 alembic 升级 + SQLite 读回）。

判别点：
- 050 接在 049 之后、全链单头；revision id ≤ 32（MySQL VARCHAR(32)）；
- 050 只给 ``kb_publication`` 增加 ``chat_enabled`` / ``daily_token_budget``：
  前者 NOT NULL DEFAULT 0（默认关闭），后者可空（NULL = 全局默认）；
- ``kb_publication`` / ``document_publication`` 的列/索引/唯一键/外键与 ORM
  模型完全一致；
- ``token_hash`` 全局唯一；``(publication_id, document_id)`` 唯一；
- ``kb_publication.knowledge_base_id`` **刻意无外键**（知识库删除不被发布记录
  阻塞），而 ``document_publication.document_id`` 有外键（文档删除链路清理）；
- downgrade 050 → 049 只删两列，049 的表/索引/白名单不受影响；源码不得逐条
  drop_index（MySQL 1553）。
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
HEAD = "050_public_kb_chat"
PREVIOUS = "049_public_kb"
PUBLICATION = "kb_publication"
DOCUMENT_PUBLICATION = "document_publication"
CHAT_COLUMNS = {"chat_enabled", "daily_token_budget"}

PUBLICATION_INDEXES = {
    "ix_kb_publication_organization_id",
    "ix_kb_publication_knowledge_base_id",
}
DOCUMENT_INDEXES = {
    "ix_document_publication_organization_id",
    "ix_document_publication_publication_id",
    "ix_document_publication_document_id",
}


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


def _columns(db_path: Path, table: str) -> dict[str, tuple]:
    with sqlite3.connect(str(db_path)) as conn:
        return {row[1]: row for row in conn.execute(f"PRAGMA table_info({table})")}


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


def test_050_revision_chain():
    script = ScriptDirectory.from_config(_cfg())
    rev = script.get_revision(HEAD)
    assert rev.down_revision == PREVIOUS
    assert len(HEAD) <= 32, "MySQL alembic_version.version_num 是 VARCHAR(32)"
    assert set(script.get_heads()) == {HEAD}, "批次十七后唯一 head 是 050"


def test_050_downgrade_drops_only_new_columns():
    """downgrade 只删 050 追加的列；不得 drop_index/表（MySQL 1553）。"""
    migration = (BACKEND_ROOT / "alembic" / "versions" / f"{HEAD}.py").read_text(encoding="utf-8")
    downgrade = migration.split("def downgrade() -> None:", 1)[1]
    assert "op.drop_column(PUBLICATION, name)" in downgrade
    assert "op.drop_table(" not in downgrade
    assert "op.drop_index(" not in downgrade
    # 51 字符 feature 键需要 VARCHAR(64)：upgrade 必须在 MySQL 方言加宽列。
    assert "op.alter_column(" in migration
    assert "TOKEN_USAGE" in migration
    # 049 自己的 downgrade 仍负责删表（历史行为不被 050 改写）。
    previous = (BACKEND_ROOT / "alembic" / "versions" / f"{PREVIOUS}.py").read_text(
        encoding="utf-8"
    )
    previous_downgrade = previous.split("def downgrade() -> None:", 1)[1]
    assert "op.drop_table(DOCUMENT_PUBLICATION)" in previous_downgrade
    assert previous_downgrade.index("op.drop_table(DOCUMENT_PUBLICATION)") < (
        previous_downgrade.index("op.drop_table(PUBLICATION)")
    )


def test_050_schema_and_downgrade_roundtrip(tmp_path, monkeypatch):
    """升级读回列/FK/索引/唯一键；回滚到 049 后新列消失且 049 对象仍在。"""
    db_path = tmp_path / "public-kb-050.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = _cfg()

    command.upgrade(cfg, HEAD)
    assert _revision(db_path) == {HEAD}
    tables = _tables(db_path)
    assert {PUBLICATION, DOCUMENT_PUBLICATION} <= tables
    # 050 之前的对象不能被本迁移误删。
    assert {"chat_sessions", "documents", "organizations", "users"} <= tables
    assert "share_expires_at" in _columns(db_path, "chat_sessions")

    with sqlite3.connect(str(db_path)) as conn:
        publication_cols = _columns(db_path, PUBLICATION)
        document_cols = _columns(db_path, DOCUMENT_PUBLICATION)
        assert set(publication_cols) == {
            c.name for c in Base.metadata.tables[PUBLICATION].columns
        }, "kb_publication 迁移列与 ORM 模型漂移"
        assert set(publication_cols) >= CHAT_COLUMNS
        assert set(document_cols) == {
            c.name for c in Base.metadata.tables[DOCUMENT_PUBLICATION].columns
        }, "document_publication 迁移列与 ORM 模型漂移"

        for name in (
            "id",
            "organization_id",
            "knowledge_base_id",
            "token_hash",
            "title",
            "enabled",
            "chat_enabled",
            "created_by",
            "created_at",
        ):
            assert publication_cols[name][3] == 1, f"kb_publication.{name} NOT NULL"
        for name in ("description", "daily_token_budget", "expires_at", "revoked_at"):
            assert publication_cols[name][3] == 0, f"kb_publication.{name} 必须可空"
        assert publication_cols["created_at"][4] is not None
        # 050：chat_enabled 需要 server_default 0 为存量行回填「默认关闭」；
        # 其余非 created_at 列仍不得有 server_default（默认值由服务层/ORM 提供）。
        assert publication_cols["chat_enabled"][4] is not None
        for name, row in publication_cols.items():
            if name not in ("created_at", "chat_enabled"):
                assert row[4] is None, f"kb_publication.{name} 不应有 server_default"

        for name in (
            "id",
            "organization_id",
            "publication_id",
            "document_id",
            "created_by",
            "created_at",
        ):
            assert document_cols[name][3] == 1, f"document_publication.{name} NOT NULL"
        assert document_cols["created_at"][4] is not None
        for name, row in document_cols.items():
            if name != "created_at":
                assert row[4] is None, f"document_publication.{name} 不应有 server_default"

        publication_fks = {
            (row[2], row[3]) for row in conn.execute(f"PRAGMA foreign_key_list({PUBLICATION})")
        }
        assert ("organizations", "organization_id") in publication_fks
        assert ("users", "created_by") in publication_fks
        # 刻意无外键：知识库删除不被发布记录阻塞（悬空发布会由检索侧重新校验 KB）。
        assert all(column != "knowledge_base_id" for _table, column in publication_fks), (
            "kb_publication.knowledge_base_id 不得建外键"
        )

        document_fks = {
            (row[2], row[3])
            for row in conn.execute(f"PRAGMA foreign_key_list({DOCUMENT_PUBLICATION})")
        }
        assert ("organizations", "organization_id") in document_fks
        assert (PUBLICATION, "publication_id") in document_fks
        assert ("documents", "document_id") in document_fks
        assert ("users", "created_by") in document_fks

        publication_indexes = {row[1] for row in conn.execute(f"PRAGMA index_list({PUBLICATION})")}
        assert publication_indexes >= PUBLICATION_INDEXES
        document_indexes = {
            row[1] for row in conn.execute(f"PRAGMA index_list({DOCUMENT_PUBLICATION})")
        }
        assert document_indexes >= DOCUMENT_INDEXES
        assert ("token_hash",) in _unique_column_sets(conn, PUBLICATION)
        assert ("publication_id", "document_id") in _unique_column_sets(conn, DOCUMENT_PUBLICATION)

    command.downgrade(cfg, PREVIOUS)
    assert _revision(db_path) == {PREVIOUS}
    tables = _tables(db_path)
    # 049 的表仍在；只缺 050 追加的两列。
    assert PUBLICATION in tables and DOCUMENT_PUBLICATION in tables
    publication_cols = _columns(db_path, PUBLICATION)
    assert not (CHAT_COLUMNS & set(publication_cols))
    assert {"token_hash", "title", "enabled"} <= set(publication_cols)
    assert set(_columns(db_path, DOCUMENT_PUBLICATION)) == {
        c.name for c in Base.metadata.tables[DOCUMENT_PUBLICATION].columns
    }
    # 048/049 的对象与列必须原样保留。
    assert "chat_sessions" in tables
    assert "share_expires_at" in _columns(db_path, "chat_sessions")


def test_050_upgrade_is_idempotent(tmp_path, monkeypatch):
    """半迁移（列已存在、版本仍停在 049）重复 upgrade 必须跳过加列。"""
    db_path = tmp_path / "public-kb-050-idempotent.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = _cfg()
    command.upgrade(cfg, HEAD)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("UPDATE alembic_version SET version_num = '049_public_kb'")
        conn.commit()
    command.upgrade(cfg, HEAD)
    assert _revision(db_path) == {HEAD}
    assert set(_columns(db_path, PUBLICATION)) >= CHAT_COLUMNS


def test_050_backfills_existing_rows_as_chat_disabled(tmp_path, monkeypatch):
    """049 存量发布升级后必须是 chat_enabled=0、daily_token_budget=NULL。

    变异：迁移默认值写成 1（或 NOT NULL 无默认）→ 本用例红。
    """
    db_path = tmp_path / "public-kb-050-backfill.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = _cfg()
    command.upgrade(cfg, PREVIOUS)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO kb_publication "
            "(id, organization_id, knowledge_base_id, token_hash, title, enabled, created_by) "
            "VALUES ('legacy-pub', 1, 'kb-main', 'legacy-hash', '存量发布', 1, 1)"
        )
        conn.commit()
    command.upgrade(cfg, HEAD)
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT chat_enabled, daily_token_budget FROM kb_publication WHERE id='legacy-pub'"
        ).fetchone()
    assert row == (0, None)


def test_049_models_are_registered_and_protected():
    """模型注册 / 组织删除保护 / 文档删除清理清单三处都不能漏。"""
    import app.models as models
    import app.services.knowledge_service as knowledge_service
    import app.services.organization_service as organization_service
    from app.models.public_kb import DocumentPublication, KbPublication

    assert models.KbPublication is KbPublication
    assert models.DocumentPublication is DocumentPublication
    assert "KbPublication" in models.__all__
    assert "DocumentPublication" in models.__all__

    attached = {
        model for _label, model in organization_service.OrganizationService._ATTACHED_DATA_MODELS
    }
    assert KbPublication in attached
    assert DocumentPublication in attached

    child_models = set(knowledge_service._DOCUMENT_CHILD_MODELS)
    assert DocumentPublication in child_models, (
        "document_publication.document_id 外键必须在文档彻底删除时被清理（MySQL 1451）"
    )


def test_050_doc_facts_counts_public_router_decorators():
    """facts 派生必须覆盖 ``public_router``：只认 ``router`` 会静默漏算公开端点。"""
    import importlib.util
    import sys

    script_path = BACKEND_ROOT / "scripts" / "check_doc_facts.py"
    spec = importlib.util.spec_from_file_location("check_doc_facts_public_kb", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    http, _websocket = module.derive_route_decorators(
        BACKEND_ROOT / "app" / "api" / "v1" / "endpoints"
    )
    handlers = {entry.split(":", 1)[1] for entry in http}
    expected = {
        "create_publication",
        "list_publications",
        "list_publication_candidates",
        "get_publication_status",
        "rotate_publication",
        "revoke_publication",
        "update_publication_chat_settings",
        "set_publication_documents",
        "list_publication_documents",
        "get_public_kb",
        "search_public_kb",
        "chat_public_kb",
    }
    assert expected <= handlers
    assert len(expected) == 12
