"""AgentEvent → ACP ``session/update`` / 审批请求的纯映射（issue #481 §5.3 / §5.4）。

本模块只做数据变换（不碰 DB / 网络），便于单测钉住 wire 形状：

- :func:`event_to_update` 返回 ``session/update`` 的 ``update`` 载荷；``None``
  表示该事件不外发（仅记日志）；
- :func:`permission_request_params` 生成服务端请求
  ``session/request_permission`` 的 params（wire 形状严格按 issue §5.4）；
- :func:`decision_from_permission_result` 解析客户端响应 →
  ``"allow"`` / ``"deny"`` / ``"cancelled"``。

映射表（issue §5.3，事件类型以当前 ``EventType`` 的 24 种为准）::

    chunk                 → agent_message_chunk
    thinking              → agent_thought_chunk
    tool_call             → tool_call（status: pending）
    tool_result           → tool_call_update（status: completed，附 content）
    tool_error            → tool_call_update（status: failed）
    plan_step/plan_complete → plan（entries 为调用方维护的累计快照）
    approval_required     → 服务端请求 session/request_permission（不走本函数）
    warning               → 不外发（调用方 info 日志）
    hook_error            → 不外发（hook 失败，调用方 warning 日志；#474）
    done/run_cancelled/error/plan_approval_required → prompt 结算（不走本函数）
    command_result/command_error → agent_message_chunk（issue 表未列命令事件；
        命令输出同样需要可见，见下方常量注释）
    其余（plan_start/retry_hint/reflection/execution_step_result/run_report/
    execution_context/steer_applied/command_start）→ 不外发（调用方 DEBUG 日志）。

``tool_call`` / ``tool_call_update`` 的 ``toolCallId`` 缺失时按
``call-{序号}`` 补齐（同一事件流内稳定）。
"""

from __future__ import annotations

from typing import Any

from app.agent.events import AgentEvent
from app.core.error_mapping import public_agent_event_content

# ``session/update`` 的 sessionUpdate 变体（ACP v1 子集）。
AGENT_MESSAGE_CHUNK = "agent_message_chunk"
AGENT_THOUGHT_CHUNK = "agent_thought_chunk"
TOOL_CALL = "tool_call"
TOOL_CALL_UPDATE = "tool_call_update"
PLAN = "plan"

# tool_result 内容进 update 前的截断上限（防止单行 NDJSON 超过读端承受范围；
# 1MB 行上限由传输层兜底，这里留出 JSON 转义余量）。
TOOL_RESULT_CONTENT_MAX = 8000

# 审批选项（issue §5.4 的 optionId/name/kind 三元组）。
PERMISSION_OPTIONS: list[dict[str, str]] = [
    {"optionId": "allow_once", "name": "允许一次", "kind": "allow_once"},
    {"optionId": "reject_once", "name": "拒绝", "kind": "reject_once"},
]

_PLAN_PRIORITY = {"high": "high", "medium": "medium", "low": "low"}
_PLAN_STATUS = {
    "pending": "pending",
    "running": "in_progress",
    "in_progress": "in_progress",
    "completed": "completed",
}


def tool_call_id_for(event: AgentEvent, fallback_index: int) -> str:
    """事件里的 toolCallId；缺失时用 ``call-{序号}`` 补齐（同一流内稳定）。"""
    return event.tool_call_id or f"call-{fallback_index}"


def plan_entry_from_event(event: AgentEvent, *, index: int) -> dict[str, Any]:
    """``plan_step`` 事件 → ACP ``PlanEntry``（content/priority/status）。"""
    return {
        "content": event.content or event.plan_step_id or f"step-{index}",
        "priority": _PLAN_PRIORITY.get(event.risk_level, "medium"),
        "status": _PLAN_STATUS.get(event.plan_step_status, "pending"),
    }


def merge_plan_entry(entries: list[dict[str, Any]], entry: dict[str, Any]) -> None:
    """按 content 去重合并（计划步骤重复播报时更新而非追加）。"""
    for existing in entries:
        if existing.get("content") == entry.get("content"):
            existing.update(entry)
            return
    entries.append(entry)


def _tool_content(text: str) -> list[dict[str, Any]]:
    if len(text) > TOOL_RESULT_CONTENT_MAX:
        text = text[:TOOL_RESULT_CONTENT_MAX] + "…（已截断）"
    return [{"type": "content", "content": {"type": "text", "text": text}}]


def event_to_update(
    event: AgentEvent,
    *,
    tool_call_id: str = "",
    plan_entries: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """单个 ``AgentEvent`` → ``session/update.update`` 载荷（None = 不外发）。

    Args:
        event: 事件。
        tool_call_id: 已解析的 toolCallId（对 tool_* 事件必须非空）。
        plan_entries: ``plan`` 更新使用的累计 entries 快照（调用方维护）。
    """
    et = event.type
    if et == "chunk":
        return {
            "sessionUpdate": AGENT_MESSAGE_CHUNK,
            "content": {"type": "text", "text": event.content},
        }
    if et == "thinking":
        return {
            "sessionUpdate": AGENT_THOUGHT_CHUNK,
            "content": {"type": "text", "text": event.content},
        }
    if et == "tool_call":
        return {
            "sessionUpdate": TOOL_CALL,
            "toolCallId": tool_call_id or "call-0",
            "title": event.tool_name or "tool",
            "status": "pending",
        }
    if et == "tool_result":
        update: dict[str, Any] = {
            "sessionUpdate": TOOL_CALL_UPDATE,
            "toolCallId": tool_call_id or "call-0",
            "status": "completed",
        }
        if event.content:
            update["content"] = _tool_content(event.content)
        return update
    if et == "tool_error":
        return {
            "sessionUpdate": TOOL_CALL_UPDATE,
            "toolCallId": tool_call_id or "call-0",
            "status": "failed",
        }
    if et in ("plan_step", "plan_complete"):
        if not plan_entries:
            return None
        return {"sessionUpdate": PLAN, "entries": [dict(entry) for entry in plan_entries]}
    if et in ("command_result", "command_error"):
        # issue §5.3 表未覆盖 #475 的命令事件；命令输出若只记日志，用户在
        # ACP 客户端会看到"空回复"。这里复用消息块通道（command_error 的
        # content 已是稳定用户文案）。
        if not event.content:
            return None
        return {
            "sessionUpdate": AGENT_MESSAGE_CHUNK,
            "content": {"type": "text", "text": event.content},
        }
    # warning / done / run_cancelled / error / plan_approval_required /
    # approval_required 与其余诊断类事件不走 update 通道。
    return None


def permission_request_params(
    session_id: str,
    *,
    tool_call_id: str,
    tool_name: str,
) -> dict[str, Any]:
    """``approval_required`` → ``session/request_permission`` 的 params（§5.4）。"""
    return {
        "sessionId": session_id,
        "toolCall": {
            "toolCallId": tool_call_id,
            "title": tool_name or "tool",
            "status": "pending",
        },
        "options": [dict(option) for option in PERMISSION_OPTIONS],
    }


def decision_from_permission_result(result: Any) -> str:
    """解析客户端审批响应 → ``"allow"`` / ``"deny"`` / ``"cancelled"``。

    严格按 issue §5.4：``result.outcome`` 是 ``{outcome: selected, optionId}``
    或 ``{outcome: cancelled}``。兼容 dispatcher 在超时/断连/``$/cancel_request``
    下返回的 ``{"cancelled": true}``；其余形状一律按拒绝（fail-closed）。
    """
    if not isinstance(result, dict):
        return "cancelled"
    if result.get("cancelled"):
        return "cancelled"
    outcome = result.get("outcome")
    if isinstance(outcome, dict):
        kind = outcome.get("outcome")
        if kind == "selected":
            return "allow" if outcome.get("optionId") == "allow_once" else "deny"
        if kind == "cancelled":
            return "cancelled"
        return "deny"
    # 防御：兼容 #480 RPC 老形状（扁平 optionId）。客户端按 ACP 形状应答时
    # 不会走到这里；不认识的一律拒绝。
    if result.get("optionId") == "allow_once":
        return "allow"
    return "deny"


def sanitized_error_content(event: AgentEvent) -> str:
    """``error`` 事件的对外文案（脱敏；绝不泄露堆栈/内部细节）。"""
    return public_agent_event_content("error", event.content, "")
