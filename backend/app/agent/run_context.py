"""Explicit run context propagated to tools.

Inspired by OpenAI Agents SDK's ``RunContext`` and Claude Agent SDK's session
context: tools receive one typed context object instead of relying on ad-hoc
keyword arguments.
"""

from dataclasses import dataclass, field
from typing import Any
from weakref import WeakSet

# issue #511 hardening: the internal-system marker is a server-side identity
# registry keyed by object identity. It lives outside ``extra`` and outside the
# dataclass constructor, so neither JSON/LLM tool arguments nor a dataclass
# kwargs payload can mint it. A WeakSet keeps the registry from extending the
# lifetime of per-request contexts.
_INTERNAL_SYSTEM_TASK_CONTEXTS: "WeakSet[RunContext]" = WeakSet()


@dataclass(eq=False)
class RunContext:
    user_id: int
    organization_id: int
    session_id: str | None = None
    agent_id: str = "default"
    config: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "organization_id": self.organization_id,
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "config": self.config,
            "extra": self.extra,
        }

    def summary(self) -> str:
        return (
            f"user={self.user_id} org={self.organization_id} "
            f"agent={self.agent_id} session={self.session_id or '-'}"
        )


def is_internal_system_task(run_context: object | None) -> bool:
    """Return True only for a RunContext object minted by the server-side factory.

    The registry uses object identity, so a dict, a JSON payload, a lookalike
    object, or a manually constructed RunContext cannot impersonate the marker.
    """
    return isinstance(run_context, RunContext) and run_context in _INTERNAL_SYSTEM_TASK_CONTEXTS


def run_context_for_internal_system_task(
    organization_id: int,
    *,
    session_id: str | None = None,
    agent_id: str = "internal-system",
    config: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> RunContext:
    """Server-side factory for the only viewer-less context allowed to bypass ACL."""
    ctx = RunContext(
        user_id=0,
        organization_id=organization_id,
        session_id=session_id,
        agent_id=agent_id,
        config=config or {},
        extra=dict(extra or {}),
    )
    _INTERNAL_SYSTEM_TASK_CONTEXTS.add(ctx)
    return ctx
