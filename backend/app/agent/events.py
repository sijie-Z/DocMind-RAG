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
    command_start  — User command execution begins (no model turn)
    command_result — User command succeeded
    command_error  — User command failed
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
    # 评审 F4：补两枚实际在发但 Literal 未列的既有事件——
    #   warning           — 非致命提醒（#301 落库失败 / 体验第四波 @引用
    #                       失效、无权限、检索降级），前端有琥珀时间线分支；
    #   execution_context — 终态运行上下文（exec_context.to_dict()：total_tokens
    #                       / duration_ms 等），前端计时与成本的数据源。
    "warning",
    "execution_context",
    # HITL（人在环）：
    #   approval_required — 执行器停在审批点（工具未执行），本流随后结束，
    #     任务态为 waiting_approval；人类经 POST /tasks/{id}/approval 应答。
    #   run_cancelled — 协作式取消在检查点被观察到；本 run 收敛为 cancelled。
    #   steer_applied — 运行中转向（steering）消息在步边界被消费并注入后续
    #     步骤提示词；content 为消息摘要。见 app/agent/steering.py。
    #   plan_approval_required — 计划审批挂起（plan_approval_mode=always）：
    #     planner 产出计划后暂停，任务态 waiting_plan_approval；plan_steps
    #     携带结构化步骤（resume 流不回放 plan_step 事件，卡片数据必须自足）。
    "approval_required",
    "run_cancelled",
    "steer_applied",
    "plan_approval_required",
    # User Commands（issue #475）：命令通道的事件三元组。命令在进入 PER 之前
    # 被拦截执行，**不创建模型消息或轮次**：
    #   command_start  — 开始执行一行命令（content 为原始命令行，command_name
    #                    为解析出的命令名）；
    #   command_result — 执行成功（content 为渲染后的结果，command_data 为
    #                    结构化载荷）；
    #   command_error  — 执行失败（content 为面向用户的稳定文案，error_type
    #                    为错误码；未知命令/权限/参数/超时/并发均走此事件）。
    "command_start",
    "command_result",
    "command_error",
    # Hooks（issue #474）：hook 失败不静默——超时/异常/非法决策以本事件外发。
    # hook / event_name / action / elapsed_ms 四个字段见 AgentEvent 的可选增补；
    # 其余事件类型不携带这些键（既有载荷零变化）。
    "hook_error",
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
    # issue #206：代码执行类工具实际使用的沙箱（"ast" / "docker"）。
    # 空串 = 该工具不涉及沙箱（事件瘦身：非空才序列化）。
    sandbox: str = ""

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
    # B（计划审批）：plan_approval_required 的结构化步骤载荷
    # （[{id, description, dependencies, tool_hint}…]；其余事件类型空）。
    plan_steps: list[dict[str, Any]] = field(default_factory=list)

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

    # single_final/v1：只在 ready 后的成功 done 携带权威正文；失败/取消为空。
    final_answer: str = ""
    task_state: str = ""

    # issue #510 第二步：本次请求的 X-Request-ID（与 HTTP 中间件 / 结构化日志 /
    # llm_calls.jsonl 同源）。同样由 service 包装层盖章；默认空 → 不序列化，
    # 既有事件载荷在无请求上下文时零变化。
    request_id: str = ""

    # User Commands（issue #475）：command_* 事件的命令名与结构化载荷。
    # 其余事件类型保持空 → 序列化时省略（既有载荷不变）。
    command_name: str = ""
    command_data: dict[str, Any] = field(default_factory=dict)

    # Hooks（issue #474）：hook_error 事件的结构化字段。默认 None → 序列化时
    # 省略，既有事件载荷不变（向后兼容）。
    hook: str | None = None
    event_name: str | None = None
    action: str | None = None
    elapsed_ms: int | None = None

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
        # issue #206：resolved sandbox 随 tool_result/tool_error 暴露（非空才带）
        if self.sandbox:
            d["sandbox"] = self.sandbox

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
        if self.plan_steps:
            d["plan_steps"] = self.plan_steps

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
        if self.type == "done":
            if self.final_answer and self.task_state not in (
                "failed",
                "cancelled",
                "paused",
                "waiting_approval",
                "waiting_plan_approval",
            ):
                d["final_answer"] = self.final_answer
            if self.task_state:
                d["task_state"] = self.task_state
        if self.request_id:
            d["request_id"] = self.request_id

        # User Commands（issue #475）：非空才带，既有事件载荷零变化。
        if self.command_name:
            d["command_name"] = self.command_name
        if self.command_data:
            d["command_data"] = self.command_data

        # Hooks（issue #474）：hook_error 专用字段；非 None 才带。
        if self.hook is not None:
            d["hook"] = self.hook
        if self.event_name is not None:
            d["event_name"] = self.event_name
        if self.action is not None:
            d["action"] = self.action
        if self.elapsed_ms is not None:
            d["elapsed_ms"] = self.elapsed_ms

        if self.iteration:
            d["iteration"] = self.iteration
        d["timestamp"] = self.timestamp

        return d


class FinalAnswerAccumulator:
    """One authoritative answer projection for every direct event consumer."""

    def __init__(self, snapshot: dict[str, Any] | None = None) -> None:
        snapshot = snapshot or {}
        is_final = snapshot.get("answer_contract") == "single_final/v1"
        self.snapshot_answer = (
            snapshot.get("final_answer", "")
            if is_final and isinstance(snapshot.get("final_answer"), str)
            else ""
        )
        self.legacy_prefix = "" if is_final else str(snapshot.get("partial_output") or "")
        self.terminal_answer = ""
        self.chunks: list[str] = []
        self.task_id = ""
        self.task_state = ""

    def add(self, event: AgentEvent) -> None:
        task_id = getattr(event, "task_id", "")
        if task_id:
            self.task_id = task_id
        event_type = getattr(event, "type", "")
        if event_type == "chunk":
            self.chunks.append(getattr(event, "content", "") or "")
        elif event_type == "done":
            self.task_state = getattr(event, "task_state", "")
            final_answer = getattr(event, "final_answer", "")
            if final_answer:
                self.terminal_answer = final_answer

    @property
    def answer(self) -> str:
        # A successful terminal frame wins over the snapshot and stream.  Ready
        # snapshots outrank replayed chunks; only legacy v1 keeps partial prefix.
        return (
            self.terminal_answer
            or self.snapshot_answer
            or self.legacy_prefix + "".join(self.chunks)
        )
