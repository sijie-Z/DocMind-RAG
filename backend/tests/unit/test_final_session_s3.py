"""阶段 3：会话消息幂等、fork 与 Alembic SQLite 判别测试。"""

from __future__ import annotations

import asyncio
import copy
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from alembic import command
from app.core.config import settings
from app.core.database import Base
from app.models.agent_task import AgentTask, TaskStatus
from app.models.chat import ChatMessage, ChatSession, MessageType
from app.services import session_service as session_service_module
from app.services.session_fork_service import _clone_snapshot, fork_session
from app.services.session_service import get_latest_ready_final_task_id, save_session_interaction

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PREV_REV = "016_fork_seq_idempotency"
NEW_REV = "017_agent_message_task_id"


@pytest.fixture
async def db_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'session-s3.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _messages(factory, session_id: str) -> list[ChatMessage]:
    async with factory() as db:
        rows = await db.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.seq, ChatMessage.id)
        )
        return list(rows.scalars().all())


def _cfg() -> Config:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return cfg


def _columns(db_path: Path, table: str) -> set[str]:
    with sqlite3.connect(str(db_path)) as conn:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _exec(db_path: Path, sql: str, params: tuple = ()) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(sql, params)
        conn.commit()


@pytest.mark.asyncio
async def test_same_task_sequential_and_concurrent_save_read_existing_assistant(
    db_factory,
    monkeypatch,
):
    session_id = "session-idem"
    seq_calls = 0
    real_next_seq = session_service_module.next_message_seq

    async def count_next_seq(db, sid):
        nonlocal seq_calls
        seq_calls += 1
        return await real_next_seq(db, sid)

    monkeypatch.setattr(session_service_module, "next_message_seq", count_next_seq)
    first_id = await save_session_interaction(
        session_id,
        1,
        "q",
        1,
        "AUTHORITATIVE",
        session_factory=db_factory,
        agent_task_id="task-sequential",
        create_if_missing=True,
    )
    second_id = await save_session_interaction(
        session_id,
        1,
        "q",
        1,
        "AUTHORITATIVE",
        session_factory=db_factory,
        agent_task_id="task-sequential",
    )
    assert first_id == second_id
    assert seq_calls == 2, "同 task 第二次保存必须只回读，不得再次分配 seq"

    ids = await asyncio.gather(
        *[
            save_session_interaction(
                session_id,
                1,
                "q",
                1,
                "AUTHORITATIVE",
                session_factory=db_factory,
                agent_task_id="task-concurrent",
            )
            for _ in range(6)
        ]
    )
    assert len(set(ids)) == 1
    messages = await _messages(db_factory, session_id)
    assert len(messages) == 4, "两个 task 各一对 user/assistant，不得重复分配 seq"
    assert len({message.seq for message in messages}) == 4
    concurrent = [m for m in messages if m.agent_task_id == "task-concurrent"]
    assert {m.message_type for m in concurrent} == {MessageType.USER, MessageType.ASSISTANT}
    assert all(m.id == ids[0] for m in concurrent if m.message_type == MessageType.ASSISTANT)


@pytest.mark.asyncio
async def test_failed_user_only_resume_backfills_assistant_and_legacy_null_is_unchanged(db_factory):
    session_id = "session-backfill"
    assert (
        await save_session_interaction(
            session_id,
            1,
            "q",
            1,
            "",
            session_factory=db_factory,
            agent_task_id="task-failed",
            create_if_missing=True,
        )
        is None
    )
    assistant_id = await save_session_interaction(
        session_id,
        1,
        "q",
        1,
        "RECOVERED",
        session_factory=db_factory,
        agent_task_id="task-failed",
    )
    assert assistant_id is not None
    assert len(await _messages(db_factory, session_id)) == 2

    # 不传 task id 的旧调用方保留历史行为：NULL 可多值，不做任务级去重。
    for _ in range(2):
        await save_session_interaction(
            "session-legacy",
            1,
            "q",
            1,
            "answer",
            session_factory=db_factory,
        )
    legacy = await _messages(db_factory, "session-legacy")
    assert len(legacy) == 4
    assert all(message.agent_task_id is None for message in legacy)


@pytest.mark.asyncio
async def test_fork_messages_drop_parent_task_id(db_factory):
    session_id = "session-parent"
    assistant_id = await save_session_interaction(
        session_id,
        1,
        "q",
        1,
        "answer",
        session_factory=db_factory,
        agent_task_id="task-parent",
        create_if_missing=True,
    )
    async with db_factory() as db:
        result = await fork_session(
            db,
            source_id=session_id,
            user_id=1,
            organization_id=1,
            message_id=assistant_id,
        )
    copied = await _messages(db_factory, result.session.id)
    assert len(copied) == 2
    assert all(message.agent_task_id is None for message in copied)


@pytest.mark.asyncio
async def test_task_scoped_late_save_does_not_recreate_missing_session(db_factory):
    missing = await save_session_interaction(
        "deleted-session",
        1,
        "q",
        1,
        "late-answer",
        session_factory=db_factory,
        agent_task_id="task-late",
    )
    assert missing is None
    async with db_factory() as db:
        assert await db.get(ChatSession, "deleted-session") is None

    # 只有显式新会话创建入口允许建行；这与 resume 收尾追加是不同语义。
    created_id = await save_session_interaction(
        "explicit-new-session",
        1,
        "q",
        1,
        "answer",
        session_factory=db_factory,
        agent_task_id="task-new",
        create_if_missing=True,
    )
    assert created_id is not None
    async with db_factory() as db:
        created = await db.get(ChatSession, "explicit-new-session")
        assert created is not None
        assert created.root_session_id == "explicit-new-session"


@pytest.mark.asyncio
async def test_latest_ready_final_task_lookup_supports_read_only_resume(db_factory):
    async with db_factory() as db:
        db.add(
            AgentTask(
                id="task-ready",
                user_id=1,
                organization_id=1,
                session_id="session-ready",
                status=TaskStatus.COMPLETED,
                query="q",
                context_snapshot={
                    "answer_contract": "single_final/v1",
                    "final_phase": "ready",
                    "final_answer": "AUTHORITATIVE",
                },
            )
        )
        await db.commit()
        assert await get_latest_ready_final_task_id(db, "session-ready") == "task-ready"


def test_final_checkpoint_fork_clears_delivery_but_preserves_evidence():
    source_snapshot = {
        "session_id": "parent",
        "answer_contract": "single_final/v1",
        "final_phase": "ready",
        "final_input_hash": "sha256:unchanged",
        "final_input": {"query": "q"},
        "final_draft": "PARENT-DRAFT",
        "final_messages": [{"role": "user", "content": "prompt"}],
        "final_answer": "PARENT-ANSWER",
        "partial_output": "MUST-NOT-COPY",
        "ctx": {
            "task_id": "task-parent",
            "artifacts": {"review": {"recommendation": "pass"}, "final_synthesis": {"tokens": 1}},
        },
        "plan": {"steps": [{"id": "s1", "evidence": [{"content": "EVIDENCE"}]}]},
        "run": {},
    }
    original = copy.deepcopy(source_snapshot)
    cloned = _clone_snapshot(
        SimpleNamespace(id="task-parent"),
        source_snapshot,
        cloned_task_id="task-child",
        child_id="child-session",
        source_id="parent-session",
        fork_point_message_id=None,
        user_id=1,
    )

    assert source_snapshot == original
    assert cloned["final_phase"] == "synthesis_pending"
    assert cloned["final_input_hash"] == "sha256:unchanged"
    assert cloned["plan"] == original["plan"]
    assert cloned["ctx"]["task_id"] == "task-child"
    assert cloned["ctx"]["artifacts"] == {}
    assert cloned["partial_output"] == ""
    assert "final_answer" not in cloned
    assert "final_draft" not in cloned
    assert "final_messages" not in cloned


@pytest.mark.asyncio
async def test_agent_save_wrapper_forwards_task_arguments_without_kwargs(monkeypatch):
    captured = {}

    async def explicit_save(
        session_id,
        user_id,
        query,
        organization_id,
        assistant_response="",
        *,
        session_factory,
        bypass_org=False,
        agent_task_id=None,
        create_if_missing=None,
        replace_existing_assistant=False,
    ):
        captured.update(
            session_id=session_id,
            user_id=user_id,
            query=query,
            organization_id=organization_id,
            assistant_response=assistant_response,
            session_factory=session_factory,
            bypass_org=bypass_org,
            agent_task_id=agent_task_id,
            create_if_missing=create_if_missing,
            replace_existing_assistant=replace_existing_assistant,
        )
        return "assistant-id"

    monkeypatch.setattr(
        "app.api.v1.endpoints.agent.save_session_interaction",
        explicit_save,
    )
    from app.api.v1.endpoints.agent import _save_session_interaction

    result = await _save_session_interaction(
        "session-wrapper",
        7,
        "q",
        1,
        "answer",
        bypass_org=True,
        agent_task_id="task-wrapper",
        create_if_missing=True,
    )

    assert result == "assistant-id"
    assert captured["bypass_org"] is True
    assert captured["agent_task_id"] == "task-wrapper"
    assert captured["create_if_missing"] is True
    assert captured["replace_existing_assistant"] is False
    assert captured["session_factory"] is not None


@pytest.mark.asyncio
async def test_agent_chat_asgi_persists_one_message_pair(db_factory, monkeypatch):
    from httpx import ASGITransport, AsyncClient

    import app.core.sse as sse_module
    from app.agent.config import AgentConfig
    from app.agent.events import AgentEvent
    from app.agent.service import agent_service
    from app.core.database import get_db
    from app.core.security import get_current_user
    from app.main import app

    async def fake_chat(**_kwargs):
        yield AgentEvent(type="chunk", content="AUTHORITATIVE", task_id="task-asgi")
        yield AgentEvent(
            type="done",
            final_answer="AUTHORITATIVE",
            task_state="completed",
            task_id="task-asgi",
        )

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
    monkeypatch.setattr(agent_service, "chat", fake_chat)
    monkeypatch.setattr(AgentConfig, "load_from_redis", no_saved_config)
    monkeypatch.setattr(AgentConfig, "save_to_redis", AsyncMock())
    monkeypatch.setattr(
        "app.api.v1.endpoints.agent._load_session_messages", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        "app.api.v1.endpoints.agent.require_organization", AsyncMock(return_value=1)
    )
    monkeypatch.setattr("app.api.v1.endpoints.agent.AsyncSessionLocal", db_factory)
    monkeypatch.setattr(sse_module, "redis_client", None)
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_db] = override_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://asgi.test") as client:
        response = await client.post(
            "/api/v1/agent/chat",
            json={
                "query": "q",
                "session_id": "asgi-session",
                "history": [],
                "enable_tools": False,
                "enable_planning": False,
                "enable_reflection": False,
                "enable_memory": False,
                "enable_thinking": False,
            },
        )
    assert response.status_code == 200, response.text
    assert "AUTHORITATIVE" in response.text

    messages = await _messages(db_factory, "asgi-session")
    assert len(messages) == 2
    assert {message.message_type for message in messages} == {
        MessageType.USER,
        MessageType.ASSISTANT,
    }
    assert all(message.agent_task_id == "task-asgi" for message in messages)
    assert messages[-1].content == "AUTHORITATIVE"


def test_agent_task_message_migration_single_head_sqlite_roundtrip(tmp_path, monkeypatch):
    db_path = tmp_path / "migration-s3.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = _cfg()
    script = ScriptDirectory.from_config(cfg)
    heads = set(script.get_heads())
    assert len(heads) == 1, f"迁移必须单头，实际 {heads}"
    chain = [rev.revision for rev in script.iterate_revisions(next(iter(heads)), "base")]
    assert NEW_REV in chain, f"017 必须仍在 016 之后的迁移主链，实际 {chain}"

    command.upgrade(cfg, PREV_REV)
    _exec(
        db_path,
        "INSERT INTO chat_sessions (id, user_id, title, status, message_count, created_at) "
        "VALUES ('sess', 1, 'legacy', 'ACTIVE', 2, '2026-01-01')",
    )
    for index in (1, 2):
        _exec(
            db_path,
            "INSERT INTO chat_messages (id, session_id, seq, content, message_type, feedback) "
            "VALUES (?, 'sess', ?, 'x', 'USER', 0)",
            (f"old-{index}", index),
        )

    command.upgrade(cfg, "head")
    assert "agent_task_id" in _columns(db_path, "chat_messages")
    # 旧行/新 NULL 行仍可多值；同 task 同类型则必须被数据库拒绝。
    _exec(
        db_path,
        "INSERT INTO chat_messages (id, session_id, seq, content, message_type, feedback) "
        "VALUES ('null-again', 'sess', 3, 'x', 'USER', 0)",
    )
    _exec(
        db_path,
        "INSERT INTO chat_messages "
        "(id, session_id, seq, content, message_type, feedback, agent_task_id) "
        "VALUES ('task-user', 'sess', 4, 'x', 'USER', 0, 'task-1')",
    )
    with pytest.raises(sqlite3.IntegrityError):
        _exec(
            db_path,
            "INSERT INTO chat_messages "
            "(id, session_id, seq, content, message_type, feedback, agent_task_id) "
            "VALUES ('task-user-dup', 'sess', 5, 'x', 'USER', 0, 'task-1')",
        )

    command.downgrade(cfg, PREV_REV)
    assert "agent_task_id" not in _columns(db_path, "chat_messages")
    with sqlite3.connect(str(db_path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0] == 4
    command.upgrade(cfg, "head")
    assert "agent_task_id" in _columns(db_path, "chat_messages")
