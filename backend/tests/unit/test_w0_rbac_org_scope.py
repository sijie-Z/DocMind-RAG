"""W0-1 · RBAC 全局旁路收口：四角色 × 跨组织矩阵（先红后绿的判别测试）。

路线依据：ADR-001 D3（per-org RBAC + 显式平台管理员）：
- 平台级操作只认显式 ``is_superuser``；``users.role == "admin"`` 不再映射到
  全局 SysAdmin；
- 组织管理员由 ``user_organization_role_association`` 中的 ``OrgAdmin`` 角色
  判定，只能管理本组织；
- 超管旁路走统一函数，组织资源端点不得散落 ``role == "admin"``。

四角色：
- P  平台超管（is_superuser=True，无组织角色）
- A  A 组织管理员（OrgAdmin 关联 org A，users.role='user'）
- B  B 组织管理员（OrgAdmin 关联 org B）
- M  A 组织普通成员
- L  仅 users.role='admin'（无 is_superuser、无 OrgAdmin 关联）—— 回归靶心

真 SQLite（``Base.metadata.create_all``）+ ``httpx.ASGITransport``，不连
MySQL/Redis/ES；manuals/MCP/llm-config 属平台级，组织管理员一律 403。
"""
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  # 注册全部表供 create_all
from app.core.database import Base
from app.models.manual import SystemManual
from app.models.organization import Organization
from app.models.prompt import PromptTemplate
from app.models.rbac import (
    Permission,
    PermissionType,
    Role,
    role_permission_association,
    user_organization_role_association,
)
from app.models.user import User
from app.models.workflow import Workflow

ORG_ADMIN_ROLE = "OrgAdmin"

# 与 permission_service.initialize_default_permissions_and_roles 同口径的
# 核心权限集（测试内自行种子，避免依赖全局 AsyncSessionLocal）。
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
    ORG_ADMIN_ROLE: [
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
        role=role,
        organization_id=org,
        is_active=True,
        is_superuser=superuser,
    )


SUPERUSER = _user(1, None, superuser=True)
ORG_A_ADMIN = _user(10, 1)
ORG_B_ADMIN = _user(20, 2)
ORG_A_MEMBER = _user(11, 1)
LEGACY_ROLE_ADMIN = _user(12, 1, role="admin")


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
            is_system_role=role_name != ORG_ADMIN_ROLE,
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
            Organization(id=1, name="org-a", owner_id=10),
            Organization(id=2, name="org-b", owner_id=20),
        ])
        await s.flush()
        await _seed_roles(s)

        s.add_all([
            User(id=10, username="a-admin", email="a-admin@example.com", hashed_password="x",
                 role="user", organization_id=1, is_active=True, is_superuser=False),
            User(id=20, username="b-admin", email="b-admin@example.com", hashed_password="x",
                 role="user", organization_id=2, is_active=True, is_superuser=False),
            User(id=11, username="a-member", email="a-member@example.com", hashed_password="x",
                 role="user", organization_id=1, is_active=True, is_superuser=False),
            # 仅 role='admin'：不得获得任何平台/组织管理员权力
            User(id=12, username="legacy-admin", email="legacy@example.com", hashed_password="x",
                 role="admin", organization_id=1, is_active=True, is_superuser=False),
            User(id=1, username="superuser", email="root@example.com", hashed_password="x",
                 role="user", organization_id=None, is_active=True, is_superuser=True),
        ])
        await s.flush()
        await s.execute(
            user_organization_role_association.insert(),
            [
                {"user_id": 10, "organization_id": 1, "role_id": ROLE_IDS[ORG_ADMIN_ROLE]},
                {"user_id": 20, "organization_id": 2, "role_id": ROLE_IDS[ORG_ADMIN_ROLE]},
            ],
        )

        s.add_all([
            PromptTemplate(id=101, name="prompt-a", content="a", is_system=False,
                           organization_id=1, scope="organization", creator_id=11,
                           version=1, is_active=True),
            PromptTemplate(id=201, name="prompt-b", content="b", is_system=False,
                           organization_id=2, scope="organization", creator_id=20,
                           version=1, is_active=True),
            Workflow(id=301, name="wf-a-admin", organization_id=1, created_by=10, is_active=True),
            Workflow(id=302, name="wf-a-member", organization_id=1, created_by=11, is_active=True),
            Workflow(id=303, name="wf-b", organization_id=2, created_by=20, is_active=True),
            Workflow(id=304, name="wf-legacy", organization_id=1, created_by=12, is_active=True),
            SystemManual(id=401, title="published", content="ok", is_published=True),
            SystemManual(id=402, title="draft", content="hidden", is_published=False),
        ])
        await s.commit()

    return engine, factory


@pytest.fixture
async def api():
    from app.core.database import get_db
    from app.core.security import get_current_user
    from app.main import app

    engine, factory = await _setup()
    state = SimpleNamespace(user=ORG_A_MEMBER)

    async def _override_db():
        async with factory() as s:
            yield s

    def _override_user():
        return state.user

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = _override_user
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield SimpleNamespace(client=ac, state=state, factory=factory)
    app.dependency_overrides.clear()
    await engine.dispose()


async def _call(api, user, method: str, url: str, **kwargs):
    api.state.user = user
    return await api.client.request(method, url, **kwargs)


# ── 1. 平台级端点：只认显式 is_superuser ─────────────────────────────


@pytest.mark.asyncio
async def test_legacy_role_admin_cannot_reach_platform_endpoints(api, monkeypatch):
    """仅有 users.role='admin'（无 is_superuser）→ 所有平台级动作 403。

    先红靶心：旧实现把 role=admin 映射为全局 SysAdmin，下列请求全部放行。
    """
    from app.agent.mcp.manager import MCPManager

    monkeypatch.setattr(
        "app.agent.mcp.manager.mcp_manager", MCPManager(configs=[])
    )

    cases = [
        ("GET", "/api/v1/mcp/servers", None),
        ("POST", "/api/v1/prompts/seed", None),
        ("POST", "/api/v1/manuals/", {"title": "x", "content": "y"}),
        ("POST", "/api/v1/organizations/", {"name": "evil-org"}),
        ("POST", "/api/v1/llm-config", {
            "provider": "deepseek", "config_name": "x", "api_key": "sk-x",
        }),
    ]
    for method, url, payload in cases:
        r = await _call(api, LEGACY_ROLE_ADMIN, method, url, json=payload)
        assert r.status_code == 403, f"{method} {url} 应 403，实际 {r.status_code}: {r.text}"


@pytest.mark.asyncio
async def test_org_admin_cannot_reach_platform_endpoints(api, monkeypatch):
    """组织管理员同样不是平台管理员：MCP / 手册写 / llm-config 写 / 建组织 403。"""
    from app.agent.mcp.manager import MCPManager

    monkeypatch.setattr(
        "app.agent.mcp.manager.mcp_manager", MCPManager(configs=[])
    )
    cases = [
        ("GET", "/api/v1/mcp/servers", None),
        ("POST", "/api/v1/manuals/", {"title": "x", "content": "y"}),
        ("POST", "/api/v1/organizations/", {"name": "evil-org"}),
        ("POST", "/api/v1/llm-config", {
            "provider": "deepseek", "config_name": "x", "api_key": "sk-x",
        }),
    ]
    for method, url, payload in cases:
        r = await _call(api, ORG_A_ADMIN, method, url, json=payload)
        assert r.status_code == 403, f"{method} {url} 应 403，实际 {r.status_code}: {r.text}"


@pytest.mark.asyncio
async def test_platform_superuser_keeps_platform_access(api, monkeypatch):
    """超管旁路保留：MCP / 手册写 / 建组织 / seed 正常。"""
    from app.agent.mcp.manager import MCPManager

    monkeypatch.setattr(
        "app.agent.mcp.manager.mcp_manager", MCPManager(configs=[])
    )
    r = await _call(api, SUPERUSER, "GET", "/api/v1/mcp/servers")
    assert r.status_code == 200, r.text
    r = await _call(api, SUPERUSER, "POST", "/api/v1/manuals/",
                    json={"title": "by-root", "content": "ok"})
    assert r.status_code == 200, r.text
    r = await _call(api, SUPERUSER, "POST", "/api/v1/organizations/",
                    json={"name": "new-org"})
    assert r.status_code == 200, r.text
    r = await _call(api, SUPERUSER, "POST", "/api/v1/prompts/seed")
    assert r.status_code == 200, r.text


# ── 2. 提示词：组织模板的激活/修改按 per-org 角色 ─────────────────────


@pytest.mark.asyncio
async def test_prompt_org_scope_matrix(api, monkeypatch):
    from app.services.audit_service import audit_service

    async def _capture(**kwargs):
        return None

    monkeypatch.setattr(audit_service, "log_activity", _capture)

    # 普通成员不能激活/停用组织模板
    r = await _call(api, ORG_A_MEMBER, "PUT", "/api/v1/prompts/101", json={"is_active": False})
    assert r.status_code == 403, r.text

    # 仅 role='admin'、无 OrgAdmin 关联：同样不能
    r = await _call(api, LEGACY_ROLE_ADMIN, "PUT", "/api/v1/prompts/101", json={"is_active": False})
    assert r.status_code == 403, r.text

    # A 组织管理员：本组织 200，B 组织模板 404（不泄露存在性），B 的写不落库
    r = await _call(api, ORG_A_ADMIN, "PUT", "/api/v1/prompts/101", json={"is_active": False})
    assert r.status_code == 200, r.text
    r = await _call(api, ORG_A_ADMIN, "PUT", "/api/v1/prompts/201", json={"is_active": False})
    assert r.status_code == 404, r.text
    async with api.factory() as s:
        b_prompt = await s.get(PromptTemplate, 201)
        assert b_prompt.is_active is True, "跨组织提示词写入不得落库"

    # 创建组织模板：组织管理员可直接激活；普通成员只能落草稿
    r = await _call(api, ORG_A_ADMIN, "POST", "/api/v1/prompts/",
                    json={"name": "admin-made", "content": "x", "scope": "organization"})
    assert r.status_code == 200, r.text
    assert r.json()["is_active"] is True

    r = await _call(api, ORG_A_MEMBER, "POST", "/api/v1/prompts/",
                    json={"name": "member-made", "content": "x", "scope": "organization"})
    assert r.status_code == 200, r.text
    assert r.json()["is_active"] is False, "普通成员创建的模板必须落草稿"


# ── 3. 工作流：组织管理员按 per-org 角色看全组织，仍不跨组织 ─────────


@pytest.mark.asyncio
async def test_workflow_scope_matrix(api):
    r = await _call(api, ORG_A_ADMIN, "GET", "/api/v1/workflows/")
    assert r.status_code == 200, r.text
    ids = {item["id"] for item in r.json()["data"]["items"]}
    assert ids == {301, 302, 304}, "A 管理员应看到 A 组织全部工作流，且看不到 B"

    r = await _call(api, ORG_A_MEMBER, "GET", "/api/v1/workflows/")
    ids = {item["id"] for item in r.json()["data"]["items"]}
    assert ids == {302}, "普通成员只看自己创建的工作流"

    # 仅 role='admin'：按普通成员口径，不再享有组织管理员的全量视图
    r = await _call(api, LEGACY_ROLE_ADMIN, "GET", "/api/v1/workflows/")
    ids = {item["id"] for item in r.json()["data"]["items"]}
    assert ids == {304}, "role='admin' 不得再获得组织管理员的全量视图"

    # 跨组织：A 管理员读写 B 工作流 → 404
    r = await _call(api, ORG_A_ADMIN, "GET", "/api/v1/workflows/303")
    assert r.status_code == 404
    r = await _call(api, ORG_A_ADMIN, "PUT", "/api/v1/workflows/303", json={"name": "hijack"})
    assert r.status_code == 404
    async with api.factory() as s:
        wf = await s.get(Workflow, 303)
        assert wf.name == "wf-b", "跨组织工作流写入不得落库"

    # 本组织：A 管理员可改他人创建的工作流
    r = await _call(api, ORG_A_ADMIN, "PUT", "/api/v1/workflows/302", json={"name": "renamed"})
    assert r.status_code == 200, r.text


# ── 4. 组织设置：写/审计按 per-org 管理员 ─────────────────────────────


@pytest.mark.asyncio
async def test_organization_settings_scope_matrix(api):
    base = "/api/v1/organizations"

    # 普通成员 & 仅 role='admin'：本组织写 403
    for user in (ORG_A_MEMBER, LEGACY_ROLE_ADMIN):
        r = await _call(api, user, "PUT", f"{base}/1/settings", json={"retrieval_top_k": 7})
        assert r.status_code == 403, r.text

    # A 管理员：本组织可写可审计；B 组织 403
    r = await _call(api, ORG_A_ADMIN, "PUT", f"{base}/1/settings", json={"retrieval_top_k": 7})
    assert r.status_code == 200, r.text
    r = await _call(api, ORG_A_ADMIN, "GET", f"{base}/1/settings/audit")
    assert r.status_code == 200, r.text
    r = await _call(api, ORG_A_ADMIN, "PUT", f"{base}/2/settings", json={"retrieval_top_k": 7})
    assert r.status_code == 403, r.text
    r = await _call(api, ORG_A_ADMIN, "GET", f"{base}/2/settings/audit")
    assert r.status_code == 403, r.text

    # 超管：可写任意组织
    r = await _call(api, SUPERUSER, "PUT", f"{base}/2/settings", json={"retrieval_top_k": 9})
    assert r.status_code == 200, r.text


# ── 5. 手册 / MCP：平台级，组织管理员只按普通用户口径读取 ─────────────


@pytest.mark.asyncio
async def test_manuals_hide_drafts_from_org_admin(api):
    r = await _call(api, ORG_A_ADMIN, "GET", "/api/v1/manuals/")
    assert r.status_code == 200, r.text
    titles = {m["title"] for m in r.json()}
    assert titles == {"published"}, "非平台管理员不得看到未发布手册"

    r = await _call(api, SUPERUSER, "GET", "/api/v1/manuals/")
    titles = {m["title"] for m in r.json()}
    assert titles == {"published", "draft"}, "平台超管可见全部手册"

    r = await _call(api, ORG_A_ADMIN, "GET", "/api/v1/manuals/402")
    assert r.status_code == 404, "未发布手册详情对组织管理员不可见"
    r = await _call(api, SUPERUSER, "GET", "/api/v1/manuals/402")
    assert r.status_code == 200, r.text


# ── 6. 知识库：跨组织统计 403；清空图谱按 per-org 管理员 ───────────────


@pytest.mark.asyncio
async def test_member_role_assignment_syncs_org_admin_association(api, monkeypatch):
    """组织成员角色端点是 OrgAdmin 关联的唯一写入口（users.role 只是展示标签）。"""
    from app.services import graph_rag_service as graph_module
    from app.services.audit_service import audit_service

    async def _clear(organization_id=None):
        return None

    async def _audit(**kwargs):
        return None

    monkeypatch.setattr(graph_module.graph_rag_service, "clear", _clear)
    monkeypatch.setattr(audit_service, "log_activity", _audit)

    async def _has_assoc(uid: int, org: int) -> bool:
        async with api.factory() as s:
            rows = await s.execute(
                select(user_organization_role_association.c.role_id)
                .join(Role, Role.id == user_organization_role_association.c.role_id)
                .where(
                    user_organization_role_association.c.user_id == uid,
                    user_organization_role_association.c.organization_id == org,
                    Role.name == ORG_ADMIN_ROLE,
                )
            )
            return rows.first() is not None

    # 超管把 org A 的成员提升为组织管理员 → 关联建立，立刻可管理本组织
    assert not await _has_assoc(11, 1)
    r = await _call(
        api, SUPERUSER, "PATCH",
        "/api/v1/organizations/1/members/11/role", params={"role": "admin"},
    )
    assert r.status_code == 200, r.text
    assert await _has_assoc(11, 1)
    r = await _call(api, ORG_A_MEMBER, "DELETE", "/api/v1/knowledge/graph/clear")
    assert r.status_code == 200, r.text

    # 撤销后立即失效
    r = await _call(
        api, SUPERUSER, "PATCH",
        "/api/v1/organizations/1/members/11/role", params={"role": "user"},
    )
    assert r.status_code == 200, r.text
    assert not await _has_assoc(11, 1)
    r = await _call(api, ORG_A_MEMBER, "DELETE", "/api/v1/knowledge/graph/clear")
    assert r.status_code == 403, r.text

    # users.py 的角色端点同样写关联（另一条既有入口）
    r = await _call(api, SUPERUSER, "PUT", "/api/v1/users/11/role", params={"role": "admin"})
    assert r.status_code == 200, r.text
    assert await _has_assoc(11, 1)
    r = await _call(api, SUPERUSER, "PUT", "/api/v1/users/11/role", params={"role": "user"})
    assert r.status_code == 200, r.text
    assert not await _has_assoc(11, 1)


@pytest.mark.asyncio
async def test_knowledge_scope_matrix(api, monkeypatch):
    from app.services import graph_rag_service as graph_module
    from app.services import knowledge_service as ks_module

    async def _stats(organization_id):
        return {"total_documents": 3}

    async def _clear(organization_id=None):
        cleared.append(organization_id)

    cleared: list[int | None] = []
    monkeypatch.setattr(ks_module.knowledge_service, "get_knowledge_stats", _stats)
    monkeypatch.setattr(graph_module.graph_rag_service, "clear", _clear)

    # 跨组织统计：A 管理员读 B → 403；本组织 → 200
    r = await _call(api, ORG_A_ADMIN, "GET", "/api/v1/knowledge/stats/2")
    assert r.status_code == 403, r.text
    r = await _call(api, ORG_A_ADMIN, "GET", "/api/v1/knowledge/stats/1")
    assert r.status_code == 200, r.text

    # 清空图谱：成员 / 仅 role='admin' → 403；A 管理员只清 A；超管放行
    r = await _call(api, ORG_A_MEMBER, "DELETE", "/api/v1/knowledge/graph/clear")
    assert r.status_code == 403, r.text
    r = await _call(api, LEGACY_ROLE_ADMIN, "DELETE", "/api/v1/knowledge/graph/clear")
    assert r.status_code == 403, r.text
    r = await _call(api, ORG_A_ADMIN, "DELETE", "/api/v1/knowledge/graph/clear")
    assert r.status_code == 200, r.text
    assert cleared[-1] == 1, "组织管理员只能清空本组织图谱"
    r = await _call(api, SUPERUSER, "DELETE", "/api/v1/knowledge/graph/clear")
    assert r.status_code == 200, r.text
