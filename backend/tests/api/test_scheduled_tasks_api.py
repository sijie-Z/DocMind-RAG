"""定时任务 CRUD API 测试（sqlite 全链 + dependency_overrides；不连真实服务）。

覆盖：创建校验（cron 非法/无解、once 过期、interval 下限、字段错配、时区无效、
无组织 403）/ 归属隔离（他人任务 404、超管全见）/ pause+resume（含过期单次 409）/
run-now（共用引擎入口、重叠响应）/ PATCH 重算与类型守卫 / DELETE。
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401  # 注册全部表供 create_all
from app.core.database import Base
from app.models.scheduled_task import ScheduledTask

BASE = "/api/v1/scheduled-tasks"


def _make_user(uid=1, org=1, superuser=False):
    return SimpleNamespace(
        id=uid,
        username=f"user{uid}",
        organization_id=org,
        is_superuser=superuser,
        is_active=True,
        role="user",
    )


def _utcnow():
    return datetime.now(UTC)


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


async def _create(api, **payload) -> dict:
    body = {"name": "日报", "prompt": "写日报", "schedule_type": "cron", "cron_expr": "0 9 * * *"}
    body.update(payload)
    r = await api.client.post(f"{BASE}/", json=body)
    assert r.status_code == 200, r.text
    return r.json()


# ── 创建 ────────────────────────────────────────────────────────────────


class TestCreate:
    @pytest.mark.asyncio
    async def test_create_cron_defaults(self, api):
        d = await _create(api)
        assert d["schedule_type"] == "cron"
        assert d["enabled"] is True
        assert d["timezone"] == "UTC"  # 服务端默认
        assert d["run_count"] == 0
        assert d["skipped_overlap_count"] == 0
        assert d["last_status"] is None
        assert datetime.fromisoformat(d["next_run_at"]) > _utcnow().replace(tzinfo=None)
        async with api.factory() as s:
            row = (await s.execute(select(ScheduledTask))).scalars().one()
        assert row.created_by == 1
        assert row.organization_id == 1

    @pytest.mark.asyncio
    async def test_create_interval(self, api):
        d = await _create(api, schedule_type="interval", cron_expr=None, interval_seconds=300)
        delta = datetime.fromisoformat(d["next_run_at"]) - _utcnow().replace(tzinfo=None)
        assert timedelta(seconds=280) < delta < timedelta(seconds=320)

    @pytest.mark.asyncio
    async def test_create_once_future(self, api):
        once_at = _utcnow() + timedelta(hours=2)
        d = await _create(api, schedule_type="once", cron_expr=None, once_at=once_at.isoformat())
        assert datetime.fromisoformat(d["next_run_at"]) == once_at.replace(tzinfo=None)

    @pytest.mark.asyncio
    async def test_create_with_timezone(self, api):
        d = await _create(api, timezone="Asia/Shanghai")
        assert d["timezone"] == "Asia/Shanghai"

    @pytest.mark.asyncio
    async def test_create_invalid_cron_400(self, api):
        r = await api.client.post(
            f"{BASE}/",
            json={
                "name": "x",
                "prompt": "p",
                "schedule_type": "cron",
                "cron_expr": "not a cron",
            },
        )
        assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_create_impossible_cron_400(self, api):
        r = await api.client.post(
            f"{BASE}/",
            json={
                "name": "x",
                "prompt": "p",
                "schedule_type": "cron",
                "cron_expr": "0 0 31 2 *",
            },
        )
        assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_create_bad_timezone_400(self, api):
        r = await api.client.post(
            f"{BASE}/",
            json={
                "name": "x",
                "prompt": "p",
                "schedule_type": "cron",
                "cron_expr": "0 9 * * *",
                "timezone": "Mars/Olympus",
            },
        )
        assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_create_once_past_400(self, api):
        r = await api.client.post(
            f"{BASE}/",
            json={
                "name": "x",
                "prompt": "p",
                "schedule_type": "once",
                "once_at": (_utcnow() - timedelta(minutes=1)).isoformat(),
            },
        )
        assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_create_interval_below_floor_422(self, api):
        r = await api.client.post(
            f"{BASE}/",
            json={
                "name": "x",
                "prompt": "p",
                "schedule_type": "interval",
                "interval_seconds": 30,
            },
        )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_create_field_mismatch_422(self, api):
        # cron 类型却给了 interval 字段
        r = await api.client.post(
            f"{BASE}/",
            json={
                "name": "x",
                "prompt": "p",
                "schedule_type": "cron",
                "cron_expr": "0 9 * * *",
                "interval_seconds": 120,
            },
        )
        assert r.status_code == 422
        # cron 类型没给 cron 字段
        r = await api.client.post(
            f"{BASE}/",
            json={
                "name": "x",
                "prompt": "p",
                "schedule_type": "cron",
            },
        )
        assert r.status_code == 422

    @pytest.mark.asyncio
    async def test_create_without_organization_403(self, api):
        api.state.user = _make_user(uid=9, org=None)
        r = await api.client.post(
            f"{BASE}/",
            json={
                "name": "x",
                "prompt": "p",
                "schedule_type": "cron",
                "cron_expr": "0 9 * * *",
            },
        )
        assert r.status_code == 403

    @pytest.mark.asyncio
    async def test_create_paused(self, api):
        d = await _create(api, enabled=False)
        assert d["enabled"] is False
        assert d["next_run_at"] is not None  # 暂停也保留时刻表展示


# ── 列表 / 详情 / 归属隔离 ──────────────────────────────────────────────


class TestOwnership:
    @pytest.mark.asyncio
    async def test_list_only_mine(self, api):
        await _create(api, name="我的")
        api.state.user = _make_user(uid=2)
        await _create(api, name="他的")
        r = await api.client.get(f"{BASE}/")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 1
        assert data["items"][0]["name"] == "他的"

    @pytest.mark.asyncio
    async def test_others_task_is_404(self, api):
        d = await _create(api)
        api.state.user = _make_user(uid=2)
        for method, path in [
            ("get", f"{BASE}/{d['id']}"),
            ("post", f"{BASE}/{d['id']}/pause"),
            ("post", f"{BASE}/{d['id']}/resume"),
            ("post", f"{BASE}/{d['id']}/run-now"),
            ("delete", f"{BASE}/{d['id']}"),
            ("patch", f"{BASE}/{d['id']}"),
        ]:
            kwargs = {"json": {"name": "x"}} if method == "patch" else {}
            r = await getattr(api.client, method)(path, **kwargs)
            assert r.status_code == 404, f"{method} {path} → {r.status_code}"

    @pytest.mark.asyncio
    async def test_superuser_sees_all(self, api):
        d = await _create(api)
        api.state.user = _make_user(uid=99, org=None, superuser=True)
        r = await api.client.get(f"{BASE}/")
        assert r.json()["total"] == 1
        r = await api.client.get(f"{BASE}/{d['id']}")
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_superuser_has_write_access_others(self, api):
        """评审 MINOR-3 记录在案的行为：超管对他人任务全权（含写）。
        对齐仓内 RBAC 上帝模式惯例——本用例把该语义钉住，防无意收紧/放宽。"""
        d = await _create(api)
        api.state.user = _make_user(uid=99, org=None, superuser=True)
        r = await api.client.post(f"{BASE}/{d['id']}/pause")
        assert r.status_code == 200 and r.json()["enabled"] is False
        r = await api.client.patch(f"{BASE}/{d['id']}", json={"name": "超管改名"})
        assert r.status_code == 200 and r.json()["name"] == "超管改名"
        r = await api.client.delete(f"{BASE}/{d['id']}")
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_list_enabled_filter(self, api):
        await _create(api, name="on")
        await _create(api, name="off", enabled=False)
        r = await api.client.get(f"{BASE}/", params={"enabled": "true"})
        assert [i["name"] for i in r.json()["items"]] == ["on"]
        r = await api.client.get(f"{BASE}/", params={"enabled": "false"})
        assert [i["name"] for i in r.json()["items"]] == ["off"]


# ── pause / resume ──────────────────────────────────────────────────────


class TestPauseResume:
    @pytest.mark.asyncio
    async def test_pause_sets_disabled(self, api):
        d = await _create(api)
        r = await api.client.post(f"{BASE}/{d['id']}/pause")
        assert r.status_code == 200 and r.json()["enabled"] is False
        async with api.factory() as s:
            row = await s.get(ScheduledTask, d["id"])
        assert row.enabled is False

    @pytest.mark.asyncio
    async def test_resume_recomputes_past_next(self, api):
        d = await _create(api)
        async with api.factory() as s:
            row = await s.get(ScheduledTask, d["id"])
            row.enabled = False
            row.next_run_at = datetime(2020, 1, 1, 0, 0)  # 暂停期间错过
            await s.commit()
        r = await api.client.post(f"{BASE}/{d['id']}/resume")
        assert r.status_code == 200
        body = r.json()
        assert body["enabled"] is True
        assert datetime.fromisoformat(body["next_run_at"]) > _utcnow().replace(tzinfo=None)

    @pytest.mark.asyncio
    async def test_resume_keeps_future_next(self, api):
        d = await _create(api)
        async with api.factory() as s:
            row = await s.get(ScheduledTask, d["id"])
            future = _utcnow().replace(tzinfo=None) + timedelta(hours=5)
            row.enabled = False
            row.next_run_at = future
            await s.commit()
        r = await api.client.post(f"{BASE}/{d['id']}/resume")
        assert datetime.fromisoformat(r.json()["next_run_at"]) == future

    @pytest.mark.asyncio
    async def test_resume_completed_once_409(self, api):
        d = await _create(
            api,
            schedule_type="once",
            cron_expr=None,
            once_at=(_utcnow() + timedelta(hours=1)).isoformat(),
        )
        async with api.factory() as s:
            row = await s.get(ScheduledTask, d["id"])
            row.enabled = False
            row.next_run_at = None
            row.last_run_at = _utcnow().replace(tzinfo=None)
            row.last_status = "success"
            await s.commit()
        r = await api.client.post(f"{BASE}/{d['id']}/resume")
        assert r.status_code == 409


# ── run-now（引擎桩）────────────────────────────────────────────────────


class TestRunNow:
    @pytest.mark.asyncio
    async def test_triggers_engine_shared_entry(self, api, monkeypatch):
        from app.api.v1.endpoints import scheduled_tasks as ep

        calls = []

        class _Stub:
            async def run_now(self, task_id):
                calls.append(task_id)
                return {"triggered": True, "reason": "manual"}

        monkeypatch.setattr(ep, "scheduler_engine", _Stub())
        d = await _create(api)
        r = await api.client.post(f"{BASE}/{d['id']}/run-now")
        assert r.status_code == 200
        assert r.json() == {"triggered": True, "reason": "manual"}
        assert calls == [d["id"]]

    @pytest.mark.asyncio
    async def test_overlap_response_passthrough(self, api, monkeypatch):
        from app.api.v1.endpoints import scheduled_tasks as ep

        class _Stub:
            async def run_now(self, task_id):
                return {"triggered": False, "reason": "overlap"}

        monkeypatch.setattr(ep, "scheduler_engine", _Stub())
        d = await _create(api)
        r = await api.client.post(f"{BASE}/{d['id']}/run-now")
        assert r.json() == {"triggered": False, "reason": "overlap"}


# ── PATCH ───────────────────────────────────────────────────────────────


class TestPatch:
    @pytest.mark.asyncio
    async def test_rename_and_prompt(self, api):
        d = await _create(api)
        r = await api.client.patch(f"{BASE}/{d['id']}", json={"name": "改", "prompt": "新指令"})
        assert r.status_code == 200
        assert r.json()["name"] == "改" and r.json()["prompt"] == "新指令"

    @pytest.mark.asyncio
    async def test_cron_change_recomputes_next(self, api):
        d = await _create(api)
        old_next = d["next_run_at"]
        r = await api.client.patch(f"{BASE}/{d['id']}", json={"cron_expr": "30 23 * * *"})
        assert r.status_code == 200
        new_next = r.json()["next_run_at"]
        assert new_next != old_next
        assert datetime.fromisoformat(new_next).hour == 23

    @pytest.mark.asyncio
    async def test_spec_field_type_guard(self, api):
        d = await _create(api, schedule_type="interval", cron_expr=None, interval_seconds=300)
        r = await api.client.patch(f"{BASE}/{d['id']}", json={"cron_expr": "0 9 * * *"})
        assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_invalid_patch_timezone_400(self, api):
        d = await _create(api)
        r = await api.client.patch(f"{BASE}/{d['id']}", json={"timezone": "Nope/Zone"})
        assert r.status_code == 400

    @pytest.mark.asyncio
    async def test_once_rearm_after_completion(self, api):
        d = await _create(
            api,
            schedule_type="once",
            cron_expr=None,
            once_at=(_utcnow() + timedelta(hours=1)).isoformat(),
        )
        async with api.factory() as s:
            row = await s.get(ScheduledTask, d["id"])
            row.enabled = False
            row.next_run_at = None
            row.last_run_at = _utcnow().replace(tzinfo=None)
            await s.commit()
        new_at = _utcnow() + timedelta(days=1)
        r = await api.client.patch(f"{BASE}/{d['id']}", json={"once_at": new_at.isoformat()})
        assert r.status_code == 200
        assert datetime.fromisoformat(r.json()["next_run_at"]) == new_at.replace(tzinfo=None)
        # 重新武装后可 resume
        r = await api.client.post(f"{BASE}/{d['id']}/resume")
        assert r.status_code == 200 and r.json()["enabled"] is True


# ── DELETE ──────────────────────────────────────────────────────────────


class TestDelete:
    @pytest.mark.asyncio
    async def test_delete_then_404(self, api):
        d = await _create(api)
        r = await api.client.delete(f"{BASE}/{d['id']}")
        assert r.status_code == 200 and r.json()["success"] is True
        r = await api.client.get(f"{BASE}/{d['id']}")
        assert r.status_code == 404
        async with api.factory() as s:
            assert (await s.execute(select(ScheduledTask))).scalars().all() == []
