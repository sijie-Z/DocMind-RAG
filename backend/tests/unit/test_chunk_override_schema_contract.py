"""迁移 036 的真实 schema 契约与模型注册护栏。

MySQL parity 的 check_schema.py 只比列，删索引/FK 仍可能 GREEN；本文件在
SQLite 上跑完整 alembic upgrade head 后用 inspector 读回 index / unique /
FK，任一被删都必须变红。registry 用例保证 model/cleanup 清单没有漂移。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import sqlalchemy as sa
from alembic.config import Config

import app.models  # noqa: F401 — 注册全部表
from alembic import command
from app.core.config import settings
from app.core.database import Base
from app.models.document import DocumentChunkOverride
from app.services import knowledge_service as ks_mod

TABLE = "document_chunk_overrides"
EXPECTED_INDEXES = {
    "ix_document_chunk_overrides_document_chunk_id",
    "ix_document_chunk_overrides_document_id",
    "ix_document_chunk_overrides_organization_id",
}
EXPECTED_FKS = {
    "document_chunks",
    "documents",
    "organizations",
    "users",
}


def _alembic_config(db_path: Path) -> Config:
    backend = Path(__file__).resolve().parents[2]
    config = Config(str(backend / "alembic.ini"))
    config.set_main_option("script_location", str(backend / "alembic"))
    return config


def test_migration_036_indexes_and_foreign_keys_are_persisted(tmp_path, monkeypatch):
    db_path = tmp_path / "chunk_override_schema.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    command.upgrade(_alembic_config(db_path), "head")

    engine = sa.create_engine(f"sqlite:///{db_path}")
    try:
        inspector = sa.inspect(engine)
        assert TABLE in inspector.get_table_names()
        indexes = {index["name"] for index in inspector.get_indexes(TABLE)}
        assert indexes >= EXPECTED_INDEXES, f"缺少迁移 036 的显式索引: {EXPECTED_INDEXES - indexes}"
        uniques = {
            tuple(constraint["column_names"])
            for constraint in inspector.get_unique_constraints(TABLE)
        }
        assert ("document_chunk_id",) in uniques, "缺 uq_document_chunk_override_chunk 唯一约束"
        targets = {fk["referred_table"] for fk in inspector.get_foreign_keys(TABLE)}
        assert targets >= EXPECTED_FKS, f"缺迁移 036 的外键: {EXPECTED_FKS - targets}"
    finally:
        engine.dispose()
    # 不依赖 inspector 的轻量交叉检查：PRAGMA 直接读回。
    with sqlite3.connect(str(db_path)) as conn:
        pragma_fks = conn.execute(f"PRAGMA foreign_key_list({TABLE})").fetchall()
    assert {row[2] for row in pragma_fks} >= EXPECTED_FKS


def test_override_model_is_registered_and_in_document_cleanup_list():
    """registry 与删除清理清单是独立契约，不能被迁移 schema 状态掩盖。"""
    assert DocumentChunkOverride.__tablename__ == TABLE
    assert TABLE in Base.metadata.tables
    assert DocumentChunkOverride in ks_mod._DOCUMENT_CHILD_MODELS
    assert {model.__tablename__ for model in ks_mod._DOCUMENT_CHILD_MODELS} >= {TABLE}
