"""B4 迁移测试：011_organization_settings 单头、建表/加列、回填与双级往返。

沿 A3 迁移测试的 ScriptDirectory + 临时 sqlite 做法，不依赖 MySQL/Redis/ES：

- 迁移链必须单头，且 011 仍在链上、直接接在 010 之后（新 head 会随迁移
  演进而后移——issue #476 之后是 012_session_fork）；
- 从 ``010_agent_tool_calls`` 升级到 ``011_organization_settings`` 后：
  * ``organization_settings`` 表存在且列齐全、organization_id 唯一；
  * ``prompt_templates.scope`` 列存在（server_default='organization'），
    且 ``is_system=1`` 的历史行被回填为 ``system``，其余为 ``organization``；
- downgrade 回 ``010_agent_tool_calls``：新表与新列都被删除，业务数据原样保留；
- 再 upgrade 到 011：双级往返可重放。
"""

import sqlite3
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

from alembic import command

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PREV_REV = "010_agent_tool_calls"
NEW_REV = "011_organization_settings"
ORG_SETTINGS_COLUMNS = {
    "id",
    "organization_id",
    "default_provider",
    "default_model",
    "retrieval_top_k",
    "allow_privacy_mode",
    "monthly_token_quota",
    "tool_whitelist",
    "created_at",
    "updated_at",
}


def _cfg() -> Config:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return cfg


def _exec(db_path: Path, sql: str, params: tuple = ()) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(sql, params)
        conn.commit()


def _scalar(db_path: Path, sql: str, params: tuple = ()):
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(sql, params).fetchone()
    return row[0] if row else None


def _columns(db_path: Path, table: str) -> dict[str, dict]:
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    # cid, name, type, notnull, dflt_value, pk
    return {
        r[1]: {"type": r[2], "notnull": bool(r[3]), "default": r[4], "pk": bool(r[5])} for r in rows
    }


def _table_names(db_path: Path) -> set[str]:
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {r[0] for r in rows}


def _unique_index_columns(db_path: Path, table: str) -> list[tuple]:
    """返回该表所有唯一索引的列组合（sqlite PRAGMA）。"""
    out: list[tuple] = []
    with sqlite3.connect(str(db_path)) as conn:
        indexes = conn.execute(f"PRAGMA index_list({table})").fetchall()
        for row in indexes:
            # seq, name, unique, origin, partial
            if not row[2]:
                continue
            cols = conn.execute(f"PRAGMA index_info({row[1]})").fetchall()
            out.append(tuple(c[2] for c in cols))
    return out


def _foreign_keys(db_path: Path, table: str) -> dict[str, dict]:
    """返回 {from_col: {table, to, on_delete}}（sqlite PRAGMA）。"""
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(f"PRAGMA foreign_key_list({table})").fetchall()
    # id, seq, table, from, to, on_update, on_delete, match
    return {r[3]: {"table": r[2], "to": r[4], "on_delete": (r[6] or "").upper()} for r in rows}


def test_b4_migration_single_head_and_two_way_round_trip(tmp_path, monkeypatch):
    from app.core.config import settings

    db_path = tmp_path / "mig_b4.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = _cfg()

    # ── 1. 迁移链：单头 + 011 在链上且直接接在 010_agent_tool_calls 之后 ──
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    assert len(heads) == 1, f"迁移必须单头，实际 {heads}"
    chain = [rev.revision for rev in script.iterate_revisions(heads[0], "base")]
    # head 随迁移演进后移（issue #476 之后是 012_session_fork）：只钉"单头 +
    # 011 仍在链上且紧接 010"，不再把 011 钉成永久 head。
    assert NEW_REV in chain, f"{NEW_REV} 必须仍在链上，实际 {chain}"
    assert PREV_REV in chain, f"{PREV_REV} 必须仍在链上，实际 {chain}"
    assert chain.index(PREV_REV) == chain.index(NEW_REV) + 1, (
        f"011 必须直接接在 {PREV_REV} 之后，实际链 {chain}"
    )
    assert len(NEW_REV) <= 32, "revision id 必须 <= 32（MySQL version_num VARCHAR(32)）"

    # ── 2. 升到旧 head：新对象都不存在 ──
    command.upgrade(cfg, PREV_REV)
    assert "organization_settings" not in _table_names(db_path)
    assert "scope" not in _columns(db_path, "prompt_templates")

    # 在旧 schema 下塞历史行（模拟升级窗口前的存量数据）
    _exec(
        db_path,
        "INSERT INTO prompt_templates "
        "(name, content, is_active, is_system, organization_id, category, version) "
        "VALUES ('sys', 'c', 1, 1, NULL, 'general', 1)",
    )
    _exec(
        db_path,
        "INSERT INTO prompt_templates "
        "(name, content, is_active, is_system, organization_id, category, version) "
        "VALUES ('org', 'c', 1, 0, 1, 'general', 1)",
    )

    # ── 3. 升到 head：建表 + 加列 + 回填 ──
    command.upgrade(cfg, "head")
    assert "organization_settings" in _table_names(db_path)
    cols = _columns(db_path, "organization_settings")
    assert set(cols) == ORG_SETTINGS_COLUMNS, f"列不齐: {sorted(cols)}"
    assert cols["organization_id"]["notnull"] is True
    assert cols["organization_id"]["pk"] is False
    assert ("organization_id",) in _unique_index_columns(db_path, "organization_settings")
    fks = _foreign_keys(db_path, "organization_settings")
    assert fks["organization_id"]["table"] == "organizations"
    assert fks["organization_id"]["on_delete"] == "CASCADE", (
        f"组织删除必须级联清理设置，实际 FK={fks}"
    )

    pcols = _columns(db_path, "prompt_templates")
    assert "scope" in pcols
    assert pcols["scope"]["notnull"] is True
    assert "organization" in (pcols["scope"]["default"] or ""), (
        f"scope 必须有 server_default='organization'，实际 {pcols['scope']['default']}"
    )
    assert _scalar(db_path, "SELECT scope FROM prompt_templates WHERE name='sys'") == "system"
    assert _scalar(db_path, "SELECT scope FROM prompt_templates WHERE name='org'") == "organization"

    # ── 4. 降级回旧 head：新表/新列删除，存量行保留 ──
    command.downgrade(cfg, PREV_REV)
    assert "organization_settings" not in _table_names(db_path)
    assert "scope" not in _columns(db_path, "prompt_templates")
    assert _scalar(db_path, "SELECT COUNT(*) FROM prompt_templates") == 2

    # ── 5. 再升级（双级往返可重放） ──
    command.upgrade(cfg, "head")
    assert "organization_settings" in _table_names(db_path)
    assert "scope" in _columns(db_path, "prompt_templates")
