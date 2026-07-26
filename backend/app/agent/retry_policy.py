"""Retry Policy — configurable retry decision engine for Agent tool calls.

Design contract (§2 in agent-reliability-design.md):
    Error → classify → lookup policy → decide repeat/adapt/reset/refresh/fatal
    Each policy carries: retryable, action, max_retries, backoff_fn, jitter, cap.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

RetryAction = Literal["repeat", "adapt_request", "reset_transport", "refresh_auth", "fatal"]


@dataclass
class RetryPolicy:
    """What to do when a ToolResult carries a specific error_type.

    action  variants:
        repeat          — same request, different time (timeout / rate_limit / 5xx)
        adapt_request   — mutate the request payload before retry
        reset_transport  — rebuild the HTTP / subprocess client
        refresh_auth    — refresh credentials, then replay
        fatal           — never retry (not_found / permission_denied / etc.)
    """

    retryable: bool
    action: RetryAction = "repeat"
    max_retries: int = 3
    backoff: Callable[[int], float] = field(default=lambda n: 0.5 * (2**n))
    jitter_ratio: float = 0.2  # ±20 % — breaks synchronised retry storms
    max_delay_seconds: float = 30.0  # hard cap for computed delay


def compute_backoff(policy: RetryPolicy, attempt: int) -> float:
    """Return a jittered sleep duration in seconds."""
    base = policy.backoff(attempt)
    base = min(base, policy.max_delay_seconds)
    if base <= 0:
        return 0.0
    jitter = random.uniform(-policy.jitter_ratio, policy.jitter_ratio)
    return max(0.0, min(policy.max_delay_seconds, base * (1.0 + jitter)))


# ── Policy registry ────────────────────────────────────────────────────
# Keyed by ToolResult.error_type (22 types from design doc §13.3 + grok audit).

RETRY_POLICIES: dict[str, RetryPolicy] = {
    # ── retryable: repeat ──
    "timeout": RetryPolicy(
        retryable=True, max_retries=3,
        backoff=lambda n: 0.5 * (2**n),
    ),
    "rate_limited": RetryPolicy(
        retryable=True, max_retries=2,
        backoff=lambda n: min(60 + 30 * n, 300),
    ),
    "api_error": RetryPolicy(
        retryable=True, max_retries=3,
        backoff=lambda n: 1.0 * (2**n),
    ),
    "idle_timeout": RetryPolicy(
        retryable=True, max_retries=2,
        backoff=lambda n: 2.0 * (2**n),
    ),

    # ── retryable: reset_transport ──
    "unreachable": RetryPolicy(
        retryable=True, action="reset_transport", max_retries=5,
        backoff=lambda n: 2.0 * (2**n),
    ),
    "interrupted": RetryPolicy(
        retryable=True, action="reset_transport", max_retries=3,
        backoff=lambda n: 1.0 * (2**n),
    ),
    "connection_error": RetryPolicy(
        retryable=True, action="reset_transport", max_retries=5,
        backoff=lambda n: 2.0 * (2**n),
    ),

    # ── retryable: adapt_request ──
    "payload_too_large": RetryPolicy(
        retryable=True, action="adapt_request", max_retries=2,
    ),
    "context_length_exceeded": RetryPolicy(
        retryable=True, action="adapt_request", max_retries=2,
    ),
    "image_processing_error": RetryPolicy(
        retryable=True, action="adapt_request", max_retries=2,
    ),

    # ── retryable: refresh_auth ──
    "auth_expired_refreshable": RetryPolicy(
        retryable=True, action="refresh_auth", max_retries=2,
        backoff=lambda n: 1.0 * (2**n),
    ),

    # ── fatal (never retry) ──
    "not_found": RetryPolicy(
        retryable=False, action="fatal",
    ),
    "permission_denied": RetryPolicy(
        retryable=False, action="fatal",
    ),
    "validation_error": RetryPolicy(
        retryable=False, action="fatal",
    ),
    "auth_expired": RetryPolicy(
        retryable=False, action="fatal",
    ),
    "budget_exceeded": RetryPolicy(
        retryable=False, action="fatal",
    ),
    "ambiguous_input": RetryPolicy(
        retryable=False, action="fatal",
    ),
    "permanent_transport_error": RetryPolicy(
        retryable=False, action="fatal",
    ),
    "empty_response": RetryPolicy(
        retryable=False, action="fatal",
    ),
    "serialization_error": RetryPolicy(
        retryable=False, action="fatal",
    ),
    "max_tokens_truncation": RetryPolicy(
        retryable=False, action="fatal",
    ),
    "invalid_configuration": RetryPolicy(
        retryable=False, action="fatal",
    ),
    "doom_loop_detected": RetryPolicy(
        retryable=False, action="fatal",
    ),
}


def get_policy(error_type: str) -> RetryPolicy:
    """Lookup with unknown-fallback."""
    return RETRY_POLICIES.get(error_type,
                              RetryPolicy(retryable=False, action="fatal"))
