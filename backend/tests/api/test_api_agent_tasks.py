"""issue #201 最小版：checkpoint/resume 的 HTTP 面契约（tests/api 惯例：
假鉴权 + 不连真实服务）。

覆盖：鉴权、404（不存在/非本人/组织不一致同一语义）、409（终态 / 并发租约，
detail 回带 state）、SSE 契约（content-type / data 行 / done 带 task_id /
会话补写拼前缀）。checkpoint 与引擎以替身隔离——真实断点续跑行为由
tests/unit/test_agent_recovery_flow.py 的 sqlite 全链用例覆盖。
"""

import json

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
