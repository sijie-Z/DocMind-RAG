"""A2 迁移 010 的离线校验：upgrade → 回填 → downgrade 往返（SQLite）。

CI 的 backend-tests 会在 SQLite 上跑 ``alembic upgrade head``；本用例在本地
不依赖 MySQL 栈也能证明：
1. 010 迁移在 SQLite 上可执行（batch 模式建列/索引/外键）；
2. 回填符合约定：工作流取 created_by 的主组织、主组织为空时回退成员表；
   非系统提示词按 creator 回填，系统模板保持 NULL；
3. downgrade 只删本轮新增列，业务数据原样保留，且可再次 upgrade（可重放）。

做法：先在 009 停下，用原生 SQL 造数据，再升到 head 断言。
（MySQL 真实链路由 CI migration-mysql job 覆盖；本地栈停止中。）
"""
import sqlite3
from pathlib import Path

from alembic.config import Config

from alembic import command

BACKEND_ROOT = Path(__file__).resolve().parents[2]
TENANT_REV = "010_tenant_boundary_org"
PREV_REV = "009_agent_task_plan_approval"


def _cfg() -> Config:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return cfg


def _exec(db_path, statement: str):
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(statement)
        conn.commit()
    finally:
        conn.close()


def _scalar(db_path, statement: str):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(statement).fetchone()[0]
    finally:
        conn.close()


def test_tenant_org_migration_backfill_and_rollback(tmp_path, monkeypatch):
    from app.core.config import settings

    db_path = tmp_path / "mig_tenant.db"
    monkeypatch.setattr(
        settings, "DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}"
    )
    cfg = _cfg()

    command.upgrade(cfg, PREV_REV)

    # ── 在 009 的状态下造数据（此时两表还没有 organization_id 列）──
    _exec(db_path, """
        INSERT INTO organizations
            (id, name, color, is_private, level, sort_order, owner_id, created_at, updated_at)
        VALUES (7, 'Org7', '#000', 0, 1, 0, 1, datetime('now'), datetime('now'))
    """)
    _exec(db_path, """
        INSERT INTO organizations
            (id, name, color, is_private, level, sort_order, owner_id, created_at, updated_at)
        VALUES (8, 'Org8', '#000', 0, 1, 0, 2, datetime('now'), datetime('now'))
    """)
    # 用户 1：主组织 7（主路径）；用户 2：主组织为空、仅有成员关系 8（回退路径）
    _exec(db_path, """
        INSERT INTO users
            (id, username, email, hashed_password, organization_id, role, is_active,
             is_superuser, created_at, updated_at)
        VALUES (1, 'u1', 'u1@example.com', 'x', 7, 'user', 1, 0, datetime('now'), datetime('now'))
    """)
    _exec(db_path, """
        INSERT INTO users
            (id, username, email, hashed_password, organization_id, role, is_active,
             is_superuser, created_at, updated_at)
        VALUES (2, 'u2', 'u2@example.com', 'x', NULL, 'user', 1, 0, datetime('now'), datetime('now'))
    """)
    _exec(db_path, "INSERT INTO user_organization (user_id, organization_id) VALUES (2, 8)")
    # 用户 3：无主组织，有两条成员关系——按「最早加入」（created_at）应回填组织 8，
    # 而不是按组织 id 最小的 7（评审 m-2 的排序判别）。
    _exec(db_path, """
        INSERT INTO users
            (id, username, email, hashed_password, organization_id, role, is_active,
             is_superuser, created_at, updated_at)
        VALUES (3, 'u3', 'u3@example.com', 'x', NULL, 'user', 1, 0, datetime('now'), datetime('now'))
    """)
    _exec(db_path, """
        INSERT INTO user_organization (user_id, organization_id, created_at)
        VALUES (3, 8, '2020-01-01 00:00:00')
    """)
    _exec(db_path, """
        INSERT INTO user_organization (user_id, organization_id, created_at)
        VALUES (3, 7, '2024-01-01 00:00:00')
    """)

    _exec(db_path, """
        INSERT INTO workflows (id, name, is_active, created_by, created_at, updated_at)
        VALUES (1, 'wf-primary', 1, 1, datetime('now'), datetime('now'))
    """)
    _exec(db_path, """
        INSERT INTO workflows (id, name, is_active, created_by, created_at, updated_at)
        VALUES (2, 'wf-membership', 1, 2, datetime('now'), datetime('now'))
    """)
    _exec(db_path, """
        INSERT INTO workflows (id, name, is_active, created_by, created_at, updated_at)
        VALUES (3, 'wf-earliest-membership', 1, 3, datetime('now'), datetime('now'))
    """)
    _exec(db_path, """
        INSERT INTO prompt_templates
            (id, name, content, is_active, is_system, category, version, creator_id, created_at)
        VALUES (1, 'org-prompt', 'c', 1, 0, 'general', 1, 1, datetime('now'))
    """)
    _exec(db_path, """
        INSERT INTO prompt_templates
            (id, name, content, is_active, is_system, category, version, creator_id, created_at)
        VALUES (2, 'system-prompt', 'c', 1, 1, 'general', 1, 1, datetime('now'))
    """)

    command.upgrade(cfg, "head")

    # ── 回填断言 ──
    assert _scalar(db_path, "SELECT organization_id FROM workflows WHERE id = 1") == 7
    assert _scalar(db_path, "SELECT organization_id FROM workflows WHERE id = 2") == 8, (
        "主组织为空时必须回退到 user_organization 成员关系"
    )
    assert _scalar(db_path, "SELECT organization_id FROM workflows WHERE id = 3") == 8, (
        "多成员关系时必须按 created_at 取最早加入的组织（8 早于 7）"
    )
    assert _scalar(db_path, "SELECT organization_id FROM prompt_templates WHERE id = 1") == 7
    assert _scalar(db_path, "SELECT organization_id FROM prompt_templates WHERE id = 2") is None, (
        "系统模板必须保持 organization_id = NULL（全组织只读可见）"
    )

    # ── 回滚：新增列消失，业务数据保留；随后可再次升级（可重放）──
    command.downgrade(cfg, PREV_REV)
    conn = sqlite3.connect(db_path)
    try:
        wf_cols = {row[1] for row in conn.execute("PRAGMA table_info(workflows)")}
        prompt_cols = {row[1] for row in conn.execute("PRAGMA table_info(prompt_templates)")}
    finally:
        conn.close()
    assert "organization_id" not in wf_cols
    assert "organization_id" not in prompt_cols
    assert _scalar(db_path, "SELECT COUNT(*) FROM workflows") == 3
    assert _scalar(db_path, "SELECT COUNT(*) FROM prompt_templates") == 2

    command.upgrade(cfg, "head")
    assert _scalar(db_path, "SELECT organization_id FROM workflows WHERE id = 1") == 7
