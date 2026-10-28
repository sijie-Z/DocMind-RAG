import asyncio
import json
import logging
import os
import uuid

from fastapi import (
    APIRouter,
    Depends,
    File,
    Query,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import settings
from app.core.database import AsyncSessionLocal, get_db
from app.core.error_mapping import (
    public_agent_event_content,
    public_diagnostic_content,
    public_exception_message,
    public_rag_error_content,
    resolve_request_id,
)
from app.core.log_safety import log_safe
from app.core.redis import redis_client
from app.core.security import (
    get_current_user,
    get_user_org_id,
    require_organization,
    require_scope,
)
from app.core.sse import SSEStream, format_sse, keepalive_stream
from app.core.ws_auth import (
    WS_CLOSE_AUTH_FAILED,
    WS_REASON_USER_INVALID,
    authenticate_ws,
    close_ws,
    extract_ws_subprotocol,
    load_active_ws_user,
)
from app.exceptions import AppError, AuthorizationError, NotFoundError, ValidationError
from app.models.chat import (
    ChatMessage,
    ChatSession,
    ChatSessionStatus,
    DeletedChatSession,
    MessageType,
)
from app.models.document import Document
from app.models.user import User
from app.schemas.chat import ChatSessionCreate, ChatStreamRequest, FeedbackRequest
from app.services.chat_service import RAGEvent, run_rag_pipeline
from app.services.rag_service import rag_service
from app.services.session_service import (
    SessionAccessScope,
    load_accessible_session,
    resolve_session_access_scope,
    resolve_session_scope,
    save_session_interaction,
    session_access_org_conditions,
    session_matches_scope,
    session_visibility_conditions,
)

router = APIRouter()
logger = logging.getLogger(__name__)


def _guard_enabled_from_env(value: str | None) -> bool:
    """解析 ``CHAT_DEEP_ANALYSIS_GUARD``：缺省/空串 = 开启（生产默认拒绝旧客户端）。

    关闭值仅接受 ``0/false/off/no``（大小写不敏感）——部署窗口期回滚用。
    注意：本开关读取**真实环境变量**（导入期求值，需重启）；不经过 pydantic
    ``.env`` 文件（与 #232 的配置口径有意区分：安全守卫只认显式环境变量）。
    """
    if value is None or not value.strip():
        return True
    return value.strip().lower() not in ("0", "false", "off", "no")


# issue #508（方案 B）：深度分析（Agent/PER）已迁至 /agent 工作台（审批/计划
# 审批/取消/回放 UI 完整）。旧客户端若仍发 useAgent=true，必须收到显式错误码
# DEEP_ANALYSIS_MOVED_TO_AGENT——既不静默跑「Chat 里没有审批 UI」的 Agent 链路，
# 也不静默降级成普通 RAG。默认开启；部署窗口期可用环境变量
# CHAT_DEEP_ANALYSIS_GUARD=false 临时放行旧链路（回滚阀，非长期配置）。
DEEP_ANALYSIS_GUARD_ENABLED = _guard_enabled_from_env(os.getenv("CHAT_DEEP_ANALYSIS_GUARD"))


def _extract_agent_sources(tool_result: str, sources: list[dict]) -> None:
    """Parse retrieval tool output and append document references to sources list.

    T4（来源伪造隔离）：检索工具的输出是结构化 JSON 信封——``sources`` 段承载
    来源元数据、``content`` 段承载正文。这里**只读 ``sources`` 段**，不再对整段
    输出跑逐行正则：修复前文档正文里构造一行 "[3] (0.99) 假.pdf" 即可伪造一张
    引用卡（documentId 为空，前端无从分辨）。

    兼容：旧纯文本格式（"[i] (score) filename" 行）保留一个 minor 版本的兜底
    解析，供滚动升级期间旧格式输出使用。DEPRECATED — 下一个 minor 移除。
    """
    from app.agent.core_tools import SEARCH_RESULTS_PAYLOAD_KIND
    from app.rag.source_provenance import provenance_of

    try:
        payload = json.loads(tool_result)
    except (TypeError, ValueError):
        payload = None
    if (
        isinstance(payload, dict)
        and payload.get("kind") == SEARCH_RESULTS_PAYLOAD_KIND
        and isinstance(payload.get("sources"), list)
    ):
        for item in payload["sources"]:
            if not isinstance(item, dict):
                continue
            filename = str(item.get("filename") or "").strip()
            if not filename:
                continue
            # Deduplicate by filename
            if not any(s.get("filename") == filename for s in sources):
                # issue #185：relevanceScore 只喂重排绝对分（可能为 None）；
                # 工具信封里的 score 是相对排序分，落到 rankScore。
                relevance = item.get("relevanceScore")
                score = item.get("score")
                sources.append(
                    {
                        "filename": filename,
                        "relevanceScore": float(relevance) if relevance is not None else None,
                        "rankScore": float(score) if score is not None else None,
                        "rank": len(sources) + 1,
                        "content": "",
                        "documentId": str(item.get("documentId") or ""),
                        "chunkId": str(item.get("chunkId") or ""),
                        "textProvenance": provenance_of(item),
                    }
                )
        return

    # DEPRECATED（legacy plain-text tool output，保留一个 minor）：旧格式无法
    # 区分来源行与正文行（这正是 T4 的缺陷），仅作滚动升级期的兜底兼容。
    import re

    for match in re.finditer(r"^\[(\d+)\]\s+\(([\d.]+)\)\s+(.+?)$", tool_result, re.MULTILINE):
        score = float(match.group(2))
        filename = match.group(3).strip()
        # Deduplicate by filename
        if not any(s.get("filename") == filename for s in sources):
            # 旧格式的分数不可辨尺度：只作为相对排序分，不冒充相关度（#185）。
            sources.append(
                {
                    "filename": filename,
                    "relevanceScore": None,
                    "rankScore": score,
                    "rank": len(sources) + 1,
                    "content": "",
                    "documentId": "",
                    "chunkId": "",
                    "textProvenance": provenance_of({}),
                }
            )


# ============================================================
# REST endpoints
# ============================================================


@router.post(
    "/messages/{message_id}/feedback", response_model=dict, dependencies=[Depends(get_current_user)]
)
async def update_message_feedback(
    message_id: str,
    body: FeedbackRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # W0-3 / T4 补漏：反馈是**写路径**——按 id 的消息访问必须与详情/删除
    # 同一 org 口径。多组织用户切到 B 组织后不得改写 A 组织会话里消息的
    # 反馈；历史 NULL 组织会话仍可反馈（属本人）。组织条件直接进 JOIN：
    # 跨组织消息视同「不存在」（404），绝不静默写入。
    scope = await resolve_session_access_scope(current_user, db)
    try:
        stmt = (
            select(ChatMessage)
            .join(ChatSession)
            .where(
                ChatMessage.id == message_id,
                ChatSession.user_id == current_user.id,
                *session_access_org_conditions(scope),
            )
        )
        message_obj = (await db.execute(stmt)).scalar_one_or_none()
        if not message_obj:
            raise NotFoundError("消息不存在或无权评价")
        message_obj.feedback = body.feedback
        if body.note is not None:
            message_obj.feedback_note = body.note
        await db.commit()
        return {"success": True, "message": "感谢您的反馈！"}
    except NotFoundError:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Update feedback error: {e}")
        raise ValidationError(f"操作失败: {str(e)}")


@router.delete(
    "/conversations/{conversation_id}/clear",
    response_model=dict,
    dependencies=[Depends(get_current_user)],
)
async def clear_conversation_messages(
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # T4：按 id 访问与列表同一 org 口径（超管旁路 / 组织 + 历史 NULL）。
    scope = await resolve_session_access_scope(current_user, db)
    try:
        session = await load_accessible_session(db, conversation_id, current_user, scope)
        if not session:
            raise NotFoundError("会话不存在")
        delete_stmt = delete(ChatMessage).where(ChatMessage.session_id == conversation_id)
        await db.execute(delete_stmt)
        await db.commit()
        return {"success": True, "message": "会话已清空"}
    except NotFoundError:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Clear conversation error: {e}")
        raise ValidationError(f"清空失败: {str(e)}")


@router.get("/conversations", response_model=dict, dependencies=[Depends(get_current_user)])
async def get_conversations(
    page: int = 1,
    page_size: int = 20,
    search: str | None = Query(None, max_length=100, description="按标题模糊过滤"),
    # 普通默认值即为查询参数；不用 Query(None)，否则直接调用（单测）会拿到
    # Query 对象而不是 None（issue #476 评审口径）。
    parent_session_id: str | None = None,
    # issue #496：读取会话列表需要 chat:read（JWT 用户无 api_key_scopes → 全量）。
    current_user: User = Depends(require_scope("chat:read")),
    db: AsyncSession = Depends(get_db),
):
    # B2 / D3：与 /agent/sessions 共用同一套 org + user 可见范围（A2 口径：
    # 非超管 fail-closed（无组织 403），超管保留旁路）。解析放在 try 之外——
    # AuthorizationError 必须原样 403，不能被下面的 `except Exception` 包装成 400。
    org_id = await resolve_session_scope(current_user, db)
    try:
        skip = (page - 1) * page_size
        conditions = session_visibility_conditions(current_user, org_id)
        # issue #476：列表支持按母会话过滤（前端谱系/分支视图）。
        if parent_session_id:
            conditions.append(ChatSession.parent_session_id == parent_session_id)
        # B30：前端搜索框一直发送 search 参数（A_sweep CONV-2 实测），端点此前
        # 未声明该参数、被 FastAPI 静默忽略 → 列表恒全量。对标题做大小写不敏感
        # LIKE；%/_/\ 转义防止通配注入（escape 字符为反斜杠）。max_length=100 校验。
        if search and search.strip():
            pattern = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            conditions.append(ChatSession.title.ilike(f"%{pattern}%", escape="\\"))

        total_query = select(func.count(ChatSession.id)).where(*conditions)
        total = (await db.execute(total_query)).scalar() or 0

        query = (
            select(ChatSession)
            .where(*conditions)
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
            data.append(
                {
                    "id": s.id,
                    "title": s.title,
                    "uuid": s.id,
                    "isEdit": False,
                    "message_count": msg_count,
                    "last_message": last_msg_content,
                    "created_at": s.created_at.isoformat() if s.created_at else "",
                    "updated_at": s.updated_at.isoformat() if s.updated_at else "",
                    # issue #476：谱系字段（前端徽标 / 分支过滤）
                    "parent_session_id": s.parent_session_id,
                    "root_session_id": s.root_session_id or s.id,
                    "fork_point_message_id": s.fork_point_message_id,
                    "fork_reason": s.fork_reason,
                    "is_fork": s.parent_session_id is not None,
                    "is_seeded": bool(
                        isinstance(s.settings, dict)
                        and isinstance(s.settings.get("fork"), dict)
                        and s.settings["fork"].get("is_seeded")
                    ),
                }
            )

        return {
            "success": True,
            "message": "获取成功",
            "data": {"data": data, "total": total, "page": page, "page_size": page_size},
        }
    except Exception as e:
        logger.error(f"Get conversations error: {e}")
        raise ValidationError(f"获取失败: {str(e)}")


@router.post("/conversations", response_model=dict, dependencies=[Depends(get_current_user)])
async def create_conversation(
    data: ChatSessionCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        title = (data.title or "新的对话")[:80]
        conv_id = str(uuid.uuid4())
        new_session = ChatSession(
            id=conv_id,
            user_id=current_user.id,
            title=title,
            status=ChatSessionStatus.ACTIVE,
            # issue #476：根会话 = 自身（fork 子会话才指向最初会话）。
            root_session_id=conv_id,
            # T4：与按 id 访问/<agent/sessions> 同一有效组织口径（含 token
            # 组织回退），不用裸 organization_id——否则回退用户的新会话落
            # NULL，随后按「历史会话」规则对任意组织上下文可见。
            organization_id=get_user_org_id(current_user),
        )
        db.add(new_session)
        await db.commit()
        await db.refresh(new_session)

        return {
            "success": True,
            "message": "创建成功",
            "data": {
                "id": new_session.id,
                "title": new_session.title,
                "created_at": new_session.created_at.isoformat()
                if new_session.created_at
                else None,
                "updated_at": new_session.updated_at.isoformat()
                if new_session.updated_at
                else None,
                "message_count": 0,
            },
        }
    except Exception as e:
        logger.error(f"Create conversation error: {e}")
        raise ValidationError(f"创建失败: {str(e)}")


@router.put(
    "/conversations/{conversation_id}",
    response_model=dict,
    dependencies=[Depends(get_current_user)],
)
async def update_conversation(
    conversation_id: str,
    data: ChatSessionCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # T4：写路径——跨组织会话明确拒绝（404），不得静默更新。
    scope = await resolve_session_access_scope(current_user, db)
    try:
        session = await load_accessible_session(db, conversation_id, current_user, scope)
        if not session:
            raise NotFoundError("对话不存在")

        if data.title:
            session.title = data.title[:80]
        await db.commit()

        return {
            "success": True,
            "message": "更新成功",
            "data": {"id": session.id, "title": session.title},
        }
    except NotFoundError:
        raise
    except Exception as e:
        logger.error(f"Update conversation error: {e}")
        raise ValidationError(f"更新失败: {str(e)}")


@router.delete(
    "/conversations/{conversation_id}",
    response_model=dict,
    dependencies=[Depends(get_current_user)],
)
async def delete_conversation(
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # T4：跨组织会话与不存在同样处理（404），不得删除他人组织的数据行。
    scope = await resolve_session_access_scope(current_user, db)
    try:
        session = await load_accessible_session(db, conversation_id, current_user, scope)
        if not session:
            raise NotFoundError("会话不存在或无权删除")
        # 与迟到保存串行化：先锁会话行；删除提交后保存侧会看到缺失行 + 墓碑。
        await db.execute(
            select(ChatSession).where(ChatSession.id == conversation_id).with_for_update()
        )
        # Delete messages first (FK constraint), then the session
        await db.execute(delete(ChatMessage).where(ChatMessage.session_id == conversation_id))
        await db.delete(session)
        # 墓碑与硬删除同事务：任何迟到的 finalize 都不得按原 id 复活会话。
        await db.execute(
            delete(DeletedChatSession).where(DeletedChatSession.session_id == conversation_id)
        )
        db.add(
            DeletedChatSession(
                session_id=conversation_id,
                user_id=session.user_id,
                organization_id=session.organization_id,
            )
        )
        await db.commit()
        return {"success": True, "message": "会话删除成功"}
    except NotFoundError:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Delete conversation error: {e}")
        raise ValidationError(f"删除失败: {str(e)}")


@router.post(
    "/conversations/{conversation_id}/unbind-docs",
    response_model=dict,
    dependencies=[Depends(get_current_user)],
)
async def unbind_conversation_docs(
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # T4：跨组织会话明确拒绝（403），不得改写他人组织的会话设置。
    scope = await resolve_session_access_scope(current_user, db)
    try:
        session_obj = await load_accessible_session(db, conversation_id, current_user, scope)
        if not session_obj:
            raise AuthorizationError("无权访问该会话")
        settings_json = session_obj.settings or {}
        if not isinstance(settings_json, dict):
            settings_json = {}
        settings_json.pop("bound_document_ids", None)
        session_obj.settings = settings_json
        flag_modified(session_obj, "settings")
        await db.commit()
        return {"success": True, "message": "已清除会话文档绑定"}
    except (AuthorizationError, NotFoundError):
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Unbind docs error: {e}")
        raise ValidationError(f"操作失败: {str(e)}")


@router.get(
    "/conversations/{conversation_id}",
    response_model=dict,
    dependencies=[Depends(get_current_user)],
)
async def get_conversation(
    conversation_id: str,
    # issue #496：按 id 读取会话同样需要 chat:read。
    current_user: User = Depends(require_scope("chat:read")),
    db: AsyncSession = Depends(get_db),
):
    # T4：按 id 读取与列表同一 org 口径。此处先按 id + user 取行，再区分
    # 「不存在」（保留既有软失败形状）与「跨组织」（显式 404，不得静默返回
    # 其他组织会话的内容）。
    scope = await resolve_session_access_scope(current_user, db)
    try:
        stmt = select(ChatSession).where(
            ChatSession.id == conversation_id,
            ChatSession.user_id == current_user.id,
        )
        session = (await db.execute(stmt)).scalar_one_or_none()
        if not session:
            return {"success": False, "message": "无权访问"}
        if not session_matches_scope(session, scope):
            raise NotFoundError("会话不存在或无权访问")

        msg_stmt = (
            select(ChatMessage)
            .where(ChatMessage.session_id == conversation_id)
            .order_by(ChatMessage.seq.asc(), ChatMessage.created_at.asc())
        )
        messages = (await db.execute(msg_stmt)).scalars().all()

        # 安全（T3）：历史附件回填只允许回读调用者**有效组织**内的文档。
        # effective org 经 require_organization 解析（非超管与列表端点同口径；
        # 无组织超管回源库中真实存在的组织，并兼容 token_organization_id）。
        # 无组织上下文时 fail closed：只跳过附件回填，不影响历史消息本身
        # （会话归属已由 user_id 校验）。
        try:
            effective_org_id: int | None = await require_organization(current_user, db)
        except AuthorizationError:
            effective_org_id = None
            logger.warning(
                "历史附件回填跳过：调用者无组织上下文（user=%s, conversation=%s）",
                current_user.id,
                log_safe(conversation_id),
            )

        formatted_messages = []
        for msg in messages:
            msg_dict = {
                "id": msg.id,
                "content": msg.content,
                "messageType": msg.message_type.value,
                "createdAt": msg.created_at.isoformat() if msg.created_at else "",
                "sources": [],
                "feedback": msg.feedback,
                "feedbackNote": msg.feedback_note,
            }
            if msg.meta_data:
                try:
                    meta = json.loads(msg.meta_data)
                    if msg.message_type == MessageType.USER and "file_ids" in meta:
                        files_info = []
                        file_ids_list = [fid for fid in meta["file_ids"] if fid]
                        if file_ids_list and effective_org_id is not None:
                            # T3：`session` 是 ChatSession ORM 对象，不是 AsyncSession
                            # —— 此处必须用 `db`（此前 await session.execute 必抛
                            # AttributeError，被下方逐消息 except 吞掉，附件回填恒失败）。
                            # 同时按有效组织过滤：meta.file_ids 是客户端自填值，缺此
                            # 条件可回读他人组织文档的文件名（跨租户泄露面）。
                            docs_result = await db.execute(
                                select(Document).where(
                                    Document.id.in_(file_ids_list),
                                    Document.organization_id == effective_org_id,
                                )
                            )
                            doc_map = {str(doc.id): doc for doc in docs_result.scalars().all()}
                            for fid in meta["file_ids"]:
                                doc = doc_map.get(str(fid))
                                if doc:
                                    files_info.append(
                                        {
                                            "id": str(doc.id),
                                            "name": doc.filename,
                                            "status": "done",
                                        }
                                    )
                        msg_dict["files"] = files_info
                    elif msg.message_type == MessageType.ASSISTANT:
                        if "sources" in meta:
                            msg_dict["sources"] = meta["sources"]
                        if "is_cached" in meta:
                            msg_dict["isCached"] = meta["is_cached"]
                except Exception:
                    # 单条消息的脏元数据（非 JSON、file_ids 形状异常等）不得拖垮
                    # 整段历史 —— 保留逐消息隔离。但必须带堆栈留痕：T3 的
                    # `session.execute` 编程错误正是被此前仅 warning 的宽 except
                    # 静默吞掉的；logger.exception 让同类回归在日志里可见。
                    logger.exception(
                        "解析历史消息元数据失败（message=%s, conversation=%s），"
                        "该条消息的附件/来源降级为空",
                        log_safe(msg.id),
                        log_safe(conversation_id),
                    )
            formatted_messages.append(msg_dict)

        fork_meta = session.settings.get("fork") if isinstance(session.settings, dict) else None
        if not isinstance(fork_meta, dict):
            fork_meta = {}
        return {
            "success": True,
            "message": "获取成功",
            "data": {
                "id": session.id,
                "title": session.title,
                "messages": formatted_messages,
                # 文档绑定真值存于 ChatSession.settings（chat_service.bind_docs_to_session
                # 写 bound_document_ids、get_or_create_session 读回；B35）。此前会话响应
                # 不含 settings —— 前端 isBoundMode 恒 false，「仅文档模式」标签与解绑
                # 入口永不出现。此处如实下发（非 dict 时给 {}）。
                "settings": session.settings if isinstance(session.settings, dict) else {},
                # issue #476：谱系元数据（前端消息级分叉入口 / 谱系徽标）
                "parent_session_id": session.parent_session_id,
                "root_session_id": session.root_session_id or session.id,
                "fork_point_message_id": session.fork_point_message_id,
                "fork_reason": session.fork_reason,
                "is_fork": session.parent_session_id is not None,
                "is_seeded": bool(fork_meta.get("is_seeded")),
                "created_at": session.created_at.isoformat() if session.created_at else "",
                "updated_at": session.updated_at.isoformat() if session.updated_at else "",
            },
        }
    except NotFoundError:
        # T4：跨组织访问显式 404，不得被下面的宽 except 吞成 200 软失败。
        raise
    except Exception as e:
        # T3：此前这里静默返回失败文案、服务端零留痕。补上堆栈日志，
        # 避免端点级的编程错误再次无声无息。
        logger.exception("Get conversation error")
        return {"success": False, "message": f"获取失败: {str(e)}"}


# ============================================================
# WebSocket endpoint
# ============================================================


class ConnectionManager:
    def __init__(self):
        self.active_connections: dict[int, list[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, user_id: int, subprotocol: str | None = None):
        # 原样回显客户端请求的 auth.* 子协议（issue #258）：浏览器在"请求了子协议
        # 而 101 响应未回显"时强制判定握手失败（close 1006）。提取不到时传 None，
        # 与裸 accept() 语义一致。
        await websocket.accept(subprotocol=subprotocol)
        self.active_connections.setdefault(user_id, []).append(websocket)

    def disconnect(self, websocket: WebSocket):
        for uid, conns in list(self.active_connections.items()):
            if websocket in conns:
                conns.remove(websocket)
                if not conns:
                    del self.active_connections[uid]
                break

    async def send_personal_message(self, message: str, websocket: WebSocket):
        await websocket.send_text(message)

    async def broadcast_to_user(self, user_id: int, message: str):
        for ws in self.active_connections.get(user_id, []):
            await ws.send_text(message)


manager = ConnectionManager()


@router.get("/metrics", response_model=dict)
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
    conversation_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    # 安全加固：握手阶段必须查库。JWT 只证明令牌本身有效——用户被删除或禁用后
    # 仍可能持有未过期的令牌，此时不得建立连接（见 issue #82）。
    user = await authenticate_ws(websocket, db)
    if user is None:
        return
    user_id = user.id

    # 原样回显客户端请求的 auth.* 子协议（issue #258）：不回显时浏览器强制判定
    # 握手失败（close 1006）。提取不到时保持裸 accept() 语义。
    subprotocol = extract_ws_subprotocol(websocket)
    await manager.connect(websocket, user_id, subprotocol)

    # 安全加固：跟踪当前 pipeline 任务，客户端断连时取消，防止孤儿任务继续烧 LLM 费用
    pipeline_task: asyncio.Task | None = None

    try:
        while True:
            data = await websocket.receive_text()
            try:
                msg_data = json.loads(data)
                user_content = msg_data.get("content", "")
                current_conv_id = msg_data.get("conversationId") or conversation_id
                file_ids = msg_data.get("fileIds", [])
                payload_data = msg_data.get("payload", {})
                # B3：payload.systemPrompt 是历史死字段（后端从未读取，前端已停发）。
                # 旧客户端若仍携带，继续忽略；勿接线。
                strict_mode = payload_data.get("strictMode", False)
                privacy_mode = payload_data.get("privacyMode", True)
                # 重新生成走与 SSE 相同的原地替换契约；内容以库中 user 行为准。
                regenerate = bool(msg_data.get("regenerate", False))

                if not user_content and not regenerate:
                    continue

                async with AsyncSessionLocal() as session:
                    # 安全加固：每条消息都重新校验用户状态——连接建立后用户被删除
                    # 或禁用时，既不回退组织继续跑 RAG，也不静默降级，直接断开。
                    user_state = await load_active_ws_user(session, user_id)
                    if user_state is None:
                        logger.warning(f"WebSocket 用户 {user_id} 已被删除或禁用，关闭连接")
                        await close_ws(websocket, WS_CLOSE_AUTH_FAILED, WS_REASON_USER_INVALID)
                        return

                    # 安全加固（issue #268）：此前 `or 1` 把无组织用户的检索静默放进
                    # 1 号组织（语义缓存与 RAG 检索双重命中原生组织数据）。现在如实
                    # 传 None，由 run_rag_pipeline 显式拒绝（不检索、不碰缓存）。
                    # W0-3 注记：WS 逐消息只复查库中用户状态（issue #82），**不**携带
                    # token 组织回退（与 SSE RAG 分支的 get_user_org_id 有效组织口径
                    # 有意不同）：无主组织用户在这里保持 fail-closed 显式拒绝，绝不放宽
                    # 到未在消息级复查成员关系的 token 组织。该差异由
                    # tests/api/test_w0_3_session_feedback_org.py::TestWebSocketOrgSemantics
                    # 锁定。
                    search_org_id = user_state.organization_id
                    org_id = user_state.organization_id or None

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
                            regenerate=regenerate,
                        )
                    )

                    # Drain events and send via WebSocket
                    while True:
                        try:
                            event: RAGEvent = await asyncio.wait_for(queue.get(), timeout=0.1)
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
                                    json.dumps(payload),
                                    websocket,
                                )
                            elif event.type == "message":
                                await manager.send_personal_message(
                                    json.dumps(
                                        {
                                            "type": "message",
                                            "content": event.content,
                                            "conversationId": event.conversation_id,
                                            "messageId": event.message_id,
                                            "sources": event.sources,
                                            "is_cached": event.is_cached,
                                        }
                                    ),
                                    websocket,
                                )
                            elif event.type == "warning":
                                # 回答已生成但落库失败：如实告知，不把未保存说成已保存。
                                await manager.send_personal_message(
                                    json.dumps(
                                        {
                                            "type": "warning",
                                            "content": event.content,
                                            "conversationId": event.conversation_id,
                                        },
                                        ensure_ascii=False,
                                    ),
                                    websocket,
                                )
                            elif event.type == "error":
                                # issue #268：RAG 链路的显式拒绝（如无组织）经此事件
                                # 转发。此前无此 case，错误事件会被静默丢弃、客户端
                                # 只等到一个无回应的空转。
                                # W0-5：RAG error 内容同样过白名单（与 SSE 同口径）。
                                await manager.send_personal_message(
                                    json.dumps(
                                        {
                                            "type": "error",
                                            "content": public_rag_error_content(event.content),
                                            "conversationId": event.conversation_id,
                                        }
                                    ),
                                    websocket,
                                )
                        except TimeoutError:
                            if pipeline_task.done():
                                # Drain remaining events
                                while not queue.empty():
                                    event = queue.get_nowait()
                                    if event.type == "message":
                                        await manager.send_personal_message(
                                            json.dumps(
                                                {
                                                    "type": "message",
                                                    "content": event.content,
                                                    "conversationId": event.conversation_id,
                                                    "messageId": event.message_id,
                                                    "sources": event.sources,
                                                    "is_cached": event.is_cached,
                                                }
                                            ),
                                            websocket,
                                        )
                                    elif event.type == "warning":
                                        await manager.send_personal_message(
                                            json.dumps(
                                                {
                                                    "type": "warning",
                                                    "content": event.content,
                                                    "conversationId": event.conversation_id,
                                                },
                                                ensure_ascii=False,
                                            ),
                                            websocket,
                                        )
                                    elif event.type == "error":
                                        await manager.send_personal_message(
                                            json.dumps(
                                                {
                                                    "type": "error",
                                                    "content": public_rag_error_content(
                                                        event.content
                                                    ),
                                                    "conversationId": event.conversation_id,
                                                }
                                            ),
                                            websocket,
                                        )
                                break

                    await pipeline_task

            except json.JSONDecodeError:
                await manager.send_personal_message(
                    json.dumps({"type": "error", "content": "无效的JSON格式"}),
                    websocket,
                )
            except Exception as e:
                logger.error(f"Error processing message: {e}", exc_info=True)
                await manager.send_personal_message(
                    json.dumps({"type": "error", "content": "服务器处理出错"}),
                    websocket,
                )

    except WebSocketDisconnect:
        pass
    finally:
        # 安全加固：客户端断连时取消正在运行的 pipeline 任务（LLM 流/DB 会话随之释放）；
        # 服务端主动关闭（认证失效）同样走这条收尾路径，连接表不会残留死连接。
        if pipeline_task is not None and not pipeline_task.done():
            pipeline_task.cancel()
            try:
                await asyncio.gather(pipeline_task, return_exceptions=True)
            except Exception:
                logger.warning("Cancelling orphan pipeline task failed", exc_info=True)
        manager.disconnect(websocket)


# ============================================================
# SSE endpoint
# ============================================================


@router.post("/parse-upload")
async def chat_parse_upload(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    上传文件并立即解析文本内容，用于聊天上下文。
    不经过完整的 RAG 链路（不向量化、不索引 ES），直接返回纯文本。
    """
    import os
    import tempfile
    from pathlib import Path

    from app.services.document_parser import document_service

    # W0-6：解析结果必须携带真实组织上下文。无组织用户显式 403（与检索链路
    # 的 require_organization 口径一致），不再把空串 / "0" 传给解析器。
    org_id = await require_organization(current_user, db)

    if not file.filename:
        raise ValidationError("文件名不能为空")

    ext = Path(file.filename).suffix.lower() or ".tmp"
    fd, temp_path = tempfile.mkstemp(suffix=ext)
    os.close(fd)

    try:
        file.file.seek(0, 2)
        declared_size = file.file.tell()
        await file.seek(0)
        if declared_size > settings.MAX_FILE_SIZE:
            raise ValidationError(f"文件过大，最大支持 {settings.MAX_FILE_SIZE // (1024 * 1024)}MB")

        raw = await file.read()
        with open(temp_path, "wb") as f:
            f.write(raw)

        result = await document_service.parse_local_file(
            temp_path,
            organization_id=str(org_id),
        )

        chunks = result.get("chunks", [])
        full_text = (
            "\n".join(c["chunk_text"] for c in chunks) if chunks else result.get("content", "")
        )

        return {
            "success": True,
            "data": {
                "filename": file.filename,
                "content": full_text,
                "chunk_count": len(chunks),
                "file_size": result.get("file_size", 0),
            },
        }
    except AppError:
        # issue #98：ValidationError（如文件过大 → 400）等控制流异常自带正确状态码，
        # 先放行；否则会被下面的 except Exception 重新包装成 500。
        raise
    except Exception as e:
        logger.error(f"[parse-upload] 解析失败: {e}", exc_info=True)
        raise AppError(f"文件解析失败: {str(e)}")
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


@router.post("/stream")
async def chat_stream_endpoint(
    body: ChatStreamRequest,
    request: Request,
    current_user: User = Depends(require_scope("chat:write")),
    db: AsyncSession = Depends(get_db),
):
    async def event_generator():
        # 安全加固：跟踪 pipeline 任务；断连时按 SSE_KEEPALIVE_ON_DISCONNECT 决定
        # 立即取消还是保留到 SSE_ORPHAN_TTL_SECONDS（issue #480 §5.2）。
        pipeline_task: asyncio.Task | None = None
        # issue #480：所有 SSE 输出经单一编码器（event/id/data），流键优先
        # conversationId，无会话时由任务 id 兜底（此处用请求级 uuid）。
        stream_id = body.conversationId or str(uuid.uuid4())
        emitter = SSEStream(stream_id, scope=f"u{current_user.id}")
        # W0-5 错误脱敏：请求编号优先取 W0-9 的 get_request_id contextvar，
        # 回退到既有 RequestIDMiddleware 写入的 request.state（不另造中间件）。
        # 完整异常/堆栈只进日志，客户端只见通用文案。
        request_id = resolve_request_id(request)
        try:
            # issue #508（方案 B）：深度分析入口已迁至 /agent 工作台。旧客户端
            # 仍发 useAgent=true 时显式拒绝（错误码 + 跳转提示），绝不静默进入
            # 无审批 UI 的 Agent 链路，也不静默降级为 RAG。放在续传处理之前：
            # 旧客户端的重连同样得到明确指引，而不是回放一条它无法完整消费的流。
            if DEEP_ANALYSIS_GUARD_ENABLED and body.useAgent:
                yield await emitter.emit(
                    "error",
                    {
                        "code": "DEEP_ANALYSIS_MOVED_TO_AGENT",
                        "content": "深度分析已移至 Agent 工作台（支持审批/回放），请前往 /agent 使用",
                        "redirect": "/agent",
                    },
                )
                return

            # issue #480 §5.2：Last-Event-ID 续传。B2：回放前必须先做会话归属
            # 校验——buffer 键只含会话 id，会话 UUID 不是授权凭证。
            last_event_id = request.headers.get("Last-Event-ID")
            if last_event_id:
                if body.conversationId:
                    replay_scope = await resolve_session_access_scope(current_user, db)
                    accessible = await load_accessible_session(
                        db, body.conversationId, current_user, replay_scope
                    )
                    if accessible is None:
                        yield format_sse("error", {"content": "会话不存在或无权访问"})
                        return
                frames, gone = await emitter.replay(last_event_id)
                if gone:
                    yield format_sse("error", {"code": "SSE_RESUME_GONE"})
                    return
                # B4：续传请求只回放缓冲事件，绝不重跑 pipeline
                # （避免重复回答、重复落库与双份 LLM 成本）。
                for frame in frames:
                    yield frame
                return

            user_content = body.content
            file_ids = body.fileIds or []
            payload_data = body.payload or {}
            # B3：payload.systemPrompt 是历史死字段（后端从未读取，前端已停发）。
            # 旧客户端若仍携带，继续忽略；勿接线。
            strict_mode = payload_data.get("strictMode", False)
            privacy_mode = payload_data.get("privacyMode", True)
            # 重新生成：只允许对现有会话的最新一轮原地重写；解析/权限由
            # run_rag_pipeline 统一执行（SSE/WS 共用同一契约）。
            regenerate = bool(payload_data.get("regenerate", False))

            if not user_content:
                yield await emitter.emit("error", {"content": "消息内容不能为空"})
                return

            # ── Agent mode: delegate to PER agent ──────────────────────────
            if body.useAgent:
                from app.agent.config import AgentConfig
                from app.agent.service import agent_service

                # B2 / D3：会话入口合并——聊天页的「深度分析（Agent）」与 /agent/chat
                # 同源：未带 conversationId 时生成并随流回传（刷新后按 id 可寻回），
                # 走完 PER 后统一落库（与 /agent 同一实现），刷新不丢本轮问答。
                conversation_id = body.conversationId or stream_id
                persist_org_id = get_user_org_id(current_user)

                # User Commands（issue #475）：与 /agent/chat 同源短路——"/" 前缀的
                # 文本走命令通道，绝不作为 prompt 漏给模型、不落库、不进入 PER。
                # 命令通道按多租户 fail-closed 要求真实组织（无组织显式拒绝，
                # 与普通聊天对本入口的宽松语义有意不同）。
                if settings.COMMANDS_ENABLED and user_content.strip().startswith("/"):
                    from app.agent.commands import (
                        CommandContext,
                        command_registry,
                        iter_command_events,
                        resolve_is_admin,
                        resolve_session_scope_for_command,
                    )

                    try:
                        command_org_id = await require_organization(current_user, db)
                    except AuthorizationError as org_err:
                        error_text = org_err.message
                        yield await emitter.emit(
                            "command_error",
                            {"content": error_text, "type": "command_error"},
                        )
                        yield await emitter.emit(
                            "message",
                            {
                                "content": error_text,
                                "conversationId": conversation_id,
                                "messageId": f"command-{uuid.uuid4()}",
                                "sources": None,
                                "is_cached": False,
                            },
                        )
                        return

                    # 纵深防御：命令作用域不得使用调用者不可见的会话 id（与
                    # POST /commands/execute 同口径）。
                    session_ok, _session_scope = await resolve_session_scope_for_command(
                        db, current_user, body.conversationId
                    )
                    if not session_ok:
                        error_text = "会话不存在或无权访问"
                        yield await emitter.emit(
                            "command_error",
                            {
                                "content": error_text,
                                "type": "command_error",
                                "errorCode": "session_not_found",
                            },
                        )
                        yield await emitter.emit(
                            "message",
                            {
                                "content": error_text,
                                "conversationId": conversation_id,
                                "messageId": f"command-{uuid.uuid4()}",
                                "sources": None,
                                "is_cached": False,
                            },
                        )
                        return

                    if file_ids:
                        # 命令通道不进入模型、不处理附件——如实提示，绝不静默忽略。
                        yield await emitter.emit(
                            "warning",
                            {"content": "命令通道不处理附件，本次附件已忽略"},
                        )
                    command_ctx = CommandContext(
                        user_id=current_user.id,
                        organization_id=command_org_id,
                        session_id=body.conversationId,
                        is_admin=await resolve_is_admin(db, current_user, command_org_id),
                        is_superuser=bool(getattr(current_user, "is_superuser", False)),
                        db=db,
                    )
                    command_content = ""
                    async for command_event in iter_command_events(
                        command_ctx, user_content, registry=command_registry
                    ):
                        yield await emitter.emit(command_event.type, command_event.to_sse_dict())
                        if command_event.type in ("command_result", "command_error"):
                            command_content = command_event.content
                    # 既有消息通道口径：聊天页现有渲染只认 message 事件——命令结果
                    # 同时以 message 外发（command_* 事件供新客户端消费）。
                    yield await emitter.emit(
                        "message",
                        {
                            "content": command_content,
                            "conversationId": conversation_id,
                            "messageId": f"command-{uuid.uuid4()}",
                            "sources": None,
                            "is_cached": False,
                        },
                    )
                    return

                # issue #480 §5.4：REST 侧幂等——与 JSON-RPC session/prompt 同键
                # （rpc:msg:{session}:{clientMessageId}，TTL 24h）。重复请求直接
                # 返回同一 messageId 的 done，不产生第二条用户消息、不重跑 LLM。
                client_message_id = payload_data.get("clientMessageId")
                if client_message_id:
                    dedup_key = f"rest:msg:{conversation_id}:{client_message_id}"
                    dedup_payload = json.dumps(
                        {
                            "messageId": f"agent-{conversation_id}",
                            "sessionId": conversation_id,
                        }
                    )
                    try:
                        created = await redis_client.set(
                            dedup_key, dedup_payload, nx=True, ex=86400
                        )
                        if created is None:
                            yield await emitter.emit(
                                "done",
                                {
                                    "type": "done",
                                    "content": "",
                                    "conversationId": conversation_id,
                                    "messageId": f"agent-{conversation_id}",
                                    "duplicate": True,
                                },
                            )
                            return
                    except Exception as dedup_exc:  # noqa: BLE001 - 幂等是增强
                        logger.warning("chat idempotency unavailable: %s", dedup_exc)

                config = AgentConfig(
                    enable_tools=True,
                    enable_planning=True,
                    enable_reflection=True,
                    enable_memory=True,
                    enable_thinking=payload_data.get("enableThinking", True),
                )
                full_response = ""
                agent_sources: list[dict] = []
                # 修复：agent 模式下加载会话历史，支持多轮对话（原实现恒传空 history）
                agent_history: list[dict[str, str]] = []
                if body.conversationId:
                    try:
                        from app.api.v1.endpoints.agent import _load_session_messages

                        # T4：历史加载与落库同一 org 口径（超管保持旁路）。
                        history_scope = SessionAccessScope(
                            bypass_org=bool(getattr(current_user, "is_superuser", False)),
                            org_id=persist_org_id,
                        )
                        agent_history = await _load_session_messages(
                            body.conversationId, current_user.id, history_scope
                        )
                    except Exception as hist_err:
                        logger.warning(f"加载 agent 会话历史失败（继续无历史对话）: {hist_err}")
                last_event_type = ""
                async for agent_event in agent_service.chat(
                    query=user_content,
                    history=agent_history,
                    # 安全加固（issue #257）：此前 `... or 1` 会把无组织用户静默放进
                    # 1 号组织的 agent 上下文（1 号组织甚至未必存在）。现在如实传
                    # None：休闲对话不受影响，需要组织上下文的工具由注册表 auth
                    # 钩子 fail-closed（与 knowledge.py 检索路径 403 加固同语义）。
                    organization_id=persist_org_id,
                    user_id=current_user.id,
                    session_id=conversation_id,
                    config=config,
                ):
                    last_event_type = agent_event.type
                    if agent_event.type == "chunk":
                        full_response += agent_event.content
                        yield await emitter.emit(
                            "chunk",
                            {
                                "content": agent_event.content,
                                "conversationId": conversation_id,
                                "messageId": f"agent-{conversation_id}",
                            },
                        )
                    elif agent_event.type == "thinking":
                        # W0-5：thinking 可能承载重试上下文（上一条错误的原文），
                        # 外发前做内部特征脱敏（traceback/路径/SQL/类名）。
                        yield await emitter.emit(
                            "thinking",
                            {
                                "content": public_diagnostic_content(agent_event.content),
                                "thinkingType": agent_event.thinking_type,
                                "planId": agent_event.plan_id,
                                "planStepId": agent_event.plan_step_id,
                                "planProgress": agent_event.plan_progress
                                if agent_event.plan_progress >= 0
                                else None,
                            },
                        )
                    elif agent_event.type == "tool_call":
                        yield await emitter.emit(
                            "tool_call",
                            {
                                "toolName": agent_event.tool_name,
                                "toolArgs": agent_event.tool_args,
                                "toolCallId": agent_event.tool_call_id,
                                "planId": agent_event.plan_id,
                                "planStepId": agent_event.plan_step_id,
                            },
                        )
                    elif agent_event.type == "tool_result":
                        yield await emitter.emit(
                            "tool_result",
                            {
                                "toolName": agent_event.tool_name,
                                "toolCallId": agent_event.tool_call_id,
                                "content": agent_event.content[:500],
                                "toolDurationMs": agent_event.tool_duration_ms,
                                "planStepId": agent_event.plan_step_id,
                                "planProgress": agent_event.plan_progress
                                if agent_event.plan_progress >= 0
                                else None,
                            },
                        )
                        if agent_event.tool_name in ("search_knowledge_base", "vector_search"):
                            _extract_agent_sources(agent_event.content, agent_sources)
                    elif agent_event.type == "tool_error":
                        # W0-5：工具失败文本按白名单/映射外发（原始异常与内部
                        # 类名只进日志；工具名属于可对外上下文）。
                        yield await emitter.emit(
                            "tool_error",
                            {
                                "toolName": agent_event.tool_name,
                                "content": public_agent_event_content(
                                    "tool_error",
                                    agent_event.content,
                                    agent_event.tool_name,
                                )[:300],
                                "toolCallId": agent_event.tool_call_id,
                            },
                        )
                    elif agent_event.type == "plan_start":
                        yield await emitter.emit(
                            "plan_start",
                            {
                                "planId": agent_event.plan_id,
                                "content": agent_event.content,
                            },
                        )
                    elif agent_event.type == "plan_step":
                        yield await emitter.emit(
                            "plan_step",
                            {
                                "planId": agent_event.plan_id,
                                "planStepId": agent_event.plan_step_id,
                                "content": agent_event.content,
                                "toolHint": agent_event.tool_hint,
                                "dependencies": agent_event.dependencies,
                            },
                        )
                    elif agent_event.type == "plan_complete":
                        yield await emitter.emit(
                            "plan_complete",
                            {
                                "planId": agent_event.plan_id,
                                "content": agent_event.content,
                            },
                        )
                    elif agent_event.type == "reflection":
                        yield await emitter.emit(
                            "reflection",
                            {
                                "content": agent_event.content,
                                "result": agent_event.reflection_result,
                            },
                        )
                    elif agent_event.type == "done":
                        pass
                    elif agent_event.type == "error":
                        # W0-5：error 事件内容过白名单——无组织拒绝等既有诚实
                        # 文案放行；含原始异常/内部类名的内容降级为通用文案。
                        yield await emitter.emit(
                            "error",
                            {
                                "content": public_agent_event_content("error", agent_event.content),
                            },
                        )
                        return
                    elif agent_event.type == "execution_context":
                        # issue #222：PER 循环收尾时真的会发出它，此前无 case 被静默丢弃。
                        # 该事件带结构性字段，投影风格与上面各分支保持一致。
                        # W0-5：诊断载荷里的失败/异常片段脱敏后再外发。
                        yield await emitter.emit(
                            "execution_context",
                            {
                                "content": public_diagnostic_content(agent_event.content),
                                "planId": agent_event.plan_id,
                                "planProgress": agent_event.plan_progress
                                if agent_event.plan_progress >= 0
                                else None,
                            },
                        )
                    else:
                        # issue #222：这里是白名单式 if/elif，缺 case 的事件（如
                        # run_report——PER 循环真的会发出）会**静默消失**，而 /agent 出口
                        # 是全量转发的——同一个 run 从两个入口看到的流不一致。未知类型
                        # 统一原样转发 content，客户端按需忽略（SSE 按 event 名分发，
                        # 没有监听器的类型是安全的）；宁可在流里多一个没人听的事件，
                        # 也不要静默丢数据。
                        # W0-5：诊断类事件（run_report 等）可能内嵌失败/异常文本，
                        # 外发前做内部特征脱敏（结构与可观测字段保持不变）。
                        logger.debug("chat SSE 转发未投影的事件类型: %s", agent_event.type)
                        content = agent_event.content
                        if agent_event.type in (
                            "run_report",
                            "execution_context",
                            "execution_step_result",
                        ):
                            content = public_diagnostic_content(content)
                        yield await emitter.emit(agent_event.type, {"content": content})

                # B2 / D3：统一会话落库（与 /agent/chat 同一实现）。HITL 暂停/取消
                # 不是"完成"——不写半成品回答（真完成由审批/恢复流收尾时补写）。
                # 落库失败不静默（issue #301 批 D 口径）：warning 事件如实告知，
                # 回答本身照常完成。
                save_error: str | None = None
                if last_event_type not in (
                    "approval_required",
                    "run_cancelled",
                    "plan_approval_required",
                ):
                    try:
                        saved_id = await save_session_interaction(
                            conversation_id,
                            current_user.id,
                            user_content,
                            persist_org_id,
                            full_response,
                            bypass_org=bool(getattr(current_user, "is_superuser", False)),
                        )
                        if full_response and saved_id is None:
                            # 会话已删除/无权写：不得静默当作已保存；空回复不是失败。
                            save_error = "session_not_saved"
                    except Exception as save_exc:
                        save_error = log_safe(save_exc)
                        logger.error(
                            "Failed to persist chat agent interaction (session=%s user=%s): %s",
                            conversation_id,
                            current_user.id,
                            save_error,
                            exc_info=True,
                        )
                if save_error is not None:
                    yield await emitter.emit(
                        "warning",
                        {
                            "content": "回答已生成，但会话记录保存失败，刷新后本轮对话可能不可见",
                        },
                    )

                yield await emitter.emit(
                    "message",
                    {
                        "content": full_response,
                        "conversationId": conversation_id,
                        "messageId": f"agent-{conversation_id}",
                        "sources": agent_sources if agent_sources else None,
                        "is_cached": False,
                    },
                )
                return

            # ── RAG mode (original) ────────────────────────────────────────
            async with AsyncSessionLocal() as session:
                # 安全加固（issue #268 / W0-3）：会话落库与检索使用**有效组织**
                # （get_user_org_id 含 token 组织回退）。修前这里用裸
                # current_user.organization_id 而 search_org_id 取库中主组织：
                # 对 token 组织回退用户（库中主组织为空）二者皆为 None，RAG 在
                # run_rag_pipeline 入口即被显式拒绝——本次与 /agent/chat、
                # create_conversation 的有效组织口径对齐，使这类用户按其已校验的
                # token 组织正常问答。无组织如实传 None，仍由 run_rag_pipeline
                # 显式拒绝（不检索、不碰缓存）。
                effective_org_id = get_user_org_id(current_user)
                search_org_id = effective_org_id
                org_id = effective_org_id

                queue: asyncio.Queue = asyncio.Queue()

                pipeline_task = asyncio.create_task(
                    run_rag_pipeline(
                        session=session,
                        user_content=user_content,
                        user_id=current_user.id,
                        search_org_id=search_org_id,
                        organization_id=org_id,
                        conversation_id=body.conversationId,
                        file_ids=file_ids,
                        strict_mode=strict_mode,
                        privacy_mode=privacy_mode,
                        event_queue=queue,
                        debug=body.debug,
                        regenerate=regenerate,
                    )
                )

                # Drain events and convert to SSE
                while True:
                    try:
                        event: RAGEvent = await asyncio.wait_for(queue.get(), timeout=0.1)
                        if event.type == "debug" and event.debug_data:
                            yield await emitter.emit(
                                "debug",
                                {
                                    "data": event.debug_data,
                                    "conversationId": event.conversation_id,
                                },
                            )
                        elif event.type == "chunk":
                            sse_data: dict = {
                                "content": event.content,
                                "conversationId": event.conversation_id,
                                "messageId": event.message_id,
                            }
                            if event.sources:
                                sse_data["sources"] = event.sources
                            yield await emitter.emit("chunk", sse_data)
                        elif event.type == "message":
                            yield await emitter.emit(
                                "message",
                                {
                                    "content": event.content,
                                    "conversationId": event.conversation_id,
                                    "messageId": event.message_id,
                                    "sources": event.sources,
                                    "is_cached": event.is_cached,
                                },
                            )
                        elif event.type == "warning":
                            # 回答已生成但落库失败：如实告知，不静默。
                            yield await emitter.emit(
                                "warning",
                                {
                                    "content": event.content,
                                    "conversationId": event.conversation_id,
                                },
                            )
                        elif event.type == "error":
                            # issue #268：RAG 链路的显式拒绝（如无组织）经此事件
                            # 转发（与 WS 路径同一语义）。此前无此 case，错误会被
                            # 静默丢弃。W0-5：内容过 RAG 白名单（白名单外通用文案）。
                            yield await emitter.emit(
                                "error",
                                {
                                    "content": public_rag_error_content(event.content),
                                },
                            )
                    except TimeoutError:
                        if pipeline_task.done():
                            # Drain remaining events
                            while not queue.empty():
                                event = queue.get_nowait()
                                if event.type == "message":
                                    yield await emitter.emit(
                                        "message",
                                        {
                                            "content": event.content,
                                            "conversationId": event.conversation_id,
                                            "messageId": event.message_id,
                                            "sources": event.sources,
                                            "is_cached": event.is_cached,
                                        },
                                    )
                                elif event.type == "warning":
                                    yield await emitter.emit(
                                        "warning",
                                        {
                                            "content": event.content,
                                            "conversationId": event.conversation_id,
                                        },
                                    )
                                elif event.type == "error":
                                    yield await emitter.emit(
                                        "error",
                                        {
                                            "content": public_rag_error_content(event.content),
                                        },
                                    )
                            break

                await pipeline_task

        except Exception as e:
            # W0-5：完整异常/堆栈只进服务端日志（带 request_id，供排障关联）；
            # 客户端只收映射后的用户可读文案——绝不出现 str(e)、文件路径、SQL、
            # 堆栈或内部类名。无组织等可预期拒绝映射为 403 语义文案；其余未预期
            # 异常为通用文案 + 请求编号。日志不记录 org。
            logger.error(
                "SSE stream error (request_id=%s user=%s): %s",
                log_safe(request_id),
                # codeql[py/clear-text-logging-sensitive-data] 数值审计 ID 非凭据
                current_user.id,
                log_safe(e),
                exc_info=True,
            )
            yield await emitter.emit(
                "error",
                {
                    "content": public_exception_message(e, request_id),
                },
            )
        finally:
            # 该 finally 只在生成器真正被关闭时执行：正常完成、或 keepalive
            # 包装器在 TTL 到期/开关关闭时取消 pump 后（issue #480 §5.2）。
            if pipeline_task is not None and not pipeline_task.done():
                pipeline_task.cancel()
                try:
                    await asyncio.gather(pipeline_task, return_exceptions=True)
                except Exception:
                    logger.warning("Cancelling orphan SSE pipeline task failed", exc_info=True)

    orphan_ttl = (
        int(settings.SSE_ORPHAN_TTL_SECONDS) if settings.SSE_KEEPALIVE_ON_DISCONNECT else None
    )
    return StreamingResponse(
        keepalive_stream(event_generator(), ttl_seconds=orphan_ttl),
        media_type="text/event-stream",
    )
