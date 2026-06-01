"""Agent API endpoints — PER agent chat via SSE, session & config management.

Endpoints:
    POST /api/v1/agent/chat           — PER agent chat (SSE streaming)
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
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from datetime import datetime

from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.config import DEFAULT_DISABLED_TOOLS, AgentConfig
from app.agent.registry import tool_registry
from app.agent.service import agent_service
from app.agent.skills import skill_manager
from app.core.database import AsyncSessionLocal, get_db
from app.core.prometheus import AGENT_FEEDBACK_TOTAL
from app.core.security import get_current_user
from app.exceptions import NotFoundError, ValidationError
from app.models.chat import ChatMessage, ChatSession, ChatSessionStatus, MessageType
from app.models.user import User

logger = logging.getLogger(__name__)
router = APIRouter()


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


class AgentConfigUpdate(BaseModel):
    model: str | None = None
    temperature: float | None = None
    enable_planning: bool | None = None
    enable_reflection: bool | None = None
    enable_tools: bool | None = None
    enable_memory: bool | None = None
    enable_thinking: bool | None = None
    personality: str | None = None
    system_prompt_override: str | None = None
    disabled_tools: list[str] | None = None


class SessionCreate(BaseModel):
    title: str = Field("New Agent Session", max_length=80)


# ── Agent Chat (SSE) ─────────────────────────────────────────────────────────

@router.post("/chat")
async def agent_chat(
    body: AgentChatRequest,
    current_user: User = Depends(get_current_user),
):
    """PER agent chat with streaming events via SSE.

    Events: thinking, plan_start, plan_step, plan_complete,
            tool_call, tool_result, tool_error, reflection, chunk, done
    """
    async def event_stream():
        try:
            # Build AgentConfig from request
            config = AgentConfig(
                model=body.model,
                temperature=body.temperature,
                enable_tools=body.enable_tools,
                enable_planning=body.enable_planning,
                enable_reflection=body.enable_reflection,
                enable_memory=body.enable_memory,
                enable_thinking=body.enable_thinking,
                system_prompt_override=body.system_prompt_override,
                disabled_tools=body.disabled_tools if body.disabled_tools else list(DEFAULT_DISABLED_TOOLS),
            )

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
                organization_id=current_user.organization_id or 1,
                user_id=current_user.id,
                session_id=body.session_id,
                config=config,
            ):
                sse_data = event.to_sse_dict()
                yield f"data: {json.dumps(sse_data, ensure_ascii=False)}\n\n"
                if event.type == "chunk":
                    final_answer += event.content

            assistant_message_id = None
            # Save interaction to session (user query + assistant response)
            if body.session_id:
                assistant_message_id = await _save_session_interaction(
                    body.session_id,
                    current_user.id,
                    body.query,
                    current_user.organization_id,
                    final_answer,
                )

        except Exception as e:
            logger.error(f"Agent chat error: {e}", exc_info=True)
            error_data = {"type": "error", "content": f"Agent 执行出错: {str(e)}"}
            yield f"data: {json.dumps(error_data, ensure_ascii=False)}\n\n"

        done_data: dict[str, Any] = {"type": "done", "content": ""}
        if assistant_message_id:
            done_data["message_id"] = assistant_message_id
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
                    bridge = AgentMemoryBridge(
                        agent_id=f"session:{message_obj.session_id}",
                        organization_id=session.organization_id or 1,
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
):
    """Create a new agent session."""
    from app.core.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        try:
            session_id = str(uuid.uuid4())
            new_session = ChatSession(
                id=session_id,
                user_id=current_user.id,
                title=body.title[:80],
                status=ChatSessionStatus.ACTIVE,
                organization_id=current_user.organization_id,
            )
            db.add(new_session)
            await db.commit()

            return {
                "success": True,
                "data": {
                    "id": session_id,
                    "title": new_session.title,
                },
            }
        except Exception as e:
            await db.rollback()
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
    if body.disabled_tools is not None:
        config.disabled_tools = body.disabled_tools

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

    Returns: the assistant message ID (or None if no response / on error).
    """
    try:
        async with AsyncSessionLocal() as session:
            # Ensure session exists
            stmt = select(ChatSession).where(ChatSession.id == session_id)
            result = await session.execute(stmt)
            if not result.scalar_one_or_none():
                new_session = ChatSession(
                    id=session_id,
                    user_id=user_id,
                    title=query[:80],
                    status=ChatSessionStatus.ACTIVE,
                    organization_id=organization_id,
                )
                session.add(new_session)

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
            existing = (await session.execute(stmt)).scalar_one_or_none()
            if existing and (not existing.title or existing.title == "New Agent Session"):
                existing.title = query[:80]
                existing.updated_at = func.now()

            await session.commit()
            return assistant_msg_id
    except Exception as e:
        logger.warning(f"Failed to save interaction: {e}")
        return None
