"""Enhanced agent event system for PER (Planning-Execution-Reflection) architecture.

Event types:
    thinking     — Streaming reasoning tokens (planning, evaluation, correction)
    plan_start   — Plan generation begins
    plan_step    — Individual plan step emitted
    plan_complete — Plan finalized with all steps
    tool_call    — Tool invocation request
    tool_result  — Tool execution result
    tool_error   — Tool execution error with retry context
    retry_hint   — Retry guidance emitted while recovering from a failure
    reflection   — Post-execution reflection result
    chunk        — Final answer streaming token
    done         — Agent finished successfully
    error        — Fatal/unrecoverable error
    execution_step_result — Per-step execution outcome for plan visualization
    run_report   — Execution summary emitted at the end of a run
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
    "retry_hint",
    "reflection",
    "chunk",
    "done",
    "error",
    "execution_step_result",
    "run_report",
    # HITL（人在环）：
    #   approval_required — 执行器停在审批点（工具未执行），本流随后结束，
    #     任务态为 waiting_approval；人类经 POST /tasks/{id}/approval 应答。
    #   run_cancelled — 协作式取消在检查点被观察到；本 run 收敛为 cancelled。
    "approval_required",
    "run_cancelled",
]

ThinkingType = Literal["reasoning", "planning", "evaluation", "correction"]
# issue #301 #7/#8：反思降级三态中的第三态（无判定）——"unavailable" = 评估
# 服务不可用；"unparsed" = 模型输出不含可用判定。两者都不是 "pass"（假成
# 功），也都不是 run 失败（好 run 不因评估缺席被打坏）。
ReflectionResult = Literal["pass", "retry", "replan", "escalate", "unavailable", "unparsed"]


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

    # Failure classification — read by Executor's retry loop to pick a RetryPolicy.
    # Distinct from `reflection_result`, which is a pass/retry/replan/escalate verdict.
    error_type: str = ""

    # Plan context
    plan_id: str = ""
    plan_goal: str = ""
    plan_step_id: str = ""
    plan_progress: float = -1.0  # -1 = not set; >= 0 = explicit progress value
    dependencies: list[str] = field(default_factory=list)
    tool_hint: str = ""
    plan_step_status: str = ""

    # Semantic fields (v1 — must survive event round-trip)
    parallel_group: int | None = None
    risk_level: str = "low"
    retry_strategy: str = "auto_retry"
    fallback_tool: str | None = None

    # Thinking
    thinking_type: ThinkingType = "reasoning"

    # Task context（issue #201 最小版）：任务级 checkpoint/resume 的关联 id。
    # 由 service 包装层盖章（chat / resume_chat）；默认空 → 既有 SSE 输出不变。
    task_id: str = ""

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
        if self.plan_goal:
            d["plan_goal"] = self.plan_goal
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

        if self.parallel_group is not None:
            d["parallel_group"] = self.parallel_group
        # HITL：approval_required 的 risk_level 是安全关键信息——即使 low 也必须
        # 显式在线上（缺字段曾让前端徽标兜底成"高风险"，并与卡片理由自相矛盾；
        # 浏览器验收 F3）。其余事件类型维持"非默认值才带"的瘦身契约。
        if self.risk_level != "low" or self.type == "approval_required":
            d["risk_level"] = self.risk_level
        if self.retry_strategy != "auto_retry":
            d["retry_strategy"] = self.retry_strategy
        if self.fallback_tool is not None:
            d["fallback_tool"] = self.fallback_tool

        if self.type == "thinking":
            d["thinking_type"] = self.thinking_type

        if self.type == "reflection":
            d["reflection_result"] = self.reflection_result

        if self.retry_attempt:
            d["retry_attempt"] = self.retry_attempt
        if self.retry_hint:
            d["retry_hint"] = self.retry_hint

        if self.error_type:
            d["error_type"] = self.error_type

        if self.task_id:
            d["task_id"] = self.task_id

        if self.iteration:
            d["iteration"] = self.iteration
        d["timestamp"] = self.timestamp

        return d
