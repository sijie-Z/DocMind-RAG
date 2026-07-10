"""Enhanced agent event system for PER (Planning-Execution-Reflection) architecture.

Event types:
    thinking     — Streaming reasoning tokens (planning, evaluation, correction)
    plan_start   — Plan generation begins
    plan_step    — Individual plan step emitted
    plan_complete — Plan finalized with all steps
    tool_call    — Tool invocation request
    tool_result  — Tool execution result
    tool_error   — Tool execution error with retry context
    reflection   — Post-execution reflection result
    chunk        — Final answer streaming token
    done         — Agent finished successfully
    error        — Fatal/unrecoverable error
"""

import time
from dataclasses import dataclass, field
from typing import Any, Literal

EventType = Literal[
    "thinking",
    "plan_start",
    "plan_step",
    "plan_complete",
    "tool_call",
    "tool_result",
    "tool_error",
    "reflection",
    "chunk",
    "done",
    "error",
    "execution_step_result",
    "run_report",
]

ThinkingType = Literal["reasoning", "planning", "evaluation", "correction"]
ReflectionResult = Literal["pass", "retry", "replan", "escalate"]


@dataclass
class AgentEvent:
    """An event emitted during agent execution.

    All fields are optional except `type`. Frontends should handle
    each `type` by reading the relevant subset of fields.
    """

    type: EventType
    content: str = ""

    # Tool context
    tool_name: str = ""
    tool_args: dict[str, Any] = field(default_factory=dict)
    tool_call_id: str = ""
    tool_duration_ms: float = 0.0

    # Plan context
    plan_id: str = ""
    plan_step_id: str = ""
    plan_progress: float = -1.0  # -1 = not set; >= 0 = explicit progress value
    dependencies: list[str] = field(default_factory=list)
    tool_hint: str = ""
    plan_step_status: str = ""

    # Thinking
    thinking_type: ThinkingType = "reasoning"

    # Reflection
    reflection_result: ReflectionResult = "pass"
    retry_attempt: int = 0
    retry_hint: str = ""

    # Iteration
    iteration: int = 0
    timestamp: float = field(default_factory=time.time)

    def to_sse_dict(self) -> dict[str, Any]:
        """Serialize to a dict for SSE JSON output.

        Only includes non-empty/non-default fields to keep payloads small.
        """
        d: dict[str, Any] = {"type": self.type, "content": self.content}

        if self.tool_name:
            d["tool_name"] = self.tool_name
        if self.tool_args:
            d["tool_args"] = self.tool_args
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.tool_duration_ms:
            d["tool_duration_ms"] = round(self.tool_duration_ms, 1)

        if self.plan_id:
            d["plan_id"] = self.plan_id
        if self.plan_step_id:
            d["plan_step_id"] = self.plan_step_id
        if self.plan_progress >= 0:
            d["plan_progress"] = round(self.plan_progress, 3)
        if self.dependencies:
            d["dependencies"] = self.dependencies
        if self.tool_hint:
            d["tool_hint"] = self.tool_hint
        if self.plan_step_status:
            d["plan_step_status"] = self.plan_step_status

        if self.type == "thinking":
            d["thinking_type"] = self.thinking_type

        if self.type == "reflection":
            d["reflection_result"] = self.reflection_result

        if self.retry_attempt:
            d["retry_attempt"] = self.retry_attempt
        if self.retry_hint:
            d["retry_hint"] = self.retry_hint

        if self.iteration:
            d["iteration"] = self.iteration
        d["timestamp"] = self.timestamp

        return d
