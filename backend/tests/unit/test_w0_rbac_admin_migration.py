"""W0-1 迁移脚本测试：users.role='admin' 分类 / OrgAdmin 回填 / 回滚。

用临时 SQLite 库执行 ``backend/scripts/migrations/w0_1_rbac_admin_scope_*.sql``
（脚本本身保持 MySQL/SQLite 双兼容的纯 SQL）。覆盖：

- 平台管理员（显式 is_superuser 或 allowlist）→ is_superuser=1；
- 其余有组织 admin → 回填该组织 OrgAdmin 关联（users.role 降为 'user'）；
- 无组织且未登记 → 'demoted'，不获得任何权限；
- OrgAdmin 角色种子的权限集与 ``ORG_ADMIN_PERMISSION_NAMES`` 完全一致
  （防止 SQL 与 Python 定义漂移）；
- 回滚依据备份表精确恢复，且只删除迁移回填的关联（迁移前已有的保留）；
- 升级/回滚均可重放（幂等）。
"""
import sqlite3
from pathlib import Path

from app.services.permission_service import ORG_ADMIN_PERMISSION_NAMES

BACKEND_ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS = BACKEND_ROOT / "scripts" / "migrations"
UP_SQL = MIGRATIONS / "w0_1_rbac_admin_scope_up.sql"
DOWN_SQL = MIGRATIONS / "w0_1_rbac_admin_scope_down.sql"

SCHEMA = """
CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    username TEXT UNIQUE,
    role TEXT DEFAULT 'user',
    is_superuser INTEGER DEFAULT 0,
    organization_id INTEGER
);
CREATE TABLE roles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE,
    description TEXT,
    is_system_role INTEGER DEFAULT 0,
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE permissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE,
    description TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE TABLE role_permission_association (
    role_id INTEGER NOT NULL,
    permission_id INTEGER NOT NULL,
    PRIMARY KEY (role_id, permission_id)
);
CREATE TABLE user_organization_role_association (
    user_id INTEGER NOT NULL,
    organization_id INTEGER NOT NULL,
    role_id INTEGER NOT NULL,
    PRIMARY KEY (user_id, organization_id, role_id)
);
CREATE TABLE w0_1_rbac_platform_allowlist (
    username TEXT NOT NULL PRIMARY KEY
);
"""

USERS = [
    # id, username, role, is_superuser, organization_id
    (1, "platform-pending", "admin", 0, None),   # allowlist → platform_admin
    (2, "platform-existing", "admin", 1, 1),     # 已是超管 → platform_admin
    (3, "org-admin-a", "admin", 0, 1),           # 回填关联（已有同关联）
    (4, "org-admin-b", "admin", 0, 2),           # 回填关联（新建）
    (5, "normal-user", "user", 0, 1),            # 不在迁移范围
    (6, "orgless-admin", "admin", 0, None),      # 未登记 → demoted
]


def _make_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "w0_rbac.sqlite3"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA)
        conn.executemany(
            "INSERT INTO users (id, username, role, is_superuser, organization_id) "
            "VALUES (?, ?, ?, ?, ?)",
            USERS,
        )
        # 权限种子：OrgAdmin 目标集 + 两个平台级权限（必须不被授予）
        perm_names = list(ORG_ADMIN_PERMISSION_NAMES) + [
            "organization:create",
            "system:health",
        ]
        conn.executemany(
            "INSERT INTO permissions (name, description) VALUES (?, ?)",
            [(name, name) for name in perm_names],
        )
        conn.executemany(
            "INSERT INTO roles (name, description, is_system_role) VALUES (?, ?, ?)",
            [
                ("SysAdmin", "系统管理员", 1),
                ("User", "普通用户", 1),
                ("OrgAdmin", "组织管理员", 0),
            ],
        )
        # 运营登记的平台管理员（迁移脚本的 allowlist 语义）
        conn.execute(
            "INSERT INTO w0_1_rbac_platform_allowlist (username) VALUES ('platform-pending')"
        )
        # 迁移前已存在的 OrgAdmin 关联（回滚时必须保留）
        org_admin_id = conn.execute(
            "SELECT id FROM roles WHERE name = 'OrgAdmin'"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO user_organization_role_association "
            "(user_id, organization_id, role_id) VALUES (3, 1, ?)",
            (org_admin_id,),
        )
        conn.commit()
    return db_path


def _run(db_path: Path, script: Path) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(script.read_text(encoding="utf-8"))
        conn.commit()


def _rows(db_path: Path, sql: str):
    with sqlite3.connect(str(db_path)) as conn:
        return conn.execute(sql).fetchall()


def _user(db_path: Path, uid: int):
    return _rows(
        db_path,
        f"SELECT username, role, is_superuser, organization_id FROM users WHERE id={uid}",
    )[0]


def _org_admin_associations(db_path: Path):
    return set(
        _rows(
            db_path,
            """
            SELECT x.user_id, x.organization_id
            FROM user_organization_role_association x, roles r
            WHERE x.role_id = r.id AND r.name = 'OrgAdmin'
            """,
        )
    )


def test_upgrade_classifies_and_backfills(tmp_path):
    db_path = _make_db(tmp_path)
    _run(db_path, UP_SQL)

    # 1) 审计/回滚备份：5 个 role='admin' 账号各一行，分类正确
    backup = {
        row[0]: (row[1], row[2], row[3], row[4])
        for row in _rows(
            db_path,
            "SELECT user_id, classification, old_role, old_is_superuser, "
            "association_existed_before FROM w0_1_rbac_admin_backup",
        )
    }
    assert set(backup) == {1, 2, 3, 4, 6}
    assert backup[1] == ("platform_admin", "admin", 0, 0)
    assert backup[2] == ("platform_admin", "admin", 1, 0)
    assert backup[3] == ("org_admin", "admin", 0, 1)
    assert backup[4] == ("org_admin", "admin", 0, 0)
    assert backup[6] == ("demoted", "admin", 0, 0)

    # 2) 平台管理员：只由显式 is_superuser 承载；users.role 统一降级
    assert _user(db_path, 1) == ("platform-pending", "user", 1, None)
    assert _user(db_path, 2) == ("platform-existing", "user", 1, 1)
    assert _user(db_path, 3) == ("org-admin-a", "user", 0, 1)
    assert _user(db_path, 4) == ("org-admin-b", "user", 0, 2)
    assert _user(db_path, 6) == ("orgless-admin", "user", 0, None)
    # 不在迁移范围的账号原样保留
    assert _user(db_path, 5) == ("normal-user", "user", 0, 1)

    # 3) OrgAdmin 角色种子：权限集与 Python 定义完全一致（含平台权限排除）
    role_perms = {
        row[0]
        for row in _rows(
            db_path,
            """
            SELECT p.name
            FROM roles r, permissions p, role_permission_association a
            WHERE r.name = 'OrgAdmin' AND a.role_id = r.id AND a.permission_id = p.id
            """,
        )
    }
    assert role_perms == set(ORG_ADMIN_PERMISSION_NAMES)
    assert "organization:create" not in role_perms
    assert "system:health" not in role_perms

    # 4) 组织角色关联：u3 原有保留，u4 新建；未越界到其他组织
    assert _org_admin_associations(db_path) == {(3, 1), (4, 2)}

    # 5) 重放幂等：行数不变、无重复关联
    _run(db_path, UP_SQL)
    assert _rows(db_path, "SELECT COUNT(*) FROM w0_1_rbac_admin_backup")[0][0] == 5
    assert _org_admin_associations(db_path) == {(3, 1), (4, 2)}


def test_downgrade_restores_exactly_and_preserves_preexisting(tmp_path):
    db_path = _make_db(tmp_path)
    _run(db_path, UP_SQL)
    _run(db_path, DOWN_SQL)

    # users 的 role / is_superuser 精确恢复
    assert _user(db_path, 1) == ("platform-pending", "admin", 0, None)
    assert _user(db_path, 2) == ("platform-existing", "admin", 1, 1)
    assert _user(db_path, 3) == ("org-admin-a", "admin", 0, 1)
    assert _user(db_path, 4) == ("org-admin-b", "admin", 0, 2)
    assert _user(db_path, 6) == ("orgless-admin", "admin", 0, None)
    assert _user(db_path, 5) == ("normal-user", "user", 0, 1)

    # 只删除迁移回填的关联；迁移前已存在的 (3,1) 保留
    assert _org_admin_associations(db_path) == {(3, 1)}

    # 回滚幂等
    _run(db_path, DOWN_SQL)
    assert _org_admin_associations(db_path) == {(3, 1)}
    assert _user(db_path, 4) == ("org-admin-b", "admin", 0, 2)
