"""Agent API endpoints — PER agent chat via SSE, session & config management.

Endpoints:
    POST /api/v1/agent/chat           — PER agent chat (SSE streaming)
    GET  /api/v1/agent/tasks/{id}     — Agent task status (checkpoint/resume, #201)
    POST /api/v1/agent/tasks/{id}/resume — Resume an interrupted task (SSE, #201)
    POST /api/v1/agent/tasks/{id}/approval — HITL 审批应答 (SSE; approve/deny/改指令)
    POST /api/v1/agent/tasks/{id}/cancel   — HITL 协作式取消 (服务端停止协议)
    POST /api/v1/agent/tasks/{id}/steer    — 运行中转向（有界队列；步边界注入）
    POST /api/v1/agent/tasks/{id}/plan-approval — 计划审批（approve/edit/reject）
    GET  /api/v1/agent/tools          — List available tools with status
    GET  /api/v1/agent/skills         — List learned skills
    GET  /api/v1/agent/sessions       — List agent sessions
    POST /api/v1/agent/sessions       — Create agent session
    GET  /api/v1/agent/sessions/{id}  — Get session messages
    DELETE /api/v1/agent/sessions/{id} — Delete session
    GET  /api/v1/agent/config         — Get user agent config
    PUT  /api/v1/agent/config         — Update user agent config
"""

import contextlib
import json
import logging
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import checkpoint, steering
from app.agent.config import DEFAULT_DISABLED_TOOLS, AgentConfig
from app.agent.registry import tool_registry
from app.agent.service import agent_service
from app.agent.skills import skill_manager
from app.core.database import AsyncSessionLocal, get_db
from app.core.log_safety import log_safe
from app.core.prometheus import AGENT_FEEDBACK_TOTAL
from app.core.security import get_current_user, require_organization
from app.exceptions import (
    AuthorizationError,
    NotFoundError,
    ServiceUnavailableError,
    ValidationError,
)
from app.models.agent_task import TaskStatus
from app.models.chat import ChatMessage, ChatSession, ChatSessionStatus, MessageType
from app.models.user import User

logger = logging.getLogger(__name__)
router = APIRouter()


def _merge_disabled_tools(client_disabled: list[str] | None) -> list[str]:
    """安全加固：服务端强制合并高危工具禁用列表，客户端无法解除。

    即使客户端传了非空 disabled_tools，execute_python/execute_sql/mcp_call
    也始终处于禁用状态。
    """
    return sorted(set(client_disabled or []) | set(DEFAULT_DISABLED_TOOLS))


# ── Request/Response models ──────────────────────────────────────────────────

class AgentChatRequest(BaseModel):
    query: str = Field(..., min_length=1, description="User query")
    session_id: str | None = Field(None, description="Agent session ID for persistence")
    history: list[dict[str, str]] | None = Field(None, description="Previous messages")
    enable_tools: bool = Field(True, description="Enable tool calling")
    enable_planning: bool = Field(True, description="Enable planning phase")
    enable_reflection: bool = Field(True, description="Enable reflection phase")
    enable_memory: bool = Field(True, description="Enable memory recall")
    enable_thinking: bool = Field(True, description="Stream thinking tokens")
    model: str = Field("deepseek-v4-flash", description="Model to use")
    temperature: float = Field(0.1, ge=0.0, le=2.0, description="LLM temperature")
    disabled_tools: list[str] = Field(default_factory=list, description="Tools to disable")
    system_prompt_override: str | None = Field(None, description="Custom system prompt")
    approval_mode: Literal["never", "risky", "always"] | None = Field(
        None,
        description=(
            "HITL 审批模式（覆盖用户已保存配置）：never=不审批 / risky=高危工具需审批（默认）"
            " / always=所有工具调用需审批"
        ),
    )
    plan_approval_mode: Literal["never", "always"] | None = Field(
        None,
        description=(
            "B：计划审批模式（覆盖用户已保存配置）：never=planner 出计划即执行（默认）"
            " / always=每版计划先暂停等人类批准/编辑/拒绝"
        ),
    )
    referenced_document_ids: list[str] | None = Field(
        None,
        description=(
            "体验第四波 @引用：用户显式引用的知识库文档 id 列表——关键摘要注入"
            " planner 上下文；失效/无权限逐条以 warning 事件诚实外发"
        ),
    )


class AgentConfigUpdate(BaseModel):
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = Field(None, ge=256, le=32768, description="单次 LLM 调用输出上限")
    max_plan_steps: int | None = Field(None, ge=1, le=50, description="单个计划的步数上限")
    max_retries_per_step: int | None = Field(None, ge=0, le=10, description="每步重试次数上限")
    enable_planning: bool | None = None
    enable_reflection: bool | None = None
    enable_tools: bool | None = None
    enable_memory: bool | None = None
    enable_thinking: bool | None = None
    personality: str | None = None
    system_prompt_override: str | None = None
    disabled_tools: list[str] | None = None
    approval_mode: Literal["never", "risky", "always"] | None = Field(
        None, description="HITL 审批模式：never / risky / always"
    )
    approval_timeout_seconds: int | None = Field(
        None, ge=0, le=86400,
        description="审批等待时限（秒）；0=无限等待（默认，绝不伪造超时拒绝）",
    )
    plan_approval_mode: Literal["never", "always"] | None = Field(
        None, description="B：计划审批模式：never（默认）/ always"
    )


class SessionCreate(BaseModel):
    title: str = Field("New Agent Session", max_length=80)


async def _build_agent_config(body: AgentChatRequest, user_id: int) -> AgentConfig:
    """组合一次 run 的 AgentConfig：加载用户已保存配置为基底 + 请求字段覆盖。

    优先级：请求字段 > 用户已保存配置 > 数据类默认值。issue #242：此前基底被整体
    忽略（纯请求构造）——设置页里 `max_plan_steps` / `max_retries_per_step` /
    `max_tokens` 等请求不携带的旋钮，保存成功也永不生效。

    - 请求模型里恒有值的字段（model / temperature / enable_*）总是以请求为准；
    - 可选字段（system_prompt_override）仅在请求显式给值时覆盖；
    - 列表字段（disabled_tools）：请求非空时以请求为准、为空时以已保存值（或数据类
      默认值）为准；**两条路径都会强制合并服务端禁用清单**（安全保证与改动前的
      无条件重合并一致，见下方注释）。
    """
    stored = await AgentConfig.load_from_redis(f"user:{user_id}")
    config = stored or AgentConfig()
    config.model = body.model
    config.temperature = body.temperature
    config.enable_tools = body.enable_tools
    config.enable_planning = body.enable_planning
    config.enable_reflection = body.enable_reflection
    config.enable_memory = body.enable_memory
    config.enable_thinking = body.enable_thinking
    if body.system_prompt_override is not None:
        config.system_prompt_override = body.system_prompt_override
    if body.approval_mode is not None:
        config.approval_mode = body.approval_mode
    if body.plan_approval_mode is not None:
        config.plan_approval_mode = body.plan_approval_mode
    # 安全加固：无论请求是否携带 disabled_tools，服务端强制禁用清单都必须合并。
    # 继承存储值时同样重新合并——存储可能来自旧版本或带外写入（不含后来新增的默认
    # 禁用项），原样继承会把 execute_python/execute_sql/mcp_call 在聊天中放开；
    # 改动前的 chat 每次无条件重合并，这里保持同等保证（评审 B-1）。
    config.disabled_tools = _merge_disabled_tools(body.disabled_tools or config.disabled_tools)
    return config


# ── Agent Chat (SSE) ─────────────────────────────────────────────────────────

@router.post("/chat")
async def agent_chat(
    body: AgentChatRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """PER agent chat with streaming events via SSE.

    Events: thinking, plan_start, plan_step, plan_complete,
            tool_call, tool_result, tool_error, reflection, chunk, done
    """
    # S3 安全加固：无组织用户直接 403（必须在进入 SSE 流之前抛出），
    # 不再兜底到 1 号组织去读取其他租户的数据；
    # 无组织的超管解析到库里真实存在的组织（无组织则 403，issue #168）
    effective_org_id = await require_organization(current_user, db)

    async def event_stream():
        assistant_message_id = None
        save_error: str | None = None
        last_event_type = ""
        try:
            # Build AgentConfig：用户已保存配置为基底 + 请求字段覆盖（issue #242）。
            # 此前基底被整体忽略——设置页旋钮保存成功也永不生效。
            config = await _build_agent_config(body, current_user.id)

            # Load history from session if provided
            history = body.history or []
            if body.session_id and not body.history:
                loaded_history = await _load_session_messages(body.session_id, current_user.id)
                if loaded_history:
                    history = loaded_history

            # Run agent
            final_answer = ""
            async for event in agent_service.chat(
                query=body.query,
                history=history,
                organization_id=effective_org_id,
                user_id=current_user.id,
                session_id=body.session_id,
                config=config,
                referenced_document_ids=body.referenced_document_ids,
            ):
                last_event_type = event.type
                sse_data = event.to_sse_dict()
                yield f"data: {json.dumps(sse_data, ensure_ascii=False)}\n\n"
                if event.type == "chunk":
                    final_answer += event.content

            # Save interaction to session (user query + assistant response)
            # HITL：暂停（approval_required）/取消（run_cancelled）不是"完成"——
            # 不落库半成品回答：真完成由 approval/resume 流收尾时补写，
            # 避免同一轮问答落成两条消息。
            if body.session_id and last_event_type not in ("approval_required", "run_cancelled", "plan_approval_required"):
                # issue #277（评审 or1268 R2 收账）：会话落库 org 必须与运行读侧
                # 同一来源（resolved org）——此前传 raw current_user.organization_id，
                # 凡 org 来自 token_organization_id 回退（或走无组织超管回源）的用户，
                # 会话落库 None 而运行读 42 → feedback lesson 命名空间写读不一致、
                # 静默失效。
                try:
                    assistant_message_id = await _save_session_interaction(
                        body.session_id,
                        current_user.id,
                        body.query,
                        effective_org_id,
                        final_answer,
                    )
                except Exception as save_exc:
                    # issue #301 批 D（#22）：落库失败不再静默。回答已流给用户，
                    # 但必须显式告知（warning 事件 + done.saved=false + ERROR 日志）：
                    # 修前 done 照发、无任何信号，刷新后本轮问答消失。
                    save_error = log_safe(save_exc)
                    logger.error(
                        "Failed to persist agent interaction (session=%s user=%s): %s",
                        body.session_id,
                        current_user.id,
                        save_error,
                        exc_info=True,
                    )

        except Exception as e:
            # 完整异常/堆栈只进服务端日志；客户端收通用文案（CodeQL 信息暴露修复：
            # 异常原文此前直出 SSE）。
            logger.error("Agent chat error: %s", log_safe(e), exc_info=True)
            error_data = {"type": "error", "content": "Agent 执行出错，请稍后重试"}
            yield f"data: {json.dumps(error_data, ensure_ascii=False)}\n\n"

        if save_error is not None:
            warning_data = {
                "type": "warning",
                "content": "回答已生成，但会话记录保存失败，刷新后本轮对话可能不可见",
            }
            yield f"data: {json.dumps(warning_data, ensure_ascii=False)}\n\n"

        done_data: dict[str, Any] = {"type": "done", "content": ""}
        if body.session_id:
            # saved=false 专门标记"本轮落库出错"（None 且无异常 = 越权拒绝或空回复，
            # 两者都不写、也都没有落库错误）。
            done_data["saved"] = save_error is None
        if assistant_message_id:
            done_data["message_id"] = assistant_message_id
        # HITL：暂停/取消时如实盖章任务态——前端据此显示"等待审批/已取消"，
        # 而不是把流结束静默当作完成（诚实的流终结信号）。
        if last_event_type == "approval_required":
            done_data["task_state"] = "waiting_approval"
        elif last_event_type == "plan_approval_required":
            # B：计划审批挂起——如实盖章（前端保持计划卡在屏）。
            done_data["task_state"] = "waiting_plan_approval"
        elif last_event_type == "run_cancelled":
            done_data["task_state"] = "cancelled"
        yield f"data: {json.dumps(done_data)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ── Checkpoint / Resume（issue #201 最小版）───────────────────────────────────

async def _fallback_resume_config(user_id: int) -> AgentConfig:
    """快照缺失/损坏（安全降级路径）时 resume 用的规则：用户已保存配置为基底，
    并强制合并服务端禁用清单（与 _build_agent_config 同一安全保证）。"""
    config = await AgentConfig.load_from_redis(f"user:{user_id}") or AgentConfig()
    config.disabled_tools = _merge_disabled_tools(config.disabled_tools)
    return config


@router.get("/tasks/{task_id}")
async def get_agent_task(
    task_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """任务状态视图（checkpoint/resume 的发现性入口；只读行、不解快照）。

    - 403：未登录 / 用户无组织上下文（S3 纪律，与 /chat 完全一致——无组织用户
      绝不带着 None 组织上下文去读任务，那会放行"已被移出组织的前成员"在旧
      组织上读取任务，评审 B1）；
    - 404：任务不存在 / 非本人 / 组织不一致（不区分，避免泄露归属）。
    - HITL：waiting_approval 时附带审批详情（approval：工具/参数/风险/时限 +
      approval_expired 只读判定）——刷新/重连后的"回到审批现场"入口。
    """
    effective_org_id = await require_organization(current_user, db)
    task = await checkpoint.load_task(
        task_id=task_id,
        user_id=current_user.id,
        organization_id=effective_org_id,
    )
    data = checkpoint.task_to_status_dict(task)
    if task.status == TaskStatus.WAITING_APPROVAL:
        pending = await checkpoint.get_pending_approval(task_id)
        if pending is not None:
            from app.agent import approval as _approval

            data["approval"] = pending
            data["approval_expired"] = _approval.approval_expired(pending)
    elif task.status == TaskStatus.WAITING_PLAN_APPROVAL:
        # B：等待计划审批时的现场（刷新/重连回到计划卡的入口）。
        pending_plan = await checkpoint.get_pending_plan_approval(task_id)
        if pending_plan is not None:
            data["plan_approval"] = pending_plan
    return {"success": True, "data": data}


@router.post("/tasks/{task_id}/resume")
async def resume_agent_task(
    task_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """从 checkpoint 恢复中断的任务（SSE 流；事件契约与 /chat 完全一致）。

    - 200：从断点继续执行，事件流正常产出（不回放中断前事件——最小版边界）；
    - 403：未登录 / 用户无组织上下文（S3 纪律，与 /chat 一致；无组织用户不得在
      "旧组织"的执行上下文里续跑任务——评审 B1）；
    - 404：任务不存在 / 非本人 / 组织不一致（不区分）；
    - 409：任务已终态，或正被另一个 resume 持有（detail 回带当前 state）。

    预检与认领（prepare_resume）必须在开流之前完成：HTTP 状态码只在开始
    流式输出前可写，403/404/409 语义全靠它拍板。
    """
    effective_org_id = await require_organization(current_user, db)
    task, payload = await agent_service.prepare_resume(
        task_id,
        user_id=current_user.id,
        organization_id=effective_org_id,
    )
    session_id = (payload or {}).get("session_id") or None
    # 崩溃前已流给用户的部分不回放（最小版边界），但收尾落库要拼成完整回复。
    prefix = (payload or {}).get("partial_output") or ""
    fallback_config = await _fallback_resume_config(current_user.id)

    async def event_stream():
        final_answer = prefix
        assistant_message_id = None
        save_error: str | None = None
        last_event_type = ""
        try:
            async for event in agent_service.resume_chat(
                task_id,
                user_id=current_user.id,
                config=fallback_config,
                claim=False,  # 已在上方 prepare_resume 认领（HTTP 409 语义在开流前）
            ):
                last_event_type = event.type
                sse_data = event.to_sse_dict()
                yield f"data: {json.dumps(sse_data, ensure_ascii=False)}\n\n"
                if event.type == "chunk":
                    final_answer += event.content

            # 收尾补写会话（与 /chat 同口径）。能走到这里说明原流从未完成落库
            # （任务可恢复 ⇒ chat 的正常收尾分支没跑过），不存在重复写入窗口。
            # HITL：resume 流也可能再次暂停（多审批循环）或被取消——不落半成品
            # （同 /chat 口径），等这次 run 真正完成时再补写。
            # org 与 /chat 同链（issue #277/R2 对齐）：传 resolved org
            # （effective_org_id，含 token 组织回退 / 无组织超管回源），
            # 不传 raw current_user.organization_id——两者对回退用户不相等。
            if session_id and last_event_type not in ("approval_required", "run_cancelled", "plan_approval_required"):
                try:
                    assistant_message_id = await _save_session_interaction(
                        session_id,
                        current_user.id,
                        task.query,
                        effective_org_id,
                        final_answer,
                    )
                except Exception as save_exc:
                    # issue #301 批 D（#22）：与 /chat 同口径——落库失败显式告知。
                    save_error = log_safe(save_exc)
                    logger.error(
                        "Failed to persist resumed interaction (session=%s user=%s): %s",
                        session_id,
                        current_user.id,
                        save_error,
                        exc_info=True,
                    )
        except Exception as e:
            # 完整异常/堆栈只进服务端日志；客户端收通用文案（CodeQL 信息暴露修复：
            # 异常原文此前直出 SSE）。
            logger.error("Agent resume error: %s", log_safe(e), exc_info=True)
            error_data = {"type": "error", "content": "Agent 恢复执行出错，请稍后重试"}
            yield f"data: {json.dumps(error_data, ensure_ascii=False)}\n\n"

        if save_error is not None:
            warning_data = {
                "type": "warning",
                "content": "回答已生成，但会话记录保存失败，刷新后本轮对话可能不可见",
            }
            yield f"data: {json.dumps(warning_data, ensure_ascii=False)}\n\n"

        done_data: dict[str, Any] = {"type": "done", "content": "", "task_id": task_id}
        if session_id:
            done_data["saved"] = save_error is None
        if assistant_message_id:
            done_data["message_id"] = assistant_message_id
        # HITL：同 /chat——再次暂停/取消时如实盖章任务态。
        if last_event_type == "approval_required":
            done_data["task_state"] = "waiting_approval"
        elif last_event_type == "plan_approval_required":
            # B：计划审批挂起——如实盖章（前端保持计划卡在屏）。
            done_data["task_state"] = "waiting_plan_approval"
        elif last_event_type == "run_cancelled":
            done_data["task_state"] = "cancelled"
        yield f"data: {json.dumps(done_data, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ── HITL：审批应答 / 协作式取消（app/agent/approval.py 的 HTTP 面）────────────


class TaskApprovalRequest(BaseModel):
    decision: Literal["approve", "deny"] = Field(
        ..., description="审批决定：approve=批准执行 / deny=拒绝（结构化失败喂给反思链路）"
    )
    modified_instruction: str | None = Field(
        None, max_length=2000,
        description="改指令续跑（仅 approve 生效）：附加人工指导，注入后续步骤提示词",
    )
    note: str | None = Field(
        None, max_length=500, description="备注（拒绝理由等；透传给反思上下文）"
    )


@router.post("/tasks/{task_id}/approval")
async def decide_agent_task_approval(
    task_id: str,
    body: TaskApprovalRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """审批应答（approve / deny / 改指令）→ 记账 + 认领 + SSE 续跑。

    语义（app/agent/approval.py 的模块契约）：
    - approve：批准的具体行动 = (tool_name, args 指纹)——粘滞于本 run，同调用
      不再打扰；续跑复用 checkpoint/resume 链（同一步骤重跑一次，at-least-once
      语义与既有 resume 一致）。
    - deny：该调用**不执行**，以结构化失败 approval_denied 喂给反思器继续/收敛
      （粘滞：同 (tool, args) 本 run 内不再执行、也不再打扰人类）。
    - approve + modified_instruction：改指令续跑——指令作为人工指导注入后续
      步骤提示词（见 loop.run 的 human_guidance）。
    续跑可能再次停在下一个未决审批点：流内再发 approval_required，done 带
    task_state=waiting_approval（前端回到审批卡片）。

    - 200：决定已记账，从暂停点续跑（SSE；事件契约与 /chat 一致）；
    - 403/404/409：与 resume 同纪律（预检必须在开流前完成）。409 细分：
      TASK_NOT_WAITING_APPROVAL（状态不符/无待审批请求，含审批超时——
      超时如实置 FAILED(approval_timeout)，**绝不**伪装成"被拒绝"）、
      TASK_NOT_RESUMABLE（已终态，含已取消）、TASK_BUSY（并发应答）。
    """
    effective_org_id = await require_organization(current_user, db)
    task, payload, decision_record = await checkpoint.apply_approval_decision(
        task_id=task_id,
        user_id=current_user.id,
        organization_id=effective_org_id,
        decision=body.decision,
        modified_instruction=body.modified_instruction,
        note=body.note,
    )
    session_id = (payload or {}).get("session_id") or None
    # 崩溃前已流给用户的部分不回放（最小版边界），但收尾落库要拼成完整回复。
    prefix = (payload or {}).get("partial_output") or ""
    fallback_config = await _fallback_resume_config(current_user.id)
    logger.info(
        "审批应答 (task=%s, decision=%s, tool=%s, by=%s)",
        log_safe(task_id), decision_record.get("decision"),
        log_safe(str(decision_record.get("tool_name"))), current_user.id,
    )

    async def event_stream():
        final_answer = prefix
        assistant_message_id = None
        save_error: str | None = None
        last_event_type = ""
        try:
            async for event in agent_service.resume_chat(
                task_id,
                user_id=current_user.id,
                config=fallback_config,
                claim=False,  # 已在上方 apply_approval_decision 认领（HTTP 409 在开流前）
            ):
                last_event_type = event.type
                sse_data = event.to_sse_dict()
                yield f"data: {json.dumps(sse_data, ensure_ascii=False)}\n\n"
                if event.type == "chunk":
                    final_answer += event.content

            # 会话补写：只在 run 真正完成时（再次暂停/取消不写半成品，同 /chat 口径）。
            if session_id and last_event_type not in ("approval_required", "run_cancelled", "plan_approval_required"):
                try:
                    assistant_message_id = await _save_session_interaction(
                        session_id,
                        current_user.id,
                        task.query,
                        effective_org_id,
                        final_answer,
                    )
                except Exception as save_exc:
                    save_error = log_safe(save_exc)
                    logger.error(
                        "Failed to persist approved interaction (session=%s user=%s): %s",
                        session_id,
                        current_user.id,
                        save_error,
                        exc_info=True,
                    )
        except Exception as e:
            # 完整异常/堆栈只进服务端日志；客户端收通用文案（同 /chat、/resume）。
            logger.error("Agent approval resume error: %s", log_safe(e), exc_info=True)
            error_data = {"type": "error", "content": "Agent 审批续跑出错，请稍后重试"}
            yield f"data: {json.dumps(error_data, ensure_ascii=False)}\n\n"

        if save_error is not None:
            warning_data = {
                "type": "warning",
                "content": "回答已生成，但会话记录保存失败，刷新后本轮对话可能不可见",
            }
            yield f"data: {json.dumps(warning_data, ensure_ascii=False)}\n\n"

        done_data: dict[str, Any] = {
            "type": "done", "content": "", "task_id": task_id,
            "decision": decision_record.get("decision"),
        }
        if session_id:
            done_data["saved"] = save_error is None
        if assistant_message_id:
            done_data["message_id"] = assistant_message_id
        if last_event_type == "approval_required":
            done_data["task_state"] = "waiting_approval"
        elif last_event_type == "plan_approval_required":
            # B：计划审批挂起——如实盖章（前端保持计划卡在屏）。
            done_data["task_state"] = "waiting_plan_approval"
        elif last_event_type == "run_cancelled":
            done_data["task_state"] = "cancelled"
        yield f"data: {json.dumps(done_data, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ── B：计划审批（plan_approval_mode=always 的 HTTP 面）───────────────────────


class PlanStepEdit(BaseModel):
    """编辑后的步骤（白名单字段；与快照 PlanStep 形状对齐的子集）。"""

    id: str = Field(..., min_length=1, max_length=36, description="步骤 id（唯一）")
    description: str = Field(..., min_length=1, max_length=2000, description="步骤描述")
    dependencies: list[str] = Field(
        default_factory=list, max_length=20, description="依赖的步骤 id（必须在列表内）"
    )
    tool_hint: str | None = Field(None, max_length=100, description="建议工具名（可空）")


class TaskPlanApprovalRequest(BaseModel):
    """计划审批决定：approve（照计划执行）/ edit（校验后替换步骤再执行）/ reject（带理由重规划一次）。"""

    decision: Literal["approve", "edit", "reject"]
    edited_steps: list[PlanStepEdit] | None = Field(
        None, max_length=50, description="edit 必填：整体替换计划的步骤列表（1..50）"
    )
    note: str | None = Field(
        None, max_length=500,
        description="拒绝理由（透传为重规划反馈）；edit/approve 时忽略",
    )

    @model_validator(mode="after")
    def _validate_decision_payload(self):
        """决策 × 载荷一致性（非法输入由 FastAPI 自然映射 422）。"""
        if self.decision == "edit":
            if not self.edited_steps:
                raise ValueError("edit 决策必须提供 edited_steps（至少 1 个步骤）")
            ids = [s.id for s in self.edited_steps]
            if len(set(ids)) != len(ids):
                raise ValueError("edited_steps 的 id 必须唯一")
            id_set = set(ids)
            for s in self.edited_steps:
                unknown = [d for d in s.dependencies if d not in id_set]
                if unknown:
                    raise ValueError(
                        f"步骤 {s.id} 的 dependencies 引用了不存在的步骤: {unknown}"
                    )
                if s.id in s.dependencies:
                    raise ValueError(f"步骤 {s.id} 不能依赖自身")
        elif self.edited_steps:
            raise ValueError("edited_steps 仅在 decision=edit 时可携带")
        return self


@router.post("/tasks/{task_id}/plan-approval")
async def decide_agent_task_plan_approval(
    task_id: str,
    body: TaskPlanApprovalRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """计划审批应答（approve / edit / reject）→ 记账 + 认领 + 续跑。

    语义（app/agent/checkpoint.py 的 apply_plan_approval_decision）：
    - approve：照计划执行（resume 复用既有链）；
    - edit：校验步骤 schema（非法输入 422）后整体替换快照计划的步骤再执行；
    - reject：**带理由重规划一次**（note → planner 反馈）——上限 1 次；
      **再次拒绝 → 终态 rejected**（last_error 记理由，诚实失败原因），
      此决定无流可续，返回 JSON（非 SSE）。

    - 200 + SSE：approve/edit/reject#1 的续跑流（事件契约与 /chat 一致；
      可能再次停在计划审批点：流内再发 plan_approval_required）；
    - 200 + JSON：reject#2（终态 rejected，data.state="rejected"）；
    - 403/404/409：与 resume 同纪律；409 细分：TASK_NOT_WAITING_PLAN_APPROVAL
      （状态不符/无待审批计划）、TASK_NOT_RESUMABLE（已终态）、TASK_BUSY（并发）。
    """
    effective_org_id = await require_organization(current_user, db)
    task, payload, record = await checkpoint.apply_plan_approval_decision(
        task_id=task_id,
        user_id=current_user.id,
        organization_id=effective_org_id,
        decision=body.decision,
        edited_steps=(
            [s.model_dump() for s in body.edited_steps]
            if body.decision == "edit" and body.edited_steps else None
        ),
        note=body.note,
    )
    logger.info(
        "计划审批应答 (task=%s, decision=%s, rejections=%s, by=%s)",
        log_safe(task_id), record.get("decision"),
        record.get("rejections"), current_user.id,
    )

    # 终态拒绝（reject#2）：决定已被受理并导致终态——JSON 回执（无流可续）。
    if record.get("terminal"):
        return {
            "success": True,
            "data": {
                "task_id": task_id,
                "state": task.status.value,
                "decision": "reject",
                "rejections": record.get("rejections"),
                "message": record.get("message"),
            },
        }

    session_id = (payload or {}).get("session_id") or None
    prefix = (payload or {}).get("partial_output") or ""
    fallback_config = await _fallback_resume_config(current_user.id)

    async def event_stream():
        final_answer = prefix
        assistant_message_id = None
        save_error: str | None = None
        last_event_type = ""
        try:
            async for event in agent_service.resume_chat(
                task_id,
                user_id=current_user.id,
                config=fallback_config,
                claim=False,  # 已在上方 apply_plan_approval_decision 认领（HTTP 409 在开流前）
            ):
                last_event_type = event.type
                sse_data = event.to_sse_dict()
                yield f"data: {json.dumps(sse_data, ensure_ascii=False)}\n\n"
                if event.type == "chunk":
                    final_answer += event.content

            # 会话补写：只在 run 真正完成时（再次暂停/取消不写半成品，同 /chat 口径）。
            if session_id and last_event_type not in (
                "approval_required", "run_cancelled", "plan_approval_required"
            ):
                try:
                    assistant_message_id = await _save_session_interaction(
                        session_id,
                        current_user.id,
                        task.query,
                        effective_org_id,
                        final_answer,
                    )
                except Exception as save_exc:
                    save_error = log_safe(save_exc)
                    logger.error(
                        "Failed to persist plan-approved interaction (session=%s user=%s): %s",
                        session_id,
                        current_user.id,
                        save_error,
                        exc_info=True,
                    )
        except Exception as e:
            # 完整异常/堆栈只进服务端日志；客户端收通用文案（同 /chat、/resume）。
            logger.error("Agent plan-approval resume error: %s", log_safe(e), exc_info=True)
            error_data = {"type": "error", "content": "Agent 计划审批续跑出错，请稍后重试"}
            yield f"data: {json.dumps(error_data, ensure_ascii=False)}\n\n"

        if save_error is not None:
            warning_data = {
                "type": "warning",
                "content": "回答已生成，但会话记录保存失败，刷新后本轮对话可能不可见",
            }
            yield f"data: {json.dumps(warning_data, ensure_ascii=False)}\n\n"

        done_data: dict[str, Any] = {
            "type": "done", "content": "", "task_id": task_id,
            "decision": record.get("decision"),
        }
        if session_id:
            done_data["saved"] = save_error is None
        if assistant_message_id:
            done_data["message_id"] = assistant_message_id
        if last_event_type == "approval_required":
            done_data["task_state"] = "waiting_approval"
        elif last_event_type == "plan_approval_required":
            done_data["task_state"] = "waiting_plan_approval"
        elif last_event_type == "run_cancelled":
            done_data["task_state"] = "cancelled"
        yield f"data: {json.dumps(done_data, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.post("/tasks/{task_id}/cancel")
async def cancel_agent_task(
    task_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """协作式取消（服务端停止协议；**诚实边界**：合作式，不是硬杀）。

    - waiting_approval / waiting_plan_approval（无活跃流）：直接如实置终态
      cancelled（200），并留下取消旗作围栏——并发/迟到的 approval、resume 尝试
      都会被拒（state=cancelled），任务绝不复活；
    - running / pending / waiting_tool / waiting_human（可能有活跃流）：
      置取消旗（202，state=cancel_requested）。活跃 run 在下一个检查点
      （步间 / 工具前 / 阶段间）观察到并收敛：流内发 run_cancelled、任务置
      cancelled。**正在执行的工具调用 / LLM 调用不会被中断**——等它返回到
      检查点才生效（诚实：取消有个收敛窗口）；
    - 已终态：409 TASK_NOT_CANCELLABLE；已 cancelled → 幂等 200；
    - Redis 不可用（取消旗通道缺失）→ 503：**绝不**假装已受理取消。
    """
    effective_org_id = await require_organization(current_user, db)
    task = await checkpoint.load_task(
        task_id=task_id,
        user_id=current_user.id,
        organization_id=effective_org_id,
    )
    if task.status == TaskStatus.CANCELLED:
        return {
            "success": True,
            "data": {"task_id": task_id, "state": "cancelled", "already": True},
        }
    if task.is_terminal:
        raise checkpoint.TaskNotCancellableError(
            f"任务已处于终态 {task.status.value}，无法取消",
            detail={"task_id": task_id, "state": task.status.value},
        )
    if task.status in (TaskStatus.WAITING_APPROVAL, TaskStatus.WAITING_PLAN_APPROVAL):
        # 无活跃流：DB 态权威收敛 + 取消旗围栏（拦并发应答/恢复的竞态窗口）。
        waiting_label = (
            "计划审批等待中" if task.status == TaskStatus.WAITING_PLAN_APPROVAL
            else "审批等待中"
        )
        ok = await checkpoint.mark_terminal(
            task_id,
            TaskStatus.CANCELLED,
            error=f"用户取消（{waiting_label}）",
            error_type="user_cancelled",
        )
        if not ok:
            raise ServiceUnavailableError("取消失败：任务状态未能落库，请重试")
        await checkpoint.request_cancel(task_id)
        return {"success": True, "data": {"task_id": task_id, "state": "cancelled"}}
    # 活跃运行期：置旗，等 run 在检查点收敛（202 = 已受理，未完成）。
    if not await checkpoint.request_cancel(task_id):
        raise ServiceUnavailableError(
            "取消通道暂不可用（协调存储不可达），请稍后重试"
        )
    return JSONResponse(
        status_code=202,
        content={
            "success": True,
            "data": {
                "task_id": task_id,
                "state": "cancel_requested",
                "message": "已发出取消请求；运行中的任务将在下一个检查点收敛为 cancelled",
            },
        },
    )


# ── 运行中转向（mid-run steering；见 app/agent/steering.py）────────────────────


class TaskSteerRequest(BaseModel):
    message: str = Field(
        ..., min_length=1, max_length=2000,
        description="运行中指导：下一个步边界注入后续步骤提示词（不打断当前步骤）",
    )

    @field_validator("message")
    @classmethod
    def _message_not_blank(cls, value: str) -> str:
        """判词 strip 后不得为空（评审 MINOR-A：纯空格消息曾以空串入队，
        步边界冒出内容为空的幻影 steer_applied）。422 明确拒绝 + 归一化。"""
        stripped = value.strip()
        if not stripped:
            raise ValueError("指导内容不能为空或仅空白")
        return stripped


@router.post("/tasks/{task_id}/steer")
async def steer_agent_task(
    task_id: str,
    body: TaskSteerRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """运行中转向：向 running / waiting_approval 的任务发送一句人工指导。

    语义（app/agent/steering.py 的模块契约）：
    - 不打断当前步骤；loop 在**下一个步边界**（步骤终态之后）取出，注入后续
      步骤提示词，并发 SSE ``steer_applied``（含消息摘要）；
    - waiting_approval（无活跃 run）：消息留在队列，审批恢复的 run 起点并入
      引导（与"改指令续跑"的 steering 通道合流）；
    - 缓冲有界（MAX_STEER_QUEUE=5）：满 → 429 如实拒绝（等消费后再发）；
      终态清缓冲。

    - 200：已入队（data.pending = 当前待消费条数）；
    - 403/404：与任务状态端点同纪律；
    - 409：任务不在可转向状态（仅 running / waiting_approval）；
    - 429：缓冲已满（STEER_BUFFER_FULL）；
    - 503：协调存储不可达——**绝不**假装已受理（诚实边界同取消旗）。
    """
    effective_org_id = await require_organization(current_user, db)
    task = await checkpoint.load_task(
        task_id=task_id,
        user_id=current_user.id,
        organization_id=effective_org_id,
    )
    if task.status not in (TaskStatus.RUNNING, TaskStatus.WAITING_APPROVAL):
        raise steering.TaskNotSteerableError(
            f"任务当前状态为 {task.status.value}，不能接收运行中指导"
            f"（仅 running / waiting_approval）",
            detail={"task_id": task_id, "state": task.status.value},
        )
    pending = await steering.push_steer(task_id, body.message)  # 已由校验器 strip
    if pending is None:
        raise ServiceUnavailableError("转向通道暂不可用（协调存储不可达），请稍后重试")
    if pending < 0:
        raise steering.SteerBufferFullError(
            f"转向缓冲已满（上限 {steering.MAX_STEER_QUEUE} 条），"
            f"请等待 agent 消费后再发送",
            detail={"task_id": task_id, "pending": steering.MAX_STEER_QUEUE},
        )
    if task.status == TaskStatus.WAITING_APPROVAL:
        note = "任务正在等待审批；指导已入队，将并入审批恢复后的执行"
    else:
        note = "指导已入队；将在下一个步边界生效"
    logger.info(
        "转向入队 (task=%s, by=%s, pending=%d, state=%s)",
        log_safe(task_id), current_user.id, pending, task.status.value,
    )
    return {
        "success": True,
        "data": {
            "task_id": task_id,
            "state": "queued",
            "pending": pending,
            "message": note,
        },
    }


# ── Agent Feedback ──────────────────────────────────────────────────────
class AgentFeedbackRequest(BaseModel):
    message_id: str = Field(..., description="Assistant message ID to rate")
    feedback: int = Field(..., ge=-1, le=1, description="反馈: 1=点赞, 0=取消, -1=点踩")
    note: str | None = Field(None, max_length=500, description="反馈备注")


@router.post("/feedback")
async def agent_feedback(
    body: AgentFeedbackRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Submit user feedback on an agent response.

    Thumbs-down feedback is recorded as a lesson in agent memory
    so the system learns from mistakes.
    """
    try:
        stmt = select(ChatMessage).join(ChatSession).where(
            ChatMessage.id == body.message_id,
            ChatSession.user_id == current_user.id,
        )
        message_obj = (await db.execute(stmt)).scalar_one_or_none()
        if not message_obj:
            raise NotFoundError("消息不存在或无权评价")

        message_obj.feedback = body.feedback
        if body.note is not None:
            message_obj.feedback_note = body.note
        await db.commit()

        # Record Prometheus metric
        fb_type = "thumbs_up" if body.feedback == 1 else ("thumbs_down" if body.feedback == -1 else "neutral")
        AGENT_FEEDBACK_TOTAL.labels(feedback_type=fb_type).inc()

        # On negative feedback, record a lesson in agent memory so the
        # agent can learn from the mistake.
        if body.feedback == -1:
            try:
                from app.agent.memory_bridge import AgentMemoryBridge
                # Derive the agent_id from the session
                session_stmt = select(ChatSession).where(ChatSession.id == message_obj.session_id)
                session_result = await db.execute(session_stmt)
                session = session_result.scalar_one_or_none()
                if session:
                    # issue #277（评审 or1268 R2 收账）：lesson 命名空间的 org 必须与
                    # agent 运行侧同一条解析链（require_organization → resolved org，
                    # 含 token 组织回退 / 无组织超管回源）——运行读 42 而 feedback 按
                    # raw session.org 写 None 时，「自己写的 lesson 自己学不到」。
                    # （#268 只对齐了「无组织 ↔ 1 号组织」这一种组合。）
                    # 调用者完全无组织上下文时（无组织非超管）退回会话自身记录的
                    # org：与对应的无组织运行（/chat/stream useAgent 传 None）同键。
                    try:
                        lesson_org_id = await require_organization(current_user, db)
                    except AuthorizationError:
                        lesson_org_id = session.organization_id
                    bridge = AgentMemoryBridge(
                        agent_id=f"session:{message_obj.session_id}",
                        organization_id=lesson_org_id,
                        user_id=current_user.id,
                    )
                    await bridge.ensure_loaded()
                    lesson = body.note or "用户对 Agent 回应不满意（未提供具体说明）"
                    await bridge.record_lesson(
                        lesson=lesson,
                        trigger=f"User feedback on message: {message_obj.content[:100]}",
                        solution="需要改进回应质量",
                    )
            except Exception as mem_err:
                logger.warning(f"Failed to record feedback lesson: {mem_err}")

        return {"success": True, "message": "感谢您的反馈！"}
    except NotFoundError:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Agent feedback error: {e}")
        raise ValidationError(f"操作失败: {str(e)}")


# ── Tools & Skills ───────────────────────────────────────────────────────────

@router.get("/tools")
async def list_tools(
    current_user: User = Depends(get_current_user),
):
    """List all available agent tools with their metadata."""
    tools = tool_registry.list_tools()
    default_disabled = set(DEFAULT_DISABLED_TOOLS)

    return {
        "success": True,
        "data": [
            {
                "name": t.name,
                "description": t.description,
                "tags": t.tags,
                "parameters": t.parameters,
                "requires_auth": t.requires_auth,
                "disabled_by_default": t.name in default_disabled,
            }
            for t in tools
        ],
        "total": len(tools),
    }


@router.get("/skills")
async def list_skills(
    current_user: User = Depends(get_current_user),
):
    """List learned agent skills."""
    await skill_manager.load()
    skills = skill_manager.list_skills()
    return {
        "success": True,
        "data": [
            {
                "id": s.id,
                "name": s.name,
                "description": s.description,
                "success_rate": round(s.success_rate, 2),
                "trigger_patterns": s.trigger_patterns,
                "tool_sequence": s.tool_sequence,
            }
            for s in skills
        ],
    }


# ── Session Management ──────────────────────────────────────────────────────

@router.get("/sessions")
async def list_sessions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List agent sessions for the current user."""
    try:
        skip = (page - 1) * page_size

        total_stmt = select(func.count(ChatSession.id)).where(
            ChatSession.user_id == current_user.id,
            ChatSession.status != ChatSessionStatus.ENDED,
        )
        total = (await db.execute(total_stmt)).scalar() or 0

        stmt = (
            select(ChatSession)
            .where(
                ChatSession.user_id == current_user.id,
                ChatSession.status != ChatSessionStatus.ENDED,
            )
            .order_by(desc(ChatSession.updated_at))
            .offset(skip)
            .limit(page_size)
        )
        sessions = (await db.execute(stmt)).scalars().all()

        data = []
        for s in sessions:
            # Count messages
            msg_count_stmt = select(func.count(ChatMessage.id)).where(
                ChatMessage.session_id == s.id
            )
            msg_count = (await db.execute(msg_count_stmt)).scalar() or 0

            data.append({
                "id": s.id,
                "title": s.title or "Untitled",
                "message_count": msg_count,
                "created_at": s.created_at.isoformat() if s.created_at else "",
                "updated_at": s.updated_at.isoformat() if s.updated_at else "",
            })

        return {
            "success": True,
            "data": {
                "sessions": data,
                "total": total,
                "page": page,
                "page_size": page_size,
            },
        }
    except Exception as e:
        logger.error(f"List agent sessions error: {e}")
        raise ValidationError(f"获取会话列表失败: {str(e)}")


@router.post("/sessions")
async def create_session(
    body: SessionCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new agent session."""
    # issue #277（评审 or1268 R2 收账）：该端点是前端 agent 页「新建会话」入口，
    # 会话 org 必须与 /agent/chat 的运行解析同链（resolved：含 token 组织回退、
    # 无组织超管回源）。此前落库 raw organization_id——token 回退 / 无组织超管
    # 建出的会话记 None，与随后 chat 的运行 org 不对称（feedback lesson 失效）。
    org_id = await require_organization(current_user, db)
    from app.core.database import AsyncSessionLocal
    async with AsyncSessionLocal() as session:
        try:
            session_id = str(uuid.uuid4())
            new_session = ChatSession(
                id=session_id,
                user_id=current_user.id,
                title=body.title[:80],
                status=ChatSessionStatus.ACTIVE,
                organization_id=org_id,
            )
            session.add(new_session)
            await session.commit()

            return {
                "success": True,
                "data": {
                    "id": session_id,
                    "title": new_session.title,
                },
            }
        except Exception as e:
            await session.rollback()
            logger.error(f"Create session error: {e}")
            raise ValidationError(f"创建会话失败: {str(e)}")


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get an agent session with its messages."""
    try:
        stmt = select(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.user_id == current_user.id,
        )
        session = (await db.execute(stmt)).scalar_one_or_none()
        if not session:
            raise NotFoundError("会话不存在")

        msg_stmt = (
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at.asc())
        )
        messages = (await db.execute(msg_stmt)).scalars().all()

        formatted = []
        for msg in messages:
            msg_dict = {
                "id": msg.id,
                "content": msg.content,
                "message_type": msg.message_type.value if hasattr(msg.message_type, "value") else str(msg.message_type),
                "created_at": msg.created_at.isoformat() if msg.created_at else "",
            }
            if msg.meta_data:
                with contextlib.suppress(json.JSONDecodeError):
                    msg_dict["meta"] = json.loads(msg.meta_data)
            formatted.append(msg_dict)

        # Try to load agent config
        agent_config = await AgentConfig.load_from_redis(session_id)

        return {
            "success": True,
            "data": {
                "id": session.id,
                "title": session.title,
                "messages": formatted,
                "config": agent_config.to_dict() if agent_config else None,
                "created_at": session.created_at.isoformat() if session.created_at else "",
                "updated_at": session.updated_at.isoformat() if session.updated_at else "",
            },
        }
    except NotFoundError:
        raise
    except Exception as e:
        logger.error(f"Get session error: {e}")
        raise ValidationError(f"获取会话失败: {str(e)}")


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete an agent session and its messages."""
    try:
        stmt = select(ChatSession).where(
            ChatSession.id == session_id,
            ChatSession.user_id == current_user.id,
        )
        session = (await db.execute(stmt)).scalar_one_or_none()
        if not session:
            raise NotFoundError("会话不存在或无权删除")

        from sqlalchemy import delete as sql_delete
        await db.execute(sql_delete(ChatMessage).where(ChatMessage.session_id == session_id))
        await db.delete(session)
        await db.commit()

        # Clean up Redis keys
        try:
            from app.core.redis import redis_client
            if redis_client:
                await redis_client.delete(f"agent:config:{session_id}")
                await redis_client.delete(f"agent:session_state:{session_id}")
        except Exception:
            pass

        return {"success": True, "message": "会话已删除"}
    except NotFoundError:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Delete session error: {e}")
        raise ValidationError(f"删除失败: {str(e)}")


# ── Agent Configuration ─────────────────────────────────────────────────────

@router.get("/config")
async def get_config(
    current_user: User = Depends(get_current_user),
):
    """Get the current user's default agent configuration."""
    config = AgentConfig()
    # Try loading from Redis
    saved = await AgentConfig.load_from_redis(f"user:{current_user.id}")
    if saved:
        config = saved

    return {
        "success": True,
        "data": config.to_dict(),
    }


@router.put("/config")
async def update_config(
    body: AgentConfigUpdate,
    current_user: User = Depends(get_current_user),
):
    """Update the current user's default agent configuration."""
    # Load existing or create default
    existing = await AgentConfig.load_from_redis(f"user:{current_user.id}")
    config = existing or AgentConfig()

    # Update fields
    if body.model is not None:
        config.model = body.model
    if body.temperature is not None:
        config.temperature = body.temperature
    if body.max_tokens is not None:
        config.max_tokens = body.max_tokens
    if body.max_plan_steps is not None:
        config.max_plan_steps = body.max_plan_steps
    if body.max_retries_per_step is not None:
        config.max_retries_per_step = body.max_retries_per_step
    if body.enable_planning is not None:
        config.enable_planning = body.enable_planning
    if body.enable_reflection is not None:
        config.enable_reflection = body.enable_reflection
    if body.enable_tools is not None:
        config.enable_tools = body.enable_tools
    if body.enable_memory is not None:
        config.enable_memory = body.enable_memory
    if body.enable_thinking is not None:
        config.enable_thinking = body.enable_thinking
    if body.personality is not None:
        config.personality = body.personality  # type: ignore
    if body.system_prompt_override is not None:
        config.system_prompt_override = body.system_prompt_override
    if body.approval_mode is not None:
        config.approval_mode = body.approval_mode
    if body.plan_approval_mode is not None:
        config.plan_approval_mode = body.plan_approval_mode
    if body.approval_timeout_seconds is not None:
        config.approval_timeout_seconds = body.approval_timeout_seconds
    if body.disabled_tools is not None:
        # 安全加固：高危工具由服务端强制禁用，客户端无法解除
        config.disabled_tools = _merge_disabled_tools(body.disabled_tools)

    await config.save_to_redis(f"user:{current_user.id}")

    return {
        "success": True,
        "data": config.to_dict(),
        "message": "配置已更新",
    }


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _load_session_messages(session_id: str, user_id: int) -> list[dict[str, str]]:
    """Load session messages as history for the agent."""
    try:
        async with AsyncSessionLocal() as session:
            stmt = select(ChatSession).where(
                ChatSession.id == session_id,
                ChatSession.user_id == user_id,
            )
            result = await session.execute(stmt)
            if not result.scalar_one_or_none():
                return []

            msg_stmt = (
                select(ChatMessage)
                .where(ChatMessage.session_id == session_id)
                .order_by(ChatMessage.created_at.asc())
                .limit(20)
            )
            messages = (await session.execute(msg_stmt)).scalars().all()

            return [
                {
                    "role": "user" if m.message_type == MessageType.USER else "assistant",
                    "content": m.content,
                }
                for m in messages
            ]
    except Exception as e:
        logger.warning(f"Failed to load session messages: {e}")
        return []


async def _save_session_interaction(
    session_id: str,
    user_id: int,
    query: str,
    organization_id: int | None,
    assistant_response: str = "",
) -> str | None:
    """Save the user query and assistant response as messages in the session.

    S6 安全加固：会话归属按 user_id 校验（与 _load_session_messages 一致），
    绝不允许把消息写进其他用户的会话。

    Returns: the assistant message ID；None 表示"没有需要写的助手文本（空回复）"
    或"会话不属于调用者（显式拒绝，日志已记录）"。

    Raises: 落库失败（DB 抖动等）时**原样上抛**，由调用方显式处理——issue #301
    批 D（#22）：修前这里把异常吞成 None，与"空回复/越权拒绝"无法区分，调用方
    done 照发且无任何提示，用户刷新后本轮问答凭空消失。
    """
    try:
        async with AsyncSessionLocal() as session:
            # S6 安全加固：按 id + user_id 校验会话归属（与 _load_session_messages 一致），
            # 绝不允许把消息写进其他用户的会话
            stmt = select(ChatSession).where(
                ChatSession.id == session_id,
                ChatSession.user_id == user_id,
            )
            result = await session.execute(stmt)
            existing = result.scalar_one_or_none()

            if existing is None:
                # id 可能已被其他用户的会话占用：确认后拒绝写入（也避免主键冲突）
                occupied = (
                    await session.execute(
                        select(ChatSession.id).where(ChatSession.id == session_id)
                    )
                ).scalar_one_or_none()
                if occupied is not None:
                    logger.warning(
                        f"Refusing to save interaction into session {session_id} "
                        f"not owned by user {user_id}"
                    )
                    return None
                new_session = ChatSession(
                    id=session_id,
                    user_id=user_id,
                    title=query[:80],
                    status=ChatSessionStatus.ACTIVE,
                    organization_id=organization_id,
                )
                session.add(new_session)
            elif existing.organization_id is None:
                # issue #277（评审 or1268 R2 收账）：历史会话自愈——本修复前以 raw
                # org 落库的会话（token 回退 / 无组织超管用户当时记 None）在本次以
                # resolved org 运行后收敛为运行 org，落库与运行解析保持对称；
                # 只补空值，绝不改写已记录的非空组织。
                existing.organization_id = organization_id

            user_msg = ChatMessage(
                id=str(uuid.uuid4()),
                session_id=session_id,
                content=query,
                message_type=MessageType.USER,
            )
            session.add(user_msg)

            assistant_msg_id = None
            if assistant_response:
                assistant_msg_id = str(uuid.uuid4())
                assistant_msg = ChatMessage(
                    id=assistant_msg_id,
                    session_id=session_id,
                    content=assistant_response,
                    message_type=MessageType.ASSISTANT,
                )
                session.add(assistant_msg)

            # Update session title from first message
            if existing is not None and (not existing.title or existing.title == "New Agent Session"):
                existing.title = query[:80]
                existing.updated_at = func.now()

            await session.commit()
            return assistant_msg_id
    except Exception as e:
        # issue #301 批 D（#22）：记录后**原样上抛**（不再吞成 None）。调用方
        # （/chat、/resume）拿到异常后负责显式告知：warning 事件 + done.saved=false
        # + ERROR 日志——落库失败绝不静默。
        logger.error(f"Failed to save interaction: {e}")
        raise
