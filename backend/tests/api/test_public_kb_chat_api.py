"""公开知识库匿名聊天 API（批次十七）：真实 SQLite + ASGI + 假 RAG/LLM。

判别点（每个用例标题即变异点）：
- 默认关闭 / 全局关闭 / 撤销 / 过期 / 禁用 / 不存在：统一 404 同形；
- 管理员开启后可用；管理面非管理员 403、跨组织 404；
- 只检索显式白名单（脏白名单/私有/停用/未就绪/跨 KB/跨组织都被读时过滤）；
- ES 命中的非白名单文档在返回前二次过滤（删掉 → 引用泄露用例红）；
- 空白名单/零命中不调用 LLM；引用只含 rank/title/snippet/score；
- 日预算 / 组织配额 / 每 token 限流均在首帧前 429（detail 带 used/budget/reset）；
- 不写 chat_sessions/chat_messages；usage 归属发布创建人 + feature 归因；
- 审计无正文/token 明文；断连仍记账且只记一次（shield）；
- SSE：sources → delta → done；生成错误 → error；安全头齐全。
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.api.v1.endpoints import public_kb as pb_endpoints
from app.core.config import settings
from app.core.database import Base, get_db
from app.core.security import get_current_user
from app.models.chat import ChatMessage, ChatSession
from app.models.document import Document, DocumentStatus, DocumentType, DocumentVisibility
from app.models.knowledge_base import KnowledgeBase
from app.models.organization import Organization
from app.models.organization_settings import OrganizationSettings
from app.models.public_kb import DocumentPublication, KbPublication
from app.models.token_usage import TokenUsageRecord
from app.services import openai_compat_service as compat_service
from app.services import public_kb_service

ORG = 1
OTHER_ORG = 2
USER = 1
KB = "kb-main"
OTHER_KB = "kb-other"

ANSWER_CHUNKS = ["根据", "制度 A，", "这是回答。"]


def _doc(
    doc_id: str,
    *,
    org_id: int = ORG,
    kb_id: str | None = KB,
    status: DocumentStatus = DocumentStatus.INDEXED,
    enabled: bool = True,
    title: str | None = None,
    visibility: DocumentVisibility = DocumentVisibility.ORGANIZATION,
) -> Document:
    return Document(
        id=doc_id,
        filename=f"{doc_id}.txt",
        file_path=f"{org_id}/secret/{doc_id}.txt",
        file_size=1,
        file_type=DocumentType.TXT,
        status=status,
        organization_id=org_id,
        uploaded_by=USER,
        knowledge_base_id=kb_id,
        title=title,
        is_enabled=enabled,
        visibility=visibility,
    )


@pytest.fixture
async def factory(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'public-kb-chat.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        db.add_all(
            [
                Organization(id=ORG, name="Org", owner_id=USER),
                Organization(id=OTHER_ORG, name="Other", owner_id=USER),
                KnowledgeBase(id=KB, organization_id=ORG, name="Main KB"),
                KnowledgeBase(id=OTHER_KB, organization_id=ORG, name="Other KB"),
                KnowledgeBase(id="kb-org2", organization_id=OTHER_ORG, name="Org2 KB"),
                _doc("doc-a", title="制度 A"),
                _doc("doc-b", title="制度 B"),
                _doc("doc-other-kb", kb_id=OTHER_KB, title="别的库"),
                _doc("doc-org2", org_id=OTHER_ORG, title="跨组织"),
                _doc("doc-parsed", status=DocumentStatus.PARSED, title="未就绪"),
                _doc("doc-disabled", enabled=False, title="已停用"),
                _doc(
                    "doc-private",
                    title="私有文档",
                    visibility=DocumentVisibility.PRIVATE,
                ),
            ]
        )
        await db.commit()
    yield maker
    await engine.dispose()


@pytest.fixture(autouse=True)
def chat_config(monkeypatch):
    monkeypatch.setattr(settings, "PUBLIC_KB_ENABLED", True)
    monkeypatch.setattr(settings, "PUBLIC_KB_CHAT_ENABLED", True)
    monkeypatch.setattr(settings, "PUBLIC_KB_RATE_LIMIT_PER_MINUTE", 60)
    monkeypatch.setattr(settings, "PUBLIC_KB_CHAT_RATE_LIMIT_PER_MINUTE", 100)
    monkeypatch.setattr(settings, "PUBLIC_KB_CHAT_DAILY_TOKENS_DEFAULT", 50_000)
    monkeypatch.setattr(settings, "PUBLIC_KB_CHAT_MAX_HISTORY", 6)
    monkeypatch.setattr(settings, "PUBLIC_KB_CHAT_MAX_QUERY_CHARS", 200)


@pytest.fixture(autouse=True)
def reset_rate_buckets():
    pb_endpoints._RATE_BUCKETS.clear()
    yield
    pb_endpoints._RATE_BUCKETS.clear()


@pytest.fixture(autouse=True)
def stub_audit(monkeypatch):
    async def _noop(**kwargs):
        return None

    monkeypatch.setattr(pb_endpoints.audit_service, "log_activity", _noop)


class _FakePipeline:
    def __init__(self, hits=None):
        self.hits = hits if hits is not None else []
        self.calls: list[dict] = []

    async def search_knowledge_base(
        self, query, organization_id, top_k=5, document_ids=None, viewer_user_id=None, **kwargs
    ):
        self.calls.append(
            {
                "query": query,
                "organization_id": organization_id,
                "top_k": top_k,
                "document_ids": document_ids,
                "viewer_user_id": viewer_user_id,
                "search_mode": kwargs.get("search_mode"),
            }
        )
        return self.hits, None


def _hit(
    doc_id: str,
    *,
    text: str | None = None,
    snippet: str | None = None,
    score: float = 0.9,
    title: str | None = None,
) -> dict:
    return {
        "document_id": doc_id,
        "chunk_id": f"chunk-{doc_id}",
        "prompt_text": text or f"{doc_id} 的正文",
        "original_text": text or f"{doc_id} 的正文",
        "text_provenance": "parser_original",
        "snippet": snippet or f"{doc_id} 片段",
        "score": score,
        # 内部字段：任何进入公开响应都算泄露。
        "filename": f"/secret/{doc_id}.pdf",
        "file_path": f"1/secret/{doc_id}.pdf",
        "organization_id": ORG,
        "uploaded_by": USER,
        "title": title,
    }


class _FakeRag:
    """openai_compat_service.rag_service 替身（真实 generation 助手消费）。"""

    openai_client = object()

    def __init__(self, *, chunks=None, usage=(12, 7), stream_error=None):
        self.chunks = list(ANSWER_CHUNKS if chunks is None else chunks)
        self.usage = usage
        self.stream_error = stream_error
        self.stream_calls: list[dict] = []
        self.closed = False

    async def chat_stream(
        self,
        query,
        context,
        history=None,
        system_prompt_override=None,
        enable_compression=True,
        enable_masking=True,
        mask_mapping_sink=None,
        model_override=None,
        client_override=None,
        raise_user_errors=False,
    ):
        self.stream_calls.append(
            {
                "query": query,
                "context": context,
                "history": history,
                "system_prompt_override": system_prompt_override,
                "enable_masking": enable_masking,
                "model_override": model_override,
                "client_override": client_override,
                "raise_user_errors": raise_user_errors,
            }
        )
        if self.stream_error is not None:
            raise self.stream_error
        try:
            for chunk in self.chunks:
                yield chunk
        finally:
            self.closed = True

    def get_last_token_usage(self):
        return self.usage


@pytest.fixture
def fake_pipeline(monkeypatch):
    pipeline = _FakePipeline(hits=[_hit("doc-a")])
    monkeypatch.setattr(public_kb_service, "get_rag_pipeline", lambda: pipeline)
    return pipeline


@pytest.fixture
def fake_rag(monkeypatch):
    rag = _FakeRag()
    monkeypatch.setattr(compat_service, "rag_service", rag)
    return rag


@pytest.fixture
def recorded_usage(monkeypatch):
    calls: list[dict] = []

    async def _record(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(pb_endpoints, "record_token_usage", _record)
    return calls


class _Ctx:
    def __init__(self, user, factory):
        self.user = user
        self.factory = factory

    async def __aenter__(self):
        from app.main import app

        user = self.user

        async def _user():
            return user

        async def _db():
            async with self.factory() as session:
                yield session

        app.dependency_overrides[get_current_user] = _user
        app.dependency_overrides[get_db] = _db
        self.app = app
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://kb")
        return self.client

    async def __aexit__(self, exc_type, exc, tb):
        await self.client.aclose()
        self.app.dependency_overrides.pop(get_current_user, None)
        self.app.dependency_overrides.pop(get_db, None)
        return False


def _user(org=ORG, *, user_id=USER, superuser=True):
    return SimpleNamespace(
        id=user_id,
        username=f"tester{user_id}",
        is_active=True,
        is_superuser=superuser,
        organization_id=org,
        role="user",
    )


async def _create(client, **overrides):
    payload = {
        "knowledge_base_id": KB,
        "title": "公开制度库",
        "description": "白名单检索 + 聊天",
        "expires_in_days": None,
        "document_ids": ["doc-a"],
    }
    payload.update(overrides)
    return await client.post("/api/v1/kb-publications", json=payload)


async def _create_ok(client, **overrides):
    resp = await _create(client, **overrides)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


async def _set_chat(client, publication_id: str, *, enabled=True, budget=None):
    return await client.post(
        f"/api/v1/kb-publications/{publication_id}/chat-settings",
        json={"chat_enabled": enabled, "daily_token_budget": budget},
    )


async def _create_chat_ready(client, *, budget=100_000, **overrides):
    created = await _create_ok(client, **overrides)
    resp = await _set_chat(client, created["id"], enabled=True, budget=budget)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"], created["token"]


async def _chat(client, token: str, **payload):
    body = {"message": "制度怎么说？"}
    body.update(payload)
    return await client.post(f"/api/v1/public/kb/{token}/chat", json=body)


def _sse_frames(text: str) -> list[tuple[str, dict]]:
    frames: list[tuple[str, dict]] = []
    for block in text.strip().split("\n\n"):
        event = ""
        data: dict = {}
        for line in block.splitlines():
            if line.startswith("event: "):
                event = line[len("event: ") :]
            elif line.startswith("data: "):
                payload = line[len("data: ") :]
                data = json.loads(payload) if payload else {}
        if event:
            frames.append((event, data))
    return frames


async def _insert_usage(factory, *, feature: str, org_id=ORG, input_tokens=5, output_tokens=6):
    async with factory() as db:
        db.add(
            TokenUsageRecord(
                user_id=USER,
                organization_id=org_id,
                model="test-model",
                source="test",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=0.0,
                feature=feature,
            )
        )
        await db.commit()


async def test_default_disabled_uniform_404_then_admin_enable(factory, fake_pipeline, fake_rag):
    async with _Ctx(_user(), factory) as ac:
        created = await _create_ok(ac)
        token = created["token"]
        assert created["chat_enabled"] is False
        assert created["daily_token_budget"] is None
        meta = (await ac.get(f"/api/v1/public/kb/{token}")).json()["data"]
        assert meta["chat_enabled"] is False

        disabled = await _chat(ac, token)
        missing = await _chat(ac, "never-existed")
        assert disabled.status_code == missing.status_code == 404
        assert disabled.json()["error_code"] == missing.json()["error_code"]
        assert disabled.json()["message"] == missing.json()["message"]

        enabled = await _set_chat(ac, created["id"], enabled=True, budget=1000)
        assert enabled.status_code == 200, enabled.text
        assert enabled.json()["data"]["chat_enabled"] is True
        assert enabled.json()["data"]["daily_token_budget"] == 1000
        assert (await ac.get(f"/api/v1/public/kb/{token}")).json()["data"]["chat_enabled"] is True

        ok = await _chat(ac, token)
        assert ok.status_code == 200, ok.text
        assert ok.headers["cache-control"] == "no-store"
        assert ok.headers["referrer-policy"] == "no-referrer"
        assert ok.headers["x-robots-tag"] == "noindex"
        assert ok.headers["x-content-type-options"] == "nosniff"
        data = ok.json()["data"]
        assert data["answer"] == "".join(ANSWER_CHUNKS)
        assert set(data) == {"answer", "sources"}
        assert fake_pipeline.calls[0]["document_ids"] == ["doc-a"]
        assert fake_pipeline.calls[0]["viewer_user_id"] is None
        assert fake_pipeline.calls[0]["search_mode"] == "hybrid"


async def test_global_switch_off_and_public_kb_off_are_uniform_404(
    factory, fake_pipeline, fake_rag
):
    async with _Ctx(_user(), factory) as ac:
        _pub, token = await _create_chat_ready(ac)
        settings.PUBLIC_KB_CHAT_ENABLED = False
        chat_off = await _chat(ac, token)
        missing = await _chat(ac, "never-existed")
        assert chat_off.status_code == missing.status_code == 404
        assert chat_off.json()["error_code"] == missing.json()["error_code"]
        assert chat_off.json()["message"] == missing.json()["message"]
        # 404 也带公开面安全头（token 链接不得被缓存/Referer 泄露）。
        assert chat_off.headers["cache-control"] == "no-store"
        assert chat_off.headers["referrer-policy"] == "no-referrer"
        assert chat_off.headers["x-robots-tag"] == "noindex"
        # 生效能力投影：全局关闭时公开页不再展示聊天。
        assert (await ac.get(f"/api/v1/public/kb/{token}")).json()["data"]["chat_enabled"] is False

        settings.PUBLIC_KB_CHAT_ENABLED = True
        settings.PUBLIC_KB_ENABLED = False
        kb_off = await _chat(ac, token)
        assert kb_off.status_code == 404
        assert kb_off.json()["error_code"] == missing.json()["error_code"]

        settings.PUBLIC_KB_ENABLED = True
        assert fake_rag.stream_calls == [], "全局/发布开关关闭绝不能调用 LLM"


async def test_revoked_expired_disabled_chat_are_uniform_404(factory, fake_pipeline, fake_rag):
    async with _Ctx(_user(), factory) as ac:
        pub, token = await _create_chat_ready(ac)
        missing = await _chat(ac, "never-existed")

        async with factory() as db:
            await db.execute(
                update(KbPublication)
                .where(KbPublication.id == pub["id"])
                .values(expires_at=datetime.now(UTC) - timedelta(minutes=1))
            )
            await db.commit()
        expired = await _chat(ac, token)
        assert expired.status_code == 404
        assert expired.json()["error_code"] == missing.json()["error_code"]

        async with factory() as db:
            await db.execute(
                update(KbPublication)
                .where(KbPublication.id == pub["id"])
                .values(expires_at=None, enabled=False)
            )
            await db.commit()
        disabled = await _chat(ac, token)
        assert disabled.status_code == 404
        assert disabled.json()["message"] == missing.json()["message"]

        async with factory() as db:
            await db.execute(
                update(KbPublication).where(KbPublication.id == pub["id"]).values(enabled=True)
            )
            await db.commit()
        await ac.post(f"/api/v1/kb-publications/{pub['id']}/revoke")
        revoked = await _chat(ac, token)
        assert revoked.status_code == 404
        assert revoked.json()["error_code"] == missing.json()["error_code"]
        assert fake_rag.stream_calls == []


async def test_whitelist_second_filter_and_read_time_document_status(
    factory, fake_rag, monkeypatch
):
    # 脏白名单：私有/停用/未就绪/跨 KB/跨组织文档被直接写入白名单行；
    # 读时必须全部被 _searchable_whitelist 剔除，只剩 doc-a。
    pipeline = _FakePipeline(
        hits=[
            _hit("doc-a", text="制度 A 正文", snippet="<script>alert(1)</script>制度 <b>A</b>"),
            _hit("doc-b", text="未发布正文"),
            _hit("doc-private", text="私有正文"),
            _hit("doc-org2", text="跨组织正文"),
            _hit("doc-other-kb", text="别的库正文"),
            _hit("doc-parsed", text="未就绪正文"),
            _hit("doc-disabled", text="已停用正文"),
        ]
    )
    monkeypatch.setattr(public_kb_service, "get_rag_pipeline", lambda: pipeline)
    async with _Ctx(_user(), factory) as ac:
        created = await _create_ok(ac)
        async with factory() as db:
            for doc_id in ("doc-private", "doc-org2", "doc-other-kb", "doc-parsed", "doc-disabled"):
                db.add(
                    DocumentPublication(
                        id=f"dirty-{doc_id}",
                        organization_id=ORG,
                        publication_id=created["id"],
                        document_id=doc_id,
                        created_by=USER,
                        created_at=datetime.now(UTC),
                    )
                )
            await db.commit()
        enabled = await _set_chat(ac, created["id"], enabled=True, budget=1000)
        assert enabled.status_code == 200

        resp = await _chat(ac, created["token"])
        assert resp.status_code == 200, resp.text
        sources = resp.json()["data"]["sources"]
        assert [row["title"] for row in sources] == ["制度 A"]
        assert set(sources[0]) == {"rank", "title", "snippet", "score"}
        assert "<" not in sources[0]["snippet"] and "alert" not in sources[0]["snippet"]
        assert pipeline.calls[0]["document_ids"] == ["doc-a"]
        # 非白名单/脏行命中绝不能进入 LLM 上下文。
        context = fake_rag.stream_calls[0]["context"]
        assert len(context) == 1
        rendered = json.dumps(context, ensure_ascii=False)
        for forbidden in (
            "doc-b",
            "doc-private",
            "doc-org2",
            "doc-other-kb",
            "未发布正文",
            "私有正文",
            # 内部元数据字段不得进入提示词上下文。
            "file_path",
            "organization_id",
            "uploaded_by",
            "chunk_id",
            "chunk-doc-a",
            "secret/",
        ):
            assert forbidden not in rendered
        body = resp.text
        for forbidden in ("doc-a", "secret/", "organization_id", "uploaded_by", "chunk-"):
            assert forbidden not in body


async def test_private_document_flip_after_publish_blocks_chat(factory, fake_pipeline, fake_rag):
    async with _Ctx(_user(), factory) as ac:
        _pub, token = await _create_chat_ready(ac)
        async with factory() as db:
            await db.execute(
                update(Document)
                .where(Document.id == "doc-a")
                .values(visibility=DocumentVisibility.PRIVATE)
            )
            await db.commit()
        resp = await _chat(ac, token)
        assert resp.status_code == 200
        assert resp.json()["data"] == {"answer": "", "sources": []}
        assert fake_pipeline.calls == [], "白名单为空时不得回退到 KB 全量检索"
        assert fake_rag.stream_calls == [], "空上下文不得调用 LLM"


async def test_empty_whitelist_never_calls_llm(factory, fake_pipeline, fake_rag):
    async with _Ctx(_user(), factory) as ac:
        pub, token = await _create_chat_ready(ac, document_ids=[])
        resp = await _chat(ac, token)
        assert resp.status_code == 200
        assert resp.json()["data"] == {"answer": "", "sources": []}
        assert fake_pipeline.calls == []
        assert fake_rag.stream_calls == []

        stream = await _chat(ac, token, stream=True)
        assert stream.status_code == 200, stream.text
        frames = _sse_frames(stream.text)
        assert [event for event, _data in frames] == ["sources", "done"]
        assert frames[0][1]["sources"] == []
        assert fake_rag.stream_calls == []


async def test_daily_budget_429_before_first_frame(factory, fake_pipeline, fake_rag):
    async with _Ctx(_user(), factory) as ac:
        pub, token = await _create_chat_ready(ac, budget=10)
        await _insert_usage(
            factory, feature=f"public_kb_chat:{pub['id']}", input_tokens=5, output_tokens=6
        )

        for stream in (False, True):
            resp = await _chat(ac, token, stream=stream)
            assert resp.status_code == 429, resp.text
            assert resp.headers["cache-control"] == "no-store"
            body = resp.json()
            assert body["error_code"] == "PUBLIC_KB_CHAT_BUDGET_EXCEEDED"
            assert body["detail"]["used"] == 11
            assert body["detail"]["budget"] == 10
            assert body["detail"]["reset_at"].endswith("+00:00")
        assert fake_rag.stream_calls == [], "预算超限必须在 LLM/首帧之前拒绝"

        # budget=0 显式禁止聊天。
        zero = await _set_chat(ac, pub["id"], enabled=True, budget=0)
        assert zero.status_code == 200
        denied = await _chat(ac, token)
        assert denied.status_code == 429
        assert denied.json()["detail"]["budget"] == 0

        # NULL = 全局默认（这里把默认压到 5）。
        settings.PUBLIC_KB_CHAT_DAILY_TOKENS_DEFAULT = 5
        reset = await _set_chat(ac, pub["id"], enabled=True, budget=None)
        assert reset.status_code == 200
        default_denied = await _chat(ac, token)
        assert default_denied.status_code == 429
        assert default_denied.json()["detail"]["budget"] == 5


async def test_org_monthly_quota_429_before_llm(factory, fake_pipeline, fake_rag):
    async with _Ctx(_user(), factory) as ac:
        _pub, token = await _create_chat_ready(ac)
        async with factory() as db:
            db.add(
                OrganizationSettings(
                    organization_id=ORG,
                    monthly_token_quota=1,
                )
            )
            await db.commit()
        await _insert_usage(factory, feature="rag_chat", input_tokens=5, output_tokens=0)

        resp = await _chat(ac, token)
        assert resp.status_code == 429, resp.text
        body = resp.json()
        assert body["error_code"] == "ORG_TOKEN_QUOTA_EXCEEDED"
        assert body["detail"]["used"] >= 1
        assert body["detail"]["quota"] == 1
        assert body["detail"]["reset_at"]
        assert fake_rag.stream_calls == []


async def test_per_token_rate_limit_429_and_isolation(factory, fake_pipeline, fake_rag):
    settings.PUBLIC_KB_CHAT_RATE_LIMIT_PER_MINUTE = 1
    async with _Ctx(_user(), factory) as ac:
        pub_a, token_a = await _create_chat_ready(ac)
        pub_b, token_b = await _create_chat_ready(ac, title="第二个库")

        first = await _chat(ac, token_a)
        assert first.status_code == 200
        second = await _chat(ac, token_a)
        assert second.status_code == 429
        assert second.json()["error_code"] == "RATE_LIMIT_EXCEEDED"
        assert len(fake_rag.stream_calls) == 1, "被限流请求不得调用 LLM"

        other_link = await _chat(ac, token_b)
        assert other_link.status_code == 200, "一条链接的限流不得影响另一条"


async def test_no_chat_session_or_message_persistence(factory, fake_pipeline, fake_rag):
    async with _Ctx(_user(), factory) as ac:
        _pub, token = await _create_chat_ready(ac)
        assert (await _chat(ac, token)).status_code == 200
        assert (await _chat(ac, token, stream=True)).status_code == 200
        async with factory() as db:
            sessions = (
                await db.execute(select(func.count()).select_from(ChatSession))
            ).scalar_one()
            messages = (
                await db.execute(select(func.count()).select_from(ChatMessage))
            ).scalar_one()
        assert sessions == 0
        assert messages == 0


async def test_usage_and_audit_have_no_content_or_token_plaintext(
    factory, fake_pipeline, fake_rag, recorded_usage, monkeypatch
):
    audits: list[dict] = []

    async def _capture(**kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(pb_endpoints.audit_service, "log_activity", _capture)
    async with _Ctx(_user(), factory) as ac:
        pub, token = await _create_chat_ready(ac)
        resp = await _chat(ac, token, message="制度 A 的秘密问题")
        assert resp.status_code == 200, resp.text

    assert len(recorded_usage) == 1
    usage = recorded_usage[0]
    assert usage["user_id"] == USER
    assert usage["organization_id"] == ORG
    assert usage["source"] == "public_kb_chat"
    assert usage["feature"] == f"public_kb_chat:{pub['id']}"
    assert (usage["input_tokens"], usage["output_tokens"]) == (12, 7)

    chat_audits = [call for call in audits if call["action"] == "public_kb_chat"]
    assert len(chat_audits) == 1
    audit = chat_audits[0]
    assert audit["action"] == "public_kb_chat"
    assert audit["target_type"] == "kb_publication"
    assert audit["target_id"] == pub["id"]
    detail = audit["detail"]
    assert "制度 A 的秘密问题" not in detail
    assert "".join(ANSWER_CHUNKS) not in detail
    assert token not in detail
    assert hashlib.sha256(token.encode("utf-8")).hexdigest() not in detail
    assert "doc-a" not in detail
    payload = json.loads(detail)
    assert payload["source_count"] == 1
    assert payload["total_tokens"] == 19


async def test_stream_sse_shape_headers_and_llm_args(factory, fake_pipeline, fake_rag):
    async with _Ctx(_user(), factory) as ac:
        _pub, token = await _create_chat_ready(ac)
        resp = await _chat(
            ac,
            token,
            stream=True,
            history=[
                {"role": "user", "content": "上一个问题"},
                {"role": "assistant", "content": "上一个回答"},
            ],
        )
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"].startswith("text/event-stream")
        assert resp.headers["cache-control"] == "no-store"
        assert resp.headers["referrer-policy"] == "no-referrer"
        assert resp.headers["x-robots-tag"] == "noindex"
        assert resp.headers["x-content-type-options"] == "nosniff"
        assert resp.headers["x-accel-buffering"] == "no"

    frames = _sse_frames(resp.text)
    events = [event for event, _data in frames]
    assert events[0] == "sources"
    assert "delta" in events
    assert events[-1] == "done"
    assert [data["delta"] for event, data in frames if event == "delta"] == ANSWER_CHUNKS
    assert frames[0][1]["sources"][0]["title"] == "制度 A"

    call = fake_rag.stream_calls[0]
    assert call["query"] == "制度怎么说？"
    assert call["history"] == [
        {"role": "user", "content": "上一个问题"},
        {"role": "assistant", "content": "上一个回答"},
    ]
    assert call["enable_masking"] is True
    assert call["raise_user_errors"] is True
    assert call["system_prompt_override"] == public_kb_service.CHAT_SYSTEM_PROMPT
    assert call["client_override"] is fake_rag.openai_client


async def test_stream_disconnect_shields_usage_and_records_once(
    factory, fake_pipeline, fake_rag, recorded_usage, monkeypatch
):
    audits: list[dict] = []

    async def _capture(**kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(pb_endpoints.audit_service, "log_activity", _capture)
    async with _Ctx(_user(), factory) as ac:
        pub, token = await _create_chat_ready(ac)
        async with factory() as db:
            publication = (
                await db.execute(select(KbPublication).where(KbPublication.id == pub["id"]))
            ).scalar_one()
            context, citations = await public_kb_service.public_chat_context(
                db, publication=publication, query="制度"
            )
            generator = pb_endpoints._stream_chat(
                db=db,
                publication=publication,
                message="制度",
                history=[],
                history_mask_mapping={},
                context=context,
                citations=citations,
            )
            first = await generator.__anext__()
            assert "event: sources" in first
            second = await generator.__anext__()
            assert "event: delta" in second
            await generator.aclose()

    assert fake_rag.closed is True
    assert len(recorded_usage) == 1
    assert recorded_usage[0]["feature"] == f"public_kb_chat:{pub['id']}"
    chat_audits = [call for call in audits if call["action"] == "public_kb_chat"]
    assert len(chat_audits) == 1
    assert json.loads(chat_audits[0]["detail"])["stream"] is True


async def test_generation_error_maps_to_safe_error_and_records_usage(
    factory, fake_pipeline, recorded_usage, monkeypatch
):
    from app.core.llm_failures import UserLLMRequestError

    rag = _FakeRag(stream_error=UserLLMRequestError("upstream secret detail"))
    monkeypatch.setattr(compat_service, "rag_service", rag)
    async with _Ctx(_user(), factory) as ac:
        _pub, token = await _create_chat_ready(ac)
        resp = await _chat(ac, token)
        assert resp.status_code == 502, resp.text
        body = resp.json()
        assert body["error_code"] == "PUBLIC_KB_CHAT_GENERATION_FAILED"
        assert "upstream secret detail" not in resp.text
        assert len(recorded_usage) == 1

        stream = await _chat(ac, token, stream=True)
        assert stream.status_code == 200
        frames = _sse_frames(stream.text)
        assert frames[-1][0] == "error"
        assert frames[-1][1]["error_code"] == "PUBLIC_KB_CHAT_GENERATION_FAILED"
        assert "upstream secret detail" not in stream.text
        assert len(recorded_usage) == 2


async def test_chat_history_and_message_bounds(factory, fake_pipeline, fake_rag):
    async with _Ctx(_user(), factory) as ac:
        _pub, token = await _create_chat_ready(ac)
        bad_requests = [
            {"message": ""},
            {"message": "   "},
            {"message": "x" * 201},
            {"message": "制度", "history": [{"role": "system", "content": "x"}]},
            {"message": "制度", "history": [{"role": "user", "content": "x" * 2001}]},
            {
                "message": "制度",
                "history": [{"role": "user", "content": "x"}] * 7,
            },
        ]
        for payload in bad_requests:
            resp = await ac.post(f"/api/v1/public/kb/{token}/chat", json=payload)
            assert resp.status_code == 400, (payload, resp.text)
        assert fake_rag.stream_calls == []

        # 恰好 6 条历史合法；服务层不截断。
        ok = await _chat(
            ac,
            token,
            history=[{"role": "user", "content": f"历史 {i}"} for i in range(6)],
        )
        assert ok.status_code == 200, ok.text
        assert len(fake_rag.stream_calls[0]["history"]) == 6


async def test_chat_settings_admin_gate_validation_and_audit(factory, fake_pipeline, monkeypatch):
    audits: list[dict] = []

    async def _capture(**kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(pb_endpoints.audit_service, "log_activity", _capture)
    async with _Ctx(_user(), factory) as ac:
        created = await _create_ok(ac, document_ids=[])

    async with _Ctx(_user(superuser=False), factory) as ac:
        denied = await _set_chat(ac, created["id"], enabled=True)
        assert denied.status_code == 403

    async with _Ctx(_user(), factory) as ac:
        for bad in (
            {"chat_enabled": True, "daily_token_budget": -1},
            {"chat_enabled": True, "daily_token_budget": 10_000_001},
            # bool 是 int 子类：True 不得被静默转成 1。
            {"chat_enabled": True, "daily_token_budget": True},
        ):
            resp = await ac.post(f"/api/v1/kb-publications/{created['id']}/chat-settings", json=bad)
            assert resp.status_code == 422, (bad, resp.text)

        ok = await _set_chat(ac, created["id"], enabled=True, budget=1234)
        assert ok.status_code == 200

    chat_audits = [call for call in audits if call["action"] == "update_kb_publication_chat"]
    assert len(chat_audits) == 1
    detail = json.loads(chat_audits[0]["detail"])
    assert detail["chat_enabled"] is True
    assert detail["daily_token_budget"] == 1234
    assert '"token"' not in chat_audits[0]["detail"]
    assert "token_hash" not in chat_audits[0]["detail"]

    async with _Ctx(_user(org=OTHER_ORG), factory) as ac:
        cross = await _set_chat(ac, created["id"], enabled=True)
        assert cross.status_code == 404


async def test_invalid_token_chat_scan_is_rate_limited(monkeypatch, factory):
    monkeypatch.setattr(pb_endpoints, "INVALID_BUCKET_MIN", 1)
    monkeypatch.setattr(settings, "PUBLIC_KB_RATE_LIMIT_PER_MINUTE", 1)
    async with _Ctx(_user(), factory) as ac:
        for _ in range(5):
            assert (await _chat(ac, "not-real")).status_code == 404
        limited = await _chat(ac, "not-real")
        assert limited.status_code == 429


async def test_history_pii_is_masked_before_llm_and_unmasked_in_answer(
    factory, fake_pipeline, monkeypatch
):
    """匿名客户端的 history 也必须掩码：PII 不得原样发给第三方模型。"""
    rag = _FakeRag(chunks=["你的号码是 [PHONE_5001]"])
    monkeypatch.setattr(compat_service, "rag_service", rag)
    async with _Ctx(_user(), factory) as ac:
        _pub, token = await _create_chat_ready(ac)
        resp = await _chat(
            ac,
            token,
            history=[{"role": "user", "content": "我的手机号 13800138000"}],
        )
        assert resp.status_code == 200, resp.text

    history_sent = json.dumps(rag.stream_calls[0]["history"], ensure_ascii=False)
    assert "13800138000" not in history_sent
    assert "[PHONE_5001]" in history_sent
    # 引用型占位符在输出阶段还原，访客看到的是自己的原号码。
    assert "13800138000" in resp.json()["data"]["answer"]


async def test_closing_response_wrapper_closes_generator_on_send_failure(
    factory, fake_pipeline, fake_rag, recorded_usage, monkeypatch
):
    """ASGI send 边界断连（Starlette 2.3 只取消 task）：包装器必须 aclose
    生成器并完成 shield 记账，而不是等 GC。"""
    from app.api.v1.streaming import ClosingStreamingResponse

    audits: list[dict] = []

    async def _capture(**kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(pb_endpoints.audit_service, "log_activity", _capture)
    async with _Ctx(_user(), factory) as ac:
        pub, token = await _create_chat_ready(ac)
        async with factory() as db:
            publication = (
                await db.execute(select(KbPublication).where(KbPublication.id == pub["id"]))
            ).scalar_one()
            context, citations = await public_kb_service.public_chat_context(
                db, publication=publication, query="制度"
            )
            generator = pb_endpoints._stream_chat(
                db=db,
                publication=publication,
                message="制度",
                history=[],
                history_mask_mapping={},
                context=context,
                citations=citations,
            )
            response = ClosingStreamingResponse(generator, media_type="text/event-stream")
            scope = {
                "type": "http",
                "asgi": {"version": "3.0", "spec_version": "2.3"},
                "method": "POST",
                "path": "/api/v1/public/kb/token/chat",
                "headers": [],
            }

            async def receive():
                return {"type": "http.disconnect"}

            body_sends = 0

            async def send(message):
                nonlocal body_sends
                if message["type"] != "http.response.body":
                    return
                body_sends += 1
                # 第一帧 sources 正常发出；第二帧（首个 delta）时模拟断连，
                # 确保上游生成器已启动，才能验证 aclose 真正关闭它。
                if body_sends >= 2:
                    raise RuntimeError("client disconnected at send boundary")

            with pytest.raises(RuntimeError):
                await response(scope, receive, send)

    assert fake_rag.closed is True, "send 边界断连后上游生成器未被关闭"
    assert len(recorded_usage) == 1, "断连后必须恰好记一次 token 账"
    chat_audits = [call for call in audits if call["action"] == "public_kb_chat"]
    assert len(chat_audits) == 1


async def test_real_usage_recording_feeds_daily_budget(
    factory, fake_pipeline, fake_rag, monkeypatch
):
    """真实 record_token_usage（不 monkeypatch）写入的 feature 必须能驱动日预算。

    这条用例覆盖「记账 51 字符 feature → 下一次请求 429」的真实闭环；SQLite
    不校验 VARCHAR 长度，但同文件另有 feature 长度断言兜住 MySQL 列宽。
    """
    import app.core.database as database

    monkeypatch.setattr(database, "AsyncSessionLocal", factory)
    async with _Ctx(_user(), factory) as ac:
        pub, token = await _create_chat_ready(ac, budget=5)
        first = await _chat(ac, token)
        assert first.status_code == 200, first.text

        async with factory() as db:
            rows = (
                (
                    await db.execute(
                        select(TokenUsageRecord).where(
                            TokenUsageRecord.feature == f"public_kb_chat:{pub['id']}"
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(rows) == 1
        assert rows[0].source == "public_kb_chat"
        assert rows[0].organization_id == ORG
        assert rows[0].user_id == USER
        assert (rows[0].input_tokens, rows[0].output_tokens) == (12, 7)

        second = await _chat(ac, token)
        assert second.status_code == 429, second.text
        assert second.json()["error_code"] == "PUBLIC_KB_CHAT_BUDGET_EXCEEDED"
        assert second.json()["detail"]["used"] == 19


async def test_non_stream_closes_request_session_before_generation(
    factory, fake_pipeline, recorded_usage, monkeypatch
):
    """stream=false 同样不能在 LLM 生成期间占着请求 DB 事务/连接。"""
    from fastapi import Response

    observed: dict[str, bool] = {}
    async with _Ctx(_user(), factory) as ac:
        pub, token = await _create_chat_ready(ac)
        async with factory() as db:
            original = _FakeRag()
            original_stream = original.chat_stream

            async def chat_stream(*args, **kwargs):
                observed["in_transaction"] = db.in_transaction()
                async for chunk in original_stream(*args, **kwargs):
                    yield chunk

            monkeypatch.setattr(original, "chat_stream", chat_stream)
            monkeypatch.setattr(compat_service, "rag_service", original)
            request = SimpleNamespace(state=SimpleNamespace(), client=None, headers={})
            response = Response()
            result = await pb_endpoints.chat_public_kb(
                token=token,
                payload=pb_endpoints.PublicKbChatRequest(message="制度怎么说？"),
                request=request,
                response=response,
                db=db,
            )
            assert result["success"] is True
            assert observed == {"in_transaction": False}, (
                "生成开始时请求 session 必须已关闭且不在事务中"
            )
            assert len(recorded_usage) == 1


async def test_public_chat_validation_422_carries_security_headers(factory):
    """Pydantic 422（请求体 >4000）也必须带公开面安全头。"""
    async with _Ctx(_user(), factory) as ac:
        _pub, token = await _create_chat_ready(ac)
        resp = await ac.post(f"/api/v1/public/kb/{token}/chat", json={"message": "x" * 4001})
        assert resp.status_code == 422, resp.text
        assert resp.headers["cache-control"] == "no-store"
        assert resp.headers["referrer-policy"] == "no-referrer"
        assert resp.headers["x-robots-tag"] == "noindex"
        assert resp.headers["x-content-type-options"] == "nosniff"


async def test_publication_isolation_same_kb_whitelists_do_not_mix(
    factory, fake_rag, recorded_usage, monkeypatch
):
    """M21：同一 KB 的两个发布白名单不得互相串用（A 只见 doc-a，B 只见 doc-b）。

    变异：删除 ``_searchable_whitelist`` 的 ``DocumentPublication.publication_id``
    过滤 → A token 的 document_ids 会包含 doc-b，本用例必红。
    """
    pipeline = _FakePipeline(
        hits=[
            _hit("doc-a", text="制度 A 正文", snippet="制度 A 片段"),
            _hit("doc-b", text="制度 B 正文", snippet="制度 B 片段"),
        ]
    )
    monkeypatch.setattr(public_kb_service, "get_rag_pipeline", lambda: pipeline)
    async with _Ctx(_user(), factory) as ac:
        pub_a = await _create_ok(ac, title="发布 A", document_ids=["doc-a"])
        pub_b = await _create_ok(ac, title="发布 B", document_ids=["doc-b"])
        assert pub_a["id"] != pub_b["id"]
        enabled = await _set_chat(ac, pub_a["id"], enabled=True, budget=1000)
        assert enabled.status_code == 200

        search_a = await ac.post(
            f"/api/v1/public/kb/{pub_a['token']}/search", json={"query": "制度"}
        )
        assert search_a.status_code == 200, search_a.text
        titles_a = [row["title"] for row in search_a.json()["data"]["results"]]
        assert titles_a == ["制度 A"]
        assert pipeline.calls[-1]["document_ids"] == ["doc-a"]

        chat_a = await _chat(ac, pub_a["token"])
        assert chat_a.status_code == 200, chat_a.text
        sources_a = chat_a.json()["data"]["sources"]
        assert [row["title"] for row in sources_a] == ["制度 A"]
        assert "制度 B" not in chat_a.text
        assert "doc-b" not in chat_a.text
        context_a = json.dumps(fake_rag.stream_calls[-1]["context"], ensure_ascii=False)
        assert "制度 B 正文" not in context_a
        assert "doc-b" not in context_a

        # 反向确认 B 发布也只看自己的白名单（两个方向都不串）。
        search_b = await ac.post(
            f"/api/v1/public/kb/{pub_b['token']}/search", json={"query": "制度"}
        )
        assert search_b.status_code == 200, search_b.text
        titles_b = [row["title"] for row in search_b.json()["data"]["results"]]
        assert titles_b == ["制度 B"]
        assert pipeline.calls[-1]["document_ids"] == ["doc-b"]


async def test_admin_kill_switch_disables_next_chat_and_public_meta(
    factory, fake_pipeline, fake_rag
):
    """M32：管理面先开启再关闭 chat，下一次 chat 404、meta 投影 false。

    变异：``update_chat_settings`` 恒置 ``chat_enabled=True`` → 本用例必红。
    """
    async with _Ctx(_user(), factory) as ac:
        pub, token = await _create_chat_ready(ac)
        assert (await ac.get(f"/api/v1/public/kb/{token}")).json()["data"]["chat_enabled"] is True
        assert (await _chat(ac, token)).status_code == 200

        off = await _set_chat(ac, pub["id"], enabled=False, budget=1000)
        assert off.status_code == 200, off.text
        assert off.json()["data"]["chat_enabled"] is False

        meta = (await ac.get(f"/api/v1/public/kb/{token}")).json()["data"]
        assert meta["chat_enabled"] is False
        disabled = await _chat(ac, token)
        missing = await _chat(ac, "never-existed")
        assert disabled.status_code == missing.status_code == 404
        assert disabled.json()["error_code"] == missing.json()["error_code"]
        assert disabled.json()["message"] == missing.json()["message"]
        # 关闭前的成功调用之外不得再进入 LLM。
        assert len(fake_rag.stream_calls) == 1


async def test_daily_budget_denies_when_used_equals_budget(
    factory, fake_pipeline, fake_rag, recorded_usage
):
    """M18 边界：used == budget 也必须 429（>= 而非 >）。"""
    async with _Ctx(_user(), factory) as ac:
        pub, token = await _create_chat_ready(ac, budget=19)
        await _insert_usage(
            factory, feature=f"public_kb_chat:{pub['id']}", input_tokens=12, output_tokens=7
        )
        resp = await _chat(ac, token)
        assert resp.status_code == 429, resp.text
        assert resp.json()["error_code"] == "PUBLIC_KB_CHAT_BUDGET_EXCEEDED"
        assert resp.json()["detail"]["used"] == 19
        assert resp.json()["detail"]["budget"] == 19
        assert fake_rag.stream_calls == []
