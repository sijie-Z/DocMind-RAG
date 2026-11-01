"""主键冗余二级索引清理（Batch 13）：元数据守卫 + 迁移 026 往返/幂等。

- 元数据守卫：任何模型的 PK 列都不得再声明 ``index=True``（create_all 路径
  不再产生与 PRIMARY 重复的二级索引）；
- 迁移 026：迁移路径上由 003/004/005/008/011 建立的 14 个 ``ix_<table>_id``
  被幂等删除，downgrade 忠实恢复；重复执行不因 1091/1061 失败。
"""

import sqlite3
from pathlib import Path

import pytest
from alembic.config import Config

import app.models  # noqa: F401  # 必须导入以填充 Base.metadata
from alembic import command
from app.core.config import settings
from app.core.database import Base

BACKEND_DIR = Path(__file__).resolve().parents[2]
PREV_REV = "025_drop_documents_id_index"

# 与 026 迁移的清单保持一致（迁移路径上真实存在的冗余索引）。
REDUNDANT = (
    ("agent_tasks", "ix_agent_tasks_id"),
    ("knowledge_processing_jobs", "ix_knowledge_processing_jobs_id"),
    ("notifications", "ix_notifications_id"),
    ("organization_settings", "ix_organization_settings_id"),
    ("permissions", "ix_permissions_id"),
    ("prompt_template_versions", "ix_prompt_template_versions_id"),
    ("prompt_templates", "ix_prompt_templates_id"),
    ("roles", "ix_roles_id"),
    ("scheduled_tasks", "ix_scheduled_tasks_id"),
    ("system_manuals", "ix_system_manuals_id"),
    ("token_usage_records", "ix_token_usage_records_id"),
    ("user_activity_logs", "ix_user_activity_logs_id"),
    ("user_login_sessions", "ix_user_login_sessions_id"),
    ("user_settings", "ix_user_settings_id"),
)


def _cfg() -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    return cfg


def _index_names(db_path: Path, table: str) -> set[str]:
    with sqlite3.connect(str(db_path)) as conn:
        return {row[1] for row in conn.execute(f"PRAGMA index_list('{table}')")}


def test_no_primary_key_column_declares_secondary_index():
    offenders = [
        f"{table_name}.{column.name}"
        for table_name, table in sorted(Base.metadata.tables.items())
        for column in table.primary_key.columns
        if column.index
    ]
    assert offenders == [], "主键列不得声明 index=True（与 PRIMARY 完全冗余）"


def test_026_upgrade_drops_and_downgrade_restores(tmp_path, monkeypatch):
    db_path = tmp_path / "pk-indexes-026.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = _cfg()

    command.upgrade(cfg, PREV_REV)
    for table, index in REDUNDANT:
        assert index in _index_names(db_path, table), (table, index)

    command.upgrade(cfg, "head")
    for table, index in REDUNDANT:
        assert index not in _index_names(db_path, table), (table, index)

    command.downgrade(cfg, PREV_REV)
    for table, index in REDUNDANT:
        assert index in _index_names(db_path, table), (table, index)


@pytest.mark.parametrize(("table", "index"), [REDUNDANT[0], REDUNDANT[-1]])
def test_026_upgrade_is_idempotent_when_index_already_dropped(tmp_path, monkeypatch, table, index):
    """MySQL DDL 非事务：上次已删索引但版本戳未推进时，重跑不得 1091 失败。"""
    db_path = tmp_path / f"pk-indexes-026-idem-{table}.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = _cfg()

    command.upgrade(cfg, PREV_REV)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(f"DROP INDEX {index}")
    command.upgrade(cfg, "head")
    assert index not in _index_names(db_path, table)
