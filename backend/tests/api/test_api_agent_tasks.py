"""issue #201 最小版：checkpoint/resume 的 HTTP 面契约（tests/api 惯例：
假鉴权 + 不连真实服务）。

覆盖：鉴权、404（不存在/非本人/组织不一致同一语义）、409（终态 / 并发租约，
detail 回带 state）、SSE 契约（content-type / data 行 / done 带 task_id /
会话补写拼前缀）。404/409 契约用例以替身隔离（真实断点续跑行为由
tests/unit/test_agent_recovery_flow.py 的 sqlite 全链用例覆盖）；末段
无组织用户 fail-closed 用例（评审 B1）走**真实链路**：真实路由 +
真实 require_organization + 真实 checkpoint + sqlite + 假 LLM（httpx ASGITransport，
与评审探针同款驱动方式——替身测不出 None 组织语义）。
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.agent import checkpoint
from app.agent.events import AgentEvent
from app.agent.service import agent_service
from app.core.security import get_current_user
from app.models.agent_task import AgentTask, TaskStatus

_TASK_ID = "task00000001"


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


@pytest.fixture
def auth():
    """登录态替身（依赖注入覆盖）；用完即撤，避免污染同进程其它 api 用例。"""
    from app.main import app
    user = _mock_user()
    app.dependency_overrides[get_current_user] = lambda: user
    yield user
    app.dependency_overrides.pop(get_current_user, None)


def _mock_user(user_id: int = 1, org_id: int | None = 1):
    class _User:
        pass

    user = _User()
    user.id = user_id
    user.organization_id = org_id
    user.is_active = True
    return user


def _task(status: TaskStatus = TaskStatus.RUNNING) -> AgentTask:
    """不落库的 AgentTask 实例（状态视图/SSE 契约测试专用）。"""
    return AgentTask(
        id=_TASK_ID,
        user_id=1,
        organization_id=1,
        query="原问题",
        status=status,
        completed_steps=1,
        total_steps=3,
        progress=0.3333,
        current_step_id="s2",
        retry_count=1,
    )


def test_get_task_requires_auth(client):
    r = client.get(f"/api/v1/agent/tasks/{_TASK_ID}")
    assert r.status_code == 401, "未登录不得读取任务状态"


def test_get_task_returns_status_view(client, auth, monkeypatch):
    captured = {}

    async def _load(**kwargs):
        captured.update(kwargs)
        return _task()

    monkeypatch.setattr(checkpoint, "load_task", _load)
    r = client.get(f"/api/v1/agent/tasks/{_TASK_ID}")

    assert r.status_code == 200
    data = r.json()["data"]
    assert data["task_id"] == _TASK_ID
    assert data["state"] == "running"
    assert data["is_recoverable"] is True
    assert data["completed_steps"] == 1 and data["total_steps"] == 3
    assert data["current_step_id"] == "s2"
    assert data["query"] == "原问题"
    assert captured == {"task_id": _TASK_ID, "user_id": 1, "organization_id": 1}, (
        "归属校验必须带 用户 id + 组织（组织不一致也按 404 处理）"
    )


def test_get_task_404_for_missing_or_foreign(client, auth, monkeypatch):
    async def _raise(**kwargs):
        raise checkpoint.TaskNotFoundError("任务不存在或无权访问")

    monkeypatch.setattr(checkpoint, "load_task", _raise)
    r = client.get(f"/api/v1/agent/tasks/{_TASK_ID}")

    assert r.status_code == 404
    body = r.json()
    assert body["error_code"] == "TASK_NOT_FOUND"


def test_resume_404_for_missing_or_foreign(client, auth, monkeypatch):
    async def _raise(*args, **kwargs):
        raise checkpoint.TaskNotFoundError("任务不存在或无权访问")

    monkeypatch.setattr(agent_service, "prepare_resume", _raise)
    r = client.post(f"/api/v1/agent/tasks/{_TASK_ID}/resume")

    assert r.status_code == 404
    assert r.json()["error_code"] == "TASK_NOT_FOUND"


def test_resume_409_for_terminal_task_with_state_detail(client, auth, monkeypatch):
    async def _raise(*args, **kwargs):
        raise checkpoint.TaskNotResumableError(
            "任务已处于终态 completed，不可恢复",
            detail={"task_id": _TASK_ID, "state": "completed"},
        )

    monkeypatch.setattr(agent_service, "prepare_resume", _raise)
    r = client.post(f"/api/v1/agent/tasks/{_TASK_ID}/resume")

    assert r.status_code == 409
    body = r.json()
    assert body["error_code"] == "TASK_NOT_RESUMABLE"
    assert body["detail"]["state"] == "completed", "409 必须可自解释（回带当前 state）"


def test_resume_409_when_lease_is_held(client, auth, monkeypatch):
    async def _raise(*args, **kwargs):
        raise checkpoint.TaskBusyError("任务正在被另一个恢复请求处理")

    monkeypatch.setattr(agent_service, "prepare_resume", _raise)
    r = client.post(f"/api/v1/agent/tasks/{_TASK_ID}/resume")

    assert r.status_code == 409
    assert r.json()["error_code"] == "TASK_BUSY"


def test_resume_streams_sse_contract_and_passes_claim_false(client, auth, monkeypatch):
    """SSE 契约：content-type + data 行 + done 带 task_id；预检认领已完成
    （resume_chat 必须 claim=False，不得重复认领）。"""
    calls: dict = {}

    async def _prepare(task_id, user_id, organization_id=None):
        calls["prepare"] = (task_id, user_id, organization_id)
        return _task(), {"session_id": "", "partial_output": ""}

    async def _fake_resume(task_id, user_id, *, config=None, claim=True):
        calls["resume"] = {"task_id": task_id, "claim": claim}
        yield AgentEvent(type="thinking", content="继续执行", plan_step_id="s3")
        yield AgentEvent(type="chunk", content="后续产出")
        yield AgentEvent(type="done", task_id=task_id)

    monkeypatch.setattr(agent_service, "prepare_resume", _prepare)
    monkeypatch.setattr(agent_service, "resume_chat", _fake_resume)

    r = client.post(f"/api/v1/agent/tasks/{_TASK_ID}/resume")

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/event-stream")
    lines = [ln for ln in r.text.split("\n\n") if ln.startswith("data: ")]
    payloads = [json.loads(ln[len("data: "):]) for ln in lines]
    assert payloads[0]["type"] == "thinking"
    assert any(p["type"] == "chunk" and p["content"] == "后续产出" for p in payloads)
    assert payloads[-1] == {"type": "done", "content": "", "task_id": _TASK_ID}
    assert calls["prepare"] == (_TASK_ID, 1, 1)
    assert calls["resume"] == {"task_id": _TASK_ID, "claim": False}


def test_resume_saves_session_interaction_with_prefix(client, auth, monkeypatch):
    """收尾补写会话：完整回复 = 快照前缀（崩溃前已产出）+ 续跑新增；
    done 事件带 message_id。"""
    import app.api.v1.endpoints.agent as agent_api

    saved: dict = {}

    async def _prepare(task_id, user_id, organization_id=None):
        return _task(), {"session_id": "sess-9", "partial_output": "前半"}

    async def _fake_resume(task_id, user_id, *, config=None, claim=True):
        yield AgentEvent(type="chunk", content="后半")

    async def _save(session_id, user_id, query, organization_id, assistant_response=""):
        saved.update(session_id=session_id, user_id=user_id, query=query,
                     organization_id=organization_id, answer=assistant_response)
        return "msg-1"

    monkeypatch.setattr(agent_service, "prepare_resume", _prepare)
    monkeypatch.setattr(agent_service, "resume_chat", _fake_resume)
    monkeypatch.setattr(agent_api, "_save_session_interaction", _save)

    r = client.post(f"/api/v1/agent/tasks/{_TASK_ID}/resume")

    assert r.status_code == 200
    assert saved == {
        "session_id": "sess-9", "user_id": 1, "query": "原问题",
        "organization_id": 1, "answer": "前半后半",
    }
    assert json.loads(r.text.strip().split("\n\n")[-1][len("data: "):])["message_id"] == "msg-1"


# ── 无组织用户 fail-closed（评审 B1：真实链路判别用例）───────────────────────

class _FakeRedisLock:
    """断言"零认领"用的最小租约替身（SET NX EX 语义）。"""

    def __bool__(self):
        return True

    def __init__(self):
        self.store: dict[str, str] = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def delete(self, key):
        self.store.pop(key, None)
        return 1

    async def expire(self, key, ttl):
        return key in self.store


@pytest.mark.asyncio
async def test_no_org_user_is_rejected_fail_closed_on_both_endpoints(tmp_path, monkeypatch):
    """B1 判别（评审探针 test_07 转正）：organization_id=None 的用户，对"自己
    旧组织（org=1）下"的任务必须被双端点 403 拒绝，且零副作用（零执行/零流/
    零认领/任务未被推进）——与 /chat 的 S3 纪律一致。此前实现把 None 原样传到
    load_task，组织守卫整段跳过，无组织用户可 GET 200 + resume 200 并在旧组织
    执行上下文里续跑。

    走真实链路：真实 require_organization + 真实 checkpoint（sqlite）+ 假 LLM。
    变异点：去掉任一端点的 `require_organization` 前置 → 对应请求 200 → 本用例精确红。
    """
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import app.api.v1.endpoints.agent as agent_api
    import app.models  # noqa: F401 — 注册全部表，供 create_all
    from app.agent.exec_context import ExecutionContext
    from app.agent.loop import PERAgentLoop
    from app.agent.planner import Plan, PlanStep
    from app.core.database import Base
    from app.main import app

    # sqlite 工厂（checkpoint 与端点模块共用）+ 副作用隔离
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'noorg.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(checkpoint, "AsyncSessionLocal", factory)
    monkeypatch.setattr(agent_api, "AsyncSessionLocal", factory)

    async def _save(self, ttl=86400 * 7):
        return False

    monkeypatch.setattr(ExecutionContext, "save", _save)
    monkeypatch.setattr(PERAgentLoop, "_write_experiences", AsyncMock())

    fake_redis = _FakeRedisLock()
    monkeypatch.setattr(checkpoint, "redis_client", fake_redis)

    # 种一个"真实可续跑"的任务：org=1、s1 已完成、s2 pending——
    # 若 fail-open，resume 会 200 开流并在 org=1 上下文里再执行一步。
    task_id = await checkpoint.create_task(user_id=1, organization_id=1, query="原问题")
    ctx = ExecutionContext(query="原问题", task_id=task_id)
    plan = Plan(id="p1", goal="g", steps=[
        PlanStep(id="s1", description="一", status="completed", result="R1"),
        PlanStep(id="s2", description="二", dependencies=["s1"]),
    ], completed_steps=1)
    assert await checkpoint.save_checkpoint(
        task_id=task_id, ctx=ctx, plan=plan, trigger="after_each_step", partial_output="R1",
    )

    calls = {"n": 0}

    async def _create(**kwargs):
        calls["n"] += 1
        msg = SimpleNamespace(content=f"结果{calls['n']}", tool_calls=None, role="assistant")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=msg, finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1),
        )

    fake_client = MagicMock()
    fake_client.chat.completions.create = _create
    monkeypatch.setattr(agent_service, "_client", fake_client, raising=False)

    # 鉴权替身：无组织用户（org=None）——require_organization 对其真实生效
    app.dependency_overrides[get_current_user] = lambda: _mock_user(org_id=None)
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://noorg"
        ) as c:
            r = await c.get(f"/api/v1/agent/tasks/{task_id}")
            assert r.status_code == 403, (
                f"无组织用户 GET 必须 fail-closed（与 /chat 一致），实际 {r.status_code}"
            )
            assert r.headers["content-type"].startswith("application/json")

            r2 = await c.post(f"/api/v1/agent/tasks/{task_id}/resume")
            assert r2.status_code == 403, (
                f"无组织用户 resume 必须 fail-closed，实际 {r2.status_code}"
            )
            assert r2.headers["content-type"].startswith("application/json"), "不得开 SSE"
            assert r2.json()["error_code"] == "AUTHORIZATION_ERROR"
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert calls["n"] == 0, "被拒的请求零执行（不得发起任何 LLM 调用）"
    assert fake_redis.store == {}, "被拒的请求零认领（租约未动）"

    async with factory() as db:
        t = await db.get(AgentTask, task_id)
    assert t.status == TaskStatus.RUNNING and t.retry_count == 0, "任务未被推进"
    await engine.dispose()
