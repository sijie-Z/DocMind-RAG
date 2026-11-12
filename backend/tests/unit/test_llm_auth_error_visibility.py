"""凭据类失败必须与「连接抖动」区分：文案不同，用户动作不同。"""

from __future__ import annotations

import pytest

from app.agent.executor import is_llm_auth_error


@pytest.mark.parametrize(
    "text",
    [
        "Error code: 401 - {'error': {'message': 'Authentication Fails, Your api key: ****7a3a is invalid'}}",
        "Authentication Fails",
        "authentication_error",
        "invalid_api_key",
        "Unauthorized",
    ],
)
def test_auth_errors_detected(text: str) -> None:
    assert is_llm_auth_error(text) is True


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "TimeoutError: ",
        "Connection refused",
        "500 Internal Server Error",
        "rate limit exceeded",
    ],
)
def test_non_auth_errors_not_misclassified(text) -> None:
    assert is_llm_auth_error(text) is False


def test_real_401_log_line_from_incident() -> None:
    """回归：2026-11-12 实测日志原文必须被判为凭据问题。"""
    line = (
        "Error code: 401 - {'error': {'message': 'Authentication Fails, "
        "Your api key: ****7a3a is invalid (request_id: 42811a74)'}}"
    )
    assert is_llm_auth_error(line) is True
