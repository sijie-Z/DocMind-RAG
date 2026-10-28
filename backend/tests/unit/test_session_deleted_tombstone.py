"""已删除会话墓碑：迟到 finalize / 复用 id 都不得复活会话。

真实 SQLite + 真实 session_service/chat_service 查询；删除语义按生产实现
（硬删消息与会话 + 同事务墓碑）。覆盖：
- save_session_interaction 在 create_if_missing=True / agent_task_id 组合下
  命中墓碑一律拒绝且零写入；
- 从未存在过的 id 仍保留“缺失即创建”的兼容路径；
- get_or_create_session 对墓碑 id 显式拒绝（RAG 不再按原 id 重建）；
- 019 迁移 upgrade/downgrade 可逆、表结构正确。
"""

import sqlite3
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
from app.exceptions import AuthorizationError
from app.models.chat import ChatMessage, ChatSession, ChatSessionStatus, DeletedChatSession
from app.services.chat_service import get_or_create_session
from app.services.session_service import save_session_interaction

BACKEND_ROOT = Path(__file__).resolve().parents[2]
PREV_REV = "018_rag_source_provenance"
NEW_REV = "019_deleted_chat_sessions"


@pytest.fixture
async def db_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'tombstone.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _seed_session(factory, session_id: str = "sess-1", user_id: int = 1, org: int = 1):
    async with factory() as db:
        db.add(
            ChatSession(
                id=session_id,
                user_id=user_id,
                title="合同核验",
                status=ChatSessionStatus.ACTIVE,
                organization_id=org,
                root_session_id=session_id,
            )
        )
        await db.commit()


async def _delete_with_tombstone(factory, session_id: str = "sess-1"):
    """按生产删除语义：消息+会话硬删 + 同事务墓碑。"""
    async with factory() as db:
        session = await db.get(ChatSession, session_id)
        assert session is not None
        await db.execute(ChatMessage.__table__.delete().where(ChatMessage.session_id == session_id))
        await db.delete(session)
        db.add(
            DeletedChatSession(
                session_id=session_id,
                user_id=session.user_id,
                organization_id=session.organization_id,
            )
        )
        await db.commit()


async def _state(factory, session_id: str):
    async with factory() as db:
        session = await db.get(ChatSession, session_id)
        messages = (
            (await db.execute(select(ChatMessage).where(ChatMessage.session_id == session_id)))
            .scalars()
            .all()
        )
        tombstone = await db.get(DeletedChatSession, session_id)
        return session, list(messages), tombstone


@pytest.mark.asyncio
async def test_late_save_cannot_resurrect_deleted_session(db_factory):
    await _seed_session(db_factory)
    await _delete_with_tombstone(db_factory)

    for kwargs in (
        {"create_if_missing": True},
        {"create_if_missing": True, "agent_task_id": "task-late"},
        {"agent_task_id": None},
    ):
        saved = await save_session_interaction(
            "sess-1",
            1,
            "问题",
            1,
            "回答",
            session_factory=db_factory,
            **kwargs,
        )
        assert saved is None, kwargs

    session, messages, tombstone = await _state(db_factory, "sess-1")
    assert session is None, "删除后不得重建会话"
    assert messages == [], "不得写入任何消息"
    assert tombstone is not None, "墓碑必须仍在"


@pytest.mark.asyncio
async def test_never_seen_id_still_creates_session(db_factory):
    """无墓碑的新 id 保持既有隐式创建语义（客户端预生成 id 兼容）。"""
    saved = await save_session_interaction(
        "sess-new",
        1,
        "问题",
        1,
        "回答",
        session_factory=db_factory,
        create_if_missing=True,
    )
    assert saved is not None

    session, messages, tombstone = await _state(db_factory, "sess-new")
    assert session is not None
    assert len(messages) == 2
    assert tombstone is None


@pytest.mark.asyncio
async def test_tombstone_blocks_existing_resurrected_row(db_factory):
    """修复前已被复活的“残留会话行”也不得继续写入。"""
    await _seed_session(db_factory, session_id="sess-resurrected")
    async with db_factory() as db:
        db.add(
            DeletedChatSession(
                session_id="sess-resurrected",
                user_id=1,
                organization_id=1,
            )
        )
        await db.commit()

    saved = await save_session_interaction(
        "sess-resurrected",
        1,
        "问题",
        1,
        "回答",
        session_factory=db_factory,
        create_if_missing=True,
    )
    assert saved is None

    async with db_factory() as db:
        with pytest.raises(AuthorizationError):
            await get_or_create_session(db, "sess-resurrected", 1, "标题", 1)
        await db.rollback()

    async with db_factory() as db:
        assert await db.get(ChatSession, "sess-resurrected") is not None
        assert (
            await db.execute(
                select(ChatMessage).where(ChatMessage.session_id == "sess-resurrected")
            )
        ).scalars().all() == []


@pytest.mark.asyncio
async def test_tombstone_blocks_other_users_guess(db_factory):
    """他用猜测 id 的保存同样被墓碑拒绝，且不泄露/不写入。"""
    async with db_factory() as db:
        db.add(DeletedChatSession(session_id="sess-gone", user_id=1, organization_id=1))
        await db.commit()

    saved = await save_session_interaction(
        "sess-gone",
        2,
        "问题",
        1,
        "回答",
        session_factory=db_factory,
        create_if_missing=True,
    )
    assert saved is None
    async with db_factory() as db:
        assert await db.get(ChatSession, "sess-gone") is None


@pytest.mark.asyncio
async def test_get_or_create_session_refuses_tombstoned_id(db_factory):
    async with db_factory() as db:
        db.add(DeletedChatSession(session_id="sess-gone", user_id=1, organization_id=1))
        await db.commit()

    async with db_factory() as db:
        with pytest.raises(AuthorizationError):
            await get_or_create_session(db, "sess-gone", 1, "标题", 1)
        await db.rollback()

    session, _, _ = await _state(db_factory, "sess-gone")
    assert session is None, "RAG 不得按墓碑 id 重建会话"


@pytest.mark.asyncio
async def test_get_or_create_session_still_creates_unseen_id(db_factory):
    async with db_factory() as db:
        conversation_id, bound = await get_or_create_session(db, "sess-fresh", 1, "标题", 1)
        await db.commit()
    assert conversation_id == "sess-fresh"
    assert bound == []

    session, _, _ = await _state(db_factory, "sess-fresh")
    assert session is not None


# ── 迁移 019 round-trip ─────────────────────────────────────────────────────


def _cfg() -> Config:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return cfg


def _columns(db_path: Path, table: str) -> dict[str, tuple]:
    with sqlite3.connect(str(db_path)) as conn:
        return {row[1]: row for row in conn.execute(f"PRAGMA table_info({table})")}


def test_019_migration_roundtrip(tmp_path, monkeypatch):
    db_path = tmp_path / "tombstone-019.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = _cfg()
    script = ScriptDirectory.from_config(cfg)

    assert len(NEW_REV) <= 32
    heads = set(script.get_heads())
    assert len(heads) == 1, f"迁移必须单头，实际 {heads}"
    chain = [rev.revision for rev in script.iterate_revisions(next(iter(heads)), "base")]
    assert NEW_REV in chain
    assert chain.index(PREV_REV) == chain.index(NEW_REV) + 1

    command.upgrade(cfg, PREV_REV)
    with sqlite3.connect(str(db_path)) as conn:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert "deleted_chat_sessions" not in tables

    command.upgrade(cfg, "head")
    columns = _columns(db_path, "deleted_chat_sessions")
    assert set(columns) == {"session_id", "user_id", "organization_id", "deleted_at"}
    with sqlite3.connect(str(db_path)) as conn:
        indexes = {
            row[1]: row for row in conn.execute("PRAGMA index_list('deleted_chat_sessions')")
        }
    assert "ix_deleted_chat_sessions_user_id" in indexes, indexes
    assert columns["session_id"][5] == 1, "session_id 必须是主键"
    assert columns["user_id"][3] == 1, "user_id NOT NULL"
    assert columns["organization_id"][3] == 0, "organization_id 可空"
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO deleted_chat_sessions (session_id, user_id, organization_id) "
            "VALUES ('s1', 1, 1)"
        )
        conn.commit()
    with sqlite3.connect(str(db_path)) as conn:
        assert conn.execute("SELECT session_id, user_id FROM deleted_chat_sessions").fetchall() == [
            ("s1", 1)
        ]

    command.downgrade(cfg, PREV_REV)
    with sqlite3.connect(str(db_path)) as conn:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert "deleted_chat_sessions" not in tables

    command.upgrade(cfg, "head")
    with sqlite3.connect(str(db_path)) as conn:
        tables = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert "deleted_chat_sessions" in tables
