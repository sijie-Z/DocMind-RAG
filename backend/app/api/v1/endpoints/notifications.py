import json
import logging
import re
from datetime import datetime

from fastapi import APIRouter, Body, Depends, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, desc, func, select, update
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.notification_ws import notification_ws_manager
from app.core.security import get_current_user
from app.core.ws_auth import authenticate_ws, extract_ws_subprotocol
from app.exceptions import NotFoundError
from app.models.notification import Notification
from app.models.user import User

router = APIRouter()
logger = logging.getLogger(__name__)


# --- Schemas ---
class NotificationResponse(BaseModel):
    id: int
    title: str
    content: str
    type: str
    is_read: bool
    target_route: str | None = None
    target_id: str | None = None
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class NotificationListResponse(BaseModel):
    items: list[NotificationResponse]
    total: int
    unread_count: int
    skip: int
    limit: int


class NotificationSummaryResponse(BaseModel):
    total: int
    unread_count: int
    by_type: dict


class NotificationBatchDeleteRequest(BaseModel):
    ids: list[int]


class NotificationCreate(BaseModel):
    title: str
    content: str
    type: str = "system"


# --- Endpoints ---


@router.websocket("/ws")
async def notification_ws(
    websocket: WebSocket,
    db: AsyncSession = Depends(get_db),
):
    # 安全加固：握手阶段必须查库（见 issue #82）。令牌只证明它自己有效——
    # 用户被删除或禁用后仍可能持有未过期的令牌，此时不得建立连接。
    # 已删除与已禁用返回完全相同的 close code + reason，不可区分。
    user = await authenticate_ws(websocket, db)
    if user is None:
        return
    user_id = user.id

    # 原样回显客户端请求的 auth.* 子协议（issue #258）：不回显时浏览器强制判定
    # 握手失败（close 1006）。提取不到时保持裸 accept() 语义。
    subprotocol = extract_ws_subprotocol(websocket)
    await notification_ws_manager.connect(user_id, websocket, subprotocol)
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
        notification_ws_manager.disconnect(user_id, websocket)


_LEGACY_ROUTE_COLUMN = r"(?:[a-z_]+\.)?(?:target_route|target_id)"
_MISSING_COLUMN_PATTERNS = (
    # MySQL/MariaDB：Unknown column 'notifications.target_route' in 'field list'
    re.compile(rf"unknown column '{_LEGACY_ROUTE_COLUMN}'"),
    # SQLite：no such column: notifications.target_route
    re.compile(rf"no such column: ?{_LEGACY_ROUTE_COLUMN}\b"),
)


def _error_body(exc: Exception) -> str:
    """取「消息主体段」用于缺列判定（issue #301 评审 B1）。

    execute 期 `str(exc)` 必含 SQLAlchemy 渲染的 `[SQL: ...]`（生产查询文本，
    恒含 target_route/target_id）与 `[parameters: ...]`（绑定值回显：q 用户可控、
    user_id 结构性含 "1054"），两者会把朴素子串判定击穿——因此判定只允许读
    驱动原文：① 优先 `exc.orig`（DBAPI 原始异常，不含任何渲染回显段）；
    ② 无 orig 时退回 str(exc) 并剥离 `[SQL: ...]`/`[parameters: ...]` 段。
    """
    orig = getattr(exc, "orig", None)
    if orig is not None:
        return str(orig).lower()
    body = str(exc).lower()
    for marker in ("[sql: ", "[parameters: "):
        body = body.split(marker, 1)[0]
    return body


def _is_legacy_missing_route_columns(exc: Exception) -> bool:
    """异常是否确为「旧库没有 target_route/target_id 列」（issue #301 / 评审 B1）。

    在消息主体段内要求**短语与精确列名紧邻**：`unknown column 'X'` /
    `no such column: X`，X 为 target_route/target_id（可带表名限定）。
    抗回显污染的机制论证：`[SQL: ...]`/`[parameters: ...]` 是 SQLAlchemy
    渲染器附加段，**从不属于驱动原文**（`exc.orig`）——剥离后仅剩驱动消息；
    瞬时故障（锁等待/掉线）的驱动消息不含该短语，参数值与查询文本也无法
    再拼出「短语+列名」的紧邻形态（独立子串“与”条件已废弃）。
    """
    body = _error_body(exc)
    return any(p.search(body) for p in _MISSING_COLUMN_PATTERNS)


@router.get("/", response_model=NotificationListResponse, summary="获取通知列表")
async def get_notifications(
    skip: int = 0,
    limit: int = Query(20, ge=1, le=100),
    unread_only: bool = False,
    type: str | None = None,
    q: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取当前用户的通知列表（支持筛选、搜索、分页）"""
    try:
        base_query = select(Notification).where(Notification.user_id == current_user.id)

        if unread_only:
            base_query = base_query.where(Notification.is_read.is_(False))
        if type:
            base_query = base_query.where(Notification.type == type)
        if q:
            keyword = f"%{q}%"
            base_query = base_query.where(
                (Notification.title.like(keyword)) | (Notification.content.like(keyword))
            )

        total_result = await db.execute(select(func.count()).select_from(base_query.subquery()))
        total = int(total_result.scalar() or 0)

        unread_result = await db.execute(
            select(func.count(Notification.id)).where(
                Notification.user_id == current_user.id, Notification.is_read.is_(False)
            )
        )
        unread_count = int(unread_result.scalar() or 0)

        result = await db.execute(
            base_query.order_by(desc(Notification.created_at)).offset(skip).limit(limit)
        )
        notifications = result.scalars().all()

        return {
            "items": notifications,
            "total": total,
            "unread_count": unread_count,
            "skip": skip,
            "limit": limit,
        }
    except (OperationalError, ProgrammingError) as exc:
        # issue #301：此前这里是 `except Exception`，把任意数据库故障（连接丢失、
        # 超时……）都吞成「回退查询成功、只是所有通知都没有路由字段」——用户面
        # 分不清「没有路由」与「查询失败」，日志零留痕。现在：
        # ① 只有确为旧库缺 target_route/target_id 列时才回退（带 warning 留痕）；
        # ② 其他故障一律抛出 → 全局异常处理如实 500，不再伪装成功。
        if not _is_legacy_missing_route_columns(exc):
            logger.error("通知列表查询失败（非缺列故障，如实上报）: %s", exc, exc_info=True)
            raise
        logger.warning(
            "通知列表主查询失败（疑似旧库缺 target_route/target_id 列），已降级为"
            "回退查询：列表仍可列出，但 target_route/target_id 一律缺失，"
            "前端将按通知 type 兜底跳转。原始错误: %s",
            exc,
        )
        # Fallback for legacy DB without target_route/target_id columns
        base_query = select(
            Notification.id,
            Notification.title,
            Notification.content,
            Notification.type,
            Notification.is_read,
            Notification.created_at,
        ).where(Notification.user_id == current_user.id)
        if unread_only:
            base_query = base_query.where(Notification.is_read.is_(False))
        if type:
            base_query = base_query.where(Notification.type == type)
        if q:
            keyword = f"%{q}%"
            base_query = base_query.where(
                (Notification.title.like(keyword)) | (Notification.content.like(keyword))
            )
        count_query = select(func.count()).select_from(base_query.subquery())
        total = int((await db.execute(count_query)).scalar() or 0)
        unread_count = int(
            (
                await db.execute(
                    select(func.count(Notification.id)).where(
                        Notification.user_id == current_user.id, Notification.is_read.is_(False)
                    )
                )
            ).scalar()
            or 0
        )
        rows = (
            await db.execute(
                base_query.order_by(desc(Notification.created_at)).offset(skip).limit(limit)
            )
        ).all()
        items = [
            {
                "id": int(r[0]),
                "title": r[1],
                "content": r[2],
                "type": r[3],
                "is_read": bool(r[4]),
                "target_route": None,
                "target_id": None,
                "created_at": r[5],
            }
            for r in rows
        ]
        return {
            "items": items,
            "total": total,
            "unread_count": unread_count,
            "skip": skip,
            "limit": limit,
        }


@router.get("/unread-count", response_model=dict, summary="获取未读通知数量")
async def get_unread_count(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """获取未读通知数量"""
    query = select(func.count(Notification.id)).where(
        Notification.user_id == current_user.id, Notification.is_read.is_(False)
    )
    result = await db.execute(query)
    count = result.scalar() or 0
    return {"count": count}


@router.get("/summary", response_model=NotificationSummaryResponse, summary="通知摘要统计")
async def get_notification_summary(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    total_result = await db.execute(
        select(func.count(Notification.id)).where(Notification.user_id == current_user.id)
    )
    unread_result = await db.execute(
        select(func.count(Notification.id)).where(
            Notification.user_id == current_user.id, Notification.is_read.is_(False)
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


@router.put("/{notification_id}/read", response_model=dict, summary="标记通知为已读")
async def mark_as_read(
    notification_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """标记单条通知为已读"""
    # 验证通知是否存在且属于当前用户
    result = await db.execute(
        select(Notification).where(
            Notification.id == notification_id, Notification.user_id == current_user.id
        )
    )
    notification = result.scalar_one_or_none()

    if not notification:
        raise NotFoundError(detail="通知不存在")

    await db.execute(
        update(Notification)
        .where(Notification.id == notification_id, Notification.user_id == current_user.id)
        .values(is_read=True)
    )
    await db.commit()
    return {"message": "标记成功"}


@router.put("/read-all", response_model=dict, summary="全部标记为已读")
async def mark_all_as_read(
    current_user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)
):
    """将所有未读通知标记为已读"""
    result = await db.execute(
        update(Notification)
        .where(Notification.user_id == current_user.id, Notification.is_read.is_(False))
        .values(is_read=True)
    )
    await db.commit()
    return {"message": "全部已读", "updated": int(result.rowcount or 0)}


@router.delete("/{notification_id}", response_model=dict, summary="删除单条通知")
async def delete_notification(
    notification_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        delete(Notification).where(
            Notification.id == notification_id, Notification.user_id == current_user.id
        )
    )
    if not result.rowcount:
        raise NotFoundError(detail="通知不存在")
    await db.commit()
    return {"message": "删除成功"}


@router.delete("/", response_model=dict, summary="批量删除通知")
async def batch_delete_notifications(
    payload: NotificationBatchDeleteRequest = Body(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    ids = payload.ids
    if not ids:
        return {"message": "没有可删除的通知", "deleted": 0}

    result = await db.execute(
        delete(Notification).where(
            Notification.user_id == current_user.id, Notification.id.in_(ids)
        )
    )
    await db.commit()
    return {"message": "批量删除成功", "deleted": int(result.rowcount or 0)}


# --- Internal Helpers ---
# create_notification 已下沉到 app/services/notification_service.py（agent 运行侧
# 也要用它，端点模块不应被运行侧反向依赖）；此处同名 re-export 保持既有调用方
# （users / organizations / knowledge 等从本模块导入）零改动。
from app.services.notification_service import create_notification  # noqa: F401
