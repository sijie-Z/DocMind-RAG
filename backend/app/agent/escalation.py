"""Escalation config — when and how the agent asks a human for help.

Design contract (§4 in agent-reliability-design.md, patched §13.7).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# ── Human outcome ───────────────────────────────────────────────────────
# Six user response types + timeout + protocol errors (§13.7).
# CRITICAL INVARIANT: "cancelled" / "dismissed" MUST NOT increment
# retry/failure counters — they are normal human decisions, not tool errors.

HumanOutcome = Literal[
    "accepted",            # user gave full input
    "rejected",            # user declined (valid outcome)
    "cancelled",           # user dismissed prompt (valid outcome)
    "partial",             # partial input, agent fills gaps
    "redirect",            # user steers to different problem
    "skip",                # user says "use what you have"
    "timeout",             # human did not respond in time
    "malformed_response",  # protocol error — unparseable
    "transport_error",     # response lost in transit
]


_HUMAN_NORMAL_OUTCOMES: set[str] = {
    "accepted", "rejected", "cancelled", "partial",
    "redirect", "skip",
}
_HUMAN_EXCEPTION_OUTCOMES: set[str] = {
    "timeout", "malformed_response", "transport_error",
}


def is_human_normal(outcome: str) -> bool:
    """True if this outcome is a normal human decision (not a failure)."""
    return outcome in _HUMAN_NORMAL_OUTCOMES


def is_human_exception(outcome: str) -> bool:
    """True if this outcome is a protocol failure (timeout / corrupt response)."""
    return outcome in _HUMAN_EXCEPTION_OUTCOMES


# ── Escalation config ───────────────────────────────────────────────────

@dataclass
class EscalationConfig:
    """When to escalate an agent run to human-in-the-loop (§4.1)."""

    # Per-step triggers
    consecutive_tool_failures: int = 3
    """Same tool fails this many times → escalate."""

    # Per-task triggers
    total_task_failures: int = 5
    """Cumulative tool failures across the whole run → escalate."""

    # Time-based triggers
    max_waiting_time_seconds: int = 600  # 10 minutes
    """Long-running tool call → escalate if no result."""

    human_timeout_seconds: int = 600
    """How long to wait for a human response before timeout."""

    # Required escalation (never auto-decided)
    requires_user_decision: list[str] = field(default_factory=lambda: [
        "permission_denied",
        "auth_expired",             # not refreshable
        "budget_exceeded",
        "ambiguous_input",
    ])

    # ── Human response options (presented in UI) ──
    response_options: list[dict] = field(default_factory=lambda: [
        {"id": "retry",        "label": "重新尝试"},
        {"id": "skip",         "label": "跳过这步"},
        {"id": "modify",       "label": "修改问题/参数"},
        {"id": "abort",        "label": "放弃任务"},
    ])
