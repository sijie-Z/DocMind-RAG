"""清空会话水位：在途 RAG/Agent 的迟到保存不得把旧问答追加回已清空会话。"""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

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
from app.models.chat import ChatMessage, ChatSession, ChatSessionStatus
from app.services import chat_service
from app.services.session_service import save_session_interaction

CONV = "conv-clear-race"
ORG = 1


class FakeRag:
    def __init__(self, factory):
        self.openai_client = object()
        self.factory = factory
        self.query = None

    async def get_embedding(self, _text):
        return None

    async def search_knowledge_base(self, *_a, **_k):
        return [], None

    async def chat_stream(self, _user_content, _context, _history, **_kwargs):
        self.query = _user_content
        yield "新"
        async with self.factory() as other:
            await other.execute(
                ChatMessage.__table__.delete().where(ChatMessage.session_id == CONV)
            )
            session = await other.get(ChatSession, CONV)
            session.messages_cleared_at = datetime.now(UTC)
            await other.commit()
        yield "答案"

    def report_grounded(self, _has_sources):
        return None

    def get_last_token_usage(self):
        return None


class FakeMemory:
    _embedding_provider = object()

    async def get_context(self, _query):
        return ""

    async def store_interaction(self, _query, _answer):
        return None


class DisabledCache:
    enabled = False

    async def get(self, *_a, **_k):
        return None


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'clear.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        db.add(
            ChatSession(
                id=CONV,
                user_id=1,
                title="clear",
                status=ChatSessionStatus.ACTIVE,
                organization_id=ORG,
                root_session_id=CONV,
            )
        )
        await db.commit()
    yield maker
    await engine.dispose()


@pytest.fixture(autouse=True)
def _fake_deps(monkeypatch):
    monkeypatch.setattr(chat_service, "get_memory_system", lambda *_a, **_k: FakeMemory())
    monkeypatch.setattr(chat_service, "_semantic_cache", DisabledCache())


@pytest.mark.asyncio
async def test_rag_clear_during_stream_drops_late_assistant(factory, monkeypatch):
    fake = FakeRag(factory)
    monkeypatch.setattr(chat_service, "rag_service", fake)
    events: list[chat_service.RAGEvent] = []
    queue: asyncio.Queue = asyncio.Queue()

    async with factory() as db:
        await chat_service.run_rag_pipeline(
            session=db,
            user_content="问题",
            user_id=1,
            search_org_id=ORG,
            organization_id=ORG,
            conversation_id=CONV,
            file_ids=[],
            strict_mode=False,
            privacy_mode=True,
            event_queue=queue,
        )
    while not queue.empty():
        events.append(queue.get_nowait())

    async with factory() as db:
        rows = (
            (await db.execute(select(ChatMessage).where(ChatMessage.session_id == CONV)))
            .scalars()
            .all()
        )
    assert rows == [], "清空后不得追加 user/assistant"
    warnings = [e for e in events if e.type == "warning"]
    assert warnings and "会话已清空" in warnings[0].content
    assert not [e for e in events if e.type == "message"], "不得把完整答案终态发给已清空会话"


@pytest.mark.asyncio
async def test_agent_save_started_before_clear_is_refused(factory):
    cleared_at = datetime.now(UTC)
    async with factory() as db:
        session = await db.get(ChatSession, CONV)
        session.messages_cleared_at = cleared_at
        await db.commit()

    saved = await save_session_interaction(
        CONV,
        1,
        "旧问题",
        ORG,
        "旧回答",
        session_factory=factory,
        create_if_missing=True,
        run_started_at=cleared_at - timedelta(minutes=5),
    )
    assert saved is None
    async with factory() as db:
        rows = (
            (await db.execute(select(ChatMessage).where(ChatMessage.session_id == CONV)))
            .scalars()
            .all()
        )
    assert rows == []


@pytest.mark.asyncio
async def test_agent_save_started_after_clear_is_allowed(factory):
    async with factory() as db:
        session = await db.get(ChatSession, CONV)
        session.messages_cleared_at = datetime.now(UTC) - timedelta(hours=1)
        await db.commit()

    saved = await save_session_interaction(
        CONV,
        1,
        "新问题",
        ORG,
        "新回答",
        session_factory=factory,
        create_if_missing=True,
        run_started_at=datetime.now(UTC),
    )
    assert saved is not None
    async with factory() as db:
        assert (
            (await db.execute(select(ChatMessage).where(ChatMessage.session_id == CONV)))
            .scalars()
            .all()
        )


# ── 迁移 021 ───────────────────────────────────────────────────────────────

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PREV_REV = "020_review_document_meta"
NEW_REV = "021_chat_clear_watermark"


def test_021_migration_roundtrip(tmp_path, monkeypatch):
    import sqlite3

    db_path = tmp_path / "clear-021.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    script = ScriptDirectory.from_config(cfg)

    heads = set(script.get_heads())
    assert len(heads) == 1
    chain = [rev.revision for rev in script.iterate_revisions(next(iter(heads)), "base")]
    assert NEW_REV in chain and PREV_REV in chain

    command.upgrade(cfg, PREV_REV)
    with sqlite3.connect(str(db_path)) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info('chat_sessions')")}
    assert "messages_cleared_at" not in cols

    command.upgrade(cfg, "head")
    with sqlite3.connect(str(db_path)) as conn:
        cols = {row[1]: row for row in conn.execute("PRAGMA table_info('chat_sessions')")}
    assert cols["messages_cleared_at"][3] == 0, "水位必须可空"

    command.downgrade(cfg, PREV_REV)
    with sqlite3.connect(str(db_path)) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info('chat_sessions')")}
    assert "messages_cleared_at" not in cols


@pytest.mark.asyncio
async def test_task_scoped_save_infers_run_start_from_task(factory):
    """ACP/RPC/webhook/resume 未显式传 run_started_at 时，用 task.created_at 兜底。"""
    task_started = datetime.now(UTC) - timedelta(hours=2)
    async with factory() as db:
        db.add(
            AgentTask(
                id="task-old",
                user_id=1,
                organization_id=ORG,
                session_id=CONV,
                status=TaskStatus.RUNNING,
                query="旧任务",
                created_at=task_started,
            )
        )
        session = await db.get(ChatSession, CONV)
        session.messages_cleared_at = datetime.now(UTC)
        await db.commit()

    saved = await save_session_interaction(
        CONV,
        1,
        "旧任务",
        ORG,
        "迟到回答",
        session_factory=factory,
        agent_task_id="task-old",
        create_if_missing=True,
    )
    assert saved is None
    async with factory() as db:
        assert (
            await db.execute(select(ChatMessage).where(ChatMessage.session_id == CONV))
        ).scalars().all() == []


@pytest.mark.asyncio
async def test_task_scoped_save_after_clear_is_allowed(factory):
    """task 在清空之后开始：推断基线晚于水位，正常保存。"""
    async with factory() as db:
        session = await db.get(ChatSession, CONV)
        session.messages_cleared_at = datetime.now(UTC) - timedelta(hours=1)
        db.add(
            AgentTask(
                id="task-new",
                user_id=1,
                organization_id=ORG,
                session_id=CONV,
                status=TaskStatus.RUNNING,
                query="新任务",
                created_at=datetime.now(UTC),
            )
        )
        await db.commit()

    saved = await save_session_interaction(
        CONV,
        1,
        "新任务",
        ORG,
        "新回答",
        session_factory=factory,
        agent_task_id="task-new",
        create_if_missing=True,
    )
    assert saved is not None
