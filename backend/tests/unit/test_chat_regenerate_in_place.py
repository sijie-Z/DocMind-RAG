"""重新生成：原地替换最新 assistant，不重复 user、不复活旧答案。

真实 SQLite + 假 RAG/记忆。钉住：
- 成功：行数不变、assistant id/seq 不变、内容与 meta 更新、反馈清空；
- 提示词历史只含目标 user 行之前的内容（旧答案不进入上下文）；
- 失败：旧答案原样保留、没有新增 user 行；
- 无目标/他人会话：显式报错且零写入；
- 语义缓存命中同样原地更新，不插入第二条 assistant。
"""

import asyncio
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm.exc import StaleDataError

import app.models  # noqa: F401
from app.core.database import Base
from app.models.chat import ChatMessage, ChatSession, ChatSessionStatus, MessageType
from app.models.organization import Organization
from app.rag.source_provenance import SourceProvenanceUnavailableError
from app.services import chat_service

CONV = "conv-regenerate"
ORG = 1
USER = 1


class FakeRag:
    def __init__(self, *, fail_with: Exception | None = None):
        self.openai_client = object()
        self.fail_with = fail_with
        self.chat_history = None
        self.embedding_calls = 0
        self.search_document_ids = None
        self.query = None

    async def get_embedding(self, _text):
        self.embedding_calls += 1
        return [0.1, 0.2]

    async def search_knowledge_base(self, *_args, **kwargs):
        self.search_document_ids = kwargs.get("document_ids")
        return [], None

    async def chat_stream(self, _user_content, _context, chat_history, **_kwargs):
        self.chat_history = chat_history
        self.query = _user_content
        if self.fail_with is not None:
            raise self.fail_with
        yield "新"
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

    async def get(self, *_args, **_kwargs):
        return None


class HitCache:
    enabled = True

    async def get(self, _vector, _org, *, query, hit_info):
        _ = query
        hit_info["cache_type"] = "semantic"
        return {
            "answer": "缓存新答案",
            "sources": [
                {
                    "filename": "policy.md",
                    "content": "原文片段",
                    "textProvenance": "parser_original",
                    "documentId": "doc-1",
                    "score": 1.0,
                }
            ],
        }


@pytest.fixture
async def factory(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'chat.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        session.add(Organization(id=ORG, name="org-a", owner_id=USER))
        await session.commit()
    yield session_factory
    await engine.dispose()


async def _seed(
    factory,
    *,
    owner: int = USER,
    org: int = ORG,
    with_assistant: bool = True,
    with_previous_turn: bool = True,
):
    async with factory() as session:
        session.add(
            ChatSession(
                id=CONV,
                user_id=owner,
                title="合同核验",
                status=ChatSessionStatus.ACTIVE,
                organization_id=org,
                root_session_id=CONV,
            )
        )
        rows = []
        seq = 1
        if with_previous_turn:
            rows.append(
                ChatMessage(
                    id="msg-u0",
                    session_id=CONV,
                    seq=seq,
                    content="上一轮问题",
                    message_type=MessageType.USER,
                )
            )
            seq += 1
            rows.append(
                ChatMessage(
                    id="msg-a0",
                    session_id=CONV,
                    seq=seq,
                    content="上一轮答案",
                    message_type=MessageType.ASSISTANT,
                )
            )
            seq += 1
        rows.append(
            ChatMessage(
                id="msg-u1",
                session_id=CONV,
                seq=seq,
                content="本轮问题",
                message_type=MessageType.USER,
            )
        )
        seq += 1
        if with_assistant:
            rows.append(
                ChatMessage(
                    id="msg-a1",
                    session_id=CONV,
                    seq=seq,
                    content="旧答案",
                    message_type=MessageType.ASSISTANT,
                    feedback=1,
                    feedback_note="旧反馈",
                )
            )
        session.add_all(rows)
        await session.commit()


async def _run(factory, fake_rag, *, user_id=USER, regenerate=True, file_ids=None):
    events: list[chat_service.RAGEvent] = []
    queue: asyncio.Queue = asyncio.Queue()
    async with factory() as session:
        await chat_service.run_rag_pipeline(
            session=session,
            user_content="客户端重发文本（应被忽略）",
            user_id=user_id,
            search_org_id=ORG,
            organization_id=ORG,
            conversation_id=CONV,
            file_ids=file_ids or [],
            strict_mode=False,
            privacy_mode=True,
            event_queue=queue,
            regenerate=regenerate,
        )
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


async def _rows(factory):
    async with factory() as session:
        return (
            (await session.execute(select(ChatMessage).order_by(ChatMessage.seq.asc())))
            .scalars()
            .all()
        )


@pytest.fixture(autouse=True)
def _fake_deps(monkeypatch):
    monkeypatch.setattr(chat_service, "get_memory_system", lambda *_a, **_k: FakeMemory())
    monkeypatch.setattr(chat_service, "_semantic_cache", DisabledCache())


@pytest.mark.asyncio
async def test_regenerate_replaces_assistant_in_place(factory, monkeypatch):
    await _seed(factory)
    fake_rag = FakeRag()
    monkeypatch.setattr(chat_service, "rag_service", fake_rag)

    events = await _run(factory, fake_rag)
    rows = await _rows(factory)

    assert len(rows) == 4, "不得新增 user/assistant 行"
    by_id = {row.id: row for row in rows}
    assert by_id["msg-a1"].content == "新答案"
    assert by_id["msg-a1"].seq == 4, "原地更新必须保留 seq"
    assert by_id["msg-a1"].feedback == 0
    assert by_id["msg-a1"].feedback_note is None
    assert by_id["msg-u1"].content == "本轮问题", "user 行内容不得被客户端重发文本覆盖"

    # 历史只到目标 user 行之前：上一轮问答在，旧答案/本轮问题不在。
    assert fake_rag.chat_history == [
        {"role": "user", "content": "上一轮问题"},
        {"role": "assistant", "content": "上一轮答案"},
    ]

    message_events = [e for e in events if e.type == "message"]
    assert len(message_events) == 1
    assert message_events[0].content == "新答案"
    assert message_events[0].message_id == "msg-a1"
    assert not [e for e in events if e.type == "error"]


@pytest.mark.asyncio
async def test_regenerate_failure_keeps_old_answer(factory, monkeypatch):
    await _seed(factory)
    fake_rag = FakeRag(fail_with=SourceProvenanceUnavailableError(["doc-1"], unverified_count=1))
    monkeypatch.setattr(chat_service, "rag_service", fake_rag)

    events = await _run(factory, fake_rag)
    rows = await _rows(factory)

    assert len(rows) == 4, "失败不得新增行"
    assert {row.id: row for row in rows}["msg-a1"].content == "旧答案"
    assert [e.type for e in events if e.type == "error"]


@pytest.mark.asyncio
async def test_regenerate_without_target_writes_nothing(factory, monkeypatch):
    await _seed(factory, with_assistant=False, with_previous_turn=False)
    fake_rag = FakeRag()
    monkeypatch.setattr(chat_service, "rag_service", fake_rag)

    events = await _run(factory, fake_rag)
    rows = await _rows(factory)

    assert [e.type for e in events] == ["error"]
    assert len(rows) == 1, "解析失败时绝不能补写 user 行"
    assert [row.id for row in rows] == ["msg-u1"]
    assert fake_rag.chat_history is None, "不得进入 LLM/检索"


@pytest.mark.asyncio
async def test_regenerate_foreign_session_is_rejected(factory, monkeypatch):
    await _seed(factory, owner=2)
    fake_rag = FakeRag()
    monkeypatch.setattr(chat_service, "rag_service", fake_rag)

    events = await _run(factory, fake_rag, user_id=USER)
    rows = await _rows(factory)

    assert [e.type for e in events] == ["error"]
    assert len(rows) == 4
    assert {row.id: row for row in rows}["msg-a1"].content == "旧答案"


@pytest.mark.asyncio
async def test_regenerate_cached_answer_still_updates_in_place(factory, monkeypatch):
    await _seed(factory)
    fake_rag = FakeRag()
    monkeypatch.setattr(chat_service, "rag_service", fake_rag)
    monkeypatch.setattr(chat_service, "_semantic_cache", HitCache())

    events = await _run(factory, fake_rag)
    rows = await _rows(factory)

    assert len(rows) == 4
    assert {row.id: row for row in rows}["msg-a1"].content == "缓存新答案"
    message_events = [e for e in events if e.type == "message"]
    assert len(message_events) == 1
    assert message_events[0].is_cached is True
    assert message_events[0].message_id == "msg-a1"
    assert fake_rag.chat_history is None, "缓存命中不得进入 LLM"


@pytest.mark.asyncio
async def test_regenerate_rejected_when_last_message_is_user(factory, monkeypatch):
    """最新消息是 user（上一轮未完成）时不得重写更早的 assistant。"""
    await _seed(factory)
    async with factory() as session:
        session.add(
            ChatMessage(
                id="msg-u2",
                session_id=CONV,
                seq=5,
                content="更新的问题",
                message_type=MessageType.USER,
            )
        )
        await session.commit()

    fake_rag = FakeRag()
    monkeypatch.setattr(chat_service, "rag_service", fake_rag)

    events = await _run(factory, fake_rag)
    rows = await _rows(factory)

    assert [e.type for e in events] == ["error"]
    assert {row.id: row for row in rows}["msg-a1"].content == "旧答案"
    assert len(rows) == 5
    assert fake_rag.chat_history is None


@pytest.mark.asyncio
async def test_regenerate_uses_db_user_content(factory, monkeypatch):
    """客户端重发文本不得成为新的提问；以库中 user 行为准。"""
    await _seed(factory)
    fake_rag = FakeRag()
    monkeypatch.setattr(chat_service, "rag_service", fake_rag)

    await _run(factory, fake_rag)

    assert fake_rag.query == "本轮问题"


@pytest.mark.asyncio
async def test_regenerate_cross_org_is_rejected(factory, monkeypatch):
    """会话属于同用户但另一组织时拒绝（与 get_or_create_session 同口径）。"""
    await _seed(factory, org=2)
    fake_rag = FakeRag()
    monkeypatch.setattr(chat_service, "rag_service", fake_rag)

    events = await _run(factory, fake_rag)
    rows = await _rows(factory)

    assert [e.type for e in events] == ["error"]
    assert {row.id: row for row in rows}["msg-a1"].content == "旧答案"
    assert len(rows) == 4
    assert fake_rag.query is None


class DeletingRag(FakeRag):
    def __init__(self, factory):
        super().__init__()
        self.factory = factory

    async def chat_stream(self, _user_content, _context, chat_history, **_kwargs):
        self.chat_history = chat_history
        self.query = _user_content
        yield "新"
        async with self.factory() as other:
            await other.execute(delete(ChatMessage).where(ChatMessage.id == "msg-a1"))
            await other.commit()
        yield "答案"


@pytest.mark.asyncio
async def test_regenerate_target_deleted_mid_run_emits_warning_without_insert(factory, monkeypatch):
    """生成期间目标行被删除：不得插入新行/复活会话，必须发 warning。"""
    await _seed(factory)
    fake_rag = DeletingRag(factory)
    monkeypatch.setattr(chat_service, "rag_service", fake_rag)

    events = await _run(factory, fake_rag)
    rows = await _rows(factory)

    assert "msg-a1" not in {row.id for row in rows}
    assert len(rows) == 3, "目标被删后不得补插 assistant 或 user"
    warnings = [e for e in events if e.type == "warning"]
    assert len(warnings) == 1
    assert warnings[0].content.startswith("回答已生成")
    assert [e for e in events if e.type == "message"], "回答本身仍应下发"


class StaleSession:
    def __init__(self):
        self.rolled_back = False

    async def get(self, _model, _key):
        return SimpleNamespace(session_id=CONV, message_type=MessageType.ASSISTANT)

    async def commit(self):
        raise StaleDataError("UPDATE expected 1 row")

    async def rollback(self):
        self.rolled_back = True


@pytest.mark.asyncio
async def test_persist_converts_stale_data_error_to_false():
    """InnoDB 旧快照下 UPDATE 0 行（StaleDataError）必须收敛为 False。"""
    session = StaleSession()
    saved = await chat_service._persist_assistant_message(
        session,
        conversation_id=CONV,
        message_id="msg-a1",
        content="新答案",
        meta_data={"sources": []},
        replace_existing=True,
    )

    assert saved is False
    assert session.rolled_back is True


@pytest.mark.asyncio
async def test_regenerate_reuses_bound_docs_and_ignores_new_file_ids(factory, monkeypatch):
    """重新生成不得被本轮新附件改变证据范围。"""
    await _seed(factory)
    async with factory() as session:
        chat_session = await session.get(ChatSession, CONV)
        assert chat_session is not None
        chat_session.settings = {"bound_document_ids": ["doc-bound"]}
        await session.commit()

    fake_rag = FakeRag()
    monkeypatch.setattr(chat_service, "rag_service", fake_rag)

    await _run(factory, fake_rag, file_ids=["doc-new"])

    assert fake_rag.search_document_ids == ["doc-bound"]


@pytest.mark.asyncio
async def test_normal_send_still_appends_new_turn(factory, monkeypatch):
    """非重新生成路径保持原语义：新 user + 新 assistant 各一行。"""
    await _seed(factory)
    fake_rag = FakeRag()
    monkeypatch.setattr(chat_service, "rag_service", fake_rag)

    events = await _run(factory, fake_rag, regenerate=False)
    rows = await _rows(factory)

    assert len(rows) == 6
    assert [row.message_type for row in rows[-2:]] == [
        MessageType.USER,
        MessageType.ASSISTANT,
    ]
    assert rows[-2].content == "客户端重发文本（应被忽略）"
    assert rows[-1].content == "新答案"
    assert rows[-1].id != "msg-a1", "普通发送不得覆盖旧 assistant"
    assert [e.type for e in events if e.type == "message"]
