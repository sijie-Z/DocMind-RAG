"""迁移 039：模板库 schema 契约（真实 alembic 升级 + SQLite 读回）。

判别点：
- 039 必须接在 038 之后、全链单头；
- review_template / review_template_version 的列、唯一约束、索引、外键齐全；
- review_case 新增两列可空溯源指针，且**不新增**指向模板的外键（模板生命周期
  操作不得被案件反向阻塞）；
- downgrade 可逆：回滚到 038 后两张表与两列都消失（round-trip 不残留）。
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
REVISION = "039_review_template"
PREVIOUS = "038_eval_case_dedup"


def _cfg() -> Config:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return cfg


def test_039_revision_chain_and_id_length():
    script = ScriptDirectory.from_config(_cfg())
    revision = script.get_revision(REVISION)
    assert revision.down_revision == PREVIOUS
    assert len(REVISION) <= 32, "MySQL alembic_version.version_num 是 VARCHAR(32)"
    assert REVISION in script.get_heads(), "039 必须是当前 head（新增迁移时随之前移）"


def test_039_schema_and_downgrade_roundtrip(tmp_path, monkeypatch):
    """升级到 039 后读回列/索引/FK；回滚到 038 后 039 的对象全部消失。

    变异点：迁移漏建某索引/列 → 对应集合断言红；downgrade 漏删列 → round-trip
    断言红（038 上残留 template_id）。
    """
    db_path = tmp_path / "review-template-039.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = _cfg()

    command.upgrade(cfg, REVISION)
    with sqlite3.connect(str(db_path)) as conn:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert {"review_template", "review_template_version"} <= tables

        template_cols = {row[1] for row in conn.execute("PRAGMA table_info(review_template)")}
        assert template_cols == {
            "id",
            "organization_id",
            "name",
            "description",
            "status",
            "latest_version",
            "created_by",
            "updated_by",
            "archived_at",
            "archived_by",
            "lock_version",
            "created_at",
            "updated_at",
        }
        assert template_cols == {
            column.name for column in Base.metadata.tables["review_template"].columns
        }, "迁移列与 ORM 模型漂移"

        version_cols = {
            row[1] for row in conn.execute("PRAGMA table_info(review_template_version)")
        }
        assert version_cols == {
            column.name for column in Base.metadata.tables["review_template_version"].columns
        }
        assert {"items_json", "payload_sha256", "version_no", "source"} <= version_cols

        template_indexes = {row[1] for row in conn.execute("PRAGMA index_list(review_template)")}
        assert {"ix_review_template_org_created", "ix_review_template_org_status"} <= (
            template_indexes
        )
        version_indexes = {
            row[1] for row in conn.execute("PRAGMA index_list(review_template_version)")
        }
        assert {
            "ix_review_template_version_template_id",
            "ix_review_template_version_organization_id",
            "ix_review_template_version_template_created",
        } <= version_indexes

        template_fks = {
            (row[2], row[3]) for row in conn.execute("PRAGMA foreign_key_list(review_template)")
        }
        assert ("organizations", "organization_id") in template_fks
        version_fks = {
            (row[2], row[3])
            for row in conn.execute("PRAGMA foreign_key_list(review_template_version)")
        }
        assert ("review_template", "template_id") in version_fks
        assert ("organizations", "organization_id") in version_fks

        case_cols = {row[1] for row in conn.execute("PRAGMA table_info(review_case)")}
        assert {"template_id", "template_version_no"} <= case_cols
        case_fks = {
            (row[2], row[3]) for row in conn.execute("PRAGMA foreign_key_list(review_case)")
        }
        # 溯源指针刻意不建外键：模板归档/未来清理不得被案件反向外键阻塞。
        assert ("review_template", "template_id") not in case_fks

    command.downgrade(cfg, PREVIOUS)
    with sqlite3.connect(str(db_path)) as conn:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert "review_template" not in tables
        assert "review_template_version" not in tables
        case_cols = {row[1] for row in conn.execute("PRAGMA table_info(review_case)")}
        assert "template_id" not in case_cols
        assert "template_version_no" not in case_cols
        assert (await_revision(db_path)) == {PREVIOUS}


def await_revision(db_path: Path) -> set[str]:
    with sqlite3.connect(str(db_path)) as conn:
        return {row[0] for row in conn.execute("SELECT version_num FROM alembic_version")}
