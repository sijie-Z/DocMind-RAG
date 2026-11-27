"""批次十五：OpenAI 兼容 ``POST /api/v1/chat/completions``。

覆盖验收矩阵：
- 认证：无 key / 无效 key / 吊销 key → 401（OpenAI error 结构）；
  缺 ``chat:write`` → 403；无组织上下文 → 403；
- 配额：批次十四 ``ensure_monthly_quota`` 在检索/LLM 前 429
  ``ORG_TOKEN_QUOTA_EXCEEDED``；
- 请求映射：system → system_prompt_override；多轮 user/assistant → history；
  空 messages / 非法角色 → 400；
- 模型映射：``docmind`` 组织范围；``docmind:<kb>`` 透传 knowledge_base_id；
  跨组织/不存在 KB → 404 同一安全文案；其它模型 → 400 MODEL_NOT_SUPPORTED；
- 非流式：标准 ``chat.completion`` 字段 + usage + 引用扩展（无内部字段）；
- 流式：标准 ``chat.completion.chunk``、最终 finish_reason=stop、
  引用扩展、``[DONE]`` 结尾；
- 计量/审计：``source=openai_compat`` / ``feature=openai_compat`` 只记一次，
  审计 detail 只含 model/KB/stream/usage，无正文。

变异判别力（任一被删则对应用例红）：
- 去掉 ``require_scope("chat:write")`` → rpc-only key 用例红；
- 去掉 ``ensure_monthly_quota`` → 429 用例红；
- KB 查询去掉 organization_id → 跨组织用例红；
- 不向检索传 knowledge_base_id → KB 隔离用例红；
- 去掉 token 记账 / 重复记账 → 计量用例红；
- 流式不发送 ``[DONE]`` → 流式用例红。
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 — 注册全部表，供 create_all
from app.core.database import Base, get_db
from app.core.llm_failures import UserLLMRequestError
from app.models.knowledge_base import KnowledgeBase
from app.models.organization import Organization, user_organization
from app.models.organization_settings import OrganizationSettings
from app.models.token_usage import TokenUsageRecord
from app.models.user import User
from app.rag.errors import LLMNotConfiguredError, RetrievalUnavailableError
from app.services import openai_compat_service
from app.services.api_key_service import api_key_service

ORG_ID = 42
OTHER_ORG_ID = 43
USER_ID = 7
NO_ORG_USER_ID = 9
OWN_KB = "kb-own"
OTHER_KB = "kb-other"
URL = "/api/v1/chat/completions"

ANSWER_CHUNKS = ["知识库", "答案"]
CONTEXT = [
    {
        "document_id": "doc-public-1",
        "chunk_id": "chunk-internal-1",
        "filename": "合同.pdf",
        "relevance_score": 0.91,
        "snippet": "内部正文片段",
        "file_path": "42/secret/合同.pdf",
        "organization_id": ORG_ID,
        "uploaded_by": USER_ID,
    }
]


class FakeRAG:
    """rag_service 替身：记录检索/生成调用，可配置 usage 与失败。"""

    def __init__(self, *, context=None, chunks=None, usage=(12, 7)):
        self.context = CONTEXT if context is None else context
        self.chunks = ANSWER_CHUNKS if chunks is None else chunks
        self.usage = usage
        # 非 None 才会让真实 pipeline 把上游异常转成 UserLLMRequestError；
        # 端点必须显式把它传给 chat_stream（生产中是全局 LLM 客户端）。
        self.openai_client = object()
        self.search_calls: list[dict] = []
        self.stream_calls: list[dict] = []
        self.stream_closed = False
        self.search_error: Exception | None = None
        self.stream_error: Exception | None = None

    async def search_knowledge_base(
        self,
        query,
        organization_id,
        top_k=5,
        document_ids=None,
        metadata_filters=None,
        knowledge_base_id=None,
        debug=False,
        search_mode=None,
        viewer_user_id=None,
    ):
        self.search_calls.append(
            {
                "query": query,
                "organization_id": organization_id,
                "top_k": top_k,
                "document_ids": document_ids,
                "knowledge_base_id": knowledge_base_id,
                "viewer_user_id": viewer_user_id,
            }
        )
        if self.search_error is not None:
            raise self.search_error
        return self.context, None

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
            self.stream_closed = True

    def get_last_token_usage(self):
        return self.usage


@pytest.fixture
async def env(monkeypatch):
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as session:
        session.add_all(
            [
                Organization(id=ORG_ID, name="Org Own", owner_id=USER_ID),
                Organization(id=OTHER_ORG_ID, name="Org Other", owner_id=8),
                User(
                    id=USER_ID,
                    username="openai-user",
                    email="openai@example.com",
                    hashed_password="x",
                    is_active=True,
                    organization_id=ORG_ID,
                ),
                User(
                    id=NO_ORG_USER_ID,
                    username="no-org-user",
                    email="noorg@example.com",
                    hashed_password="x",
                    is_active=True,
                    organization_id=None,
                ),
                KnowledgeBase(id=OWN_KB, organization_id=ORG_ID, name="自有库"),
                KnowledgeBase(id=OTHER_KB, organization_id=OTHER_ORG_ID, name="他组织库"),
                # NULL 配额 = 不限额；配额用例会改为具体值。
                OrganizationSettings(organization_id=ORG_ID, monthly_token_quota=None),
            ]
        )
        await session.execute(
            user_organization.insert().values(user_id=USER_ID, organization_id=ORG_ID)
        )
        await session.commit()

    async def _override_db():
        async with factory() as session:
            yield session

    from app.main import app

    app.dependency_overrides[get_db] = _override_db
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    fake = FakeRAG()

    async def _make_key(scopes=("chat:write",), user_id=USER_ID):
        async with factory() as session:
            user = await session.get(User, user_id)
            _, raw = await api_key_service.create_key(
                session, user=user, name="openai-test", scopes=list(scopes)
            )
            return raw

    async def _set_quota(quota: int | None, used: int = 0):
        from sqlalchemy import select

        async with factory() as session:
            row = (
                await session.execute(
                    select(OrganizationSettings).where(
                        OrganizationSettings.organization_id == ORG_ID
                    )
                )
            ).scalar_one()
            row.monthly_token_quota = quota
            if used:
                session.add(
                    TokenUsageRecord(
                        user_id=USER_ID,
                        organization_id=ORG_ID,
                        model="seed",
                        source="rag_chat",
                        input_tokens=used,
                        output_tokens=0,
                    )
                )
            await session.commit()

    monkeypatch.setattr(openai_compat_service, "rag_service", fake)
    async with AsyncClient(transport=transport, base_url="http://openaicompat") as client:
        yield SimpleNamespace(
            client=client,
            factory=factory,
            fake=fake,
            transport=transport,
            make_key=_make_key,
            set_quota=_set_quota,
        )
    app.dependency_overrides.pop(get_db, None)
    await engine.dispose()


def _auth(raw: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw}"}


def _body(**overrides) -> dict:
    payload = {
        "model": "docmind",
        "messages": [{"role": "user", "content": "你好"}],
    }
    payload.update(overrides)
    return payload


async def _post(env, payload=None, scopes=("chat:write",)):
    raw = await env.make_key(scopes=scopes)
    body = _body() if payload is None else payload
    return await env.client.post(URL, json=body, headers=_auth(raw))


def _assert_openai_error(response, status: int, *, code: str | None = None) -> dict:
    assert response.status_code == status, response.text
    assert response.headers.get("content-type", "").startswith("application/json")
    body = response.json()
    assert set(body) == {"error"}, body
    assert set(body["error"]) == {"message", "type", "code"}, body
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]
    if code is not None:
        assert body["error"]["code"] == code, body
    return body["error"]


def _sse_data(response) -> list[str]:
    return [
        line[len("data: ") :] for line in response.text.splitlines() if line.startswith("data: ")
    ]


# ── 认证 / scope ────────────────────────────────────────────────────


async def test_missing_key_is_401_openai_error(env):
    response = await env.client.post(URL, json=_body())
    error = _assert_openai_error(response, 401)
    assert error["type"] == "authentication_error"
    assert response.headers.get("www-authenticate") == "Bearer"


async def test_invalid_key_is_401(env):
    response = await env.client.post(URL, json=_body(), headers=_auth("dm_this-key-does-not-exist"))
    _assert_openai_error(response, 401, code="invalid_api_key")


async def test_revoked_key_is_401(env):
    raw = await env.make_key()
    async with env.factory() as session:
        from sqlalchemy import select

        from app.models.api_key import ApiKey

        record = (
            await session.execute(select(ApiKey).where(ApiKey.prefix == raw[:8]))
        ).scalar_one()
        await api_key_service.revoke_key(session, record)
    response = await env.client.post(URL, json=_body(), headers=_auth(raw))
    _assert_openai_error(response, 401)


async def test_key_without_chat_write_scope_is_403(env):
    response = await _post(env, scopes=("rpc",))
    error = _assert_openai_error(response, 403)
    assert error["type"] == "permission_error"
    # 403 必须发生在检索/LLM 之前。
    assert env.fake.search_calls == []


async def test_key_without_organization_is_403(env):
    raw = await env.make_key(user_id=NO_ORG_USER_ID)
    response = await env.client.post(URL, json=_body(), headers=_auth(raw))
    error = _assert_openai_error(response, 403, code="ORGANIZATION_REQUIRED")
    assert error["type"] == "permission_error"


# ── 配额 ────────────────────────────────────────────────────────────


async def test_quota_exceeded_is_429_before_retrieval_and_llm(env):
    await env.set_quota(100, used=150)
    response = await _post(env)
    error = _assert_openai_error(response, 429, code="ORG_TOKEN_QUOTA_EXCEEDED")
    assert error["type"] == "insufficient_quota"
    assert env.fake.search_calls == []
    assert env.fake.stream_calls == []


async def test_quota_exceeded_stream_is_json_429_before_stream(env):
    """流式请求同样必须在开流前返回 JSON 429，而不是半截 SSE。"""
    await env.set_quota(100, used=150)
    response = await _post(env, _body(stream=True))
    error = _assert_openai_error(response, 429, code="ORG_TOKEN_QUOTA_EXCEEDED")
    assert error["type"] == "insufficient_quota"
    assert env.fake.search_calls == []
    assert env.fake.stream_calls == []


# ── 请求映射 / 校验 ─────────────────────────────────────────────────


async def test_empty_messages_is_400(env):
    response = await _post(env, {"model": "docmind", "messages": []})
    _assert_openai_error(response, 400, code="INVALID_MESSAGES")
    assert env.fake.search_calls == []


async def test_missing_user_message_is_400(env):
    response = await _post(
        env,
        {
            "model": "docmind",
            "messages": [{"role": "system", "content": "只有系统提示"}],
        },
    )
    _assert_openai_error(response, 400, code="INVALID_MESSAGES")


async def test_invalid_role_is_400(env):
    response = await _post(
        env,
        {
            "model": "docmind",
            "messages": [
                {"role": "user", "content": "你好"},
                {"role": "tool", "content": "工具结果"},
            ],
        },
    )
    _assert_openai_error(response, 400, code="INVALID_MESSAGES")
    assert env.fake.search_calls == []


@pytest.mark.parametrize(
    "payload",
    [
        {"messages": [{"role": "user", "content": "hi"}]},  # 缺 model
        {"model": "docmind"},  # 缺 messages
        {"model": 123, "messages": [{"role": "user", "content": "hi"}]},
        {"model": "docmind", "messages": "not-a-list"},
        {"model": "docmind", "messages": [{"content": "缺 role"}]},
    ],
)
async def test_schema_invalid_body_is_openai_400(env, payload):
    """Pydantic 校验失败也必须走 OpenAI error 信封（而非 DocMind 422）。"""
    response = await _post(env, payload)
    error = _assert_openai_error(response, 400, code="INVALID_REQUEST")
    assert error["type"] == "invalid_request_error"
    assert env.fake.search_calls == []


@pytest.mark.parametrize(
    "payload,code",
    [
        # docmind: 空后缀
        (
            {"model": "docmind:", "messages": [{"role": "user", "content": "hi"}]},
            "MODEL_NOT_SUPPORTED",
        ),
        # 空白 user 内容
        (
            {"model": "docmind", "messages": [{"role": "user", "content": "   "}]},
            "INVALID_MESSAGES",
        ),
        # assistant prefill（最后一条非 system 不是 user）
        (
            {
                "model": "docmind",
                "messages": [
                    {"role": "user", "content": "hi"},
                    {"role": "assistant", "content": "prefill"},
                ],
            },
            "INVALID_MESSAGES",
        ),
        # 多模态 content
        (
            {
                "model": "docmind",
                "messages": [
                    {"role": "user", "content": [{"type": "text", "text": "hi"}]},
                ],
            },
            "INVALID_MESSAGES",
        ),
        # user 之后还有 system
        (
            {
                "model": "docmind",
                "messages": [
                    {"role": "user", "content": "hi"},
                    {"role": "system", "content": "trailing"},
                ],
            },
            "INVALID_MESSAGES",
        ),
    ],
)
async def test_invalid_message_shapes_are_400(env, payload, code):
    response = await _post(env, payload)
    _assert_openai_error(response, 400, code=code)
    assert env.fake.search_calls == []


async def test_unknown_fields_are_ignored(env):
    payload = _body(
        stream=False,
        temperature=0.3,
        max_tokens=128,
        top_p=0.9,
        n=1,
        stream_options={"include_usage": True},
        messages=[
            {
                "role": "user",
                "content": "你好",
                "name": "caller",
                "extra": {"ignored": True},
            }
        ],
    )
    response = await _post(env, payload)
    assert response.status_code == 200, response.text


async def test_multi_turn_and_system_prompt_mapping(env):
    response = await _post(
        env,
        {
            "model": "docmind",
            "messages": [
                {"role": "system", "content": "你是合同助手"},
                {"role": "user", "content": "第一问"},
                {"role": "assistant", "content": "第一答"},
                {"role": "user", "content": "第二问"},
            ],
        },
    )
    assert response.status_code == 200, response.text
    call = env.fake.stream_calls[-1]
    assert call["query"] == "第二问"
    assert call["history"] == [
        {"role": "user", "content": "第一问"},
        {"role": "assistant", "content": "第一答"},
    ]
    assert call["system_prompt_override"] == "你是合同助手"
    assert env.fake.search_calls[-1]["query"] == "第二问"


async def test_multiple_system_messages_are_joined(env):
    response = await _post(
        env,
        {
            "model": "docmind",
            "messages": [
                {"role": "system", "content": "规则一"},
                {"role": "system", "content": "规则二"},
                {"role": "user", "content": "问题"},
            ],
        },
    )
    assert response.status_code == 200, response.text
    assert env.fake.stream_calls[-1]["system_prompt_override"] == "规则一\n\n规则二"


async def test_org_policy_model_topk_privacy_are_wired(env):
    """组织默认模型 / 检索 top_k / 隐私开关必须真正传到生成与检索。"""
    from sqlalchemy import select

    async with env.factory() as session:
        row = (
            await session.execute(
                select(OrganizationSettings).where(OrganizationSettings.organization_id == ORG_ID)
            )
        ).scalar_one()
        row.default_model = "org-model"
        row.retrieval_top_k = 9
        row.allow_privacy_mode = False
        await session.commit()

    response = await _post(env)
    assert response.status_code == 200, response.text
    search = env.fake.search_calls[-1]
    call = env.fake.stream_calls[-1]
    assert search["top_k"] == 9
    assert search["organization_id"] == ORG_ID
    assert call["model_override"] == "org-model"
    assert call["enable_masking"] is False
    assert call["raise_user_errors"] is True
    assert call["client_override"] is env.fake.openai_client


# ── 模型 / 知识库映射 ───────────────────────────────────────────────


async def test_docmind_model_uses_org_scope(env):
    response = await _post(env)
    assert response.status_code == 200, response.text
    search = env.fake.search_calls[-1]
    assert search["organization_id"] == ORG_ID
    assert search["knowledge_base_id"] is None
    assert search["viewer_user_id"] == USER_ID
    assert response.json()["model"] == "docmind"


async def test_docmind_kb_model_scopes_retrieval(env):
    response = await _post(env, _body(model=f"docmind:{OWN_KB}"))
    assert response.status_code == 200, response.text
    search = env.fake.search_calls[-1]
    assert search["knowledge_base_id"] == OWN_KB
    assert response.json()["model"] == f"docmind:{OWN_KB}"


async def test_cross_org_kb_and_missing_kb_are_indistinguishable_404(env):
    cross = await _post(env, _body(model=f"docmind:{OTHER_KB}"))
    missing = await _post(env, _body(model="docmind:kb-not-exist"))
    cross_error = _assert_openai_error(cross, 404)
    missing_error = _assert_openai_error(missing, 404)
    assert cross_error["code"] == "KNOWLEDGE_BASE_NOT_FOUND"
    assert cross_error == missing_error
    assert env.fake.search_calls == []
    assert env.fake.stream_calls == []


async def test_unsupported_model_is_400(env):
    error = _assert_openai_error(
        await _post(env, _body(model="gpt-4o")), 400, code="MODEL_NOT_SUPPORTED"
    )
    assert error["type"] == "invalid_request_error"
    assert env.fake.search_calls == []


# ── 非流式 ──────────────────────────────────────────────────────────


async def test_non_stream_response_shape_usage_and_citations(env, monkeypatch):
    recorded = AsyncMock()
    monkeypatch.setattr("app.api.v1.endpoints.token_usage.record_token_usage", recorded)
    response = await _post(env)
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["object"] == "chat.completion"
    assert body["id"].startswith("chatcmpl-")
    assert body["model"] == "docmind"
    assert isinstance(body["created"], int)
    assert body["choices"][0]["index"] == 0
    assert body["choices"][0]["finish_reason"] == "stop"
    message = body["choices"][0]["message"]
    assert message["role"] == "assistant"
    assert message["content"] == "知识库答案"
    assert body["usage"] == {
        "prompt_tokens": 12,
        "completion_tokens": 7,
        "total_tokens": 19,
    }

    citations = message["docmind_citations"]
    assert len(citations) == 1
    assert citations[0]["id"].startswith("cite_")
    assert citations[0]["title"] == "合同.pdf"
    assert citations[0]["score"] == 0.91
    assert set(citations[0]) == {"id", "title", "score"}
    serialized = json.dumps(citations, ensure_ascii=False)
    # 正确性评审：不得出现 document_id / chunk_id / 内部路径 / 组织 / 上传者。
    for internal in (
        "doc-public-1",
        "chunk-internal-1",
        "file_path",
        "organization_id",
        "uploaded_by",
    ):
        assert internal not in serialized

    # OpenAI 官方模型可解析（未知扩展字段被忽略）。
    from openai.types.chat import ChatCompletion

    parsed = ChatCompletion.model_validate(body)
    assert parsed.choices[0].message.content == "知识库答案"

    recorded.assert_awaited_once()
    kwargs = recorded.await_args.kwargs
    assert kwargs["source"] == "openai_compat"
    assert kwargs["feature"] == "openai_compat"
    assert kwargs["organization_id"] == ORG_ID
    assert kwargs["user_id"] == USER_ID
    assert (kwargs["input_tokens"], kwargs["output_tokens"]) == (12, 7)
    assert kwargs["model"]


async def test_non_stream_without_usage_reports_honest_zero(env, monkeypatch):
    env.fake.usage = None
    recorded = AsyncMock()
    monkeypatch.setattr("app.api.v1.endpoints.token_usage.record_token_usage", recorded)
    response = await _post(env)
    assert response.status_code == 200, response.text
    assert response.json()["usage"] == {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }
    recorded.assert_not_awaited()


async def test_usage_recording_failure_does_not_break_response(env, monkeypatch):
    async def _boom(**kwargs):
        raise RuntimeError("ledger down")

    monkeypatch.setattr("app.api.v1.endpoints.token_usage.record_token_usage", _boom)
    response = await _post(env)
    assert response.status_code == 200, response.text
    assert response.json()["choices"][0]["message"]["content"] == "知识库答案"


async def test_usage_is_persisted_with_openai_compat_source(env, monkeypatch):
    """真实写入 DB：source/feature/model/org/user/tokens 可被后台报表读到。"""
    from sqlalchemy import select

    monkeypatch.setattr("app.core.database.AsyncSessionLocal", env.factory)
    response = await _post(env)
    assert response.status_code == 200, response.text

    async with env.factory() as session:
        rows = (
            (
                await session.execute(
                    select(TokenUsageRecord).where(TokenUsageRecord.source == "openai_compat")
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1
    row = rows[0]
    assert row.feature == "openai_compat"
    assert row.organization_id == ORG_ID
    assert row.user_id == USER_ID
    assert (row.input_tokens, row.output_tokens) == (12, 7)
    assert row.model


async def test_failed_generation_still_records_measured_usage(env, monkeypatch):
    """上游失败前已产生的 token 也要入账（诚实成本归因）。"""
    recorded = AsyncMock()
    monkeypatch.setattr("app.api.v1.endpoints.token_usage.record_token_usage", recorded)
    env.fake.stream_error = RuntimeError("upstream down")
    response = await _post(env)
    _assert_openai_error(response, 500, code="COMPLETION_FAILED")
    recorded.assert_awaited_once()
    kwargs = recorded.await_args.kwargs
    assert kwargs["source"] == "openai_compat"
    assert (kwargs["input_tokens"], kwargs["output_tokens"]) == (12, 7)


async def test_audit_detail_has_no_body(env, monkeypatch):
    captured: list[dict] = []

    async def _capture(**kwargs):
        captured.append(kwargs)

    monkeypatch.setattr(openai_compat_service.audit_service, "log_activity", _capture)
    response = await _post(
        env,
        {
            "model": "docmind",
            "messages": [{"role": "user", "content": "绝密问题文本"}],
        },
    )
    assert response.status_code == 200, response.text
    assert len(captured) == 1
    call = captured[0]
    assert call["action"] == "openai_chat_completion"
    detail = json.loads(call["detail"])
    assert set(detail) == {
        "model",
        "knowledge_base_id",
        "stream",
        "input_tokens",
        "output_tokens",
        "total_tokens",
    }
    assert detail["model"] == "docmind"
    assert detail["knowledge_base_id"] is None
    assert detail["stream"] is False
    assert detail["input_tokens"] == 12
    assert "绝密问题文本" not in call["detail"]
    assert "知识库答案" not in call["detail"]


async def test_retrieval_failure_is_openai_503(env):

    env.fake.search_error = RetrievalUnavailableError("es down")
    response = await _post(env)
    error = _assert_openai_error(response, 503, code="RETRIEVAL_UNAVAILABLE")
    assert error["type"] == "server_error"
    assert "es down" not in json.dumps(error)
    assert env.fake.stream_calls == []


async def test_stream_retrieval_failure_is_sse_error(env):
    """流式检索失败也必须以 OpenAI error SSE 收尾，而不是 500 信封。"""

    env.fake.search_error = RetrievalUnavailableError("es down")
    response = await _post(env, _body(stream=True))
    assert response.status_code == 200, response.text
    data = _sse_data(response)
    assert data[-1] == "[DONE]"
    payload = json.loads(data[-2])
    assert payload["error"]["code"] == "RETRIEVAL_UNAVAILABLE"
    assert payload["error"]["type"] == "server_error"
    assert "es down" not in response.text
    assert env.fake.stream_calls == []


@pytest.mark.parametrize(
    "error,status,code",
    [
        (LLMNotConfiguredError("no key"), 503, "LLM_NOT_CONFIGURED"),
        (UserLLMRequestError("provider down"), 502, "LLM_REQUEST_FAILED"),
        (RuntimeError("upstream stack"), 500, "COMPLETION_FAILED"),
    ],
)
async def test_generation_failure_maps_to_openai_error(env, error, status, code):
    env.fake.stream_error = error
    response = await _post(env)
    payload = _assert_openai_error(response, status, code=code)
    assert "stack" not in payload["message"]
    assert "no key" not in payload["message"]
    assert "provider down" not in payload["message"]


async def test_unexpected_endpoint_exception_is_openai_500(env, monkeypatch):
    async def _boom(*args, **kwargs):
        raise RuntimeError("db driver exploded")

    monkeypatch.setattr(openai_compat_service, "resolve_model_target", _boom)
    response = await _post(env)
    payload = _assert_openai_error(response, 500, code="INTERNAL_ERROR")
    assert payload["type"] == "server_error"
    assert "db driver" not in json.dumps(payload)


# ── 流式 ────────────────────────────────────────────────────────────


async def test_stream_chunks_done_and_citations(env, monkeypatch):
    recorded = AsyncMock()
    monkeypatch.setattr("app.api.v1.endpoints.token_usage.record_token_usage", recorded)
    audited: list[dict] = []

    async def _capture_audit(**kwargs):
        audited.append(kwargs)

    monkeypatch.setattr(openai_compat_service.audit_service, "log_activity", _capture_audit)
    response = await _post(env, _body(stream=True))
    assert response.status_code == 200, response.text
    assert response.headers.get("content-type", "").startswith("text/event-stream")

    data = _sse_data(response)
    assert data, response.text
    assert data[-1] == "[DONE]"
    chunk_dicts = [json.loads(item) for item in data[:-1]]

    # 首帧 role、正文增量、最终 finish_reason。
    assert chunk_dicts[0]["choices"][0]["delta"] == {"role": "assistant"}
    assert all(chunk["object"] == "chat.completion.chunk" for chunk in chunk_dicts)
    assert all(chunk["id"] == chunk_dicts[0]["id"] for chunk in chunk_dicts)
    assert all(chunk["model"] == "docmind" for chunk in chunk_dicts)
    assert all(chunk["choices"][0]["index"] == 0 for chunk in chunk_dicts)
    for intermediate in chunk_dicts[:-1]:
        assert intermediate["choices"][0]["finish_reason"] is None
    content = "".join(
        chunk["choices"][0]["delta"].get("content", "")
        for chunk in chunk_dicts
        if chunk["choices"][0]["delta"]
    )
    assert content == "知识库答案"

    final = chunk_dicts[-1]
    assert final["choices"][0]["finish_reason"] == "stop"
    assert final["choices"][0]["delta"] == {}
    assert len(final["docmind_citations"]) == 1
    assert set(final["docmind_citations"][0]) == {"id", "title", "score"}
    assert final["docmind_citations"][0]["id"].startswith("cite_")
    assert final["docmind_citations"][0]["title"] == "合同.pdf"
    assert final["docmind_citations"][0]["score"] == 0.91
    assert final["usage"] == {
        "prompt_tokens": 12,
        "completion_tokens": 7,
        "total_tokens": 19,
    }
    # 整条流都不得出现内部引用字段。
    for internal in (
        "doc-public-1",
        "chunk-internal-1",
        "file_path",
        "organization_id",
        "uploaded_by",
    ):
        assert internal not in response.text

    from openai.types.chat import ChatCompletionChunk

    for chunk in chunk_dicts:
        ChatCompletionChunk.model_validate(chunk)

    recorded.assert_awaited_once()
    kwargs = recorded.await_args.kwargs
    assert kwargs["source"] == "openai_compat"
    assert kwargs["feature"] == "openai_compat"
    assert (kwargs["input_tokens"], kwargs["output_tokens"]) == (12, 7)

    # 审计也只在完成后记一次，且 stream=True、无正文。
    assert len(audited) == 1
    detail = json.loads(audited[0]["detail"])
    assert audited[0]["action"] == "openai_chat_completion"
    assert set(detail) == {
        "model",
        "knowledge_base_id",
        "stream",
        "input_tokens",
        "output_tokens",
        "total_tokens",
    }
    assert detail["stream"] is True
    assert detail["input_tokens"] == 12
    assert "你好" not in audited[0]["detail"]
    assert "知识库答案" not in audited[0]["detail"]
    assert env.fake.stream_closed is True


async def test_stream_without_usage_omits_usage_and_skips_ledger(env, monkeypatch):
    env.fake.usage = None
    recorded = AsyncMock()
    monkeypatch.setattr("app.api.v1.endpoints.token_usage.record_token_usage", recorded)
    response = await _post(env, _body(stream=True))
    assert response.status_code == 200, response.text
    data = _sse_data(response)
    assert data[-1] == "[DONE]"
    final = json.loads(data[-2])
    assert "usage" not in final
    recorded.assert_not_awaited()


async def test_stream_error_uses_openai_error_payload(env):
    class _BoomRAG(FakeRAG):
        async def chat_stream(self, *args, **kwargs):
            raise RuntimeError("stack trace must not leak")
            yield ""  # pragma: no cover

    env.fake = _BoomRAG()
    import app.services.openai_compat_service as service_module

    original = service_module.rag_service
    service_module.rag_service = env.fake
    try:
        response = await _post(env, _body(stream=True))
    finally:
        service_module.rag_service = original

    data = _sse_data(response)
    assert data[-1] == "[DONE]"
    payload = json.loads(data[-2])
    assert set(payload) == {"error"}
    assert payload["error"]["type"] == "server_error"
    assert payload["error"]["code"] == "COMPLETION_FAILED"
    assert "stack trace" not in response.text


async def test_stream_disconnect_closes_upstream_and_still_records_usage(env, monkeypatch):
    """客户端 aclose：上游生成器被关闭，且已产生的用量仍落账（不双记）。"""
    import asyncio

    from app.api.v1.endpoints import openai_compat as endpoint_module

    recorded = AsyncMock()
    monkeypatch.setattr("app.api.v1.endpoints.token_usage.record_token_usage", recorded)
    audited: list[dict] = []

    async def _capture_audit(**kwargs):
        audited.append(kwargs)

    monkeypatch.setattr(openai_compat_service.audit_service, "log_activity", _capture_audit)

    async with env.factory() as session:
        user = await session.get(User, USER_ID)
        gen = endpoint_module._stream_response(
            body=SimpleNamespace(model="docmind", stream=True),
            current_user=user,
            db=session,
            organization_id=ORG_ID,
            target=openai_compat_service.ModelTarget(
                requested_model="docmind", knowledge_base_id=None
            ),
            turn=openai_compat_service.ChatTurn(query="你好", history=[], system_prompt=None),
            model="test-model",
            top_k=5,
            enable_masking=True,
            request=SimpleNamespace(headers={}, client=None),
        )
        await gen.__anext__()  # 首帧 role
        # 消费到第一段正文，确认上游生成器已启动后模拟客户端断开。
        while True:
            frame = await asyncio.wait_for(gen.__anext__(), timeout=5)
            if '"content"' in frame:
                break
        await gen.aclose()
    assert env.fake.stream_closed is True
    recorded.assert_awaited_once()
    kwargs = recorded.await_args.kwargs
    assert kwargs["source"] == "openai_compat"
    assert (kwargs["input_tokens"], kwargs["output_tokens"]) == (12, 7)
    assert len(audited) == 1
    assert json.loads(audited[0]["detail"])["stream"] is True


# ── 服务层单元：关闭 / 占位符 / 引用派生 ────────────────────────────


class _TrackedAsyncStream:
    """显式 aclose 才关的异步迭代器（无 GC finalizer）。"""

    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.explicit_close = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)

    async def aclose(self):
        self.explicit_close = True


async def test_iter_completion_explicitly_closes_upstream(env, monkeypatch):
    tracked = _TrackedAsyncStream(["a", "b"])

    class _Svc:
        openai_client = object()

        def chat_stream(self, *args, **kwargs):
            return tracked

    monkeypatch.setattr(openai_compat_service, "rag_service", _Svc())
    chunks = [
        chunk
        async for chunk in openai_compat_service.iter_completion_chunks(
            query="q",
            context_results=[],
            history=[],
            system_prompt=None,
            model="m",
            enable_masking=False,
        )
    ]
    assert "".join(chunks) == "ab"
    assert tracked.explicit_close is True


def test_split_partial_placeholder_is_bounded():
    from app.services.openai_compat_service import _split_partial_placeholder

    assert _split_partial_placeholder("hello [PHONE_") == ("hello ", "[PHONE_")
    assert _split_partial_placeholder("x [PHONE_1] done") == ("x [PHONE_1] done", "")
    # 普通未闭合 [（JSON 数组/区间）不得扣留后续正文。
    assert _split_partial_placeholder("array [0, 1") == ("array [0, 1", "")
    long_bracket = "a [" + "x" * 40
    assert _split_partial_placeholder(long_bracket) == (long_bracket, "")


async def test_streaming_unmasks_placeholder_split_across_chunks(env, monkeypatch):
    class _MaskingRAG:
        openai_client = object()

        async def chat_stream(self, *args, **kwargs):
            sink = kwargs.get("mask_mapping_sink")
            if sink is not None:
                sink["[PHONE_1]"] = "13800000000"
            yield "联系 [PH"
            yield "ONE_1] 完成"

    monkeypatch.setattr(openai_compat_service, "rag_service", _MaskingRAG())
    chunks = [
        chunk
        async for chunk in openai_compat_service.iter_completion_chunks(
            query="q",
            context_results=[],
            history=[],
            system_prompt=None,
            model="m",
            enable_masking=True,
        )
    ]
    assert "".join(chunks) == "联系 13800000000 完成"


def test_public_citations_are_hashed_stable_and_deduped():
    context = [
        {
            "document_id": "doc-1",
            "filename": "a.pdf",
            "relevance_score": 0.2,
            "file_path": "42/secret/a.pdf",
            "organization_id": ORG_ID,
            "uploaded_by": USER_ID,
        },
        {"document_id": "doc-1", "filename": "a.pdf", "relevance_score": 0.8},
        {
            "chunk_id": "chunk-2",
            "filename": "b.pdf",
            "score": 0.5,
            "organization_id": ORG_ID,
        },
    ]
    first = openai_compat_service.build_public_citations(context)
    second = openai_compat_service.build_public_citations(context)

    assert first == second, "同一输入必须得到稳定 id"
    assert len(first) == 2
    assert all(set(item) == {"id", "title", "score"} for item in first)
    assert all(item["id"].startswith("cite_") for item in first)
    assert first[0]["score"] == 0.8, "同一文档保留最高分"
    serialized = json.dumps(first, ensure_ascii=False)
    for internal in (
        "doc-1",
        "chunk-2",
        "file_path",
        "organization_id",
        f'"uploaded_by": {USER_ID}',
    ):
        assert internal not in serialized


async def test_real_pipeline_raises_upstream_error_when_client_override_given(monkeypatch):
    """与 finding #1 对应的合同：有 client_override 时上游异常必须抛出而非当正文。"""
    from app.rag.pipeline import RAGPipeline

    async def _boom(*args, **kwargs):
        raise RuntimeError("llm down")

    monkeypatch.setattr("app.rag.pipeline.create_chat_completion", _boom)
    pipeline = RAGPipeline(openai_client=object())
    with pytest.raises(UserLLMRequestError):
        async for _chunk in pipeline.chat_stream(
            "q", [], client_override=object(), raise_user_errors=True
        ):
            pass  # pragma: no cover


async def test_pipeline_shields_upstream_close_on_cancellation(monkeypatch):
    """ASGI 2.3 断连会以取消收尾；上游 close 必须在 shield 内真正完成。"""
    import asyncio

    import anyio

    from app.rag.pipeline import RAGPipeline

    class _SlowStream:
        def __init__(self):
            self.started = asyncio.Event()
            self.close_called = asyncio.Event()

        def __aiter__(self):
            return self._generate()

        async def _generate(self):
            self.started.set()
            yield SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content="第一段"))]
            )
            await asyncio.Event().wait()  # 永挂起，等待取消

        async def close(self):
            await anyio.sleep(0)
            self.close_called.set()

    stream = _SlowStream()

    async def _create(*args, **kwargs):
        return stream

    monkeypatch.setattr("app.rag.pipeline.create_chat_completion", _create)
    pipeline = RAGPipeline(openai_client=object())

    async def _consume():
        async for _chunk in pipeline.chat_stream(
            "q", [], client_override=object(), raise_user_errors=True
        ):
            pass

    task = asyncio.create_task(_consume())
    await asyncio.wait_for(stream.started.wait(), timeout=5)
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert stream.close_called.is_set(), "取消时上游 close 被 cancel scope 打断"


async def test_pipeline_writes_pii_sink_before_first_llm_chunk(monkeypatch):
    """M6：context/query 占位符映射必须在调用上游前写入 sink（流式还原依赖它）。"""
    import app.rag.pipeline as pipeline_module
    from app.rag.pipeline import RAGPipeline

    monkeypatch.setattr(pipeline_module.settings, "ENABLE_PII_MASKING", True, raising=False)
    seen: dict = {}
    sink: dict = {}

    async def _create(*args, **kwargs):
        seen["sink_at_call"] = dict(sink)

        async def _gen():
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="回答"))])

        return _gen()

    monkeypatch.setattr(pipeline_module, "create_chat_completion", _create)
    pipeline = RAGPipeline(openai_client=object())
    chunks = [
        chunk
        async for chunk in pipeline.chat_stream(
            "联系我 13800138000", [], mask_mapping_sink=sink, enable_masking=True
        )
    ]
    assert "".join(chunks)
    assert seen["sink_at_call"], "调用上游前 sink 仍为空：流式无法还原占位符"
    assert any(key.startswith("[PHONE_") for key in seen["sink_at_call"])
    assert "13800138000" in seen["sink_at_call"].values()
    assert any(key.startswith("[PHONE_") for key in sink)


@pytest.mark.parametrize("slow_send", [False, True])
async def test_asgi_disconnect_records_usage_once_with_real_pipeline(env, monkeypatch, slow_send):
    """真实 RAGPipeline + ASGI 2.3 断连：ACLOSE 后必须恰好记一次非零 usage。

    slow_send=False：断连发生在等待下一 chunk 时。
    slow_send=True：断连发生在 ASGI send（生成器停在 yield，不会进自己的帧），
    只有端点 finally 主动 aclose 才能触发 pipeline.report_tokens。
    """
    import asyncio

    import app.rag.pipeline as pipeline_module
    from app.api.v1.endpoints import openai_compat as endpoint_module
    from app.rag.pipeline import RAGPipeline
    from app.services import rag_service as rag_service_module

    class _SlowStream:
        def __init__(self):
            self.started = asyncio.Event()
            self.closed = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self.started.is_set():
                self.started.set()
                return SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content="第一段"))]
                )
            await asyncio.Event().wait()
            raise StopAsyncIteration  # pragma: no cover - 永不可达，等待取消

        async def close(self):
            await asyncio.sleep(0)
            self.closed = True

        async def aclose(self):
            self.closed = True

    stream = _SlowStream()

    async def _create(*args, **kwargs):
        return stream

    monkeypatch.setattr(pipeline_module, "create_chat_completion", _create)
    pipeline = RAGPipeline(openai_client=object())
    monkeypatch.setattr(rag_service_module, "get_rag_pipeline", lambda: pipeline)
    # 让端点走真实 facade（fixture 默认换成 FakeRAG）。
    monkeypatch.setattr(openai_compat_service, "rag_service", rag_service_module.rag_service)

    async def _retrieve(**kwargs):
        return []

    monkeypatch.setattr(openai_compat_service, "retrieve_context", _retrieve)

    recorded: list[dict] = []
    audited: list[dict] = []

    async def _rec(**kwargs):
        # 真实落库会有 await；加一个 checkpoint，让「去掉 shield」的变异暴露。
        await asyncio.sleep(0.05)
        recorded.append(kwargs)

    async def _audit(**kwargs):
        await asyncio.sleep(0.05)
        audited.append(kwargs)

    monkeypatch.setattr("app.api.v1.endpoints.token_usage.record_token_usage", _rec)
    monkeypatch.setattr(openai_compat_service.audit_service, "log_activity", _audit)

    gen = endpoint_module._stream_response(
        body=SimpleNamespace(model="docmind", stream=True),
        current_user=SimpleNamespace(id=USER_ID, organization_id=ORG_ID),
        db=None,
        organization_id=ORG_ID,
        target=openai_compat_service.ModelTarget(requested_model="docmind", knowledge_base_id=None),
        turn=openai_compat_service.ChatTurn(query="你好", history=[], system_prompt=None),
        model="probe-model",
        top_k=5,
        enable_masking=False,
        request=SimpleNamespace(headers={}, client=None),
    )

    disconnect = asyncio.Event()
    body_frames: list[bytes] = []

    async def receive():
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.body" and message.get("body"):
            body_frames.append(message["body"])
            if len(body_frames) == 2:  # role frame + 第一段正文已发出
                disconnect.set()
                if slow_send:
                    await asyncio.sleep(0.3)  # 取消落在 send 内部

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "method": "POST",
        "path": "/api/v1/chat/completions",
        "headers": [],
    }
    try:
        await endpoint_module.OpenAICompatStreamingResponse(gen, media_type="text/event-stream")(
            scope, receive, send
        )
    except BaseException:  # Starlette 以 task-group cancellation 结束
        pass
    await asyncio.sleep(0.3)

    assert stream.closed is True, "断连后上游流未被关闭"
    assert len(recorded) == 1, f"必须恰好一条 usage 账，实际 {recorded}"
    kwargs = recorded[0]
    assert kwargs["source"] == "openai_compat"
    assert kwargs["feature"] == "openai_compat"
    assert kwargs["input_tokens"] > 0
    assert kwargs["output_tokens"] > 0
    assert len(audited) == 1
    detail = json.loads(audited[0]["detail"])
    assert detail["input_tokens"] > 0
    assert detail["output_tokens"] > 0


async def test_real_pipeline_upstream_failure_is_openai_502(env, monkeypatch):
    """M2：真实 pipeline + 非流式，上游失败必须 502，绝不能 200 正文。"""
    import app.rag.pipeline as pipeline_module
    from app.rag.pipeline import RAGPipeline
    from app.services import rag_service as rag_service_module

    async def _boom(*args, **kwargs):
        raise RuntimeError("upstream stack must not leak")

    monkeypatch.setattr(pipeline_module, "create_chat_completion", _boom)
    pipeline = RAGPipeline(openai_client=object())
    monkeypatch.setattr(rag_service_module, "get_rag_pipeline", lambda: pipeline)
    monkeypatch.setattr(openai_compat_service, "rag_service", rag_service_module.rag_service)

    async def _retrieve(**kwargs):
        return []

    monkeypatch.setattr(openai_compat_service, "retrieve_context", _retrieve)

    response = await _post(env)
    payload = _assert_openai_error(response, 502, code="LLM_REQUEST_FAILED")
    assert "upstream stack" not in json.dumps(payload)


# ── 官方 OpenAI Python SDK（真实 SSE/JSON 解析，ASGI 传输）───────────


def _sdk(env, raw: str):
    from openai import AsyncOpenAI

    http_client = AsyncClient(transport=env.transport, base_url="http://openaicompat")
    return AsyncOpenAI(
        api_key=raw,
        base_url="http://openaicompat/api/v1",
        http_client=http_client,
        max_retries=0,
    )


async def test_official_openai_sdk_non_stream(env):
    raw = await env.make_key()
    sdk = _sdk(env, raw)
    try:
        completion = await sdk.chat.completions.create(
            model="docmind", messages=[{"role": "user", "content": "你好"}]
        )
    finally:
        await sdk.close()

    assert completion.object == "chat.completion"
    assert completion.model == "docmind"
    assert completion.choices[0].message.content == "知识库答案"
    assert completion.choices[0].finish_reason == "stop"
    assert completion.usage.prompt_tokens == 12
    assert completion.usage.completion_tokens == 7
    # 扩展字段被 SDK 保留但不影响标准字段解析。
    assert completion.choices[0].message.docmind_citations  # type: ignore[attr-defined]


async def test_official_openai_sdk_stream(env):
    raw = await env.make_key()
    sdk = _sdk(env, raw)
    try:
        stream = await sdk.chat.completions.create(
            model="docmind",
            messages=[{"role": "user", "content": "你好"}],
            stream=True,
        )
        chunks = [chunk async for chunk in stream]
    finally:
        await sdk.close()

    assert chunks, "SDK 未收到任何 chunk"
    text = "".join(
        chunk.choices[0].delta.content or ""
        for chunk in chunks
        if chunk.choices and chunk.choices[0].delta
    )
    assert text == "知识库答案"
    assert chunks[0].choices[0].delta.role == "assistant"
    assert chunks[-1].choices[0].finish_reason == "stop"
    assert chunks[-1].docmind_citations  # type: ignore[attr-defined]
