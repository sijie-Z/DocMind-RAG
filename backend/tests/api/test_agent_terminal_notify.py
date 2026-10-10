"""Agent 终态站内通知（后端 C）—— completed / failed / cancelled 闭环判别。

覆盖（tests/api 惯例：假鉴权 + 真路由 + sqlite + 假 LLM）：
- completed 全链（/chat 规划关 → 跑完 → COMPLETED）→ Notification 行落库：
  type=agent、target_route="Agent"、target_id=task_id（前端既有跳转链按 ?id=
  导航到 /agent 打开回放）、标题含 task_id（防 create_notification 的
  title+user 1 分钟去重把同分钟的第二个任务通知吞掉）；
- failed / cancelled：直接驱动 service 终态收敛点（真实 mark_terminal + 真实
  通知链）；
- 诚实降级：通知发送抛异常 → 任务状态不回滚（仍 COMPLETED）、SSE 收尾不受
  扰动（无 error 事件、done 照发）；
- 只在状态真落库后发：非法转换（mark_terminal 拒绝）→ 零通知。

变异判别：
- M2 终态不发通知（删 _finalize_task_terminal 的通知调用）→ 用例 1 红；
- M5 通知异常向上抛（去掉 notify_task_terminal 的 try/except）→ 用例 4 红。
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.agent.service as service_mod
import app.api.v1.endpoints.agent as agent_api
import app.models  # noqa: F401 — 注册全部表，供 create_all
from app.agent import checkpoint
from app.agent.exec_context import ExecutionContext
from app.agent.loop import PERAgentLoop
from app.agent.service import agent_service
from app.core.database import Base
from app.core.security import get_current_user
from app.main import app
from app.models.agent_task import AgentTask, TaskStatus
from app.models.notification import Notification

_CHAT_BODY = {
    "query": "给我做件事",
    "enable_planning": False,
    "enable_reflection": False,
    "enable_memory": False,
    "enable_thinking": True,
}


def _mock_user():
    class _User:
        id = 1
        organization_id = 1
        is_active = True

    return _User()


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """与 HITL 全环同款隔离：回放落盘不写真文件、经验写库替身。"""
    async def _save(self, ttl=86400 * 7):
        return False

    monkeypatch.setattr(ExecutionContext, "save", _save)
    monkeypatch.setattr(PERAgentLoop, "_write_experiences", AsyncMock())


@pytest.fixture
async def db(tmp_path, monkeypatch):
    """sqlite 全链工厂：checkpoint / service（通知旁路）/ 端点共用。"""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'notify.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(checkpoint, "AsyncSessionLocal", factory)
    monkeypatch.setattr(agent_api, "AsyncSessionLocal", factory)
    monkeypatch.setattr(service_mod, "AsyncSessionLocal", factory)
    yield factory
    await engine.dispose()


def _fake_client(text: str = "任务完成。"):
    async def _create(*, messages, tools=None, **kwargs):
        msg = SimpleNamespace(content=text, tool_calls=None, role="assistant")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=msg, finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
        )

    client = MagicMock()
    client.chat.completions.create = _create
    return client


async def _notifications(factory):
    async with factory() as session:
        rows = (
            await session.execute(select(Notification).where(Notification.user_id == 1))
        ).scalars().all()
    return list(rows)


async def _task_status(factory, task_id: str) -> TaskStatus:
    async with factory() as session:
        t = await session.get(AgentTask, task_id)
        return t.status


async def _run_chat(c) -> tuple[list[dict], str]:
    r = await c.post("/api/v1/agent/chat", json=_CHAT_BODY)
    assert r.status_code == 200
    payloads = [
        json.loads(ln[len("data: "):]) for ln in r.text.splitlines() if ln.startswith("data: ")
    ]
    task_ids = {p.get("task_id") for p in payloads if p.get("task_id")}
    assert len(task_ids) == 1, f"整条流必须只对应一个 task_id，实际 {task_ids}"
    return payloads, task_ids.pop()


# ── completed：全链 ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_completed_run_notifies_with_agent_link(db, monkeypatch):
    """跑完 → COMPLETED → 站内通知（含 task 链接与去重安全的标题）。

    变异点 M2：删掉 _finalize_task_terminal 的 notify 调用 → 本用例红。
    """
    monkeypatch.setattr(agent_service, "_client", _fake_client(), raising=False)
    app.dependency_overrides[get_current_user] = lambda: _mock_user()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://notify"
        ) as c:
            payloads, task_id = await _run_chat(c)
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert not any(p.get("type") == "error" for p in payloads), "正常收尾不得有 error 事件"
    assert await _task_status(db, task_id) == TaskStatus.COMPLETED

    rows = await _notifications(db)
    assert len(rows) == 1, "完成的 run 必须恰发一条站内通知"
    n = rows[0]
    assert n.type == "agent"
    assert n.target_route == "Agent", "跳转约定：路由名（前端既有链）"
    assert n.target_id == task_id, "task 链接：/agent?id=<task_id> 直达回放"
    assert task_id in n.title, "标题带 task_id——防 1 分钟窗口去重吞通知"
    assert "已完成" in n.title
    assert "给我做件事" in n.content


# ── failed / cancelled：收敛点直驱（真实 mark_terminal + 通知链）─────────────


@pytest.mark.asyncio
async def test_failed_finalize_notifies_failure(db):
    task_id = await checkpoint.create_task(user_id=1, organization_id=1, query="会炸的任务")
    ev = await agent_service._finalize_task_terminal(
        task_id, failed_run=True, failure_text="LLM Error: boom"
    )
    assert ev is None, "落库成功时零扰动（既有语义）"
    assert await _task_status(db, task_id) == TaskStatus.FAILED

    rows = await _notifications(db)
    assert len(rows) == 1
    assert "失败" in rows[0].title and task_id in rows[0].title
    assert "boom" in rows[0].content, "失败原因如实进通知正文"
    assert rows[0].target_id == task_id


@pytest.mark.asyncio
async def test_cancelled_finalize_notifies_cancelled(db):
    task_id = await checkpoint.create_task(user_id=1, organization_id=1, query="要取消的任务")
    ev = await agent_service._finalize_task_cancelled(task_id)
    assert ev is None
    assert await _task_status(db, task_id) == TaskStatus.CANCELLED

    rows = await _notifications(db)
    assert len(rows) == 1
    assert "已取消" in rows[0].title and task_id in rows[0].title
    assert rows[0].target_route == "Agent" and rows[0].target_id == task_id


@pytest.mark.asyncio
async def test_cancel_endpoint_waiting_approval_notifies(db):
    """审批等待中经端点直接取消（无活跃流）→ 终态通知同样发出。"""
    task_id = await checkpoint.create_task(user_id=1, organization_id=1, query="待审批任务")
    assert await checkpoint.mark_waiting_approval(task_id)

    app.dependency_overrides[get_current_user] = lambda: _mock_user()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://notify2"
        ) as c:
            r = await c.post(f"/api/v1/agent/tasks/{task_id}/cancel")
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert r.status_code == 200 and r.json()["data"]["state"] == "cancelled"
    assert await _task_status(db, task_id) == TaskStatus.CANCELLED
    rows = await _notifications(db)
    assert len(rows) == 1 and "已取消" in rows[0].title
    assert rows[0].target_id == task_id


# ── 诚实降级 / 不越权发送 ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_notification_failure_does_not_rollback_task_or_stream(db, monkeypatch):
    """通知发送失败：任务终态不回滚、SSE 收尾不受扰动（旁路非主链）。

    变异点 M5：去掉 notify_task_terminal 的 try/except（异常向上抛）→
    本用例红（流内出现 error 事件）。
    """
    monkeypatch.setattr(agent_service, "_client", _fake_client(), raising=False)

    async def _boom(*args, **kwargs):
        raise RuntimeError("notification channel down")

    monkeypatch.setattr(service_mod, "create_notification", _boom)

    app.dependency_overrides[get_current_user] = lambda: _mock_user()
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://notify3"
        ) as c:
            payloads, task_id = await _run_chat(c)
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert await _task_status(db, task_id) == TaskStatus.COMPLETED, (
        "通知失败绝不影响已落的任务终态"
    )
    assert not any(p.get("type") == "error" for p in payloads), (
        "通知失败不得把错误冒泡进用户的 SSE 流"
    )
    assert await _notifications(db) == []


@pytest.mark.asyncio
async def test_no_notify_when_terminal_write_rejected(db):
    """只在状态真落库后发：非法转换（已 CANCELLED → COMPLETED 拒绝）→ 零通知。"""
    task_id = await checkpoint.create_task(user_id=1, organization_id=1, query="已取消任务")
    assert await checkpoint.mark_terminal(
        task_id, TaskStatus.CANCELLED, error="x", error_type="user_cancelled"
    )
    rows_before = await _notifications(db)

    ev = await agent_service._finalize_task_terminal(task_id, failed_run=False)
    assert ev is not None, "收尾失败按既有语义如实返回错误事件"
    assert await _task_status(db, task_id) == TaskStatus.CANCELLED, "任务状态不得被改写"
    assert await _notifications(db) == rows_before == [], "状态未落库则零通知"


@pytest.mark.asyncio
async def test_same_minute_tasks_get_distinct_notifications(db):
    """同分钟、同 query 的两个任务：标题含各自 task_id → 两条通知互不吞并。"""
    t1 = await checkpoint.create_task(user_id=1, organization_id=1, query="同一查询")
    t2 = await checkpoint.create_task(user_id=1, organization_id=1, query="同一查询")
    await agent_service._finalize_task_terminal(t1, failed_run=False)
    await agent_service._finalize_task_terminal(t2, failed_run=False)

    rows = await _notifications(db)
    assert len(rows) == 2, "通知去重（title+user 1 分钟）不得吞掉第二个任务的完成通知"
    assert {r.target_id for r in rows} == {t1, t2}
    assert len({r.title for r in rows}) == 2
