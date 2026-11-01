"""主键冗余二级索引清理（Batch 13）：元数据守卫 + 迁移 026 往返/幂等。

- 元数据守卫：任何模型的 PK 首列都不得再声明二级索引（列级 ``index=True``
  与显式 ``Index()`` 两种方式都覆盖；复合 PK 的非首列可能是业务需要）；
- 迁移 026：迁移路径上由 003/004/005/008/011 建立的 14 个 ``ix_<table>_id``
  被幂等删除，downgrade 忠实恢复；重复执行不因 1091/1061 失败；
- 往返用「全库索引快照集合差」断言：升级恰好删掉清单内的 14 个（多删任何
  真实业务索引会变红）、且降级精确恢复到升级前快照。
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
TARGET_REV = "026_drop_redundant_pk_indexes"

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


def _index_snapshot(db_path: Path) -> dict[str, set[str]]:
    """全库索引快照（表 → 索引名集合，排除 sqlite 自动索引）。

    用集合差而不是抽样断言：升级「恰好删掉清单里的 14 个」、降级
    「精确恢复升级前快照」——任何多删/少删业务索引都会变红（评审 M13）。
    """
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(
            "SELECT tbl_name, name FROM sqlite_master "
            "WHERE type='index' AND name NOT LIKE 'sqlite_autoindex_%'"
        ).fetchall()
    snapshot: dict[str, set[str]] = {}
    for table, name in rows:
        snapshot.setdefault(table, set()).add(name)
    return snapshot


def _index_definition(db_path: Path, table: str, name: str) -> tuple[bool, list[str]]:
    """索引定义：(是否唯一, 列序)。只断言名字会放过列/唯一性变异。"""
    with sqlite3.connect(str(db_path)) as conn:
        unique = any(
            row[1] == name and row[2] == 1 for row in conn.execute(f"PRAGMA index_list('{table}')")
        )
        columns = [row[2] for row in conn.execute(f"PRAGMA index_info('{name}')")]
    return unique, columns


# 只在 create_all 路径出现过的 PK 冗余索引：迁移路径从没建过它们，
# 模型侧移除 index=True 后两条路径都应为零。
CREATE_ALL_ONLY = (
    ("chat_messages", "ix_chat_messages_id"),
    ("chat_sessions", "ix_chat_sessions_id"),
    ("document_chunks", "ix_document_chunks_id"),
    ("document_tags", "ix_document_tags_id"),
    ("organizations", "ix_organizations_id"),
    ("tags", "ix_tags_id"),
    ("users", "ix_users_id"),
)
ALL_PK_ID_INDEXES = REDUNDANT + CREATE_ALL_ONLY


def _pk_index_offenders(metadata) -> list[str]:
    """PK 前缀上的二级索引：列级 index=True 或显式 Index() 都是冗余。

    命中规则是「索引列序列 == PK 列序列的前缀（长度 ≥1）」：
    - 单列 PK 上的 Index(id) 命中；
    - 复合 PK 上的 Index(a)、Index(a, b) 命中（与 PRIMARY 或其左前缀重复）；
    - 复合 PK 的非首列 Index(b) 放行（可能是业务需要）。
    """
    offenders: list[str] = []
    for table_name, table in sorted(metadata.tables.items()):
        pk_columns = list(table.primary_key.columns)
        if not pk_columns:
            continue
        pk_names = [column.name for column in pk_columns]
        if pk_columns[0].index:
            offenders.append(f"{table_name}.{pk_names[0]} (index=True)")
        for index in table.indexes:
            columns = [column.name for column in index.columns]
            if columns and columns == pk_names[: len(columns)]:
                offenders.append(f"{table_name}.{index.name} (Index on PK prefix)")
    return offenders


def test_primary_key_prefix_has_no_secondary_index():
    assert _pk_index_offenders(Base.metadata) == [], (
        "PK 前缀列不得声明二级索引（与 PRIMARY 完全冗余）"
    )


def test_pk_prefix_guard_semantics_on_synthetic_metadata():
    """用合成元数据正面锁住守卫两个方向的语义（当前模型不触发这些分支）。"""
    from sqlalchemy import Column, Index, Integer, MetaData, String, Table

    metadata = MetaData()
    Table("single", metadata, Column("id", Integer, primary_key=True, index=True))
    composite = Table(
        "composite",
        metadata,
        Column("a", String(10), primary_key=True),
        Column("b", Integer, primary_key=True),
    )
    Index("ix_composite_pair", composite.c.a, composite.c.b)
    Index("ix_composite_second", composite.c.b)

    offenders = _pk_index_offenders(metadata)
    assert "single.id (index=True)" in offenders
    assert "composite.ix_composite_pair (Index on PK prefix)" in offenders
    assert not any("ix_composite_second" in item for item in offenders)


def test_026_upgrade_drops_and_downgrade_restores(tmp_path, monkeypatch):
    db_path = tmp_path / "pk-indexes-026.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = _cfg()

    command.upgrade(cfg, PREV_REV)
    snapshot_before = _index_snapshot(db_path)
    for table, index in REDUNDANT:
        assert index in snapshot_before.get(table, set()), (table, index)

    command.upgrade(cfg, TARGET_REV)
    snapshot_head = _index_snapshot(db_path)
    disappeared = {
        (table, index)
        for table, indexes in snapshot_before.items()
        for index in indexes
        if index not in snapshot_head.get(table, set())
    }
    # 恰好删掉清单内的 14 个——多删任何真实业务索引（或清单挂错表）都会变红
    assert disappeared == set(REDUNDANT)
    added = {
        (table, index)
        for table, indexes in snapshot_head.items()
        for index in indexes
        if index not in snapshot_before.get(table, set())
    }
    assert added == set(), f"026 不得新增任何索引: {added}"
    for table, index in ALL_PK_ID_INDEXES:
        assert index not in snapshot_head.get(table, set()), (table, index)

    command.downgrade(cfg, PREV_REV)
    # 降级必须精确恢复到升级前快照（不只是清单里名字存在）
    assert _index_snapshot(db_path) == snapshot_before
    for table, index in REDUNDANT:
        assert _index_definition(db_path, table, index) == (False, ["id"])


@pytest.mark.parametrize(("table", "index"), [REDUNDANT[0], REDUNDANT[-1]])
def test_026_upgrade_is_idempotent_when_index_already_dropped(tmp_path, monkeypatch, table, index):
    """MySQL DDL 非事务：上次已删索引但版本戳未推进时，重跑不得 1091 失败。"""
    db_path = tmp_path / f"pk-indexes-026-idem-{table}.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = _cfg()

    command.upgrade(cfg, PREV_REV)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(f"DROP INDEX {index}")
    command.upgrade(cfg, TARGET_REV)
    # 断言全部 14 个都已删除：no-op/stale-guard 的「升级什么都不做」也变红
    for table, index in REDUNDANT:
        assert index not in _index_names(db_path, table), (table, index)


@pytest.mark.parametrize(("table", "index"), [REDUNDANT[0], REDUNDANT[-1]])
def test_026_downgrade_is_idempotent_when_index_already_exists(tmp_path, monkeypatch, table, index):
    """对称方向：索引已被提前重建时，downgrade 不得因重复建索引（1061）失败。"""
    db_path = tmp_path / f"pk-indexes-026-down-idem-{table}.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = _cfg()

    command.upgrade(cfg, TARGET_REV)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(f"CREATE INDEX {index} ON {table} (id)")
    command.downgrade(cfg, PREV_REV)
    # 断言全部 14 个都已恢复：downgrade no-op 也会变红（其余 13 个缺失）
    for table, index in REDUNDANT:
        assert _index_definition(db_path, table, index) == (False, ["id"])


def test_create_all_path_has_no_redundant_pk_indexes(tmp_path):
    """create_all 路径（模型声明）同样不得产生 PK 冗余索引。

    这条直接锁定「模型无声明」这一事实，而不是依赖迁移库里没有残留。
    """
    from sqlalchemy import create_engine

    db_path = tmp_path / "create-all.db"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}")
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()

    for table, index in ALL_PK_ID_INDEXES:
        assert index not in _index_names(db_path, table), (table, index)
