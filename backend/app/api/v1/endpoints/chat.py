import asyncio
import json
import logging
import uuid
from dataclasses import dataclass, field
from typing import Optional, List, AsyncGenerator

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query, Depends, HTTPException, Body
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func, delete, or_
from sqlalchemy.orm.attributes import flag_modified

from app.core.database import get_db, AsyncSessionLocal
from app.core.config import settings
from app.models.user import User
from app.models.chat import ChatSession, ChatMessage, ChatSessionStatus, MessageType
from app.models.document import Document
from app.models.prompt import PromptTemplate
from app.services.auth_service import AuthService
from app.services.rag_service import rag_service
from app.services.semantic_cache import semantic_cache
from app.services.memory_service import get_memory_system
from app.core.security import get_current_user

router = APIRouter()
logger = logging.getLogger(__name__)
auth_service = AuthService()


STRICT_MODE_PROMPT = (
    "你是企业知识库问答助手，当前处于【严格模式】。\n"
    "⚠️ 核心约束：\n"
    "1. **完全基于提供内容**：你只能根据下面提供的【参考文档】回答问题。如果你在文档中找不到答案，请直接回复「根据提供的文档内容，无法回答该问题」，绝不能结合外部知识或进行任何合理的推测。\n"
    "2. **拒绝发散**：不要提供与文档无关的背景知识、引申解释或建议。\n"
    "3. **精准引用**：每一句话必须使用 [n] 格式标注引用来源（如 [1]、[2]）。\n"
)


@dataclass
class RAGEvent:
    type: str  # "chunk", "message", "cache_hit"
    content: str = ""
    conversation_id: str = ""
    message_id: str = ""
    sources: list[dict] = field(default_factory=list)
    is_cached: bool = False


def build_sources_list(context_results: list[dict]) -> list[dict]:
    return [
        {
            "filename": r.get("filename", "未知文档"),
            "relevanceScore": r.get("score", 0.0),
            "content": r.get("snippet") or (r.get("text", "")[:240]),
            "documentId": r.get("document_id"),
            "hasKeyword": r.get("has_keyword", False),
            "hasVector": r.get("has_vector", False),
            "rewriteHits": r.get("rewrite_hits", 1),
            "freshFactor": r.get("fresh_factor", 1.0),
        }
        for r in context_results
    ]


async def get_or_create_session(
    session: AsyncSession,
    conversation_id: str | None,
    user_id: int,
    title: str,
    organization_id: int | None,
) -> tuple[str, list[str]]:
    """获取或创建会话，返回 (conversation_id, bound_doc_ids)"""
    bound_doc_ids: list[str] = []

    if not conversation_id:
        new_session = ChatSession(
            id=str(uuid.uuid4()), user_id=user_id, title=title[:20],
            status=ChatSessionStatus.ACTIVE, organization_id=organization_id,
        )
        session.add(new_session)
        await session.flush()
        return new_session.id, bound_doc_ids

    existing = (
        await session.execute(
            select(ChatSession).where(ChatSession.id == conversation_id)
        )
    ).scalar_one_or_none()

    if not existing:
        session.add(ChatSession(
            id=conversation_id, user_id=user_id, title=title[:20],
            status=ChatSessionStatus.ACTIVE, organization_id=organization_id,
        ))
    else:
        settings_json = existing.settings or {}
        if isinstance(settings_json, dict):
            bound_doc_ids = settings_json.get("bound_document_ids") or []

    return conversation_id, bound_doc_ids


async def bind_docs_to_session(
    session: AsyncSession,
    conversation_id: str,
    doc_ids: list[str],
):
    conv = (
        await session.execute(
            select(ChatSession).where(ChatSession.id == conversation_id)
        )
    ).scalar_one_or_none()
    if conv:
        existing = conv.settings or {}
        if not isinstance(existing, dict):
            existing = {}
        existing["bound_document_ids"] = doc_ids
        conv.settings = existing
        flag_modified(conv, "settings")
        await session.commit()


async def get_system_prompt(
    session: AsyncSession,
    strict_mode: bool,
) -> str | None:
    if strict_mode:
        return STRICT_MODE_PROMPT

    result = await session.execute(
        select(PromptTemplate)
        .where(PromptTemplate.category == "rag", PromptTemplate.is_active == True)
        .order_by(desc(PromptTemplate.updated_at), desc(PromptTemplate.id))
        .limit(1)
    )
    rag_prompt = result.scalar_one_or_none()
    if rag_prompt and rag_prompt.content:
        return rag_prompt.content
    return None


async def run_rag_pipeline(
    *,
    session: AsyncSession,
    user_content: str,
    user_id: int,
    search_org_id: int,
    organization_id: int | None,
    conversation_id: str | None,
    file_ids: list[str],
    strict_mode: bool,
    privacy_mode: bool,
    event_queue: asyncio.Queue | None = None,
) -> None:
    """
    RAG 管线核心逻辑。将事件推入 event_queue。
    如果 event_queue 为 None，直接返回（调用方自行从返回值获取结果）。
    """
    def _emit(event: RAGEvent):
        if event_queue is not None:
            event_queue.put_nowait(event)

    conversation_id, bound_doc_ids = await get_or_create_session(
        session, conversation_id, user_id, user_content, organization_id,
    )

    # 加载历史
    history_msgs = (
        await session.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == conversation_id)
            .order_by(ChatMessage.created_at.asc())
            .limit(10)
        )
    ).scalars().all()
    chat_history = [
        {
            "role": "user" if h.message_type == MessageType.USER else "assistant",
            "content": h.content,
        }
        for h in history_msgs
    ]

    # 保存用户消息
    user_meta_data = {}
    if file_ids:
        user_meta_data["file_ids"] = file_ids
    user_msg = ChatMessage(
        id=str(uuid.uuid4()),
        session_id=conversation_id,
        content=user_content,
        message_type=MessageType.USER,
        meta_data=json.dumps(user_meta_data) if user_meta_data else None,
    )
    session.add(user_msg)
    await session.commit()

    active_doc_ids = [str(fid) for fid in file_ids if fid] if file_ids else bound_doc_ids

    # 语义缓存
    query_vector = None
    if not active_doc_ids and not strict_mode:
        query_vector = await rag_service.get_embedding(user_content)
        if query_vector:
            cached_res = await semantic_cache.get_similar_answer(query_vector)
            if cached_res:
                full_response = cached_res.get("answer", "")
                sources_list = cached_res.get("sources", [])
                ai_msg_id = str(uuid.uuid4())

                _emit(RAGEvent(
                    type="message", content=full_response,
                    conversation_id=conversation_id, message_id=ai_msg_id,
                    sources=sources_list, is_cached=True,
                ))

                ai_msg = ChatMessage(
                    id=ai_msg_id, session_id=conversation_id,
                    content=full_response, message_type=MessageType.ASSISTANT,
                    meta_data=json.dumps(
                        {"sources": sources_list, "is_cached": True},
                        ensure_ascii=False,
                    ),
                )
                session.add(ai_msg)
                await session.commit()
                return

    # 知识库检索
    context_results: list[dict] = []
    active_doc_ids = [str(fid) for fid in file_ids if fid] if file_ids else bound_doc_ids

    if active_doc_ids:
        context_results = await rag_service.search_knowledge_base(
            user_content, search_org_id, 8, document_ids=active_doc_ids,
        )
        if file_ids:
            await bind_docs_to_session(session, conversation_id, active_doc_ids)
    elif not strict_mode:
        context_results = await rag_service.search_knowledge_base(
            user_content, search_org_id, 5,
        )

    sources_list = build_sources_list(context_results)
    rag_service.report_grounded(bool(sources_list))

    system_prompt_override = await get_system_prompt(session, strict_mode)

    # Agent memory: recall relevant memories and inject into system prompt
    memory_context = ""
    try:
        memory_system = get_memory_system(str(user_id))
        if memory_system._embedding_provider is None:
            memory_system.set_embedding_provider(rag_service.get_embedding)
        memory_context = await memory_system.get_context(user_content)
        if memory_context:
            system_prompt_override = (system_prompt_override or "") + "\n\n" + memory_context
            logger.info(f"Memory context injected for user {user_id}")
    except Exception as e:
        logger.warning(f"Memory recall failed (non-fatal): {e}")

    # LLM 流式生成
    full_response = ""
    ai_msg_id = str(uuid.uuid4())

    _emit(RAGEvent(
        type="chunk", content="",
        conversation_id=conversation_id, message_id=ai_msg_id,
        sources=sources_list,
    ))

    async for chunk in rag_service.chat_stream(
        user_content, context_results, chat_history,
        system_prompt_override=system_prompt_override,
        enable_masking=privacy_mode,
    ):
        full_response += chunk
        _emit(RAGEvent(
            type="chunk", content=chunk,
            conversation_id=conversation_id, message_id=ai_msg_id,
        ))

    _emit(RAGEvent(
        type="message", content=full_response,
        conversation_id=conversation_id, message_id=ai_msg_id,
        sources=sources_list, is_cached=False,
    ))

    ai_msg = ChatMessage(
        id=ai_msg_id, session_id=conversation_id,
        content=full_response, message_type=MessageType.ASSISTANT,
        meta_data=json.dumps({"sources": sources_list}, ensure_ascii=False),
    )
    session.add(ai_msg)
    await session.commit()

    if not active_doc_ids and not strict_mode and query_vector is not None:
        await semantic_cache.set_cache(
            query=user_content, embedding=query_vector,
            answer=full_response, sources=sources_list,
        )

    # Store interaction in agent memory
    try:
        memory_system = get_memory_system(str(user_id))
        await memory_system.store_interaction(user_content, full_response)
    except Exception as e:
        logger.warning(f"Memory store failed (non-fatal): {e}")


# ============================================================
# REST endpoints
# ============================================================


@router.post("/messages/{message_id}/feedback", dependencies=[Depends(get_current_user)])
async def update_message_feedback(
    message_id: str,
    feedback: int = Body(..., embed=True),
    note: Optional[str] = Body(None, embed=True),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        stmt = select(ChatMessage).join(ChatSession).where(
            ChatMessage.id == message_id,
            ChatSession.user_id == current_user.id,
        )
        message_obj = (await db.execute(stmt)).scalar_one_or_none()
        if not message_obj:
            return {"success": False, "message": "消息不存在或无权评价"}
        message_obj.feedback = feedback
        if note is not None:
            message_obj.feedback_note = note
        await db.commit()
        return {"success": True, "message": "感谢您的反馈！"}
    except Exception as e:
        await db.rollback()
        logger.error(f"Update feedback error: {e}")
        return {"success": False, "message": f"操作失败: {str(e)}"}


@router.delete("/conversations/{conversation_id}/clear", dependencies=[Depends(get_current_user)])
async def clear_conversation_messages(
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        stmt = select(ChatSession).where(
            ChatSession.id == conversation_id,
            ChatSession.user_id == current_user.id,
        )
        session = (await db.execute(stmt)).scalar_one_or_none()
        if not session:
            raise HTTPException(status_code=404, detail="会话不存在")
        delete_stmt = delete(ChatMessage).where(ChatMessage.session_id == conversation_id)
        await db.execute(delete_stmt)
        await db.commit()
        return {"success": True, "message": "会话已清空"}
    except Exception as e:
        await db.rollback()
        logger.error(f"Clear conversation error: {e}")
        return {"success": False, "message": str(e)}


@router.get("/conversations", dependencies=[Depends(get_current_user)])
async def get_conversations(
    page: int = 1, page_size: int = 20,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        skip = (page - 1) * page_size
        if current_user.organization_id:
            org_filter = or_(
                ChatSession.organization_id == current_user.organization_id,
                ChatSession.organization_id == None,
            )
        else:
            org_filter = ChatSession.organization_id == None

        total_query = select(func.count(ChatSession.id)).where(
            ChatSession.user_id == current_user.id,
            ChatSession.status != ChatSessionStatus.ENDED,
            org_filter,
        )
        total = (await db.execute(total_query)).scalar() or 0

        query = (
            select(ChatSession)
            .where(
                ChatSession.user_id == current_user.id,
                ChatSession.status != ChatSessionStatus.ENDED,
                org_filter,
            )
            .order_by(desc(ChatSession.updated_at))
            .offset(skip)
            .limit(page_size)
        )
        sessions = (await db.execute(query)).scalars().all()

        session_ids = [s.id for s in sessions]
        msg_counts: dict[str, int] = {}
        last_messages: dict[str, ChatMessage] = {}

        if session_ids:
            count_stmt = (
                select(ChatMessage.session_id, func.count(ChatMessage.id))
                .where(ChatMessage.session_id.in_(session_ids))
                .group_by(ChatMessage.session_id)
            )
            for sid, count in (await db.execute(count_stmt)).all():
                msg_counts[sid] = count

            subq = (
                select(
                    ChatMessage.session_id,
                    func.max(ChatMessage.created_at).label("max_created_at"),
                )
                .where(ChatMessage.session_id.in_(session_ids))
                .group_by(ChatMessage.session_id)
                .subquery()
            )
            last_msg_stmt = select(ChatMessage).join(
                subq,
                (ChatMessage.session_id == subq.c.session_id)
                & (ChatMessage.created_at == subq.c.max_created_at),
            )
            for msg in (await db.execute(last_msg_stmt)).scalars().all():
                last_messages[msg.session_id] = msg

        data = []
        for s in sessions:
            msg_count = msg_counts.get(s.id, 0)
            last_msg = last_messages.get(s.id)
            last_msg_content = "暂无消息"
            if last_msg:
                last_msg_content = (
                    last_msg.content[:50] + "..."
                    if len(last_msg.content) > 50
                    else last_msg.content
                )
            data.append({
                "id": s.id, "title": s.title, "uuid": s.id, "isEdit": False,
                "message_count": msg_count, "last_message": last_msg_content,
                "created_at": s.created_at.isoformat() if s.created_at else "",
                "updated_at": s.updated_at.isoformat() if s.updated_at else "",
            })

        return {
            "success": True, "message": "获取成功",
            "data": {"data": data, "total": total, "page": page, "page_size": page_size},
        }
    except Exception as e:
        logger.error(f"Get conversations error: {e}")
        return {"success": False, "message": f"获取失败: {str(e)}", "data": []}


@router.delete("/conversations/{conversation_id}", dependencies=[Depends(get_current_user)])
async def delete_conversation(
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        stmt = select(ChatSession).where(
            ChatSession.id == conversation_id,
            ChatSession.user_id == current_user.id,
        )
        session = (await db.execute(stmt)).scalar_one_or_none()
        if not session:
            return {"success": False, "message": "会话不存在或无权删除"}
        from sqlalchemy import text
        await db.execute(
            text("DELETE FROM chat_messages WHERE session_id = :session_id"),
            {"session_id": conversation_id},
        )
        await db.delete(session)
        await db.commit()
        return {"success": True, "message": "会话删除成功"}
    except Exception as e:
        await db.rollback()
        return {"success": False, "message": f"删除失败: {str(e)}"}


@router.post("/conversations/{conversation_id}/unbind-docs", dependencies=[Depends(get_current_user)])
async def unbind_conversation_docs(
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        stmt = select(ChatSession).where(
            ChatSession.id == conversation_id,
            ChatSession.user_id == current_user.id,
        )
        session_obj = (await db.execute(stmt)).scalar_one_or_none()
        if not session_obj:
            return {"success": False, "message": "无权访问"}
        settings_json = session_obj.settings or {}
        if not isinstance(settings_json, dict):
            settings_json = {}
        settings_json.pop("bound_document_ids", None)
        session_obj.settings = settings_json
        flag_modified(session_obj, "settings")
        await db.commit()
        return {"success": True, "message": "已清除会话文档绑定"}
    except Exception as e:
        await db.rollback()
        return {"success": False, "message": f"操作失败: {str(e)}"}


@router.get("/conversations/{conversation_id}", dependencies=[Depends(get_current_user)])
async def get_conversation(
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        stmt = select(ChatSession).where(
            ChatSession.id == conversation_id,
            ChatSession.user_id == current_user.id,
        )
        session = (await db.execute(stmt)).scalar_one_or_none()
        if not session:
            return {"success": False, "message": "无权访问"}

        msg_stmt = (
            select(ChatMessage)
            .where(ChatMessage.session_id == conversation_id)
            .order_by(ChatMessage.created_at.asc())
        )
        messages = (await db.execute(msg_stmt)).scalars().all()

        formatted_messages = []
        for msg in messages:
            msg_dict = {
                "id": msg.id, "content": msg.content,
                "messageType": msg.message_type.value,
                "createdAt": msg.created_at.isoformat() if msg.created_at else "",
                "sources": [], "feedback": msg.feedback,
                "feedbackNote": msg.feedback_note,
            }
            if msg.meta_data:
                try:
                    meta = json.loads(msg.meta_data)
                    if msg.message_type == MessageType.USER and "file_ids" in meta:
                        files_info = []
                        file_ids_list = [fid for fid in meta["file_ids"] if fid]
                        if file_ids_list:
                            docs_result = await session.execute(
                                select(Document).where(Document.id.in_(file_ids_list))
                            )
                            doc_map = {str(doc.id): doc for doc in docs_result.scalars().all()}
                            for fid in meta["file_ids"]:
                                doc = doc_map.get(str(fid))
                                if doc:
                                    files_info.append({
                                        "id": str(doc.id), "name": doc.filename,
                                        "status": "done",
                                    })
                        msg_dict["files"] = files_info
                    elif msg.message_type == MessageType.ASSISTANT:
                        if "sources" in meta:
                            msg_dict["sources"] = meta["sources"]
                        if "is_cached" in meta:
                            msg_dict["isCached"] = meta["is_cached"]
                except Exception as parse_e:
                    logger.warning(f"Error parsing message metadata: {parse_e}")
            formatted_messages.append(msg_dict)

        return {
            "success": True, "message": "获取成功",
            "data": {
                "id": session.id, "title": session.title,
                "messages": formatted_messages,
                "created_at": session.created_at.isoformat() if session.created_at else "",
                "updated_at": session.updated_at.isoformat() if session.updated_at else "",
            },
        }
    except Exception as e:
        return {"success": False, "message": f"获取失败: {str(e)}"}


# ============================================================
# WebSocket endpoint
# ============================================================


class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def send_personal_message(self, message: str, websocket: WebSocket):
        await websocket.send_text(message)


manager = ConnectionManager()


@router.get("/metrics")
async def get_rag_metrics(
    window_seconds: int = Query(0, ge=0, le=86400),
    current_user: User = Depends(get_current_user),
):
    return {
        "success": True,
        "data": rag_service.get_metrics(window_seconds=window_seconds),
    }


@router.websocket("/ws")
async def websocket_endpoint(
    websocket: WebSocket,
    token: str = Query(None),
    user_id: int = Query(None),
    conversation_id: Optional[str] = Query(None),
):
    if not token:
        await websocket.close(code=4003, reason="Token required")
        return
    try:
        payload = auth_service.verify_token(token.strip().replace('"', "").replace("'", ""))
        if not payload:
            await websocket.close(code=4001, reason="Invalid token")
            return
        user_id = payload.get("user_id") or user_id
    except Exception as e:
        await websocket.close(code=4002, reason=str(e))
        return

    await manager.connect(websocket)

    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg_data = json.loads(data)
                user_content = msg_data.get("content", "")
                current_conv_id = msg_data.get("conversationId") or conversation_id
                file_ids = msg_data.get("fileIds", [])
                payload_data = msg_data.get("payload", {})
                strict_mode = payload_data.get("strictMode", False)
                privacy_mode = payload_data.get("privacyMode", True)

                if not user_content:
                    continue

                async with AsyncSessionLocal() as session:
                    user_result = await session.execute(
                        select(User).where(User.id == user_id)
                    )
                    db_user = user_result.scalar_one_or_none()
                    search_org_id = (
                        db_user.organization_id
                        if (db_user and db_user.organization_id)
                        else 1
                    )
                    org_id = (
                        db_user.organization_id
                        if (db_user and db_user.organization_id)
                        else None
                    )

                    # Use an asyncio.Queue to collect pipeline events
                    queue: asyncio.Queue = asyncio.Queue()

                    # Run pipeline in background, collect events concurrently
                    pipeline_task = asyncio.create_task(
                        run_rag_pipeline(
                            session=session,
                            user_content=user_content,
                            user_id=user_id,
                            search_org_id=search_org_id,
                            organization_id=org_id,
                            conversation_id=current_conv_id,
                            file_ids=file_ids,
                            strict_mode=strict_mode,
                            privacy_mode=privacy_mode,
                            event_queue=queue,
                        )
                    )

                    # Drain events and send via WebSocket
                    while True:
                        try:
                            event: RAGEvent = await asyncio.wait_for(
                                queue.get(), timeout=0.1
                            )
                            if event.type in ("chunk",):
                                payload = {
                                    "type": event.type,
                                    "content": event.content,
                                    "conversationId": event.conversation_id,
                                    "messageId": event.message_id,
                                }
                                if event.sources:
                                    payload["sources"] = event.sources
                                await manager.send_personal_message(
                                    json.dumps(payload), websocket,
                                )
                            elif event.type == "message":
                                await manager.send_personal_message(json.dumps({
                                    "type": "message",
                                    "content": event.content,
                                    "conversationId": event.conversation_id,
                                    "messageId": event.message_id,
                                    "sources": event.sources,
                                    "is_cached": event.is_cached,
                                }), websocket)
                        except asyncio.TimeoutError:
                            if pipeline_task.done():
                                # Drain remaining events
                                while not queue.empty():
                                    event = queue.get_nowait()
                                    if event.type == "message":
                                        await manager.send_personal_message(
                                            json.dumps({
                                                "type": "message",
                                                "content": event.content,
                                                "conversationId": event.conversation_id,
                                                "messageId": event.message_id,
                                                "sources": event.sources,
                                                "is_cached": event.is_cached,
                                            }), websocket,
                                        )
                                break

                    await pipeline_task

            except json.JSONDecodeError:
                pass
            except Exception as e:
                logger.error(f"Error processing message: {e}", exc_info=True)
                await manager.send_personal_message(
                    json.dumps({"type": "error", "content": "服务器处理出错"}),
                    websocket,
                )

    except WebSocketDisconnect:
        manager.disconnect(websocket)


# ============================================================
# SSE endpoint
# ============================================================


async def sse_event(event_type: str, data: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/stream")
async def chat_stream_endpoint(
    content: str = Body(...),
    conversationId: Optional[str] = Body(None),
    fileIds: Optional[List[str]] = Body(None),
    payload: Optional[dict] = Body(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    async def event_generator():
        try:
            user_content = content
            file_ids = fileIds or []
            payload_data = payload or {}
            strict_mode = payload_data.get("strictMode", False)
            privacy_mode = payload_data.get("privacyMode", True)

            if not user_content:
                yield await sse_event("error", {"content": "消息内容不能为空"})
                return

            async with AsyncSessionLocal() as session:
                db_user = await session.get(User, current_user.id)
                search_org_id = (
                    db_user.organization_id
                    if (db_user and db_user.organization_id)
                    else 1
                )
                org_id = getattr(current_user, "organization_id", None)

                queue: asyncio.Queue = asyncio.Queue()

                pipeline_task = asyncio.create_task(
                    run_rag_pipeline(
                        session=session,
                        user_content=user_content,
                        user_id=current_user.id,
                        search_org_id=search_org_id,
                        organization_id=org_id,
                        conversation_id=conversationId,
                        file_ids=file_ids,
                        strict_mode=strict_mode,
                        privacy_mode=privacy_mode,
                        event_queue=queue,
                    )
                )

                # Drain events and convert to SSE
                while True:
                    try:
                        event: RAGEvent = await asyncio.wait_for(
                            queue.get(), timeout=0.1
                        )
                        if event.type == "chunk":
                            sse_data: dict = {
                                "content": event.content,
                                "conversationId": event.conversation_id,
                                "messageId": event.message_id,
                            }
                            if event.sources:
                                sse_data["sources"] = event.sources
                            yield await sse_event("chunk", sse_data)
                        elif event.type == "message":
                            yield await sse_event("message", {
                                "content": event.content,
                                "conversationId": event.conversation_id,
                                "messageId": event.message_id,
                                "sources": event.sources,
                                "is_cached": event.is_cached,
                            })
                    except asyncio.TimeoutError:
                        if pipeline_task.done():
                            # Drain remaining events
                            while not queue.empty():
                                event = queue.get_nowait()
                                if event.type == "message":
                                    yield await sse_event("message", {
                                        "content": event.content,
                                        "conversationId": event.conversation_id,
                                        "messageId": event.message_id,
                                        "sources": event.sources,
                                        "is_cached": event.is_cached,
                                    })
                            break

                await pipeline_task

        except Exception as e:
            logger.error(f"SSE error: {e}", exc_info=True)
            yield await sse_event("error", {"content": f"服务器处理出错: {str(e)}"})

    return StreamingResponse(event_generator(), media_type="text/event-stream")
