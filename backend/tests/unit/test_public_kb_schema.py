"""迁移 049：公开知识库发布 schema 契约（真实 alembic 升级 + SQLite 读回）。

判别点：
- 049 接在 048 之后、全链单头；revision id ≤ 32（MySQL VARCHAR(32)）；
- ``kb_publication`` / ``document_publication`` 的列/索引/唯一键/外键与 ORM
  模型完全一致；除 ``created_at`` 外无 server_default；
- ``token_hash`` 全局唯一；``(publication_id, document_id)`` 唯一；
- ``kb_publication.knowledge_base_id`` **刻意无外键**（知识库删除不被发布记录
  阻塞），而 ``document_publication.document_id`` 有外键（文档删除链路清理）；
- 两张表都纳入组织删除保护；白名单表纳入文档子表清理清单；
- downgrade 回滚到 048 后两表消失，048 的对象不受影响；源码不得逐条 drop_index。
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
HEAD = "049_public_kb"
PREVIOUS = "048_session_share_expiry"
PUBLICATION = "kb_publication"
DOCUMENT_PUBLICATION = "document_publication"

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


def test_049_revision_chain():
    script = ScriptDirectory.from_config(_cfg())
    rev = script.get_revision(HEAD)
    assert rev.down_revision == PREVIOUS
    assert len(HEAD) <= 32, "MySQL alembic_version.version_num 是 VARCHAR(32)"
    assert set(script.get_heads()) == {HEAD}, "批次十三后唯一 head 是 049"


def test_049_downgrade_does_not_drop_indexes_before_tables():
    """downgrade 不得逐条 drop_index（MySQL 1553：drop_table 会随表删除索引）。"""
    migration = (BACKEND_ROOT / "alembic" / "versions" / f"{HEAD}.py").read_text(encoding="utf-8")
    downgrade = migration.split("def downgrade() -> None:", 1)[1]
    assert "op.drop_table(DOCUMENT_PUBLICATION)" in downgrade
    assert "op.drop_table(PUBLICATION)" in downgrade
    # 必须先删子表（FK 指向 kb_publication）再删父表；顺序颠倒会在 MySQL 1553/1217 失败。
    assert downgrade.index("op.drop_table(DOCUMENT_PUBLICATION)") < downgrade.index(
        "op.drop_table(PUBLICATION)"
    )
    assert "op.drop_index(" not in downgrade


def test_049_schema_and_downgrade_roundtrip(tmp_path, monkeypatch):
    """升级读回列/FK/索引/唯一键；回滚到 048 后两表消失且 048 对象仍在。"""
    db_path = tmp_path / "public-kb-049.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = _cfg()

    command.upgrade(cfg, HEAD)
    assert _revision(db_path) == {HEAD}
    tables = _tables(db_path)
    assert {PUBLICATION, DOCUMENT_PUBLICATION} <= tables
    # 048 的对象不能被本迁移误删。
    assert {"chat_sessions", "documents", "organizations", "users"} <= tables
    assert "share_expires_at" in _columns(db_path, "chat_sessions")

    with sqlite3.connect(str(db_path)) as conn:
        publication_cols = _columns(db_path, PUBLICATION)
        document_cols = _columns(db_path, DOCUMENT_PUBLICATION)
        assert set(publication_cols) == {
            c.name for c in Base.metadata.tables[PUBLICATION].columns
        }, "kb_publication 迁移列与 ORM 模型漂移"
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
            "created_by",
            "created_at",
        ):
            assert publication_cols[name][3] == 1, f"kb_publication.{name} NOT NULL"
        for name in ("description", "expires_at", "revoked_at"):
            assert publication_cols[name][3] == 0, f"kb_publication.{name} 必须可空"
        assert publication_cols["created_at"][4] is not None
        for name, row in publication_cols.items():
            if name != "created_at":
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
    assert PUBLICATION not in tables and DOCUMENT_PUBLICATION not in tables
    # 048 的对象与列必须原样保留。
    assert "chat_sessions" in tables
    assert "share_expires_at" in _columns(db_path, "chat_sessions")


def test_049_upgrade_is_idempotent(tmp_path, monkeypatch):
    """半迁移（表已存在、版本仍停在 048）重复 upgrade 必须跳过建表。"""
    db_path = tmp_path / "public-kb-049-idempotent.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = _cfg()
    command.upgrade(cfg, HEAD)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("UPDATE alembic_version SET version_num = '048_session_share_expiry'")
        conn.commit()
    command.upgrade(cfg, HEAD)
    assert _revision(db_path) == {HEAD}
    assert {PUBLICATION, DOCUMENT_PUBLICATION} <= _tables(db_path)


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


def test_049_doc_facts_counts_public_router_decorators():
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
        "set_publication_documents",
        "list_publication_documents",
        "get_public_kb",
        "search_public_kb",
    }
    assert expected <= handlers
    assert len(expected) == 10
