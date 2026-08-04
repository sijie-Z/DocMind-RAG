"""Chat pipeline service — session handling and RAG streaming orchestration."""

import asyncio
import json
import logging
import re
import uuid
from dataclasses import dataclass, field

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import settings
from app.exceptions import AuthorizationError
from app.models.chat import ChatMessage, ChatSession, ChatSessionStatus, MessageType
from app.models.prompt import PromptTemplate
from app.rag.cache import SemanticCache
from app.services.memory_service import get_memory_system
from app.services.rag_service import rag_service

logger = logging.getLogger(__name__)

_semantic_cache = SemanticCache()


STRICT_MODE_PROMPT = (
    "你是企业知识库问答助手，当前处于【严格模式】。\n"
    "⚠️ 核心约束：\n"
    "1. **完全基于提供内容**：你只能根据下面提供的【参考文档】回答问题。如果你在文档中找不到答案，请直接回复「根据提供的文档内容，无法回答该问题」，绝不能结合外部知识或进行任何合理的推测。\n"
    "2. **拒绝发散**：不要提供与文档无关的背景知识、引申解释或建议。\n"
    "3. **精准引用**：每一句话必须使用 [n] 格式标注引用来源（如 [1]、[2]）。\n"
)


@dataclass
class RAGEvent:
    type: str  # "chunk", "message", "cache_hit", "debug"
    content: str = ""
    conversation_id: str = ""
    message_id: str = ""
    sources: list[dict] = field(default_factory=list)
    is_cached: bool = False
    debug_data: dict | None = None


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
        if existing.user_id != user_id:
            raise AuthorizationError("无权访问该会话")
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
        .where(PromptTemplate.category == "rag", PromptTemplate.is_active)
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
    debug: bool = False,
) -> None:
    """RAG pipeline core logic; pushes events into ``event_queue``."""

    def _emit(event: RAGEvent):
        if event_queue is not None:
            event_queue.put_nowait(event)

    conversation_id, bound_doc_ids = await get_or_create_session(
        session, conversation_id, user_id, user_content, organization_id,
    )

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

    query_vector = None
    if not active_doc_ids and not strict_mode:
        query_vector = await rag_service.get_embedding(user_content)
        if query_vector:
            cached_res = await _semantic_cache.get(query_vector, search_org_id)
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

    context_results: list[dict] = []
    active_doc_ids = [str(fid) for fid in file_ids if fid] if file_ids else bound_doc_ids
    debug_info: dict | None = None

    if active_doc_ids:
        context_results, debug_info = await rag_service.search_knowledge_base(
            user_content, search_org_id, 8, document_ids=active_doc_ids, debug=debug,
        )
        if file_ids:
            await bind_docs_to_session(session, conversation_id, active_doc_ids)
    elif not strict_mode:
        context_results, debug_info = await rag_service.search_knowledge_base(
            user_content, search_org_id, 5, debug=debug,
        )

    if debug and debug_info:
        final_context_items = []
        for i, item in enumerate(context_results[:5], 1):
            final_context_items.append({
                "rank": i,
                "filename": item.get("filename", "未知"),
                "score": item.get("score", 0),
                "content": (item.get("snippet") or item.get("text", ""))[:300],
            })
        debug_info["final_context"] = final_context_items
        _emit(RAGEvent(
            type="debug", debug_data=debug_info,
            conversation_id=conversation_id,
        ))

    sources_list = build_sources_list(context_results)
    rag_service.report_grounded(bool(sources_list))

    system_prompt_override = await get_system_prompt(session, strict_mode)

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

    cited_indices = set(int(m) for m in re.findall(r"\[(\d+)\]", full_response))
    cited_sources = [
        {**src, "cited": True}
        for i, src in enumerate(sources_list, 1)
        if i in cited_indices
    ] if cited_indices else sources_list[:3]

    _emit(RAGEvent(
        type="message", content=full_response,
        conversation_id=conversation_id, message_id=ai_msg_id,
        sources=cited_sources, is_cached=False,
    ))

    ai_msg = ChatMessage(
        id=ai_msg_id, session_id=conversation_id,
        content=full_response, message_type=MessageType.ASSISTANT,
        meta_data=json.dumps(
            {"sources": cited_sources, "citations_used": sorted(cited_indices)},
            ensure_ascii=False,
        ),
    )
    session.add(ai_msg)
    await session.commit()

    try:
        token_usage = rag_service.get_last_token_usage()
        if token_usage and token_usage[0] > 0:
            from app.api.v1.endpoints.token_usage import record_token_usage
            model = settings.LOCAL_LLM_MODEL if settings.ENABLE_LOCAL_LLM else settings.DEEPSEEK_MODEL
            await record_token_usage(
                db=session,
                user_id=user_id,
                organization_id=organization_id,
                model=model,
                source='rag_chat',
                input_tokens=token_usage[0],
                output_tokens=token_usage[1],
            )
    except Exception as e:
        logger.warning(f'Token usage recording failed (non-fatal): {e}')

    if not active_doc_ids and not strict_mode and query_vector is not None:
        await _semantic_cache.set(
            query=user_content, embedding=query_vector,
            answer=full_response, sources=sources_list,
            organization_id=search_org_id,
        )

    try:
        memory_system = get_memory_system(str(user_id))
        await memory_system.store_interaction(user_content, full_response)
    except Exception as e:
        logger.warning(f"Memory store failed (non-fatal): {e}")
