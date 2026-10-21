"""A2 租户边界：工作流双组织矩阵（先红后绿的判别测试）。

矩阵四个端点 × 两向：
- org A 的 admin 对 org B 的工作流：列表不可见 / 详情 404 / 更新 404 / 删除 404 / 执行 404；
- 同组织内：admin 全量、普通用户仅见本人创建，创建落在自己的组织。

真 SQLite（``Base.metadata.create_all``）+ ``httpx.ASGITransport``，不依赖任何外部服务。
"""

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.core.database import get_db
from app.core.security import get_current_user
from app.models import Base
from app.models.organization import Organization
from app.models.rbac import Role, user_organization_role_association
from app.models.user import User
from app.models.workflow import Workflow, WorkflowExecution

_MIN_FLOW = {
    "nodes": [
        {
            "id": "in1",
            "type": "input",
            "data": {"prompt": "{{input}}"},
            "position": {"x": 0, "y": 0},
        },
        {"id": "out1", "type": "output", "data": {}, "position": {"x": 1, "y": 0}},
    ],
    "edges": [{"id": "e1", "source": "in1", "target": "out1"}],
}


def _user(uid: int, org: int | None, *, role: str = "user", superuser: bool = False) -> User:
    return User(
        id=uid,
        username=f"user{uid}",
        email=f"user{uid}@example.com",
        hashed_password="x",
        role=role,
        organization_id=org,
        is_active=True,
        is_superuser=superuser,
    )


async def _setup():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        s.add_all(
            [
                Organization(id=1, name="OrgA", owner_id=1),
                Organization(id=2, name="OrgB", owner_id=3),
                _user(1, 1, role="admin"),  # org A 管理员
                _user(2, 1, role="user"),  # org A 普通用户
                _user(3, 2, role="admin"),  # org B 管理员
                _user(99, None, role="admin", superuser=True),  # 全局超管
            ]
        )
        await s.flush()
        # W0-1：组织管理员 = per-org OrgAdmin 角色关联（users.role 不再参与鉴权）
        org_admin_role = Role(name="OrgAdmin", description="组织管理员", is_system_role=False)
        s.add(org_admin_role)
        await s.flush()
        await s.execute(
            user_organization_role_association.insert(),
            [
                {"user_id": 1, "organization_id": 1, "role_id": org_admin_role.id},
                {"user_id": 3, "organization_id": 2, "role_id": org_admin_role.id},
            ],
        )
        s.add_all(
            [
                Workflow(name="wf-A-admin", flow_data=_MIN_FLOW, created_by=1, organization_id=1),
                Workflow(name="wf-A-user", flow_data=_MIN_FLOW, created_by=2, organization_id=1),
                Workflow(name="wf-B-admin", flow_data=_MIN_FLOW, created_by=3, organization_id=2),
            ]
        )
        await s.commit()
        ids = dict(
            wf_a_admin=(
                await s.execute(select(Workflow.id).where(Workflow.name == "wf-A-admin"))
            ).scalar_one(),
            wf_a_user=(
                await s.execute(select(Workflow.id).where(Workflow.name == "wf-A-user"))
            ).scalar_one(),
            wf_b=(
                await s.execute(select(Workflow.id).where(Workflow.name == "wf-B-admin"))
            ).scalar_one(),
        )
    return engine, factory, ids


async def _call(factory, user, method: str, url: str, **kwargs):
    from app.main import app

    async def _override_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_user] = lambda: user
    try:
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            return await ac.request(method, url, **kwargs)
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_org_admin_list_only_sees_own_org():
    engine, factory, ids = await _setup()
    try:
        r = await _call(factory, _user(1, 1, role="admin"), "GET", "/api/v1/workflows/")
        assert r.status_code == 200, r.text
        ids_in_list = {item["id"] for item in r.json()["data"]["items"]}
        assert ids["wf_a_admin"] in ids_in_list
        assert ids["wf_b"] not in ids_in_list, "org A 管理员不得看到 org B 的工作流"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_org_admin_cannot_get_foreign_workflow():
    engine, factory, ids = await _setup()
    try:
        r = await _call(
            factory, _user(1, 1, role="admin"), "GET", f"/api/v1/workflows/{ids['wf_b']}"
        )
        assert r.status_code == 404, "跨组织详情必须 404（不泄露存在性）"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_org_admin_cannot_update_foreign_workflow():
    engine, factory, ids = await _setup()
    try:
        r = await _call(
            factory,
            _user(1, 1, role="admin"),
            "PUT",
            f"/api/v1/workflows/{ids['wf_b']}",
            json={"name": "hijacked"},
        )
        assert r.status_code == 404, "跨组织更新必须 404"
        async with factory() as s:
            wf = await s.get(Workflow, ids["wf_b"])
            assert wf.name == "wf-B-admin", "跨组织写入不得落库"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_org_admin_cannot_delete_foreign_workflow():
    engine, factory, ids = await _setup()
    try:
        r = await _call(
            factory, _user(1, 1, role="admin"), "DELETE", f"/api/v1/workflows/{ids['wf_b']}"
        )
        assert r.status_code == 404, "跨组织删除必须 404"
        async with factory() as s:
            wf = await s.get(Workflow, ids["wf_b"])
            assert wf.is_active is True, "跨组织删除不得生效"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_org_admin_cannot_execute_foreign_workflow():
    engine, factory, ids = await _setup()
    try:
        r = await _call(
            factory,
            _user(1, 1, role="admin"),
            "POST",
            f"/api/v1/workflows/{ids['wf_b']}/execute",
            params={"stream": "false"},
            json={"text": "x"},
        )
        assert r.status_code == 404, "跨组织执行必须 404"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_same_org_admin_has_full_access_and_create_sets_org():
    engine, factory, ids = await _setup()
    try:
        admin_a = _user(1, 1, role="admin")
        r = await _call(factory, admin_a, "GET", f"/api/v1/workflows/{ids['wf_a_admin']}")
        assert r.status_code == 200
        r = await _call(
            factory,
            admin_a,
            "PUT",
            f"/api/v1/workflows/{ids['wf_a_admin']}",
            json={"name": "renamed"},
        )
        assert r.status_code == 200 and r.json()["data"]["name"] == "renamed"
        r = await _call(factory, admin_a, "POST", "/api/v1/workflows/", json={"name": "new-wf"})
        assert r.status_code == 200, r.text
        new_id = r.json()["data"]["id"]
        async with factory() as s:
            created = await s.get(Workflow, new_id)
            assert created is not None
            assert created.organization_id == 1, "创建工作流必须落在调用者的组织"
            assert created.created_by == 1
        r = await _call(factory, admin_a, "DELETE", f"/api/v1/workflows/{ids['wf_a_admin']}")
        assert r.status_code == 200
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_regular_user_in_same_org_sees_only_own_workflows():
    engine, factory, ids = await _setup()
    try:
        r = await _call(factory, _user(2, 1, role="user"), "GET", "/api/v1/workflows/")
        assert r.status_code == 200
        ids_in_list = {item["id"] for item in r.json()["data"]["items"]}
        assert ids_in_list == {ids["wf_a_user"]}, "普通用户只能看到本组织内自己创建的工作流"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_superuser_sees_all_orgs():
    engine, factory, ids = await _setup()
    try:
        r = await _call(
            factory, _user(99, None, role="admin", superuser=True), "GET", "/api/v1/workflows/"
        )
        assert r.status_code == 200
        ids_in_list = {item["id"] for item in r.json()["data"]["items"]}
        assert {ids["wf_a_admin"], ids["wf_a_user"], ids["wf_b"]} <= ids_in_list
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_orgless_non_superuser_fails_closed():
    engine, factory, _ = await _setup()
    try:
        r = await _call(factory, _user(50, None, role="user"), "GET", "/api/v1/workflows/")
        assert r.status_code == 403, "无组织用户必须 fail-closed（403），不得落到任何组织"
    finally:
        await engine.dispose()


async def _seed_executions(factory, ids: dict) -> None:
    async with factory() as s:
        s.add_all(
            [
                WorkflowExecution(id=101, workflow_id=ids["wf_a_admin"], status="completed"),
                WorkflowExecution(id=102, workflow_id=ids["wf_a_user"], status="completed"),
                WorkflowExecution(id=103, workflow_id=ids["wf_b"], status="completed"),
            ]
        )
        await s.commit()


@pytest.mark.asyncio
async def test_execution_detail_isolated_across_orgs_and_owners():
    """A2 评审 m-6：执行记录详情的三向矩阵（跨组织 / 同组织他人 / 本人）。"""
    engine, factory, ids = await _setup()
    try:
        await _seed_executions(factory, ids)
        # 跨组织：org A admin 读 org B 的执行记录 → 404
        r = await _call(
            factory, _user(1, 1, role="admin"), "GET", "/api/v1/workflows/executions/103"
        )
        assert r.status_code == 404, "跨组织执行记录必须 404"
        # 同组织他人（普通用户读 admin 的）→ 404
        r = await _call(
            factory, _user(2, 1, role="user"), "GET", "/api/v1/workflows/executions/101"
        )
        assert r.status_code == 404, "同组织普通用户不得读他人执行记录"
        # 本人 → 200
        r = await _call(
            factory, _user(2, 1, role="user"), "GET", "/api/v1/workflows/executions/102"
        )
        assert r.status_code == 200, r.text
        # 同组织 admin → 200
        r = await _call(
            factory, _user(1, 1, role="admin"), "GET", "/api/v1/workflows/executions/101"
        )
        assert r.status_code == 200, r.text
    finally:
        await engine.dispose()
