"""legacy Agent 全败：诚实终态、失败不落占位、resume 权威答案入库。

全部使用 fake provider + 真实内存 SQLite + 真实 ASGI 端点；不访问模型、
MySQL、Redis 或工具服务。legacy 配置即 ``AGENT_UNIFIED_FINAL_ENABLED=False``。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — 注册全部表
from app.agent import checkpoint
from app.agent import executor as executor_module
from app.agent.config import AgentConfig
from app.agent.events import AgentEvent, FinalAnswerAccumulator
from app.agent.exec_context import ExecutionContext
from app.agent.service import agent_service
from app.api.v1.endpoints.agent import _final_save_allowed
from app.core.config import ai as ai_config
from app.core.database import Base, get_db
from app.core.security import get_current_user
from app.models.agent_task import AgentTask, TaskStatus
from app.models.chat import ChatMessage, ChatSession, ChatSessionStatus, MessageType
from app.services.session_service import save_session_interaction
from tests.unit.test_final_synthesis_s2 import _Executor, _FakeProvider, _loop, _stored

SESSION_ID = "legacy-failed-session"
TASK_ID = "legacy-failed-task"
QUERY = "解释第20条"
PLACEHOLDER = executor_module.STEP_FAILURE_PLACEHOLDER
REAL_ANSWER = "恢复成功后的权威答案"


class _ScriptedExecutor(_Executor):
    """用固定文本模拟 legacy executor 的最终 chunk（成功/占位/有效降级）。"""

    def __init__(
        self,
        memory,
        statuses: tuple[str, ...],
        *,
        completed_text: str,
        failed_text: str,
    ) -> None:
        super().__init__(memory, statuses)
        self.completed_text = completed_text
        self.failed_text = failed_text

    def _fill_step(self, step, plan, *, retry: bool = False) -> None:
        super()._fill_step(step, plan, retry=retry)
        step.result = self.completed_text if step.status == "completed" else self.failed_text
        step.local_synthesis = _stored(step.result)


def _legacy_loop(
    *,
    statuses: tuple[str, ...],
    completed_text: str = "成功结果",
    failed_text: str = PLACEHOLDER,
):
    loop = _loop(_FakeProvider("unused-by-legacy-executor"), statuses=statuses)
    loop._executor = _ScriptedExecutor(
        loop.memory,
        statuses,
        completed_text=completed_text,
        failed_text=failed_text,
    )
    return loop


def _events(*events: AgentEvent) -> FinalAnswerAccumulator:
    answers = FinalAnswerAccumulator()
    for event in events:
        answers.add(event)
    return answers


@pytest.fixture(autouse=True)
def _legacy_and_no_external_context(monkeypatch):
    monkeypatch.setattr(ai_config, "unified_final_enabled", lambda: False)
    monkeypatch.setattr(ExecutionContext, "save", AsyncMock())


@pytest.fixture
async def db_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'legacy-failed.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _messages(factory, session_id: str = SESSION_ID) -> list[ChatMessage]:
    async with factory() as db:
        rows = await db.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.seq, ChatMessage.id)
        )
        return list(rows.scalars().all())


async def _task(factory, task_id: str = TASK_ID) -> AgentTask | None:
    async with factory() as db:
        return await db.get(AgentTask, task_id)


async def _seed_session_and_task(factory, *, status: TaskStatus = TaskStatus.RUNNING) -> None:
    async with factory() as db:
        db.add(
            ChatSession(
                id=SESSION_ID,
                user_id=7,
                organization_id=1,
                title=QUERY,
                status=ChatSessionStatus.ACTIVE,
                root_session_id=SESSION_ID,
            )
        )
        db.add(
            AgentTask(
                id=TASK_ID,
                user_id=7,
                organization_id=1,
                session_id=SESSION_ID,
                status=status,
                query=QUERY,
            )
        )
        await db.commit()


def _sse_payloads(text: str) -> list[dict]:
    return [
        json.loads(line[len("data: ") :]) for line in text.splitlines() if line.startswith("data: ")
    ]


@pytest.mark.asyncio
async def test_legacy_all_failed_done_is_failed_and_accumulator_cannot_save():
    loop = _legacy_loop(statuses=("failed",))

    events = [event async for event in loop.run(QUERY)]
    done = [event for event in events if event.type == "done"][-1]
    answers = _events(*events)

    assert loop.run_failed is True
    assert done.task_state == "failed"
    assert done.final_answer == ""
    assert answers.blocked is True
    assert answers.task_state == "failed"
    assert answers.answer == ""
    assert answers.can_save is False
    assert _final_save_allowed("done", answers) is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("statuses", "completed_text", "failed_text"),
    [
        (("completed",), "完整成功", PLACEHOLDER),
        (("completed", "failed"), "部分成功", PLACEHOLDER),
        (("failed",), PLACEHOLDER, "有效降级：基于缓存给出可用结论"),
    ],
    ids=["success", "partial-success", "valid-degradation"],
)
async def test_legacy_success_partial_success_and_valid_degradation_stay_completed(
    statuses, completed_text, failed_text
):
    loop = _legacy_loop(
        statuses=statuses,
        completed_text=completed_text,
        failed_text=failed_text,
    )

    events = [event async for event in loop.run(QUERY)]
    done = [event for event in events if event.type == "done"][-1]
    answers = _events(*events)

    assert loop.run_failed is False
    assert done.task_state == "completed"
    assert answers.blocked is False
    assert _final_save_allowed("done", answers) is True


def test_final_save_requires_explicit_completed_terminal_state():
    legacy_without_state = _events(
        AgentEvent(type="chunk", content="PARTIAL"),
        AgentEvent(type="done", task_state=""),
    )
    assert legacy_without_state.answer == "PARTIAL"
    assert _final_save_allowed("done", legacy_without_state) is False

    completed = _events(
        AgentEvent(type="chunk", content="PARTIAL"),
        AgentEvent(type="done", task_state="completed"),
    )
    assert _final_save_allowed("done", completed) is True


@pytest.mark.asyncio
async def test_authoritative_resume_replaces_existing_failed_placeholder_in_place(db_factory):
    assistant_id = await save_session_interaction(
        SESSION_ID,
        7,
        QUERY,
        1,
        PLACEHOLDER,
        session_factory=db_factory,
        agent_task_id=TASK_ID,
        create_if_missing=True,
    )
    before = await _messages(db_factory)
    old_assistant = next(m for m in before if m.message_type == MessageType.ASSISTANT)

    replaced_id = await save_session_interaction(
        SESSION_ID,
        7,
        QUERY,
        1,
        REAL_ANSWER,
        session_factory=db_factory,
        agent_task_id=TASK_ID,
        replace_existing_assistant=True,
    )
    after = await _messages(db_factory)

    assert replaced_id == assistant_id
    assert len(after) == 2
    replacement = next(m for m in after if m.message_type == MessageType.ASSISTANT)
    assert replacement.id == old_assistant.id
    assert replacement.seq == old_assistant.seq
    assert replacement.content == REAL_ANSWER

    # 普通同 task 幂等保存仍不回写：权威替换只由 resume/审批收尾显式开启。
    same_id = await save_session_interaction(
        SESSION_ID,
        7,
        QUERY,
        1,
        "另一段迟到文本",
        session_factory=db_factory,
        agent_task_id=TASK_ID,
    )
    unchanged = await _messages(db_factory)
    assert same_id == assistant_id
    assert next(m for m in unchanged if m.message_type == MessageType.ASSISTANT).content == (
        REAL_ANSWER
    )


async def _asgi_client(db_factory, monkeypatch):
    import app.api.v1.endpoints.agent as agent_api
    import app.core.sse as sse_module
    from app.main import app

    async def no_saved_config(*_args, **_kwargs):
        return None

    async def override_db():
        async with db_factory() as session:
            yield session

    user = SimpleNamespace(
        id=7,
        organization_id=1,
        is_active=True,
        is_superuser=False,
        token_organization_id=None,
    )

    monkeypatch.setattr(agent_api, "AsyncSessionLocal", db_factory)
    monkeypatch.setattr(checkpoint, "AsyncSessionLocal", db_factory)
    monkeypatch.setattr(agent_api, "_load_session_messages", AsyncMock(return_value=[]))
    monkeypatch.setattr(agent_api, "require_organization", AsyncMock(return_value=1))
    monkeypatch.setattr(AgentConfig, "load_from_redis", no_saved_config)
    monkeypatch.setattr(AgentConfig, "save_to_redis", AsyncMock())
    monkeypatch.setattr("app.agent.service.notify_task_terminal", AsyncMock())
    monkeypatch.setattr(sse_module, "redis_client", None)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = override_db

    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://legacy-failed.test"), app


def _stamp(stream, task_id: str = TASK_ID):
    async def _wrapped():
        async for event in stream:
            event.task_id = task_id
            yield event

    return _wrapped()


@pytest.mark.asyncio
async def test_successful_legacy_asgi_still_saves_completed_assistant(db_factory, monkeypatch):
    await _seed_session_and_task(db_factory, status=TaskStatus.RUNNING)
    client, app = await _asgi_client(db_factory, monkeypatch)

    async def fake_success_chat(**_kwargs):
        loop = _legacy_loop(statuses=("completed",), completed_text=REAL_ANSWER)
        async for event in loop.run(QUERY, task_id=TASK_ID, session_id=SESSION_ID):
            yield event
        await agent_service._finalize_task_terminal(
            TASK_ID,
            failed_run=bool(loop.run_failed),
            failure_text=loop.run_failure,
        )

    monkeypatch.setattr(agent_service, "chat", fake_success_chat)
    try:
        response = await client.post(
            "/api/v1/agent/chat",
            json={"query": QUERY, "session_id": SESSION_ID, "history": []},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200, response.text
    done = _sse_payloads(response.text)[-1]
    assert done["task_state"] == "completed"
    assert done["saved"] is True
    assert done["message_id"]
    messages = await _messages(db_factory)
    assert [m.message_type for m in messages] == [MessageType.USER, MessageType.ASSISTANT]
    assert messages[1].id == done["message_id"]
    assert messages[1].content == REAL_ANSWER
    completed_task = await _task(db_factory)
    assert completed_task is not None and completed_task.status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_failed_legacy_asgi_does_not_save_assistant_and_resume_keeps_real_answer(
    db_factory, monkeypatch
):
    await _seed_session_and_task(db_factory, status=TaskStatus.RUNNING)
    client, app = await _asgi_client(db_factory, monkeypatch)

    async def fake_failed_chat(**_kwargs):
        loop = _legacy_loop(statuses=("failed",))
        async for event in loop.run(QUERY, task_id=TASK_ID, session_id=SESSION_ID):
            yield event
        await agent_service._finalize_task_terminal(
            TASK_ID,
            failed_run=bool(loop.run_failed),
            failure_text=loop.run_failure,
        )

    monkeypatch.setattr(agent_service, "chat", fake_failed_chat)
    try:
        response = await client.post(
            "/api/v1/agent/chat",
            json={"query": QUERY, "session_id": SESSION_ID, "history": []},
        )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db, None)
    assert response.status_code == 200, response.text
    failed_payloads = _sse_payloads(response.text)
    failed_done = failed_payloads[-1]
    assert failed_done["type"] == "done"
    assert failed_done["task_state"] == "failed"
    assert failed_done["saved"] is False
    assert "message_id" not in failed_done
    assert await _messages(db_factory) == []
    failed_task = await _task(db_factory)
    assert failed_task is not None and failed_task.status == TaskStatus.FAILED

    async def fake_prepare(task_id, user_id, organization_id=None):
        async with db_factory() as db:
            task = await db.get(AgentTask, TASK_ID)
            # Real prepare_resume -> begin_resume claims FAILED→RUNNING first.
            task.status = TaskStatus.RUNNING
            task.retry_count += 1
            await db.commit()
        return task, {
            "session_id": SESSION_ID,
            "partial_output": "",
            "ctx": {"query": QUERY},
            "config": {},
        }

    async def fake_resume(task_id, user_id, **_kwargs):
        loop = _legacy_loop(statuses=("completed",), completed_text=REAL_ANSWER)
        async for event in loop.run(QUERY, task_id=TASK_ID, session_id=SESSION_ID):
            yield event
        await agent_service._finalize_task_terminal(
            TASK_ID,
            failed_run=bool(loop.run_failed),
            failure_text=loop.run_failure,
        )

    monkeypatch.setattr(agent_service, "prepare_resume", fake_prepare)
    monkeypatch.setattr(agent_service, "resume_chat", fake_resume)
    client, app = await _asgi_client(db_factory, monkeypatch)
    try:
        resumed = await client.post(f"/api/v1/agent/tasks/{TASK_ID}/resume")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db, None)
    assert resumed.status_code == 200, resumed.text
    resumed_done = _sse_payloads(resumed.text)[-1]
    assert resumed_done["type"] == "done"
    assert resumed_done["task_state"] == "completed"
    assert resumed_done["saved"] is True
    assert resumed_done["message_id"]

    messages = await _messages(db_factory)
    assert [m.message_type for m in messages] == [MessageType.USER, MessageType.ASSISTANT]
    assistant = messages[1]
    assert assistant.id == resumed_done["message_id"]
    assert assistant.content == REAL_ANSWER
    assert assistant.content != PLACEHOLDER
    assert assistant.agent_task_id == TASK_ID
    completed_task = await _task(db_factory)
    assert completed_task is not None and completed_task.status == TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_legacy_resume_replaces_preexisting_failed_placeholder(db_factory, monkeypatch):
    await _seed_session_and_task(db_factory, status=TaskStatus.FAILED)
    old_assistant_id = await save_session_interaction(
        SESSION_ID,
        7,
        QUERY,
        1,
        PLACEHOLDER,
        session_factory=db_factory,
        agent_task_id=TASK_ID,
    )
    before = await _messages(db_factory)
    old_assistant = next(m for m in before if m.message_type == MessageType.ASSISTANT)
    assert old_assistant.id == old_assistant_id

    async def fake_prepare(task_id, user_id, organization_id=None):
        async with db_factory() as db:
            task = await db.get(AgentTask, TASK_ID)
            # Real prepare_resume -> begin_resume claims FAILED→RUNNING first.
            task.status = TaskStatus.RUNNING
            task.retry_count += 1
            await db.commit()
        return task, {
            "session_id": SESSION_ID,
            "partial_output": "",
            "ctx": {"query": QUERY},
            "config": {},
        }

    async def fake_resume(task_id, user_id, **_kwargs):
        loop = _legacy_loop(statuses=("completed",), completed_text=REAL_ANSWER)
        async for event in loop.run(QUERY, task_id=TASK_ID, session_id=SESSION_ID):
            yield event
        await agent_service._finalize_task_terminal(
            TASK_ID,
            failed_run=bool(loop.run_failed),
            failure_text=loop.run_failure,
        )

    monkeypatch.setattr(agent_service, "prepare_resume", fake_prepare)
    monkeypatch.setattr(agent_service, "resume_chat", fake_resume)
    client, app = await _asgi_client(db_factory, monkeypatch)
    try:
        response = await client.post(f"/api/v1/agent/tasks/{TASK_ID}/resume")
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 200, response.text
    done = _sse_payloads(response.text)[-1]
    assert done["saved"] is True
    assert done["message_id"] == old_assistant_id

    messages = await _messages(db_factory)
    assert len(messages) == 2
    assistant = next(m for m in messages if m.message_type == MessageType.ASSISTANT)
    assert assistant.id == old_assistant_id
    assert assistant.seq == old_assistant.seq
    assert assistant.content == REAL_ANSWER
    assert all(m.content != PLACEHOLDER for m in messages)
