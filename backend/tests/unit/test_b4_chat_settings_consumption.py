"""B4：RAG 聊天链路的组织设置真实消费（sqlite + 假 RAG/记忆，不连外部服务）。

钉住三个消费点（不是只测解析助手）：
- ``organization_settings.retrieval_top_k`` → 传给 ``search_knowledge_base``；
  未设置时保持既有默认 5（文档绑定路径为 8）；
- ``allow_privacy_mode = False`` → ``privacy_mode=True`` 被服务端强制关闭
  （enable_masking=False，不信任客户端）；
- ``default_model`` → 透传给 ``chat_stream(model_override=...)``。
"""
import asyncio

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.config import settings
from app.core.database import Base
from app.models.organization import Organization
from app.models.organization_settings import OrganizationSettings
from app.services import chat_service


class FakeRag:
    def __init__(self):
        self.openai_client = object()
        self.search_calls: list[dict] = []
        self.chat_kwargs: dict | None = None

    async def get_embedding(self, _text):
        return None

    async def search_knowledge_base(
        self, query, organization_id, top_k, document_ids=None,
        search_mode=None, debug=False,
    ):
        self.search_calls.append(
            {
                "query": query,
                "organization_id": organization_id,
                "top_k": top_k,
                "document_ids": document_ids,
            }
        )
        return [], None

    async def chat_stream(self, *args, **kwargs):
        self.chat_kwargs = kwargs
        yield ""

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


@pytest.fixture
async def db_session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(Organization(id=1, name="org-a", owner_id=1))
        await session.commit()
    async with factory() as session:
        yield session
    await engine.dispose()


async def _run(session, fake_rag) -> None:
    await chat_service.run_rag_pipeline(
        session=session,
        user_content="报销流程",
        user_id=1,
        search_org_id=1,
        organization_id=1,
        conversation_id=None,
        file_ids=[],
        strict_mode=False,
        privacy_mode=True,
        event_queue=asyncio.Queue(),
    )


async def test_chat_consumes_org_settings(db_session, monkeypatch):
    fake_rag = FakeRag()
    monkeypatch.setattr(chat_service, "rag_service", fake_rag)
    monkeypatch.setattr(
        chat_service, "get_memory_system", lambda *_a, **_k: FakeMemory()
    )

    db_session.add(
        OrganizationSettings(
            organization_id=1,
            retrieval_top_k=3,
            allow_privacy_mode=False,
            default_model="org-model",
        )
    )
    await db_session.commit()

    await _run(db_session, fake_rag)

    assert fake_rag.search_calls, "聊天必须执行检索"
    assert fake_rag.search_calls[0]["top_k"] == 3
    assert fake_rag.chat_kwargs is not None
    # 组织策略禁用隐私模式：客户端传 True，服务端仍强制关闭
    assert fake_rag.chat_kwargs["enable_masking"] is False
    assert fake_rag.chat_kwargs["model_override"] == "org-model"


async def test_chat_falls_back_to_existing_defaults(db_session, monkeypatch):
    fake_rag = FakeRag()
    monkeypatch.setattr(chat_service, "rag_service", fake_rag)
    monkeypatch.setattr(
        chat_service, "get_memory_system", lambda *_a, **_k: FakeMemory()
    )

    await _run(db_session, fake_rag)

    # 未设置组织值：通用 RAG 保持既有默认 5、隐私保持请求值、模型保持平台默认
    assert fake_rag.search_calls[0]["top_k"] == 5
    assert fake_rag.chat_kwargs is not None
    assert fake_rag.chat_kwargs["enable_masking"] is True
    assert fake_rag.chat_kwargs["model_override"] == settings.DEEPSEEK_MODEL
