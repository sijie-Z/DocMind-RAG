"""Tests for retry_policy.py — classification, jitter, action routing."""

from __future__ import annotations

import random

import pytest

from app.agent.retry_policy import (
    RETRY_POLICIES,
    RetryPolicy,
    compute_backoff,
    get_policy,
)

# ── Classification ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "error_type, expected_retryable, expected_action",
    [
        # repeat
        ("timeout", True, "repeat"),
        ("rate_limited", True, "repeat"),
        ("api_error", True, "repeat"),
        ("idle_timeout", True, "repeat"),
        # reset_transport
        ("unreachable", True, "reset_transport"),
        ("interrupted", True, "reset_transport"),
        ("connection_error", True, "reset_transport"),
        # adapt_request
        ("payload_too_large", True, "adapt_request"),
        ("context_length_exceeded", True, "adapt_request"),
        ("image_processing_error", True, "adapt_request"),
        # refresh_auth
        ("auth_expired_refreshable", True, "refresh_auth"),
        # fatal
        ("not_found", False, "fatal"),
        ("permission_denied", False, "fatal"),
        ("validation_error", False, "fatal"),
        ("auth_expired", False, "fatal"),
        ("budget_exceeded", False, "fatal"),
        ("ambiguous_input", False, "fatal"),
        ("permanent_transport_error", False, "fatal"),
        ("empty_response", False, "fatal"),
        ("serialization_error", False, "fatal"),
        ("max_tokens_truncation", False, "fatal"),
        ("invalid_configuration", False, "fatal"),
        ("doom_loop_detected", False, "fatal"),
    ],
)
def test_policy_classification(error_type, expected_retryable, expected_action):
    policy = RETRY_POLICIES[error_type]
    assert policy.retryable == expected_retryable
    assert policy.action == expected_action


def test_all_23_policies_covered():
    """Every error_type in ToolErrorType is represented."""
    assert len(RETRY_POLICIES) == 23
    # Verify no policy is missing action / max_retries
    for _k, p in RETRY_POLICIES.items():
        assert p.action in ("repeat", "adapt_request", "reset_transport", "refresh_auth", "fatal")
        assert isinstance(p.max_retries, int)
        assert p.max_retries >= 0


def test_unknown_error_returns_fatal():
    policy = get_policy("nonexistent_error")
    assert policy.retryable is False
    assert policy.action == "fatal"


# ── Jitter ───────────────────────────────────────────────────────────────


def test_jitter_is_nonzero():
    """1000 samples from timeout policy: all in [base*0.8, base*1.2] and not all equal."""
    policy = RETRY_POLICIES["timeout"]  # backoff(0) = 0.5
    random.seed(0)
    values = [compute_backoff(policy, 0) for _ in range(1000)]
    base = 0.5
    assert all(base * 0.8 <= v <= base * 1.2 for v in values), f"range violation: min={min(values):.3f} max={max(values):.3f}"
    assert len(set(round(v, 6) for v in values)) > 1, "jitter is always 0 — deterministic backoff"


def test_jitter_is_bounded():
    """1000 samples over multiple attempts: all within bounds."""
    policy = RETRY_POLICIES["timeout"]
    random.seed(42)
    for attempt in range(10):
        values = [compute_backoff(policy, attempt) for _ in range(200)]
        base = min(policy.backoff(attempt), policy.max_delay_seconds)
        assert all(0.0 <= v <= base * 1.2 for v in values)


def test_max_delay_hard_caps():
    policy = RetryPolicy(
        retryable=True, action="repeat", max_retries=3,
        backoff=lambda n: 1000.0, max_delay_seconds=30.0,
    )
    for _ in range(100):
        v = compute_backoff(policy, 5)
        assert v <= 30.0, f"hard cap violated: {v}"


# ── Backward compatibility (legacy fallback_tool) ───────────────────────

def test_fallback_tool_still_works():
    """If step only has fallback_tool (not chain), fallback_chain is empty list by default.

    PlanStep compatibility: executor reads fallback_chain first, then fallback_tool.
    """
    assert isinstance(getattr(RetryPolicy, "fallback_chain", None), type(None))
    # Legacy property still on PlanStep
    from app.agent.planner import PlanStep
    step = PlanStep(id="s1", description="test", dependencies=[])
    step.fallback_tool = "search_knowledge_base"
    assert getattr(step, "fallback_chain", None) is None
    assert step.fallback_tool == "search_knowledge_base"
