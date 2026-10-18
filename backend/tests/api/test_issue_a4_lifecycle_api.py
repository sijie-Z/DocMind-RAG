"""A4 生命周期边界 API 测试（sqlite 全链 + dependency_overrides，不连真实服务）。

覆盖三类边界：
1. 工作流软删（is_active=False）后：列表不出现、详情 404、更新 404
   （含 is_active 翻回 True 的复活路径）；
2. 停用（软删）用户不出现在用户列表；活跃用户照常（误伤对照）；
3. `last_task_id` 悬空引用：agent 任务行被删除（历史清理）后，列表/详情
   读到即置 NULL；agent 任务仍存在时原样保留（误伤对照）。

先红证据：修复前运行本文件，详情/更新软删工作流返回 200、停用用户出现在
列表、悬空 last_task_id 原样返回——对应断言红；修复后全绿。
"""
from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  # 注册全部表供 create_all
from app.core.database import Base
from app.models.agent_task import AgentTask
from app.models.scheduled_task import ScheduledTask, ScheduleType
from app.models.user import User
from app.models.workflow import Workflow


def _make_user(uid=1, org=1, superuser=False, active=True):
    return SimpleNamespace(
        id=uid,
        username=f"user{uid}",
        organization_id=org,
        is_superuser=superuser,
        is_active=active,
        role="user",
    )


@pytest.fixture
async def api():
    from app.core.database import get_db
    from app.core.security import get_current_user
    from app.main import app

    pg = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with pg.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(pg, expire_on_commit=False)
    state = SimpleNamespace(user=_make_user())

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
    await pg.dispose()


# ── 1. 工作流软删过滤 ───────────────────────────────────────────────────


async def _add_workflow(api, *, active=True, name="wf") -> int:
    async with api.factory() as s:
        wf = Workflow(name=name, flow_data=None, created_by=1, is_active=active)
        s.add(wf)
        await s.commit()
        return wf.id


class TestWorkflowSoftDelete:
    @pytest.mark.asyncio
    async def test_soft_deleted_workflow_absent_from_list(self, api):
        await _add_workflow(api, active=True, name="alive")
        await _add_workflow(api, active=False, name="deleted")
        r = await api.client.get("/api/v1/workflows/")
        assert r.status_code == 200
        names = [i["name"] for i in r.json()["data"]["items"]]
        assert names == ["alive"]

    @pytest.mark.asyncio
    async def test_soft_deleted_workflow_detail_404(self, api):
        wf_id = await _add_workflow(api, active=False)
        r = await api.client.get(f"/api/v1/workflows/{wf_id}")
        assert r.status_code == 404, r.text

    @pytest.mark.asyncio
    async def test_soft_deleted_workflow_update_404(self, api):
        wf_id = await _add_workflow(api, active=False)
        # 普通更新同样不可达
        r = await api.client.put(f"/api/v1/workflows/{wf_id}", json={"name": "改名"})
        assert r.status_code == 404, r.text
        # 复活路径（is_active 翻回 True）也被同一过滤封死
        r = await api.client.put(f"/api/v1/workflows/{wf_id}", json={"is_active": True})
        assert r.status_code == 404, r.text
        async with api.factory() as s:
            row = await s.get(Workflow, wf_id)
        assert row.name == "wf" and row.is_active is False

    @pytest.mark.asyncio
    async def test_active_workflow_detail_and_update_ok(self, api):
        """误伤对照：活跃工作流详情/更新照常。"""
        wf_id = await _add_workflow(api, active=True)
        r = await api.client.get(f"/api/v1/workflows/{wf_id}")
        assert r.status_code == 200 and r.json()["data"]["id"] == wf_id
        r = await api.client.put(f"/api/v1/workflows/{wf_id}", json={"name": "改名"})
        assert r.status_code == 200 and r.json()["data"]["name"] == "改名"


# ── 2. 停用用户不出现在用户列表 ────────────────────────────────────────


class TestUserListLifecycle:
    @pytest.mark.asyncio
    async def test_inactive_user_absent_from_list(self, api):
        api.state.user = _make_user(uid=99, org=1, superuser=True)
        async with api.factory() as s:
            s.add_all([
                User(id=1, username="active1", email="a1@example.com",
                     hashed_password="x", organization_id=1, is_active=True),
                User(id=2, username="deleted1", email="d1@example.com",
                     hashed_password="x", organization_id=1, is_active=False),
            ])
            await s.commit()
        r = await api.client.get("/api/v1/users/")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["total"] == 1
        assert [u["username"] for u in data["users"]] == ["active1"]

    @pytest.mark.asyncio
    async def test_active_users_still_listed(self, api):
        """误伤对照：全部活跃时列表与总数口径不变。"""
        api.state.user = _make_user(uid=99, org=1, superuser=True)
        async with api.factory() as s:
            s.add_all([
                User(id=1, username="a", email="a@example.com",
                     hashed_password="x", organization_id=1, is_active=True),
                User(id=2, username="b", email="b@example.com",
                     hashed_password="x", organization_id=1, is_active=True),
            ])
            await s.commit()
        r = await api.client.get("/api/v1/users/")
        assert r.json()["total"] == 2
        assert {u["username"] for u in r.json()["users"]} == {"a", "b"}


# ── 3. last_task_id 悬空引用清理 ───────────────────────────────────────


async def _add_ref_task(api, *, last_task_id="at-1") -> str:
    async with api.factory() as s:
        s.add(ScheduledTask(
            id="st1",
            name="日报",
            prompt="写日报",
            schedule_type=ScheduleType.CRON,
            cron_expr="0 9 * * *",
            timezone="UTC",
            enabled=True,
            next_run_at=datetime(2030, 1, 1),
            created_by=1,
            organization_id=1,
            last_task_id=last_task_id,
            last_run_at=datetime(2026, 10, 10, 12, 0),
            last_status="success",
            run_count=1,
        ))
        await s.commit()
    return "st1"


async def _add_agent_task(api, task_id="at-1") -> None:
    async with api.factory() as s:
        s.add(AgentTask(id=task_id, user_id=1, query="写日报"))
        await s.commit()


async def _delete_agent_task(api, task_id="at-1") -> None:
    """模拟 agent 任务历史被删除/清理（运维保留策略；应用内当前无删除入口）。"""
    async with api.factory() as s:
        await s.execute(delete(AgentTask).where(AgentTask.id == task_id))
        await s.commit()


class TestDanglingLastTaskRef:
    @pytest.mark.asyncio
    async def test_reference_kept_while_agent_task_exists(self, api):
        """误伤对照：agent 任务行仍在时引用原样保留。"""
        await _add_agent_task(api)
        await _add_ref_task(api)
        r = await api.client.get("/api/v1/scheduled-tasks/st1")
        assert r.status_code == 200
        assert r.json()["last_task_id"] == "at-1"

    @pytest.mark.asyncio
    async def test_dangling_reference_cleared_on_detail(self, api):
        await _add_agent_task(api)
        await _add_ref_task(api)
        await _delete_agent_task(api)
        r = await api.client.get("/api/v1/scheduled-tasks/st1")
        assert r.status_code == 200
        assert r.json()["last_task_id"] is None
        # 运行事实保留：置 NULL 只清链接，不抹掉「跑过、结果如何」
        assert r.json()["last_status"] == "success"
        async with api.factory() as s:
            row = await s.get(ScheduledTask, "st1")
        assert row.last_task_id is None

    @pytest.mark.asyncio
    async def test_dangling_reference_cleared_on_list(self, api):
        await _add_agent_task(api)
        await _add_ref_task(api)
        await _delete_agent_task(api)
        r = await api.client.get("/api/v1/scheduled-tasks/")
        assert r.status_code == 200
        items = r.json()["items"]
        assert len(items) == 1 and items[0]["last_task_id"] is None
        async with api.factory() as s:
            rows = (await s.execute(select(ScheduledTask))).scalars().all()
        assert [row.last_task_id for row in rows] == [None]

    @pytest.mark.asyncio
    async def test_patch_response_has_no_dangling_reference(self, api):
        """更新响应同样走对账入口（_get_owned_task 共同前置）。"""
        await _add_ref_task(api)
        r = await api.client.patch(
            "/api/v1/scheduled-tasks/st1", json={"name": "改名"}
        )
        assert r.status_code == 200
        assert r.json()["last_task_id"] is None
        assert r.json()["name"] == "改名"

    @pytest.mark.asyncio
    async def test_pause_response_has_no_dangling_reference(self, api):
        await _add_ref_task(api)
        r = await api.client.post("/api/v1/scheduled-tasks/st1/pause")
        assert r.status_code == 200
        assert r.json()["last_task_id"] is None
        assert r.json()["enabled"] is False

    @pytest.mark.asyncio
    async def test_reconcile_cas_does_not_clobber_new_reference(self, api):
        """并发保护：对账基于旧快照，但库内引用已被改成新的有效 id 时，
        CAS 匹配 0 行——不覆盖并发写入的有效链接。"""
        from app.api.v1.endpoints.scheduled_tasks import (
            _clear_dangling_last_task_ids,
        )

        await _add_agent_task(api, "at-2")
        await _add_ref_task(api, last_task_id="at-1")  # at-1 不存在 = 悬空
        async with api.factory() as s:
            task = await s.get(ScheduledTask, "st1")  # 读出悬空值（旧快照）
            # 模拟并发写入者（如引擎 finalize）把引用改成有效的新 id；
            # synchronize_session=False 保持内存对象为旧快照。
            await s.execute(
                update(ScheduledTask)
                .where(ScheduledTask.id == "st1")
                .values(last_task_id="at-2")
                .execution_options(synchronize_session=False)
            )
            await s.commit()
            assert task.last_task_id == "at-1"  # 快照仍为旧值
            await _clear_dangling_last_task_ids(s, [task])  # CAS 应失配
            assert task.last_task_id == "at-2"  # refresh 后对齐库内新值
        async with api.factory() as s:
            row = await s.get(ScheduledTask, "st1")
        assert row.last_task_id == "at-2"

    @pytest.mark.asyncio
    async def test_reconcile_failure_degrades_open(self):
        """对账失败 fail-open：只读端点不得因清理失败而 500。"""
        from app.api.v1.endpoints.scheduled_tasks import (
            _clear_dangling_last_task_ids,
        )

        class _BoomSession:
            async def execute(self, *args, **kwargs):
                raise RuntimeError("db down")

            async def rollback(self):
                pass

        stale = SimpleNamespace(id="st1", last_task_id="at-1")
        # 不抛异常即通过（内部告警并保留原值）
        await _clear_dangling_last_task_ids(_BoomSession(), [stale])
