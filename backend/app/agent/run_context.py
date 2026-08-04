"""Explicit run context propagated to tools.

Inspired by OpenAI Agents SDK's ``RunContext`` and Claude Agent SDK's session
context: tools receive one typed context object instead of relying on ad-hoc
keyword arguments.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
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
