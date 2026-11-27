"""批次八 · 会话树导航：迁移 / 树结构 / 分支上下文 / 标签 / 摘要。

判别性回归点（任一被改坏，对应用例必红）：

- 迁移 043 必须是唯一 head、只追加四列；历史行（042 时插入）升级后照常加载，
  四列全为 NULL；downgrade 只删这四列，数据行与既有列不受影响；
- 树按 root_session_id/parent_session_id 还原 3 级结构（深度 0/1/2），
  每个分支的工件计数/版本按 conversation_id 隔离，绝不串到其它分支；
- 切换分支的 branch-context 是**只读**：会话数、消息数在调用前后完全不变
  （切视图 ≠ fork）；
- fake 摘要确定性（同一输入逐字相同）且会持久化；无消息时如实「无摘要」；
- LLM 摘要不可用时抛 503 ``SUMMARY_MODEL_UNAVAILABLE``，且不落任何摘要
  （绝不降级到 fake 冒充成功）；
- 跨组织/跨用户访问 tree/branch-context/label/summary 一律 404（不泄露存在性）。
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — 注册全部表，供 create_all
from alembic import command
from app.core.config import settings
from app.core.database import Base
from app.exceptions import NotFoundError, ValidationError
from app.models.artifact import AgentArtifact
from app.models.chat import ChatMessage, ChatSession, ChatSessionStatus, MessageType
from app.services import session_fork_service as svc
from app.services.session_fork_service import BranchSummaryUnavailableError

BACKEND_ROOT = Path(__file__).resolve().parents[2]
MIGRATION = "043_session_tree"
PREVIOUS = "042_rubric_eval"
NEW_COLUMNS = ("branch_label", "branch_summary", "summary_model", "summarized_at")

USER_ID = 1
ORG_ID = 1


# ── 基座 ────────────────────────────────────────────────────────────────────


@pytest.fixture
async def db_factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'session-tree.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    yield factory
    await engine.dispose()


async def _mk_session(
    db,
    sid: str,
    *,
    user_id: int = USER_ID,
    org_id: int | None = ORG_ID,
    title: str = "分支会话",
    parent_session_id: str | None = None,
    root_session_id: str | None = None,
    fork_point_message_id: str | None = None,
    branch_label: str | None = None,
    branch_summary: str | None = None,
    summary_model: str | None = None,
) -> ChatSession:
    session = ChatSession(
        id=sid,
        user_id=user_id,
        organization_id=org_id,
        title=title,
        status=ChatSessionStatus.ACTIVE,
        parent_session_id=parent_session_id,
        root_session_id=root_session_id or sid,
        fork_point_message_id=fork_point_message_id,
        branch_label=branch_label,
        branch_summary=branch_summary,
        summary_model=summary_model,
    )
    db.add(session)
    await db.flush()
    return session


async def _mk_msg(
    db, sid: str, seq: int, mtype: MessageType, content: str, *, mid: str | None = None
) -> ChatMessage:
    msg = ChatMessage(
        id=mid or str(uuid4()),
        session_id=sid,
        seq=seq,
        content=content,
        message_type=mtype,
    )
    db.add(msg)
    await db.flush()
    return msg


async def _mk_artifact(
    db,
    aid: str,
    conversation_id: str,
    *,
    version_no: int = 1,
    user_id: int = USER_ID,
    org_id: int = ORG_ID,
) -> AgentArtifact:
    row = AgentArtifact(
        id=aid,
        organization_id=org_id,
        user_id=user_id,
        conversation_id=conversation_id,
        kind="markdown",
        title=f"artifact {aid}",
        mime_type="text/markdown",
        content_sha256=hashlib.sha256(aid.encode("utf-8")).hexdigest(),
        file_size=3,
        version_no=version_no,
        status="ready",
    )
    db.add(row)
    await db.flush()
    return row


# ── 迁移 ────────────────────────────────────────────────────────────────────


def _cfg() -> Config:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return cfg


def _revision(db_path: Path) -> set[str]:
    with sqlite3.connect(str(db_path)) as conn:
        return {row[0] for row in conn.execute("SELECT version_num FROM alembic_version")}


def _columns(db_path: Path, table: str = "chat_sessions") -> dict[str, tuple]:
    with sqlite3.connect(str(db_path)) as conn:
        return {row[1]: row for row in conn.execute(f"PRAGMA table_info({table})")}


def test_043_single_head_migration_source_guard():
    """043 接在 042 之后；批次十二集成后唯一 head 是 048。DDL 无 server_default、无逐条 drop_index。"""
    script = ScriptDirectory.from_config(_cfg())
    rev = script.get_revision(MIGRATION)
    assert rev is not None and rev.down_revision == PREVIOUS
    assert len(MIGRATION) <= 32, "MySQL alembic_version.version_num 是 VARCHAR(32)"
    assert set(script.get_heads()) == {"048_session_share_expiry"}

    migration = (BACKEND_ROOT / "alembic" / "versions" / f"{MIGRATION}.py").read_text(
        encoding="utf-8"
    )
    assert "server_default=" not in migration, "新列默认值只能由 ORM/服务层提供"
    assert "mysql.DATETIME(fsp=6)" in migration, "summarized_at 必须与 ORM 的 fsp=6 变体一致"
    downgrade = migration.split("def downgrade() -> None:", 1)[1]
    assert "op.drop_index(" not in downgrade, "downgrade 不得逐条 drop_index"
    assert "reversed(_COLUMNS)" in downgrade, "downgrade 必须逆序删除本迁移的列"
    for name in NEW_COLUMNS:
        assert f'"{name}"' in migration


def test_043_roundtrip_preserves_historical_rows(tmp_path, monkeypatch):
    """042 时插入的历史行升级后照常读取且四列为 NULL；降级只删列，数据仍在。"""
    db_path = tmp_path / "session-tree-043.db"
    monkeypatch.setattr(settings, "DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")
    cfg = _cfg()

    command.upgrade(cfg, PREVIOUS)
    assert _revision(db_path) == {PREVIOUS}
    assert not (set(NEW_COLUMNS) & set(_columns(db_path)))

    # 在 042 schema 下写入一条「历史会话」：只提供当时的 NOT NULL 列。
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO chat_sessions "
            "(id, user_id, organization_id, title, status, message_count, "
            " next_message_seq, source, created_at) "
            "VALUES ('legacy-row', 7, NULL, '旧会话', 'ACTIVE', 0, 0, 'user', "
            "        '2026-01-01 00:00:00')"
        )
        conn.commit()

    command.upgrade(cfg, "head")
    assert _revision(db_path) == {"048_session_share_expiry"}
    columns = _columns(db_path)
    assert set(NEW_COLUMNS) <= set(columns)
    for name in NEW_COLUMNS:
        # PRAGMA table_info：notnull=3, default=4 → 四列均可空且无 server_default。
        assert columns[name][3] == 0, f"{name} 必须可空"
        assert columns[name][4] is None, f"{name} 不得有 server_default"

    async def _load():
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with factory() as db:
                row = await db.get(ChatSession, "legacy-row")
                assert row is not None
                assert row.title == "旧会话"
                assert row.branch_label is None
                assert row.branch_summary is None
                assert row.summary_model is None
                assert row.summarized_at is None
        finally:
            await engine.dispose()

    import asyncio

    asyncio.run(_load())

    command.downgrade(cfg, PREVIOUS)
    assert _revision(db_path) == {PREVIOUS}
    assert not (set(NEW_COLUMNS) & set(_columns(db_path)))
    with sqlite3.connect(str(db_path)) as conn:
        assert conn.execute("SELECT title FROM chat_sessions WHERE id='legacy-row'").fetchone() == (
            "旧会话",
        )

    # 再次升级仍幂等成功，历史行不受影响。
    command.upgrade(cfg, "head")
    assert _revision(db_path) == {"048_session_share_expiry"}
    assert set(NEW_COLUMNS) <= set(_columns(db_path))


# ── 树结构 / 工件统计 ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tree_three_levels_and_artifact_scoping(db_factory):
    async with db_factory() as db:
        await _mk_session(db, "t-root", title="根")
        await _mk_session(
            db,
            "t-child",
            title="子",
            parent_session_id="t-root",
            root_session_id="t-root",
            fork_point_message_id="fp-1",
        )
        await _mk_session(
            db,
            "t-grand",
            title="孙",
            parent_session_id="t-child",
            root_session_id="t-root",
            fork_point_message_id="fp-2",
        )
        await _mk_session(db, "t-other", title="无关根")
        await _mk_msg(db, "t-root", 1, MessageType.USER, "根问题", mid="fp-1")
        await _mk_msg(db, "t-root", 2, MessageType.ASSISTANT, "根回答")
        await _mk_msg(db, "t-child", 1, MessageType.USER, "子问题", mid="fp-2")
        await _mk_msg(db, "t-child", 2, MessageType.ASSISTANT, "子回答")
        await _mk_msg(db, "t-grand", 1, MessageType.USER, "孙问题")
        await _mk_artifact(db, "a-root", "t-root", version_no=1)
        await _mk_artifact(db, "a-child-v1", "t-child", version_no=1)
        await _mk_artifact(db, "a-child-v2", "t-child", version_no=2)
        await _mk_artifact(db, "a-other", "t-other", version_no=1)
        await db.commit()

        tree = await svc.get_session_tree(
            db, session_id="t-grand", user_id=USER_ID, organization_id=ORG_ID
        )

    assert tree["root_session_id"] == "t-root"
    assert tree["current_session_id"] == "t-grand"
    assert tree["root_present"] is True
    assert tree["tree_truncated"] is False
    assert tree["branch_count"] == 3
    branches = {node["session_id"]: node for node in tree["branches"]}
    assert list(branches) == ["t-root", "t-child", "t-grand"]

    assert branches["t-root"]["depth"] == 0
    assert branches["t-child"]["depth"] == 1
    assert branches["t-grand"]["depth"] == 2
    assert branches["t-child"]["parent_session_id"] == "t-root"
    assert branches["t-grand"]["parent_session_id"] == "t-child"
    assert branches["t-child"]["fork_point_message_id"] == "fp-1"
    assert branches["t-child"]["fork_point_preview"] == "根问题"
    assert branches["t-grand"]["fork_point_preview"] == "子问题"
    assert [n["is_current"] for n in tree["branches"]] == [False, False, True]
    assert branches["t-root"]["is_root"] is True
    assert branches["t-root"]["message_count"] == 2
    assert branches["t-child"]["message_count"] == 2
    assert branches["t-grand"]["message_count"] == 1
    # 工件统计严格按分支隔离：根 1/1、子 2 行且最新版本 2、孙 0。
    assert (
        branches["t-root"]["artifact_count"],
        branches["t-root"]["artifact_latest_version"],
    ) == (1, 1)
    assert (
        branches["t-child"]["artifact_count"],
        branches["t-child"]["artifact_latest_version"],
    ) == (2, 2)
    assert (
        branches["t-grand"]["artifact_count"],
        branches["t-grand"]["artifact_latest_version"],
    ) == (0, 0)
    assert "t-other" not in branches


@pytest.mark.asyncio
async def test_tree_from_root_marks_root_current(db_factory):
    async with db_factory() as db:
        await _mk_session(db, "r2")
        await _mk_session(
            db, "c2", parent_session_id="r2", root_session_id="r2", fork_point_message_id=None
        )
        await db.commit()
        tree = await svc.get_session_tree(
            db, session_id="r2", user_id=USER_ID, organization_id=ORG_ID
        )
    assert tree["current_session_id"] == "r2"
    assert [n["is_current"] for n in tree["branches"]] == [True, False]


# ── 分支上下文只读 ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_branch_context_is_read_only_and_returns_summary_artifacts(db_factory):
    async with db_factory() as db:
        await _mk_session(
            db,
            "ctx-branch",
            title="待切换分支",
            parent_session_id="ctx-root",
            root_session_id="ctx-root",
            branch_label="试验 A",
            branch_summary="既有摘要",
            summary_model="fake",
        )
        await _mk_msg(db, "ctx-branch", 1, MessageType.USER, "第一问")
        await _mk_msg(db, "ctx-branch", 2, MessageType.ASSISTANT, "第一答")
        await _mk_artifact(db, "ctx-a1", "ctx-branch", version_no=1)
        await _mk_artifact(db, "ctx-a2", "ctx-branch", version_no=2)
        await db.commit()

        before_sessions = (await db.execute(select(func.count(ChatSession.id)))).scalar_one()
        before_messages = (await db.execute(select(func.count(ChatMessage.id)))).scalar_one()

        context = await svc.get_branch_context(
            db, session_id="ctx-branch", user_id=USER_ID, organization_id=ORG_ID
        )

        after_sessions = (await db.execute(select(func.count(ChatSession.id)))).scalar_one()
        after_messages = (await db.execute(select(func.count(ChatMessage.id)))).scalar_one()

    assert after_sessions == before_sessions, "branch-context 不得创建会话"
    assert after_messages == before_messages, "branch-context 不得复制/改写消息"
    assert context["branch_label"] == "试验 A"
    assert context["branch_summary"] == "既有摘要"
    assert context["summary_model"] == "fake"
    assert context["message_count"] == 2
    assert [m["content"] for m in context["messages"]] == ["第一问", "第一答"]
    assert context["messages"][0]["messageType"] == "user"
    assert context["artifact_count"] == 2
    assert {a["id"] for a in context["artifacts"]} == {"ctx-a1", "ctx-a2"}


# ── 标签 ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_branch_label_trims_clears_and_validates(db_factory):
    async with db_factory() as db:
        await _mk_session(db, "label-s")
        await db.commit()

        result = await svc.set_branch_label(
            db,
            session_id="label-s",
            user_id=USER_ID,
            label="  试验分支  ",
            organization_id=ORG_ID,
        )
        assert result["branch_label"] == "试验分支"
        await db.commit()

        async with db_factory() as fresh:
            row = await fresh.get(ChatSession, "label-s")
            assert row.branch_label == "试验分支"

        cleared = await svc.set_branch_label(
            db, session_id="label-s", user_id=USER_ID, label="   ", organization_id=ORG_ID
        )
        assert cleared["branch_label"] is None

        with pytest.raises(ValidationError):
            await svc.set_branch_label(
                db,
                session_id="label-s",
                user_id=USER_ID,
                label="x" * 101,
                organization_id=ORG_ID,
            )


# ── 摘要 ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fake_summary_deterministic_persisted_and_absent_when_empty(db_factory):
    async with db_factory() as db:
        await _mk_session(db, "sum-s")
        await _mk_msg(db, "sum-s", 1, MessageType.USER, "第一个问题")
        await _mk_msg(db, "sum-s", 2, MessageType.ASSISTANT, "第一个回答")
        await _mk_session(db, "sum-empty")
        await db.commit()

        first = await svc.summarize_branch(
            db, session_id="sum-s", user_id=USER_ID, mode="fake", organization_id=ORG_ID
        )
        second = await svc.summarize_branch(
            db, session_id="sum-s", user_id=USER_ID, mode="fake", organization_id=ORG_ID
        )
        empty = await svc.summarize_branch(
            db, session_id="sum-empty", user_id=USER_ID, mode="fake", organization_id=ORG_ID
        )

        async with db_factory() as fresh:
            row = await fresh.get(ChatSession, "sum-s")
            empty_row = await fresh.get(ChatSession, "sum-empty")

    assert first["generated"] is True
    assert first["summary"] == second["summary"], "fake 摘要必须确定性"
    assert "第一个问题" in first["summary"]
    assert "第一个回答" in first["summary"]
    assert first["summary_model"] == "fake"
    assert first["summarized_at"] is not None
    assert row.branch_summary == first["summary"]
    assert row.summary_model == "fake"
    assert row.summarized_at is not None

    assert empty == {
        "session_id": "sum-empty",
        "generated": False,
        "summary": None,
        "summary_model": None,
        "summarized_at": None,
        "message_count": 0,
    }
    assert empty_row.branch_summary is None
    assert empty_row.summary_model is None


@pytest.mark.asyncio
async def test_llm_summary_unavailable_fails_explicitly_without_fake_fallback(
    db_factory, monkeypatch
):
    async with db_factory() as db:
        await _mk_session(db, "llm-s")
        await _mk_msg(db, "llm-s", 1, MessageType.USER, "需要摘要的问题")
        await db.commit()

        async def _no_client(_db, user=None, *, user_id=None):
            return SimpleNamespace(
                client=None, model="unavailable", source="server", has_api_key=False
            )

        monkeypatch.setattr(svc.llm_settings_service, "resolve_for_user", _no_client)

        with pytest.raises(BranchSummaryUnavailableError) as info:
            await svc.summarize_branch(
                db, session_id="llm-s", user_id=USER_ID, mode="llm", organization_id=ORG_ID
            )
        assert info.value.error_code == "SUMMARY_MODEL_UNAVAILABLE"
        assert info.value.status_code == 503

        async with db_factory() as fresh:
            row = await fresh.get(ChatSession, "llm-s")
        assert row.branch_summary is None, "不可用时绝不能落 fake 摘要冒充成功"
        assert row.summary_model is None

        with pytest.raises(ValidationError):
            await svc.summarize_branch(
                db, session_id="llm-s", user_id=USER_ID, mode="bogus", organization_id=ORG_ID
            )


# ── 租户隔离 ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cross_org_and_cross_user_access_is_404(db_factory):
    async with db_factory() as db:
        await _mk_session(db, "foreign-org", user_id=USER_ID, org_id=2)
        await _mk_session(db, "foreign-user", user_id=2, org_id=ORG_ID)
        await _mk_msg(db, "foreign-org", 1, MessageType.USER, "机密")
        await _mk_msg(db, "foreign-user", 1, MessageType.USER, "他人")
        await db.commit()

        for sid in ("foreign-org", "foreign-user"):
            with pytest.raises(NotFoundError):
                await svc.get_session_tree(
                    db, session_id=sid, user_id=USER_ID, organization_id=ORG_ID
                )
            with pytest.raises(NotFoundError):
                await svc.get_branch_context(
                    db, session_id=sid, user_id=USER_ID, organization_id=ORG_ID
                )
            with pytest.raises(NotFoundError):
                await svc.set_branch_label(
                    db, session_id=sid, user_id=USER_ID, label="越权", organization_id=ORG_ID
                )
            with pytest.raises(NotFoundError):
                await svc.summarize_branch(
                    db,
                    session_id=sid,
                    user_id=USER_ID,
                    mode="fake",
                    organization_id=ORG_ID,
                )
