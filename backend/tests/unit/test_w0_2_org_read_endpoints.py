"""W0-2 · 四个未收口读接口的租户边界矩阵（先红后绿）。

路线依据：ADR-001 D3（per-org RBAC + 显式平台管理员）+ W0-2 任务书。

覆盖端点：
1. ``GET /api/v1/organizations/``  —— 平台管理员看全部；其余只看
   **成员表/角色关联/主组织**给出的组织集合；无组织 403；
2. ``GET /api/v1/organizations/tree`` —— 同上（森林按可见节点构建，
   不可见父节点不得泄露）；
3. ``GET /api/v1/monitoring/org-summary`` —— 平台管理员全量；组织管理员
   只看自己管理的组织聚合；普通成员/无组织 403；
4. ``GET /api/v1/llm-config``、``/default/{provider}``、``/detail/{id}`` ——
   过渡期只允许平台管理员，其余 403（且必须在读 Redis 之前拒绝）。

四角色矩阵：平台超管 / A 组织管理员 / B 组织管理员 / 普通成员 / 无组织。
额外判别靶：
- 多组织成员（``user_organization`` 两行）——必须看到全部成员组织，
  而不是只看 ``users.organization_id`` 单组织（W0-2 的核心增量）；
- 跨组织角色（主组织 A、OrgAdmin 关联只在 B）——列表/树可见 A+B，
  org-summary 只返回 B；
- 仅角色关联、无成员行/主组织 —— 角色关联同样是可见性来源；
- 仅 ``users.role='admin'`` 标签 —— 不得获得平台级读取；
- 私有组织 —— 非成员不得因 ``is_private=False`` 过滤而看到。

真 SQLite（``Base.metadata.create_all``）+ ``httpx.ASGITransport``，不连
MySQL/Redis/ES。
"""
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  # 注册全部表供 create_all
from app.core.database import Base
from app.models.document import Document, DocumentStatus, DocumentType
from app.models.organization import Organization, user_organization
from app.models.rbac import (
    ORG_ADMIN_ROLE_NAME,
    Permission,
    PermissionType,
    Role,
    role_permission_association,
    user_organization_role_association,
)
from app.models.user import User

# 与 permission_service.initialize_default_permissions_and_roles 同口径的核心
# 权限集（测试内自行种子，避免依赖全局 AsyncSessionLocal）。W0-2 的端点不再
# 依赖 permission_required，但保留同口径种子可保证红阶段失败原因落在
# 「数据可见范围」而不是「角色种子缺失」。
SYSTEM_ROLE_PERMISSIONS = {
    "SysAdmin": [p.value for p in PermissionType],
    "User": [
        PermissionType.DOCUMENT_READ.value,
        PermissionType.DOCUMENT_UPLOAD.value,
        PermissionType.DOCUMENT_DELETE.value,
        PermissionType.VIEW_KNOWLEDGE_BASE.value,
        PermissionType.SEARCH_KNOWLEDGE_BASE.value,
        PermissionType.VIEW_PROMPT.value,
    ],
    ORG_ADMIN_ROLE_NAME: [
        PermissionType.VIEW_USER_DETAIL.value,
        PermissionType.MANAGE_USER_ROLES.value,
        PermissionType.VIEW_ORGANIZATION.value,
        PermissionType.UPDATE_ORGANIZATION.value,
        PermissionType.MANAGE_ORGANIZATION_MEMBERS.value,
        PermissionType.DOCUMENT_UPLOAD.value,
        PermissionType.DOCUMENT_READ.value,
        PermissionType.DOCUMENT_UPDATE.value,
        PermissionType.DOCUMENT_DELETE.value,
        PermissionType.VIEW_KNOWLEDGE_BASE.value,
        PermissionType.SEARCH_KNOWLEDGE_BASE.value,
        PermissionType.MANAGE_KNOWLEDGE_BASE.value,
        PermissionType.REBUILD_DOCUMENT_INDEX.value,
        PermissionType.VIEW_PROMPT.value,
        PermissionType.CREATE_PROMPT.value,
        PermissionType.UPDATE_PROMPT.value,
        PermissionType.DELETE_PROMPT.value,
    ],
}

ROLE_IDS: dict[str, int] = {}


def _user(uid: int, org: int | None, *, role: str = "user", superuser: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        id=uid,
        username=f"user{uid}",
        email=f"user{uid}@example.com",
        hashed_password="x",
        full_name=None,
        preferences=None,
        created_at=None,
        role=role,
        organization_id=org,
        is_active=True,
        is_superuser=superuser,
    )


# 组织：1/2 为两个租户，3/4 分别是 1/2 的子组织，5 是私有组织。
ORG_A, ORG_B, ORG_A_CHILD, ORG_B_CHILD, ORG_PRIVATE = 1, 2, 3, 4, 5

SUPERUSER = _user(1, None, superuser=True)
ORG_A_ADMIN = _user(10, ORG_A)
ORG_B_ADMIN = _user(20, ORG_B)
ORG_A_MEMBER = _user(11, ORG_A)
ORGLESS = _user(12, None)
LEGACY_ROLE_ADMIN = _user(13, ORG_A, role="admin")
MULTI_MEMBER = _user(14, ORG_A)          # user_organization: A + B
CROSS_ORG_ROLE = _user(15, ORG_A)        # 主组织 A；成员 + OrgAdmin 关联在 B
CHILD_MEMBER = _user(16, ORG_A_CHILD)    # 仅子组织 3 成员
ROLE_ONLY_ADMIN = _user(17, None)        # 无主组织/成员行，仅 B 的 OrgAdmin 关联
MEMBERSHIP_ONLY = _user(18, None)        # 无主组织，仅 A 的成员行


async def _seed_roles(s) -> None:
    perms = {}
    for names in SYSTEM_ROLE_PERMISSIONS.values():
        for name in names:
            perms.setdefault(name, Permission(name=name, description=name))
    s.add_all(perms.values())
    await s.flush()

    for role_name, names in SYSTEM_ROLE_PERMISSIONS.items():
        role = Role(
            name=role_name,
            description=role_name,
            is_system_role=role_name != ORG_ADMIN_ROLE_NAME,
        )
        s.add(role)
        await s.flush()
        ROLE_IDS[role_name] = role.id
        for name in names:
            await s.execute(
                role_permission_association.insert().values(
                    role_id=role.id, permission_id=perms[name].id
                )
            )


async def _setup():
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as s:
        s.add_all([
            Organization(id=ORG_A, name="org-a", owner_id=10),
            Organization(id=ORG_B, name="org-b", owner_id=20),
            Organization(id=ORG_A_CHILD, name="org-a-child", owner_id=10, parent_id=ORG_A, level=2),
            Organization(id=ORG_B_CHILD, name="org-b-child", owner_id=20, parent_id=ORG_B, level=2),
            Organization(id=ORG_PRIVATE, name="private-org", owner_id=1, is_private=True),
        ])
        await s.flush()
        await _seed_roles(s)

        s.add_all([
            User(id=10, username="a-admin", email="a-admin@example.com", hashed_password="x",
                 role="user", organization_id=ORG_A, is_active=True, is_superuser=False),
            User(id=20, username="b-admin", email="b-admin@example.com", hashed_password="x",
                 role="user", organization_id=ORG_B, is_active=True, is_superuser=False),
            User(id=11, username="a-member", email="a-member@example.com", hashed_password="x",
                 role="user", organization_id=ORG_A, is_active=True, is_superuser=False),
            User(id=12, username="orgless", email="orgless@example.com", hashed_password="x",
                 role="user", organization_id=None, is_active=True, is_superuser=False),
            # 仅 users.role='admin' 标签：不得获得任何平台级读取
            User(id=13, username="legacy-admin", email="legacy@example.com", hashed_password="x",
                 role="admin", organization_id=ORG_A, is_active=True, is_superuser=False),
            # 主组织 A，但成员关系覆盖 A+B（W0-2 核心增量：全部成员组织）
            User(id=14, username="multi-member", email="multi@example.com", hashed_password="x",
                 role="user", organization_id=ORG_A, is_active=True, is_superuser=False),
            # 主组织 A，成员关系 + OrgAdmin 关联只在 B（per-org 判别靶）
            User(id=15, username="cross-role", email="cross@example.com", hashed_password="x",
                 role="user", organization_id=ORG_A, is_active=True, is_superuser=False),
            # 仅子组织 3 成员：父组织 1 不可见，树必须把 3 作为根且不泄露 1
            User(id=16, username="child-member", email="child@example.com", hashed_password="x",
                 role="user", organization_id=ORG_A_CHILD, is_active=True, is_superuser=False),
            # 无主组织/无成员行，仅 B 的 OrgAdmin 关联：角色关联是可见性来源
            User(id=17, username="role-only-admin", email="roleonly@example.com", hashed_password="x",
                 role="user", organization_id=None, is_active=True, is_superuser=False),
            # 无主组织，仅 A 的成员行：成员表同样单独构成可见性来源
            User(id=18, username="membership-only", email="membershiponly@example.com", hashed_password="x",
                 role="user", organization_id=None, is_active=True, is_superuser=False),
            User(id=1, username="superuser", email="root@example.com", hashed_password="x",
                 role="user", organization_id=None, is_active=True, is_superuser=True),
        ])
        await s.flush()

        await s.execute(
            user_organization_role_association.insert(),
            [
                {"user_id": 10, "organization_id": ORG_A, "role_id": ROLE_IDS[ORG_ADMIN_ROLE_NAME]},
                {"user_id": 20, "organization_id": ORG_B, "role_id": ROLE_IDS[ORG_ADMIN_ROLE_NAME]},
                {"user_id": 15, "organization_id": ORG_B, "role_id": ROLE_IDS[ORG_ADMIN_ROLE_NAME]},
                {"user_id": 17, "organization_id": ORG_B, "role_id": ROLE_IDS[ORG_ADMIN_ROLE_NAME]},
            ],
        )
        await s.execute(
            user_organization.insert(),
            [
                {"user_id": 10, "organization_id": ORG_A},
                {"user_id": 20, "organization_id": ORG_B},
                {"user_id": 11, "organization_id": ORG_A},
                {"user_id": 14, "organization_id": ORG_A},
                {"user_id": 14, "organization_id": ORG_B},
                {"user_id": 15, "organization_id": ORG_B},
                {"user_id": 16, "organization_id": ORG_A_CHILD},
                {"user_id": 18, "organization_id": ORG_A},
            ],
        )

        s.add_all([
            Document(id="doc-a-1", filename="a1.pdf", file_path="documents/1/a1.pdf",
                     file_size=1, file_type=DocumentType.PDF, organization_id=ORG_A,
                     uploaded_by=10, status=DocumentStatus.INDEXED),
            Document(id="doc-a-2", filename="a2.pdf", file_path="documents/1/a2.pdf",
                     file_size=1, file_type=DocumentType.PDF, organization_id=ORG_A,
                     uploaded_by=10, status=DocumentStatus.INDEXED),
            Document(id="doc-b-1", filename="b1.pdf", file_path="documents/2/b1.pdf",
                     file_size=1, file_type=DocumentType.PDF, organization_id=ORG_B,
                     uploaded_by=20, status=DocumentStatus.INDEXED),
        ])
        await s.commit()

    return engine, factory


@pytest.fixture
async def api():
    from app.core.database import get_db
    from app.core.security import get_current_user
    from app.main import app
    from app.services.auth_service import auth_service

    engine, factory = await _setup()
    state = SimpleNamespace(user=ORG_A_MEMBER)

    async def _override_db():
        async with factory() as s:
            yield s

    def _override_user():
        return state.user

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = _override_user
    app.dependency_overrides[auth_service.get_current_user] = _override_user
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield SimpleNamespace(client=ac, state=state, factory=factory)
    app.dependency_overrides.clear()
    await engine.dispose()


async def _call(api, user, method: str, url: str, **kwargs):
    api.state.user = user
    return await api.client.request(method, url, **kwargs)


def _org_ids(resp) -> set[int]:
    return {row["id"] for row in resp.json()["data"]["data"]}


def _collect_names(nodes: list[dict]) -> set[str]:
    names: set[str] = set()
    for node in nodes:
        names.add(node["name"])
        names.update(_collect_names(node.get("children") or []))
    return names


# ── 1. GET /organizations/：成员表/角色关联全量，而非 primary 单组织 ──────


@pytest.mark.asyncio
async def test_org_list_matrix(api):
    r = await _call(api, SUPERUSER, "GET", "/api/v1/organizations/")
    assert r.status_code == 200, r.text
    assert _org_ids(r) == {ORG_A, ORG_B, ORG_A_CHILD, ORG_B_CHILD, ORG_PRIVATE}

    # 组织管理员只看自己管理的组织（本组织）
    r = await _call(api, ORG_A_ADMIN, "GET", "/api/v1/organizations/")
    assert r.status_code == 200, r.text
    assert _org_ids(r) == {ORG_A}
    assert r.json()["data"]["total"] == 1

    r = await _call(api, ORG_B_ADMIN, "GET", "/api/v1/organizations/")
    assert r.status_code == 200, r.text
    assert _org_ids(r) == {ORG_B}

    # 普通成员：只看自己所属组织
    r = await _call(api, ORG_A_MEMBER, "GET", "/api/v1/organizations/")
    assert r.status_code == 200, r.text
    assert _org_ids(r) == {ORG_A}

    # 多组织成员：全部成员组织，而不是 primary 单组织（W0-2 核心增量）
    r = await _call(api, MULTI_MEMBER, "GET", "/api/v1/organizations/")
    assert r.status_code == 200, r.text
    assert _org_ids(r) == {ORG_A, ORG_B}, "成员表里的全部组织都必须可见"

    # 跨组织角色：主组织 A + 成员/角色 B —— 两者都可见
    r = await _call(api, CROSS_ORG_ROLE, "GET", "/api/v1/organizations/")
    assert r.status_code == 200, r.text
    assert _org_ids(r) == {ORG_A, ORG_B}

    # 仅角色关联（无主组织、无成员行）：角色关联是可见性来源
    r = await _call(api, ROLE_ONLY_ADMIN, "GET", "/api/v1/organizations/")
    assert r.status_code == 200, r.text
    assert _org_ids(r) == {ORG_B}

    # 只有子组织成员：只看子组织，不向上泄露父组织（名称与 ID）
    r = await _call(api, CHILD_MEMBER, "GET", "/api/v1/organizations/")
    assert r.status_code == 200, r.text
    assert _org_ids(r) == {ORG_A_CHILD}
    assert r.json()["data"]["data"][0]["parent_id"] is None, "不可见父组织的 ID 不得泄露"

    # 仅成员行、无主组织：成员表同样单独构成可见性来源
    r = await _call(api, MEMBERSHIP_ONLY, "GET", "/api/v1/organizations/")
    assert r.status_code == 200, r.text
    assert _org_ids(r) == {ORG_A}

    # 仅 role='admin' 标签：按普通成员口径，绝不是平台管理员
    r = await _call(api, LEGACY_ROLE_ADMIN, "GET", "/api/v1/organizations/")
    assert r.status_code == 200, r.text
    assert _org_ids(r) == {ORG_A}

    # 无组织用户：403，不得返回空列表冒充成功
    r = await _call(api, ORGLESS, "GET", "/api/v1/organizations/")
    assert r.status_code == 403, f"无组织用户必须 403，实际 {r.status_code}: {r.text}"


@pytest.mark.asyncio
async def test_org_list_search_cannot_widen_scope(api):
    """search 只能过滤可见集，不能成为跨租户枚举通道。"""
    r = await _call(api, ORG_A_ADMIN, "GET", "/api/v1/organizations/", params={"search": "org-b"})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["data"] == []
    assert r.json()["data"]["total"] == 0

    r = await _call(api, ORG_A_ADMIN, "GET", "/api/v1/organizations/", params={"search": "org"})
    assert r.status_code == 200, r.text
    assert _org_ids(r) == {ORG_A}


# ── 2. GET /organizations/tree：可见节点森林，不泄露不可见父节点 ─────────


@pytest.mark.asyncio
async def test_org_tree_matrix(api):
    r = await _call(api, SUPERUSER, "GET", "/api/v1/organizations/tree")
    assert r.status_code == 200, r.text
    roots = {node["id"]: node for node in r.json()["data"]}
    assert set(roots) == {ORG_A, ORG_B, ORG_PRIVATE}
    assert {c["id"] for c in roots[ORG_A]["children"]} == {ORG_A_CHILD}
    assert {c["id"] for c in roots[ORG_B]["children"]} == {ORG_B_CHILD}

    # A 管理员：只有 A；子组织 3 未授权（不在成员/角色关系里）→ 不得出现
    r = await _call(api, ORG_A_ADMIN, "GET", "/api/v1/organizations/tree")
    assert r.status_code == 200, r.text
    assert {node["id"] for node in r.json()["data"]} == {ORG_A}
    assert r.json()["data"][0]["children"] == []

    r = await _call(api, ORG_B_ADMIN, "GET", "/api/v1/organizations/tree")
    assert {node["id"] for node in r.json()["data"]} == {ORG_B}

    # 普通成员：只看自己的组织
    r = await _call(api, ORG_A_MEMBER, "GET", "/api/v1/organizations/tree")
    assert {node["id"] for node in r.json()["data"]} == {ORG_A}

    # 多组织成员：两棵根都在
    r = await _call(api, MULTI_MEMBER, "GET", "/api/v1/organizations/tree")
    assert {node["id"] for node in r.json()["data"]} == {ORG_A, ORG_B}

    # 只有子组织成员：父组织不可见 → 子组织作为根，且父组织名称/ID 均不出网
    r = await _call(api, CHILD_MEMBER, "GET", "/api/v1/organizations/tree")
    assert r.status_code == 200, r.text
    assert {node["id"] for node in r.json()["data"]} == {ORG_A_CHILD}
    root = r.json()["data"][0]
    assert _collect_names([root]) == {"org-a-child"}
    assert root["parent_id"] is None, "不可见父组织的 ID 不得泄露"

    # 仅角色关联
    r = await _call(api, ROLE_ONLY_ADMIN, "GET", "/api/v1/organizations/tree")
    assert {node["id"] for node in r.json()["data"]} == {ORG_B}

    # 无组织用户：403（不得用空树冒充成功，W0-2 语义）
    r = await _call(api, ORGLESS, "GET", "/api/v1/organizations/tree")
    assert r.status_code == 403, f"无组织用户必须 403，实际 {r.status_code}: {r.text}"


# ── 3. GET /monitoring/org-summary：平台全量 / 组织管理员本组织 / 其余 403 ──


def _summary_rows(resp) -> dict[int, dict]:
    return {row["org_id"]: row for row in resp.json()["organizations"]}


@pytest.mark.asyncio
async def test_org_summary_matrix(api):
    r = await _call(api, SUPERUSER, "GET", "/api/v1/monitoring/org-summary")
    assert r.status_code == 200, r.text
    rows = _summary_rows(r)
    assert set(rows) == {ORG_A, ORG_B, ORG_A_CHILD, ORG_B_CHILD, ORG_PRIVATE}
    assert rows[ORG_A]["user_count"] == 5 and rows[ORG_A]["doc_count"] == 2
    assert rows[ORG_B]["user_count"] == 1 and rows[ORG_B]["doc_count"] == 1
    assert rows[ORG_A_CHILD]["user_count"] == 1 and rows[ORG_A_CHILD]["doc_count"] == 0
    assert "degraded" not in r.json()

    # A 组织管理员：只返回本组织聚合
    r = await _call(api, ORG_A_ADMIN, "GET", "/api/v1/monitoring/org-summary")
    assert r.status_code == 200, r.text
    rows = _summary_rows(r)
    assert set(rows) == {ORG_A}
    assert rows[ORG_A]["user_count"] == 5 and rows[ORG_A]["doc_count"] == 2

    # B 组织管理员：对称
    r = await _call(api, ORG_B_ADMIN, "GET", "/api/v1/monitoring/org-summary")
    assert r.status_code == 200, r.text
    assert set(_summary_rows(r)) == {ORG_B}

    # 跨组织角色：只回管理中的 B（主组织 A 不因其成员身份而外泄聚合）
    r = await _call(api, CROSS_ORG_ROLE, "GET", "/api/v1/monitoring/org-summary")
    assert r.status_code == 200, r.text
    assert set(_summary_rows(r)) == {ORG_B}

    # 仅角色关联：B
    r = await _call(api, ROLE_ONLY_ADMIN, "GET", "/api/v1/monitoring/org-summary")
    assert r.status_code == 200, r.text
    assert set(_summary_rows(r)) == {ORG_B}

    # 普通成员 / 仅 role='admin' / 无组织：403
    for user in (ORG_A_MEMBER, LEGACY_ROLE_ADMIN, ORGLESS):
        r = await _call(api, user, "GET", "/api/v1/monitoring/org-summary")
        assert r.status_code == 403, (
            f"{user.username} 不应读取组织聚合，实际 {r.status_code}: {r.text}"
        )


# ── 4. llm_config 三个读端点：过渡期只允许平台管理员 ─────────────────────


@pytest.mark.asyncio
async def test_llm_config_reads_platform_admin_only(api, monkeypatch):
    import app.api.v1.endpoints.llm_config as llm_config_module

    calls = {"count": 0}

    async def _fake_load_all():
        calls["count"] += 1
        return {
            "cfg1": {
                "id": "cfg1",
                "provider": "deepseek",
                "config_name": "默认配置",
                "api_key": "sk-abcdefghijklmnop",
                "is_default": True,
            }
        }

    monkeypatch.setattr(llm_config_module, "_load_all", _fake_load_all)

    paths = [
        "/api/v1/llm-config",
        "/api/v1/llm-config/default/deepseek",
        "/api/v1/llm-config/detail/cfg1",
    ]

    # 平台管理员：三个读端点均可用
    for path in paths:
        r = await _call(api, SUPERUSER, "GET", path)
        assert r.status_code == 200, f"{path}: {r.status_code} {r.text}"
    assert calls["count"] == 3, "平台管理员读取应真正加载配置"

    # 其他角色：一律 403，且不得触碰配置存储
    calls["count"] = 0
    for user in (ORG_A_ADMIN, ORG_B_ADMIN, ORG_A_MEMBER, LEGACY_ROLE_ADMIN, ORGLESS):
        for path in paths:
            r = await _call(api, user, "GET", path)
            assert r.status_code == 403, (
                f"{user.username} GET {path} 应 403，实际 {r.status_code}: {r.text}"
            )
    assert calls["count"] == 0, "非平台管理员的请求不得读取配置存储"


# ── 5. 可见组织辅助函数：成员表/角色关联/主组织的并集（防回退） ──────────


@pytest.mark.asyncio
async def test_visible_org_ids_helper_union(api):
    from app.services.organization_service import organization_service

    async with api.factory() as s:
        multi = await s.get(User, 14)
        ids = await organization_service.resolve_visible_organization_ids(s, multi)
        assert ids == {ORG_A, ORG_B}

        orgless = await s.get(User, 12)
        ids = await organization_service.resolve_visible_organization_ids(s, orgless)
        assert ids == set()

        # 仅成员行、无主组织：成员表路径独立成立
        membership_only = await s.get(User, 18)
        ids = await organization_service.resolve_visible_organization_ids(s, membership_only)
        assert ids == {ORG_A}

        superuser = await s.get(User, 1)
        ids = await organization_service.resolve_visible_organization_ids(s, superuser)
        assert ids is None, "平台管理员返回 None 哨兵（全量），而不是具体集合"
