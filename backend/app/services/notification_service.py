"""站内通知的 service 层入口。

`create_notification` 从 `app/api/v1/endpoints/notifications.py` 原样下沉：
agent 终态通知发生在运行侧（`app.agent.service`），不应反向依赖 HTTP 端点
模块；端点模块保留同名 re-export，既有 8 处调用点（users / organizations /
knowledge / auth 等）零改动。

语义与下沉前完全一致：同一 title+user 1 分钟去重（命中即返回旧行、跳过实时
推送）；提交自身 DB 会话；提交后经 `notification_ws_manager` 推送实时消息。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.notification_ws import notification_ws_manager
from app.models.notification import Notification

logger = logging.getLogger(__name__)


async def create_notification(
    db: AsyncSession,
    user_id: int,
    title: str,
    content: str,
    type: str = "system",
    target_route: str | None = None,
    target_id: str | None = None,
):
    """创建通知（内部使用）并推送实时消息，同一 title+user 1 分钟内去重"""
    cutoff = datetime.now(UTC) - timedelta(minutes=1)
    existing = (
        await db.execute(
            select(Notification)
            .where(
                and_(
                    Notification.user_id == user_id,
                    Notification.title == title,
                    Notification.created_at >= cutoff,
                )
            )
            .limit(1)
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

    await notification_ws_manager.push(
        user_id,
        {
            "id": notification.id,
            "title": notification.title,
            "content": notification.content,
            "type": notification.type,
            "is_read": notification.is_read,
            "created_at": notification.created_at.isoformat() if notification.created_at else None,
            "target_route": target_route,
            "target_id": target_id,
        },
    )

    return notification
