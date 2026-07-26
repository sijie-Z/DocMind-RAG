"""Tests for error classification in registry error mapper."""

import pytest

from app.agent.registry import ToolResult


class FakeException(Exception):
    pass


# ── Timeout classification ──────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw_text, expected_code",
    [
        ("timed out after 30s", "timeout"),
        ("deadline exceeded", "timeout"),
        ("operation timed out", "timeout"),
        # idle timeout (no-progress)
        ("stream hung — no progress for 60s", "idle_timeout"),
        ("stalled after 30s idle", "idle_timeout"),
    ],
)
def test_timeout_classification(raw_text, expected_code):
    result = ToolResult.fail("unknown", "call failed", raw=FakeException(raw_text))
    import asyncio

    from app.agent.registry import tool_registry

    async def apply():
        ctx = type("ctx", (), {"tool_name": "test", "arguments": {}, "entry": type("e", (), {"requires_auth": False})()})()
        await tool_registry._builtin_error_mapper_hook(result, ctx)

    asyncio.run(apply())
    assert result.error.code == expected_code


# ── Connection classification ───────────────────────────────────────────


@pytest.mark.parametrize(
    "raw_text, expected_code",
    [
        ("connection refused", "unreachable"),
        ("ECONNREFUSED", "unreachable"),
        ("getaddrinfo failed — no route to host", "unreachable"),
        ("network is unreachable", "unreachable"),
        ("connection reset by peer", "interrupted"),
        ("broken pipe", "interrupted"),
        ("incomplete read: expected 4096 bytes, got 1024", "interrupted"),
        ("socket error: transport failure", "connection_error"),
    ],
)
def test_connection_classification(raw_text, expected_code):
    result = ToolResult.fail("unknown", "connection failure", raw=FakeException(raw_text))
    import asyncio

    from app.agent.registry import tool_registry

    async def apply():
        ctx = type("ctx", (), {"tool_name": "test", "arguments": {}, "entry": type("e", (), {"requires_auth": False})()})()
        await tool_registry._builtin_error_mapper_hook(result, ctx)

    asyncio.run(apply())
    assert result.error.code == expected_code


# ── Rate limit ──────────────────────────────────────────────────────────


def test_rate_limit():
    result = ToolResult.fail("unknown", "rate limited", raw=FakeException("429 too many requests"))
    import asyncio

    from app.agent.registry import tool_registry

    async def apply():
        ctx = type("ctx", (), {"tool_name": "test", "arguments": {}, "entry": type("e", (), {"requires_auth": False})()})()
        await tool_registry._builtin_error_mapper_hook(result, ctx)

    asyncio.run(apply())
    assert result.error.code == "rate_limited"


# ── Auth classification ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw_text, expected_code",
    [
        ("401 unauthorized", "auth_expired"),
        ("invalid api key", "auth_expired"),
        ("token expired, please refresh", "auth_expired_refreshable"),
    ],
)
def test_auth_classification(raw_text, expected_code):
    result = ToolResult.fail("unknown", "auth error", raw=FakeException(raw_text))
    import asyncio

    from app.agent.registry import tool_registry

    async def apply():
        ctx = type("ctx", (), {"tool_name": "test", "arguments": {}, "entry": type("e", (), {"requires_auth": False})()})()
        await tool_registry._builtin_error_mapper_hook(result, ctx)

    asyncio.run(apply())
    assert result.error.code == expected_code


# ── Payload / context ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw_text, expected_code",
    [
        ("413 request entity too large", "payload_too_large"),
        ("maximum context length exceeded", "context_length_exceeded"),
        ("reduce the length of the messages", "context_length_exceeded"),
    ],
)
def test_payload_context_classification(raw_text, expected_code):
    result = ToolResult.fail("unknown", "payload error", raw=FakeException(raw_text))
    import asyncio

    from app.agent.registry import tool_registry

    async def apply():
        ctx = type("ctx", (), {"tool_name": "test", "arguments": {}, "entry": type("e", (), {"requires_auth": False})()})()
        await tool_registry._builtin_error_mapper_hook(result, ctx)

    asyncio.run(apply())
    assert result.error.code == expected_code


# ── Serialization / empty ───────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw_text, expected_code",
    [
        ("JSONDecodeError: unexpected token", "serialization_error"),
        ("cannot parse response", "serialization_error"),
        ("empty response — no completion", "empty_response"),
        ("model returned no choices", "empty_response"),
    ],
)
def test_serialization_empty_classification(raw_text, expected_code):
    result = ToolResult.fail("unknown", "json error", raw=FakeException(raw_text))
    import asyncio

    from app.agent.registry import tool_registry

    async def apply():
        ctx = type("ctx", (), {"tool_name": "test", "arguments": {}, "entry": type("e", (), {"requires_auth": False})()})()
        await tool_registry._builtin_error_mapper_hook(result, ctx)

    asyncio.run(apply())
    assert result.error.code == expected_code


# ── Doom loop ───────────────────────────────────────────────────────────


def test_doom_loop_detected():
    result = ToolResult.fail("unknown", "stuck", raw=FakeException("repetitive identical response detected"))
    import asyncio

    from app.agent.registry import tool_registry

    async def apply():
        ctx = type("ctx", (), {"tool_name": "test", "arguments": {}, "entry": type("e", (), {"requires_auth": False})()})()
        await tool_registry._builtin_error_mapper_hook(result, ctx)

    asyncio.run(apply())
    assert result.error.code == "doom_loop_detected"


# ── Not found / permission / validation ─────────────────────────────────


@pytest.mark.parametrize(
    "raw_text, expected_code",
    [
        ("404 not found", "not_found"),
        ("document not found", "not_found"),
        ("403 forbidden", "permission_denied"),
        ("access denied", "permission_denied"),
        ("400 bad request — missing required parameter", "validation_error"),
        ("invalid parameter out of range", "validation_error"),
        ("500 internal server error", "api_error"),
        ("billing quota exceeded — insufficient funds", "budget_exceeded"),
        ("model glmx-v4 not found — unknown model", "invalid_configuration"),
        ("unsupported image format: webp", "image_processing_error"),
    ],
)
def test_remaining_classification(raw_text, expected_code):
    result = ToolResult.fail("unknown", "some error", raw=FakeException(raw_text))
    import asyncio

    from app.agent.registry import tool_registry

    async def apply():
        ctx = type("ctx", (), {"tool_name": "test", "arguments": {}, "entry": type("e", (), {"requires_auth": False})()})()
        await tool_registry._builtin_error_mapper_hook(result, ctx)

    asyncio.run(apply())
    assert result.error.code == expected_code


# ── No raw — leaves code unchanged ──────────────────────────────────────


def test_no_raw_leaves_original_code():
    result = ToolResult.fail("timeout", "already classified")
    # No raw attribute — hook should be a no-op
    import asyncio

    from app.agent.registry import tool_registry

    async def apply():
        ctx = type("ctx", (), {"tool_name": "test", "arguments": {}, "entry": type("e", (), {"requires_auth": False})()})()
        await tool_registry._builtin_error_mapper_hook(result, ctx)

    asyncio.run(apply())
    assert result.error.code == "timeout"  # unchanged
    assert result.error.message == "already classified"
