"""迁移 040：对方 DOCX 修订台账 schema 契约（真实 alembic 升级 + SQLite 读回）。

判别点：
- 040 接在 039 之后且单头；
- 两张表的列/索引/外键与 ORM 模型完全一致；
- ``evidence_id`` 无外键、``text_provenance`` 常量与 ORM 声明一致；
- ``lock_version`` 无 server_default（由服务层/ORM 提供）；
- downgrade 回滚到 039 后两张表消失，039 的表不受影响（round-trip 不残留）。
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
from app.models.review import (
    REVIEW_REDLINE_DISPOSITIONS,
    REVIEW_REDLINE_ROW_TYPES,
    REVIEW_REDLINE_SUBMISSION_STATUSES,
    REVIEW_REDLINE_TEXT_PROVENANCE,
)

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REVISION = "040_inbound_redline_ledger"
PREVIOUS = "039_review_template"
SUBMISSION = "review_redline_submission"
ROW = "review_redline_row"


SUBMISSION_INDEX = "ix_review_redline_submission_organization_id"


def _cfg() -> Config:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return cfg


def _revision(db_path: Path) -> set[str]:
    with sqlite3.connect(str(db_path)) as conn:
        return {row[0] for row in conn.execute("SELECT version_num FROM alembic_version")}


def test_040_submission_org_index_declared_in_orm():
    """ORM 侧也必须声明单列 organization_id 索引，否则 create_all 库与迁移库漂移。"""
    indexes = {idx.name for idx in Base.metadata.tables[SUBMISSION].indexes}
    assert SUBMISSION_INDEX in indexes


def test_040_migration_source_guard():
    """040 downgrade 不得逐条 drop_index（MySQL 1553：drop_table 会随表删除索引）。

    先例：035 的同款源码扫描（test_retrieval_eval_run_history.py:518）。
    """
    migration = (BACKEND_ROOT / "alembic" / "versions" / "040_inbound_redline_ledger.py").read_text(
        encoding="utf-8"
    )
    downgrade = migration.split("def downgrade() -> None:", 1)[1]
    assert "op.drop_table(ROW)" in downgrade
    assert "op.drop_table(SUBMISSION)" in downgrade
    assert "op.drop_index(" not in downgrade


def test_040_revision_chain():
    script = ScriptDirectory.from_config(_cfg())
    rev = script.get_revision(REVISION)
    assert rev.down_revision == PREVIOUS
    assert len(REVISION) <= 32, "MySQL alembic_version.version_num 是 VARCHAR(32)"
    # 040 不再是全链 head（批次七在它之后追加迁移），但必须仍在链上且只有一个 head。
    assert REVISION in {r.revision for r in script.walk_revisions()}
    assert len(script.get_heads()) == 1


def test_revision_constants_match_contract():
    """枚举常量与溯源标记必须与写接口契约一致，防止有人就地改字面量。"""
    assert REVIEW_REDLINE_SUBMISSION_STATUSES == ("parsed", "partial")
    assert REVIEW_REDLINE_ROW_TYPES == ("insertion", "deletion", "comment", "unparsed")
    assert REVIEW_REDLINE_DISPOSITIONS == (
        "pending",
        "reviewed",
        "needs_clarification",
        "not_relevant",
    )
    assert REVIEW_REDLINE_TEXT_PROVENANCE == "counterparty_docx_ooxml"
    assert "parser_original" not in REVIEW_REDLINE_TEXT_PROVENANCE


def test_040_schema_and_downgrade_roundtrip(tmp_path, monkeypatch):
    db_path = tmp_path / "redline-ledger-040.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = _cfg()

    command.upgrade(cfg, REVISION)
    with sqlite3.connect(str(db_path)) as conn:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {SUBMISSION, ROW, "review_template", "review_template_version"} <= tables

        for table in (SUBMISSION, ROW):
            cols = {row[1]: row for row in conn.execute(f"PRAGMA table_info({table})")}
            assert set(cols) == {c.name for c in Base.metadata.tables[table].columns}, (
                f"{table} 迁移列与 ORM 模型漂移"
            )
            assert cols["created_at"][3] == 1, f"{table}.created_at NOT NULL"

        submission_cols = {row[1]: row for row in conn.execute(f"PRAGMA table_info({SUBMISSION})")}
        # lock_version 只能由 ORM/服务层提供默认值，不能落到 DDL 的 server_default。
        assert submission_cols["status"][4] is None
        row_cols = {row[1]: row for row in conn.execute(f"PRAGMA table_info({ROW})")}
        assert row_cols["lock_version"][4] is None
        assert row_cols["disposition"][4] is None

        submission_fks = {
            (row[2], row[3]) for row in conn.execute(f"PRAGMA foreign_key_list({SUBMISSION})")
        }
        assert ("organizations", "organization_id") in submission_fks
        assert ("review_case", "case_id") in submission_fks
        assert ("users", "uploaded_by") in submission_fks

        row_fks = {(row[2], row[3]) for row in conn.execute(f"PRAGMA foreign_key_list({ROW})")}
        assert (SUBMISSION, "submission_id") in row_fks
        assert ("review_case", "case_id") in row_fks
        assert ("organizations", "organization_id") in row_fks
        assert ("users", "disposition_by") in row_fks
        assert ("review_item", "item_id") in row_fks
        # evidence_id 生命周期独立，刻意不建外键。
        assert not any(local == "evidence_id" for _, local in row_fks)

        submission_indexes = {row[1] for row in conn.execute(f"PRAGMA index_list({SUBMISSION})")}
        assert {
            SUBMISSION_INDEX,
            "ix_review_redline_submission_org_created",
            "ix_review_redline_submission_case_created",
        } <= submission_indexes
        # 单列 organization_id 索引是 create_all（ORM index=True）与迁移库的
        # parity 项；删掉迁移或模型里的索引定义，本断言必须红。
        org_index_cols = [
            info[2]
            for info in conn.execute(f"PRAGMA index_xinfo({SUBMISSION_INDEX})")
            if info[2] is not None
        ]
        assert org_index_cols == ["organization_id"]
        # SQLite 把命名的 UniqueConstraint 渲染成 sqlite_autoindex_*，按列+唯一性
        # 反查，避免把方言差异误判为迁移漏建约束。
        unique_index_cols = set()
        for index_row in conn.execute(f"PRAGMA index_list({SUBMISSION})"):
            if index_row[2]:  # unique=1
                cols = tuple(
                    info[2]
                    for info in conn.execute(f"PRAGMA index_xinfo({index_row[1]})")
                    if info[2] is not None
                )
                unique_index_cols.add(cols)
        assert ("case_id", "sequence_no") in unique_index_cols

        row_indexes = {row[1] for row in conn.execute(f"PRAGMA index_list({ROW})")}
        assert {
            "ix_review_redline_row_submission_ordinal",
            "ix_review_redline_row_case_item",
            "ix_review_redline_row_org_created",
            "ix_review_redline_row_organization_id",
        } <= row_indexes

        provenance = Base.metadata.tables[ROW].c.text_provenance
        assert provenance.default.arg == REVIEW_REDLINE_TEXT_PROVENANCE

    assert _revision(db_path) == {REVISION}

    command.downgrade(cfg, PREVIOUS)
    with sqlite3.connect(str(db_path)) as conn:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert SUBMISSION not in tables
        assert ROW not in tables
        # 039 的表不能被 040 的 downgrade 误删。
        assert {"review_template", "review_template_version"} <= tables
    assert _revision(db_path) == {PREVIOUS}
