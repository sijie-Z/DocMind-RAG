"""Python SDK 契约测试（issue #480 §5.6；httpx.MockTransport，不连真实服务）。"""

import json

import httpx
import pytest

from docmind import AsyncClient, Client, DocMindError

SSE_BODY = "\n".join(
    [
        "event: chunk",
        'data: {"type": "chunk", "content": "你好", "conversationId": "s1"}',
        "",
        "id: s1:2",
        "event: message",
        'data: {"type": "message", "content": "你好", "conversationId": "s1"}',
        "",
        "",
    ]
)


def _client(handler, **kwargs) -> Client:
    return Client(
        "http://testserver",
        api_key="dm_test_key",
        transport=httpx.MockTransport(handler),
        retry_backoff=0,
        **kwargs,
    )


def test_sessions_create_sends_auth_and_returns_session():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        seen["path"] = request.url.path
        return httpx.Response(
            200, json={"success": True, "data": {"id": "s1", "title": "sdk-demo"}}
        )

    client = _client(handler)
    session = client.sessions.create(title="sdk-demo")

    assert seen["auth"] == "Bearer dm_test_key"
    assert seen["path"] == "/api/v1/agent/sessions"
    assert seen["body"] == {"title": "sdk-demo"}
    assert session.id == "s1" and session.title == "sdk-demo"


def test_chat_stream_yields_chunks_and_normalizes_done():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        seen["accept"] = request.headers.get("accept")
        seen["last_event_id"] = request.headers.get("last-event-id")
        return httpx.Response(
            200, text=SSE_BODY, headers={"content-type": "text/event-stream"}
        )

    client = _client(handler)
    events = list(
        client.chat.stream(
            "s1", "你好", client_message_id="msg-1", last_event_id="s1:1"
        )
    )

    assert seen["body"]["clientMessageId"] == "msg-1"
    assert seen["body"]["conversationId"] == "s1"
    assert seen["body"]["useAgent"] is True
    assert seen["accept"] == "text/event-stream"
    assert seen["last_event_id"] == "s1:1"
    assert [event.type for event in events] == ["chunk", "message", "done"]
    assert events[0].content == "你好"
    assert events[1].id == "s1:2"
    assert events[-1].type == "done"


@pytest.mark.asyncio
async def test_async_client_stream_and_create():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sessions"):
            return httpx.Response(
                200, json={"success": True, "data": {"id": "s2", "title": "async"}}
            )
        return httpx.Response(
            200, text=SSE_BODY, headers={"content-type": "text/event-stream"}
        )

    async with AsyncClient(
        "http://testserver",
        api_key="dm_test_key",
        transport=httpx.MockTransport(handler),
        retry_backoff=0,
    ) as client:
        session = await client.sessions.create(title="async")
        events = [event async for event in client.chat.stream(session.id, "你好")]
    assert session.id == "s2"
    assert [event.type for event in events] == ["chunk", "message", "done"]


def test_error_mapping_uses_error_code_and_status():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"success": False, "error_code": "AUTHENTICATION_ERROR", "message": "认证失败"},
        )

    client = _client(handler)
    with pytest.raises(DocMindError) as exc:
        client.sessions.list()
    assert exc.value.code == "AUTHENTICATION_ERROR"
    assert exc.value.status == 401
    assert exc.value.message == "认证失败"


def test_retries_only_on_retryable_status_for_non_stream():
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(503, json={"message": "busy"})
        return httpx.Response(200, json={"success": True, "data": {"sessions": []}})

    client = _client(handler)
    assert client.sessions.list() == []
    assert attempts["n"] == 3


def test_stream_is_not_retried():
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(503, json={"message": "busy"})

    client = _client(handler)
    with pytest.raises(DocMindError) as exc:
        list(client.chat.stream("s1", "hi", client_message_id="m"))
    assert exc.value.status == 503
    assert attempts["n"] == 1, "流式请求绝不自动重试（避免重复消息）"


def test_fork_tools_and_knowledge():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request.content))
        if request.url.path.endswith("/fork"):
            return httpx.Response(
                200, json={"success": True, "data": {"id": "child", "title": "fork"}}
            )
        if request.url.path.endswith("/tools"):
            return httpx.Response(
                200,
                json={"success": True, "data": [{"name": "search_knowledge_base"}]},
            )
        return httpx.Response(
            200,
            json={"query": "q", "results": [{"id": "d1", "text": "t"}], "total_count": 1},
        )

    client = _client(handler)
    forked = client.sessions.fork("s1", message_id="m1", include_checkpoint=True)
    assert forked.id == "child"
    assert client.tools.list() == [{"name": "search_knowledge_base"}]
    result = client.knowledge.search("q", top_k=3, search_mode="hybrid")
    assert result["total_count"] == 1

    fork_body = json.loads(calls[0][2])
    assert fork_body == {"message_id": "m1", "include_checkpoint": True}
    search_body = json.loads(calls[2][2])
    assert search_body == {"query": "q", "top_k": 3, "search_mode": "hybrid"}


APPROVAL_SSE = "\n".join(
    [
        "event: tool_result",
        'data: {"type": "tool_result", "content": "ok"}',
        "",
        "event: done",
        'data: {"type": "done", "decision": "approve"}',
        "",
        "",
    ]
)


def test_approvals_respond_consumes_sse():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200, text=APPROVAL_SSE, headers={"content-type": "text/event-stream"}
        )

    client = _client(handler)
    result = client.approvals.respond("s1", "t1", "allow")
    assert seen["path"] == "/api/v1/agent/tasks/t1/approval"
    assert seen["body"]["decision"] == "approve"
    assert result["decision"] == "approve"


@pytest.mark.asyncio
async def test_async_approvals_respond_consumes_sse():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text=APPROVAL_SSE, headers={"content-type": "text/event-stream"}
        )

    async with AsyncClient(
        "http://testserver",
        api_key="dm_test_key",
        transport=httpx.MockTransport(handler),
    ) as client:
        result = await client.approvals.respond("s1", "t1", "deny")
    assert result["decision"] == "approve"
