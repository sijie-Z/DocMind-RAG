"""Explicit run context propagated to tools.

Inspired by OpenAI Agents SDK's ``RunContext`` and Claude Agent SDK's session
context: tools receive one typed context object instead of relying on ad-hoc
keyword arguments.
"""

from dataclasses import dataclass, field
from typing import Any

# issue #511 hardening: the internal-system marker lives outside ``extra``,
# which is ordinary tool context data. A caller can only set it through the
# dedicated factory below, never through LLM tool arguments or a request body.
_INTERNAL_SYSTEM_TASK = object()


@dataclass
class RunContext:
    user_id: int
    organization_id: int
    session_id: str | None = None
    agent_id: str = "default"
    config: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)
    _internal_system_task: object | None = field(default=None, repr=False, compare=False)

    @property
    def is_internal_system_task(self) -> bool:
        return self._internal_system_task is _INTERNAL_SYSTEM_TASK

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
    """Return True only for a real RunContext minted by the server-side factory.

    Checking the concrete type prevents a tool payload from impersonating the
    marker with a dict or an object exposing a same-named attribute.
    """
    return isinstance(run_context, RunContext) and run_context.is_internal_system_task


def run_context_for_internal_system_task(
    organization_id: int,
    *,
    session_id: str | None = None,
    agent_id: str = "internal-system",
    config: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> RunContext:
    """Server-side factory for the only viewer-less context allowed to bypass ACL."""
    return RunContext(
        user_id=0,
        organization_id=organization_id,
        session_id=session_id,
        agent_id=agent_id,
        config=config or {},
        extra=dict(extra or {}),
        _internal_system_task=_INTERNAL_SYSTEM_TASK,
    )
