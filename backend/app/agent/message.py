"""Message protocol — Agent-to-Agent communication for the Multi-Agent OS.

Architecture:
    Each agent (Planner, Executor, Reviewer, Orchestrator) communicates
    exclusively through AgentMessage objects sent via the MessageBus.

    Message flow for a single user request:
        User → Orchestrator
             → [Planner]  : type="plan_request"
             ← [Planner]  : type="plan"        (payload={goal, steps})
             → [Executor] : type="execute"     (payload={plan})
             ← [Executor] : type="result"      (payload={step_results})
             → [Reviewer] : type="review"      (payload={plan, results})
             ← [Reviewer] : type="verdict"     (payload={passed, issues})
             → Orchestrator → User

Usage:
    bus = MessageBus()
    bus.send(AgentMessage(type="plan", sender="planner", target="executor", ...))
    msgs = bus.poll(target="executor")
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# ─── Supported message types ────────────────────────────────────────────────
# plan_request   — Orchestrator → Planner: "plan this query"
# plan           — Planner → Orchestrator: "here's the plan"
# execute        — Orchestrator → Executor: "execute this plan"
# result         — Executor → Orchestrator: "here are the results"
# review         — Orchestrator → Reviewer: "review this execution"
# verdict        — Reviewer → Orchestrator: "here's my assessment"
# error          — Any → Orchestrator: "something went wrong"
# abort          — Orchestrator → All: "cancel this trace"


@dataclass
class AgentMessage:
    """Single message in the Agent-to-Agent protocol.

    Every message carries a trace_id linking it to one user request,
    enabling the Orchestrator to reconstruct the full conversation.
    """

    msg_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    msg_type: str = ""  # plan_request | plan | execute | result | review | verdict | error | abort
    sender: str = ""  # planner | executor | reviewer | orchestrator
    target: str = ""  # planner | executor | reviewer | orchestrator
    payload: dict[str, Any] = field(default_factory=dict)
    trace_id: str = ""  # links all messages for one user request
    parent_msg_id: str | None = None  # for threading / causal chains
    created_at: float = field(default_factory=lambda: datetime.now().timestamp())
    error: str | None = None  # set when msg_type == "error"

    def to_dict(self) -> dict[str, Any]:
        return {
            "msg_id": self.msg_id,
            "msg_type": self.msg_type,
            "sender": self.sender,
            "target": self.target,
            "payload": self.payload,
            "trace_id": self.trace_id,
            "parent_msg_id": self.parent_msg_id,
            "created_at": self.created_at,
            "error": self.error,
        }

    @classmethod
    def plan_request(cls, trace_id: str, query: str, context: dict | None = None) -> "AgentMessage":
        return cls(
            msg_type="plan_request",
            sender="orchestrator",
            target="planner",
            trace_id=trace_id,
            payload={"query": query, "context": context or {}},
        )

    @classmethod
    def execute_request(cls, trace_id: str, plan: dict, parent_id: str) -> "AgentMessage":
        return cls(
            msg_type="execute",
            sender="orchestrator",
            target="executor",
            trace_id=trace_id,
            payload={"plan": plan},
            parent_msg_id=parent_id,
        )

    @classmethod
    def review_request(
        cls, trace_id: str, plan: dict, results: list[dict], parent_id: str
    ) -> "AgentMessage":
        return cls(
            msg_type="review",
            sender="orchestrator",
            target="reviewer",
            trace_id=trace_id,
            payload={"plan": plan, "results": results},
            parent_msg_id=parent_id,
        )

    @classmethod
    def error_msg(cls, trace_id: str, sender: str, message: str) -> "AgentMessage":
        return cls(
            msg_type="error",
            sender=sender,
            target="orchestrator",
            trace_id=trace_id,
            error=message,
            payload={"message": message},
        )


class MessageBus:
    """Simple in-process message bus for Agent-to-Agent communication.

    Messages are stored per trace_id. Each agent polls for messages
    addressed to it. Once consumed, messages are moved to history.

    This is intentionally minimal — no persistence, no pub/sub.
    It's a coordination layer, not a message queue.
    """

    def __init__(self):
        # trace_id → list of AgentMessage (unconsumed)
        self._queues: dict[str, list[AgentMessage]] = {}
        # trace_id → list of AgentMessage (consumed, for replay / debugging)
        self._history: dict[str, list[AgentMessage]] = {}

    def send(self, msg: AgentMessage) -> None:
        """Send a message to the bus. Each agent polls its own target."""
        trace = msg.trace_id
        if trace not in self._queues:
            self._queues[trace] = []
        self._queues[trace].append(msg)

    def poll(self, target: str, trace_id: str) -> list[AgentMessage]:
        """Get all unconsumed messages for a specific agent + trace."""
        trace_msgs = self._queues.get(trace_id, [])
        matching = [m for m in trace_msgs if m.target == target]
        # Move to history
        if matching:
            self._queues[trace_id] = [m for m in trace_msgs if m.target != target]
            if trace_id not in self._history:
                self._history[trace_id] = []
            self._history[trace_id].extend(matching)
        return matching

    def poll_one(self, target: str, trace_id: str, timeout: float = 30.0) -> AgentMessage | None:
        """Wait for the next message for a specific agent + trace.

        In-process: since agents run sequentially (not concurrently),
        this returns the first matching message or None after timeout.
        """
        import time

        deadline = time.time() + timeout
        while time.time() < deadline:
            msgs = self.poll(target, trace_id)
            if msgs:
                return msgs[0]
            time.sleep(0.05)  # 50ms poll interval
        return None

    def get_history(self, trace_id: str) -> list[dict[str, Any]]:
        """Get message history for a trace (for logging / debugging)."""
        return [m.to_dict() for m in self._history.get(trace_id, [])]

    def clear_trace(self, trace_id: str) -> None:
        """Clean up all messages for a completed trace."""
        self._queues.pop(trace_id, None)
        self._history.pop(trace_id, None)


# Global singleton (shared across all agents in the process)
message_bus = MessageBus()
