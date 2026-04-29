# -*- coding: utf-8 -*-
import logging
from typing import Optional, Any
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user_audit import UserActivityLog
from app.core.database import AsyncSessionLocal

logger = logging.getLogger(__name__)

class AuditService:
    @staticmethod
    async def log_activity(
        user_id: int,
        action: str,
        target_type: Optional[str] = None,
        target_id: Optional[str] = None,
        detail: Optional[str] = None,
        request: Optional[Request] = None,
        db: Optional[AsyncSession] = None
    ):
        """
        记录用户活动日志
        """
        try:
            ip_address = None
            user_agent = None
            
            if request:
                ip_address = request.client.host if request.client else None
                user_agent = request.headers.get("user-agent")

            log_entry = UserActivityLog(
                user_id=user_id,
                action=action,
                target_type=target_type,
                target_id=str(target_id) if target_id else None,
                detail=detail,
                ip_address=ip_address,
                user_agent=user_agent
            )

            if db:
                db.add(log_entry)
                await db.commit()
            else:
                async with AsyncSessionLocal() as session:
                    session.add(log_entry)
                    await session.commit()
                    
        except Exception as e:
            logger.error(f"Failed to log activity: {e}")

audit_service = AuditService()
