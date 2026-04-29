# -*- coding: utf-8 -*-
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Body, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, delete, desc, func
from pydantic import BaseModel, ConfigDict
from datetime import datetime
import json

from app.core.database import get_db
from app.models.user import User
from app.models.notification import Notification
from app.core.security import get_current_user
from app.services.auth_service import AuthService
from app.core.notification_ws import notification_ws_manager

router = APIRouter()
auth_service = AuthService()

# --- Schemas ---
class NotificationResponse(BaseModel):
    id: int
    title: str
    content: str
    type: str
    is_read: bool
    target_route: Optional[str] = None
    target_id: Optional[str] = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

class NotificationListResponse(BaseModel):
    items: List[NotificationResponse]
    total: int
    unread_count: int
    skip: int
    limit: int

class NotificationSummaryResponse(BaseModel):
    total: int
    unread_count: int
    by_type: dict

class NotificationBatchDeleteRequest(BaseModel):
    ids: List[int]

class NotificationCreate(BaseModel):
    title: str
    content: str
    type: str = "system"

# --- Endpoints ---

@router.websocket("/ws")
async def notification_ws(websocket: WebSocket, token: str = Query(...)):
    payload = auth_service.verify_token(token.strip().replace('"', '').replace("'", ""))
    if not payload:
        await websocket.close(code=4001, reason="Invalid token")
        return

    user_id = payload.get("user_id")
    if not user_id:
        await websocket.close(code=4002, reason="Missing user id")
        return

    await notification_ws_manager.connect(int(user_id), websocket)
    try:
        while True:
            data = await websocket.receive_text()
            # 响应客户端应用层心跳 Ping
            try:
                parsed = json.loads(data)
                if parsed.get("type") == "ping":
                    await websocket.send_text(json.dumps({"type": "pong"}))
            except Exception:
                pass
    except WebSocketDisconnect:
        pass
    finally:
        notification_ws_manager.disconnect(int(user_id), websocket)

@router.get("/", response_model=NotificationListResponse, summary="获取通知列表")
async def get_notifications(
    skip: int = 0,
    limit: int = Query(20, ge=1, le=100),
    unread_only: bool = False,
    type: Optional[str] = None,
    q: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """获取当前用户的通知列表（支持筛选、搜索、分页）"""
    try:
        base_query = select(Notification).where(Notification.user_id == current_user.id)

        if unread_only:
            base_query = base_query.where(Notification.is_read == False)
        if type:
            base_query = base_query.where(Notification.type == type)
        if q:
            keyword = f"%{q}%"
            base_query = base_query.where((Notification.title.like(keyword)) | (Notification.content.like(keyword)))

        total_result = await db.execute(select(func.count()).select_from(base_query.subquery()))
        total = int(total_result.scalar() or 0)

        unread_result = await db.execute(
            select(func.count(Notification.id)).where(
                Notification.user_id == current_user.id,
                Notification.is_read == False
            )
        )
        unread_count = int(unread_result.scalar() or 0)

        result = await db.execute(base_query.order_by(desc(Notification.created_at)).offset(skip).limit(limit))
        notifications = result.scalars().all()

        return {
            "items": notifications,
            "total": total,
            "unread_count": unread_count,
            "skip": skip,
            "limit": limit,
        }
    except Exception:
        # 兼容历史数据库尚未新增 target_route/target_id 字段的场景
        from sqlalchemy import text
        where_sql = "WHERE user_id = :user_id"
        params = {"user_id": current_user.id, "skip": skip, "limit": limit}
        if unread_only:
            where_sql += " AND is_read = 0"
        if type:
            where_sql += " AND type = :type"
            params["type"] = type
        if q:
            where_sql += " AND (title LIKE :kw OR content LIKE :kw)"
            params["kw"] = f"%{q}%"

        total_sql = text(f"SELECT COUNT(1) AS c FROM notifications {where_sql}")
        total_result = await db.execute(total_sql, params)
        total = int(total_result.scalar() or 0)

        unread_sql = text("SELECT COUNT(1) AS c FROM notifications WHERE user_id = :user_id AND is_read = 0")
        unread_result = await db.execute(unread_sql, {"user_id": current_user.id})
        unread_count = int(unread_result.scalar() or 0)

        list_sql = text(
            f"SELECT id, title, content, type, is_read, created_at FROM notifications {where_sql} "
            "ORDER BY created_at DESC LIMIT :limit OFFSET :skip"
        )
        rows = (await db.execute(list_sql, params)).mappings().all()

        items = [{
            "id": int(r["id"]),
            "title": r["title"],
            "content": r["content"],
            "type": r["type"],
            "is_read": bool(r["is_read"]),
            "target_route": None,
            "target_id": None,
            "created_at": r["created_at"],
        } for r in rows]

        return {
            "items": items,
            "total": total,
            "unread_count": unread_count,
            "skip": skip,
            "limit": limit,
        }

@router.get("/unread-count", response_model=dict, summary="获取未读通知数量")
async def get_unread_count(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """获取未读通知数量"""
    query = select(func.count(Notification.id)).where(
        Notification.user_id == current_user.id,
        Notification.is_read == False
    )
    result = await db.execute(query)
    count = result.scalar() or 0
    return {"count": count}

@router.get("/summary", response_model=NotificationSummaryResponse, summary="通知摘要统计")
async def get_notification_summary(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    total_result = await db.execute(
        select(func.count(Notification.id)).where(Notification.user_id == current_user.id)
    )
    unread_result = await db.execute(
        select(func.count(Notification.id)).where(
            Notification.user_id == current_user.id,
            Notification.is_read == False
        )
    )
    type_result = await db.execute(
        select(Notification.type, func.count(Notification.id))
        .where(Notification.user_id == current_user.id)
        .group_by(Notification.type)
    )

    by_type = {row[0] or "system": int(row[1]) for row in type_result.all()}

    return {
        "total": int(total_result.scalar() or 0),
        "unread_count": int(unread_result.scalar() or 0),
        "by_type": by_type,
    }

@router.put("/{notification_id}/read", summary="标记通知为已读")
async def mark_as_read(
    notification_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """标记单条通知为已读"""
    # 验证通知是否存在且属于当前用户
    result = await db.execute(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.user_id == current_user.id
        )
    )
    notification = result.scalar_one_or_none()
    
    if not notification:
        raise HTTPException(status_code=404, detail="通知不存在")
        
    await db.execute(
        update(Notification)
        .where(
            Notification.id == notification_id,
            Notification.user_id == current_user.id
        )
        .values(is_read=True)
    )
    await db.commit()
    return {"message": "标记成功"}

@router.put("/read-all", summary="全部标记为已读")
async def mark_all_as_read(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """将所有未读通知标记为已读"""
    result = await db.execute(
        update(Notification)
        .where(
            Notification.user_id == current_user.id,
            Notification.is_read == False
        )
        .values(is_read=True)
    )
    await db.commit()
    return {"message": "全部已读", "updated": int(result.rowcount or 0)}

@router.delete("/{notification_id}", summary="删除单条通知")
async def delete_notification(
    notification_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        delete(Notification).where(
            Notification.id == notification_id,
            Notification.user_id == current_user.id
        )
    )
    if not result.rowcount:
        raise HTTPException(status_code=404, detail="通知不存在")
    await db.commit()
    return {"message": "删除成功"}

@router.delete("/", summary="批量删除通知")
async def batch_delete_notifications(
    payload: NotificationBatchDeleteRequest = Body(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    ids = payload.ids
    if not ids:
        return {"message": "没有可删除的通知", "deleted": 0}

    result = await db.execute(
        delete(Notification).where(
            Notification.user_id == current_user.id,
            Notification.id.in_(ids)
        )
    )
    await db.commit()
    return {"message": "批量删除成功", "deleted": int(result.rowcount or 0)}

# --- Internal Helpers ---
async def create_notification(
    db: AsyncSession,
    user_id: int,
    title: str,
    content: str,
    type: str = "system",
    target_route: Optional[str] = None,
    target_id: Optional[str] = None,
):
    """创建通知（内部使用）并推送实时消息，同一 title+user 1 分钟内去重"""
    from datetime import datetime, timedelta
    from sqlalchemy import select, and_

    cutoff = datetime.utcnow() - timedelta(minutes=1)
    existing = (
        await db.execute(
            select(Notification).where(
                and_(
                    Notification.user_id == user_id,
                    Notification.title == title,
                    Notification.created_at >= cutoff,
                )
            ).limit(1)
        )
    ).scalar_one_or_none()

    if existing:
        return existing

    notification = Notification(
        user_id=user_id,
        title=title,
        content=content,
        type=type,
        target_route=target_route,
        target_id=target_id,
    )
    db.add(notification)
    await db.commit()
    await db.refresh(notification)

    await notification_ws_manager.push(user_id, {
        "id": notification.id,
        "title": notification.title,
        "content": notification.content,
        "type": notification.type,
        "is_read": notification.is_read,
        "created_at": notification.created_at.isoformat() if notification.created_at else None,
        "target_route": target_route,
        "target_id": target_id,
    })

    return notification
