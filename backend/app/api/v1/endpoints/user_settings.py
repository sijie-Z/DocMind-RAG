# -*- coding: utf-8 -*-
"""
派聪明AI知识库系统 - 用户设置端点
"""
import logging
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.models.user_settings import UserSettings
from app.schemas.user_settings import UserSettingsResponse, UserSettingsUpdate

logger = logging.getLogger(__name__)

router = APIRouter()


async def _get_or_create_settings(
    db: AsyncSession, user_id: int
) -> UserSettings:
    """获取用户设置，不存在则创建默认设置"""
    result = await db.execute(
        select(UserSettings).where(UserSettings.user_id == user_id)
    )
    settings = result.scalar_one_or_none()

    if settings is None:
        settings = UserSettings(user_id=user_id)
        db.add(settings)
        await db.commit()
        await db.refresh(settings)

    return settings


@router.get("/settings", response_model=dict)
async def get_user_settings(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取当前用户的个性化设置（语言、主题等）"""
    settings = await _get_or_create_settings(db, current_user.id)
    return {
        "success": True,
        "message": "获取成功",
        "data": UserSettingsResponse.model_validate(settings).model_dump(),
    }


@router.put("/settings", response_model=dict)
async def update_user_settings(
    body: UserSettingsUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """更新当前用户的个性化设置"""
    settings = await _get_or_create_settings(db, current_user.id)

    update_data = body.model_dump(exclude_unset=True, exclude_none=True)
    for field, value in update_data.items():
        setattr(settings, field, value)

    await db.commit()
    await db.refresh(settings)

    return {
        "success": True,
        "message": "更新成功",
        "data": UserSettingsResponse.model_validate(settings).model_dump(),
    }
