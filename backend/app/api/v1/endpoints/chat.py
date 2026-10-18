import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, Depends, File, Query, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.core.config import settings
from app.core.database import AsyncSessionLocal, get_db
from app.core.security import get_current_user
from app.core.ws_auth import (
    WS_CLOSE_AUTH_FAILED,
    WS_REASON_USER_INVALID,
    authenticate_ws,
    close_ws,
    extract_ws_subprotocol,
    load_active_ws_user,
)
from app.exceptions import AppError, AuthorizationError, NotFoundError, ValidationError
from app.models.chat import ChatMessage, ChatSession, ChatSessionStatus, MessageType
from app.models.document import Document
from app.models.user import User
from app.schemas.chat import ChatSessionCreate, ChatStreamRequest, FeedbackRequest
from app.services.chat_service import RAGEvent, run_rag_pipeline
from app.services.rag_service import rag_service

router = APIRouter()
logger = logging.getLogger(__name__)


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
                sources.append({
                    "filename": filename,
                    "relevanceScore": float(item.get("score") or 0.0),
                    "content": "",
                    "documentId": str(item.get("documentId") or ""),
                })
        return

    # DEPRECATED（legacy plain-text tool output，保留一个 minor）：旧格式无法
    # 区分来源行与正文行（这正是 T4 的缺陷），仅作滚动升级期的兜底兼容。
    import re
    for match in re.finditer(r"^\[(\d+)\]\s+\(([\d.]+)\)\s+(.+?)$", tool_result, re.MULTILINE):
        score = float(match.group(2))
        filename = match.group(3).strip()
        # Deduplicate by filename
        if not any(s.get("filename") == filename for s in sources):
            sources.append({
                "filename": filename,
                "relevanceScore": score,
                "content": "",
                "documentId": "",
            })


# ============================================================
# REST endpoints
# ============================================================


@router.post("/messages/{message_id}/feedback", response_model=dict, dependencies=[Depends(get_current_user)])
async def update_message_feedback(
    message_id: str,
    body: FeedbackRequest,
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


@router.delete("/conversations/{conversation_id}/clear", response_model=dict, dependencies=[Depends(get_current_user)])
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
    page: int = 1, page_size: int = 20,
    search: str | None = Query(None, max_length=100, description="按标题模糊过滤"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        skip = (page - 1) * page_size
        if current_user.organization_id:
            org_filter = or_(
                ChatSession.organization_id == current_user.organization_id,
                ChatSession.organization_id.is_(None),
            )
        else:
            org_filter = ChatSession.organization_id.is_(None)

        conditions = [
            ChatSession.user_id == current_user.id,
            ChatSession.status != ChatSessionStatus.ENDED,
            org_filter,
        ]
        # B30：前端搜索框一直发送 search 参数（A_sweep CONV-2 实测），端点此前
        # 未声明该参数、被 FastAPI 静默忽略 → 列表恒全量。对标题做大小写不敏感
        # LIKE；%/_/\ 转义防止通配注入（escape 字符为反斜杠）。max_length=100 校验。
        if search and search.strip():
            pattern = (
                search.strip()
                .replace("\\", "\\\\")
                .replace("%", "\\%")
                .replace("_", "\\_")
            )
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
            organization_id=current_user.organization_id,
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
                "created_at": new_session.created_at.isoformat() if new_session.created_at else None,
                "updated_at": new_session.updated_at.isoformat() if new_session.updated_at else None,
                "message_count": 0,
            },
        }
    except Exception as e:
        logger.error(f"Create conversation error: {e}")
        raise ValidationError(f"创建失败: {str(e)}")


@router.put("/conversations/{conversation_id}", response_model=dict, dependencies=[Depends(get_current_user)])
async def update_conversation(
    conversation_id: str,
    data: ChatSessionCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        stmt = select(ChatSession).where(
            ChatSession.id == conversation_id,
            ChatSession.user_id == current_user.id,
        )
        result = await db.execute(stmt)
        session = result.scalar_one_or_none()
        if not session:
            raise NotFoundError("对话不存在")

        if data.title:
            session.title = data.title[:80]
        await db.commit()

        return {"success": True, "message": "更新成功", "data": {"id": session.id, "title": session.title}}
    except NotFoundError:
        raise
    except Exception as e:
        logger.error(f"Update conversation error: {e}")
        raise ValidationError(f"更新失败: {str(e)}")


@router.delete("/conversations/{conversation_id}", response_model=dict, dependencies=[Depends(get_current_user)])
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
            raise NotFoundError("会话不存在或无权删除")
        # Delete messages first (FK constraint), then the session
        await db.execute(
            delete(ChatMessage).where(ChatMessage.session_id == conversation_id)
        )
        await db.delete(session)
        await db.commit()
        return {"success": True, "message": "会话删除成功"}
    except NotFoundError:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Delete conversation error: {e}")
        raise ValidationError(f"删除失败: {str(e)}")


@router.post("/conversations/{conversation_id}/unbind-docs", response_model=dict, dependencies=[Depends(get_current_user)])
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


@router.get("/conversations/{conversation_id}", response_model=dict, dependencies=[Depends(get_current_user)])
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
                # 文档绑定真值存于 ChatSession.settings（chat_service.bind_docs_to_session
                # 写 bound_document_ids、get_or_create_session 读回；B35）。此前会话响应
                # 不含 settings —— 前端 isBoundMode 恒 false，「仅文档模式」标签与解绑
                # 入口永不出现。此处如实下发（非 dict 时给 {}）。
                "settings": session.settings if isinstance(session.settings, dict) else {},
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

                if not user_content:
                    continue

                async with AsyncSessionLocal() as session:
                    # 安全加固：每条消息都重新校验用户状态——连接建立后用户被删除
                    # 或禁用时，既不回退组织继续跑 RAG，也不静默降级，直接断开。
                    user_state = await load_active_ws_user(session, user_id)
                    if user_state is None:
                        logger.warning(
                            f"WebSocket 用户 {user_id} 已被删除或禁用，关闭连接"
                        )
                        await close_ws(websocket, WS_CLOSE_AUTH_FAILED, WS_REASON_USER_INVALID)
                        return

                    # 安全加固（issue #268）：此前 `or 1` 把无组织用户的检索静默放进
                    # 1 号组织（语义缓存与 RAG 检索双重命中原生组织数据）。现在如实
                    # 传 None，由 run_rag_pipeline 显式拒绝（不检索、不碰缓存）。
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
                            elif event.type == "error":
                                # issue #268：RAG 链路的显式拒绝（如无组织）经此事件
                                # 转发。此前无此 case，错误事件会被静默丢弃、客户端
                                # 只等到一个无回应的空转。
                                await manager.send_personal_message(json.dumps({
                                    "type": "error",
                                    "content": event.content,
                                    "conversationId": event.conversation_id,
                                }), websocket)
                        except TimeoutError:
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
                                    elif event.type == "error":
                                        await manager.send_personal_message(
                                            json.dumps({
                                                "type": "error",
                                                "content": event.content,
                                                "conversationId": event.conversation_id,
                                            }), websocket,
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
):
    """
    上传文件并立即解析文本内容，用于聊天上下文。
    不经过完整的 RAG 链路（不向量化、不索引 ES），直接返回纯文本。
    """
    import os
    import tempfile
    from pathlib import Path

    from app.services.document_parser import document_service

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
            # A2（评审 m-5）：不再 `or 1` 伪造组织；无组织如实传空串，
            # 该值仅写入解析结果元数据，不参与任何租户数据访问。
            organization_id=str(current_user.organization_id)
            if current_user.organization_id is not None
            else "",
        )

        chunks = result.get("chunks", [])
        full_text = "\n".join(c["chunk_text"] for c in chunks) if chunks else result.get("content", "")

        return {
            "success": True,
            "data": {
                "filename": file.filename,
                "content": full_text,
                "chunk_count": len(chunks),
                "file_size": result.get("file_size", 0),
            }
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


async def sse_event(event_type: str, data: dict) -> str:
    return f"event: {event_type}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/stream")
async def chat_stream_endpoint(
    body: ChatStreamRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    async def event_generator():
        # 安全加固：跟踪 pipeline 任务，客户端断连（GeneratorExit）时取消，防止孤儿任务继续烧 LLM 费用
        pipeline_task: asyncio.Task | None = None
        try:
            user_content = body.content
            file_ids = body.fileIds or []
            payload_data = body.payload or {}
            # B3：payload.systemPrompt 是历史死字段（后端从未读取，前端已停发）。
            # 旧客户端若仍携带，继续忽略；勿接线。
            strict_mode = payload_data.get("strictMode", False)
            privacy_mode = payload_data.get("privacyMode", True)

            if not user_content:
                yield await sse_event("error", {"content": "消息内容不能为空"})
                return

            # ── Agent mode: delegate to PER agent ──────────────────────────
            if body.useAgent:
                from app.agent.config import AgentConfig
                from app.agent.service import agent_service

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
                        agent_history = await _load_session_messages(
                            body.conversationId, current_user.id
                        )
                    except Exception as hist_err:
                        logger.warning(f"加载 agent 会话历史失败（继续无历史对话）: {hist_err}")
                async for agent_event in agent_service.chat(
                    query=user_content,
                    history=agent_history,
                    # 安全加固（issue #257）：此前 `... or 1` 会把无组织用户静默放进
                    # 1 号组织的 agent 上下文（1 号组织甚至未必存在）。现在如实传
                    # None：休闲对话不受影响，需要组织上下文的工具由注册表 auth
                    # 钩子 fail-closed（与 knowledge.py 检索路径 403 加固同语义）。
                    organization_id=getattr(current_user, "organization_id", None),
                    user_id=current_user.id,
                    session_id=body.conversationId or None,
                    config=config,
                ):
                    if agent_event.type == "chunk":
                        full_response += agent_event.content
                        yield await sse_event("chunk", {
                            "content": agent_event.content,
                            "conversationId": body.conversationId,
                            "messageId": f"agent-{body.conversationId or 'new'}",
                        })
                    elif agent_event.type == "thinking":
                        yield await sse_event("thinking", {
                            "content": agent_event.content,
                            "thinkingType": agent_event.thinking_type,
                            "planId": agent_event.plan_id,
                            "planStepId": agent_event.plan_step_id,
                            "planProgress": agent_event.plan_progress if agent_event.plan_progress >= 0 else None,
                        })
                    elif agent_event.type == "tool_call":
                        yield await sse_event("tool_call", {
                            "toolName": agent_event.tool_name,
                            "toolArgs": agent_event.tool_args,
                            "toolCallId": agent_event.tool_call_id,
                            "planId": agent_event.plan_id,
                            "planStepId": agent_event.plan_step_id,
                        })
                    elif agent_event.type == "tool_result":
                        yield await sse_event("tool_result", {
                            "toolName": agent_event.tool_name,
                            "toolCallId": agent_event.tool_call_id,
                            "content": agent_event.content[:500],
                            "toolDurationMs": agent_event.tool_duration_ms,
                            "planStepId": agent_event.plan_step_id,
                            "planProgress": agent_event.plan_progress if agent_event.plan_progress >= 0 else None,
                        })
                        if agent_event.tool_name in ("search_knowledge_base", "vector_search"):
                            _extract_agent_sources(agent_event.content, agent_sources)
                    elif agent_event.type == "tool_error":
                        yield await sse_event("tool_error", {
                            "toolName": agent_event.tool_name,
                            "content": agent_event.content[:300],
                            "toolCallId": agent_event.tool_call_id,
                        })
                    elif agent_event.type == "plan_start":
                        yield await sse_event("plan_start", {
                            "planId": agent_event.plan_id,
                            "content": agent_event.content,
                        })
                    elif agent_event.type == "plan_step":
                        yield await sse_event("plan_step", {
                            "planId": agent_event.plan_id,
                            "planStepId": agent_event.plan_step_id,
                            "content": agent_event.content,
                            "toolHint": agent_event.tool_hint,
                            "dependencies": agent_event.dependencies,
                        })
                    elif agent_event.type == "plan_complete":
                        yield await sse_event("plan_complete", {
                            "planId": agent_event.plan_id,
                            "content": agent_event.content,
                        })
                    elif agent_event.type == "reflection":
                        yield await sse_event("reflection", {
                            "content": agent_event.content,
                            "result": agent_event.reflection_result,
                        })
                    elif agent_event.type == "done":
                        pass
                    elif agent_event.type == "error":
                        yield await sse_event("error", {"content": agent_event.content})
                        return
                    elif agent_event.type == "execution_context":
                        # issue #222：PER 循环收尾时真的会发出它，此前无 case 被静默丢弃。
                        # 该事件带结构性字段，投影风格与上面各分支保持一致。
                        yield await sse_event("execution_context", {
                            "content": agent_event.content,
                            "planId": agent_event.plan_id,
                            "planProgress": agent_event.plan_progress if agent_event.plan_progress >= 0 else None,
                        })
                    else:
                        # issue #222：这里是白名单式 if/elif，缺 case 的事件（如
                        # run_report——PER 循环真的会发出）会**静默消失**，而 /agent 出口
                        # 是全量转发的——同一个 run 从两个入口看到的流不一致。未知类型
                        # 统一原样转发 content，客户端按需忽略（SSE 按 event 名分发，
                        # 没有监听器的类型是安全的）；宁可在流里多一个没人听的事件，
                        # 也不要静默丢数据。
                        logger.debug("chat SSE 转发未投影的事件类型: %s", agent_event.type)
                        yield await sse_event(agent_event.type, {"content": agent_event.content})

                yield await sse_event("message", {
                    "content": full_response,
                    "conversationId": body.conversationId,
                    "messageId": f"agent-{body.conversationId or 'new'}",
                    "sources": agent_sources if agent_sources else None,
                    "is_cached": False,
                })
                return

            # ── RAG mode (original) ────────────────────────────────────────
            async with AsyncSessionLocal() as session:
                db_user = await session.get(User, current_user.id)
                # 安全加固（issue #268）：与 WS 路径同一语义——不再 `else 1`
                # 兜底到 1 号组织；无组织如实传 None，由 run_rag_pipeline 显式拒绝。
                search_org_id = (
                    db_user.organization_id
                    if (db_user and db_user.organization_id)
                    else None
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
                        conversation_id=body.conversationId,
                        file_ids=file_ids,
                        strict_mode=strict_mode,
                        privacy_mode=privacy_mode,
                        event_queue=queue,
                        debug=body.debug,
                    )
                )

                # Drain events and convert to SSE
                while True:
                    try:
                        event: RAGEvent = await asyncio.wait_for(
                            queue.get(), timeout=0.1
                        )
                        if event.type == "debug" and event.debug_data:
                            yield await sse_event("debug", {
                                "data": event.debug_data,
                                "conversationId": event.conversation_id,
                            })
                        elif event.type == "chunk":
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
                        elif event.type == "error":
                            # issue #268：RAG 链路的显式拒绝（如无组织）经此事件
                            # 转发（与 WS 路径同一语义）。此前无此 case，错误会被
                            # 静默丢弃。
                            yield await sse_event("error", {"content": event.content})
                    except TimeoutError:
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
                                elif event.type == "error":
                                    yield await sse_event("error", {"content": event.content})
                            break

                await pipeline_task

        except Exception as e:
            logger.error(f"SSE error: {e}", exc_info=True)
            yield await sse_event("error", {"content": f"服务器处理出错: {str(e)}"})
        finally:
            # 客户端断连（GeneratorExit/CancelledError 属 BaseException，finally 兜底）
            if pipeline_task is not None and not pipeline_task.done():
                pipeline_task.cancel()
                try:
                    await asyncio.gather(pipeline_task, return_exceptions=True)
                except Exception:
                    logger.warning("Cancelling orphan SSE pipeline task failed", exc_info=True)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
