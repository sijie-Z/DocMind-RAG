# -*- coding: utf-8 -*-
from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from pydantic import BaseModel, ConfigDict

from app.core.database import get_db
from app.models.manual import SystemManual
from app.models.user import User
from app.services.auth_service import AuthService
from app.core.security import get_current_user, permission_required
from app.models.rbac import PermissionType

router = APIRouter()
auth_service = AuthService()

# --- Schemas ---
class ManualBase(BaseModel):
    title: str
    content: str
    category: str = "general"
    sort_order: int = 0
    is_published: bool = True

class ManualCreate(ManualBase):
    pass

class ManualUpdate(BaseModel):
    title: Optional[str] = None
    content: Optional[str] = None
    category: Optional[str] = None
    sort_order: Optional[int] = None
    is_published: Optional[bool] = None

class ManualResponse(ManualBase):
    id: int
    created_at: datetime
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)

# --- Endpoints ---

@router.get("/", response_model=List[ManualResponse], summary="获取手册列表")
async def get_manuals(
    category: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """获取系统操作手册列表 (所有已登录用户可见)"""
    query = select(SystemManual)
    
    # 如果不是管理员，只能看发布的
    if current_user.role != "admin":
        query = query.where(SystemManual.is_published == True)
        
    if category:
        query = query.where(SystemManual.category == category)
        
    query = query.order_by(SystemManual.sort_order.desc(), SystemManual.id.desc())
    
    result = await db.execute(query)
    return result.scalars().all()

@router.get("/{manual_id}", response_model=ManualResponse, summary="获取手册详情")
async def get_manual(
    manual_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """获取单个手册详情"""
    query = select(SystemManual).where(SystemManual.id == manual_id)
    if current_user.role != "admin":
        query = query.where(SystemManual.is_published == True)
        
    result = await db.execute(query)
    manual = result.scalar_one_or_none()
    
    if not manual:
        raise HTTPException(status_code=404, detail="手册不存在")
        
    return manual

@router.post("/", response_model=ManualResponse, summary="创建手册", dependencies=[Depends(permission_required([PermissionType.MANAGE_SYSTEM_PROMPTS]))])
async def create_manual(
    manual_in: ManualCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """创建新的操作手册 (需要系统管理权限)"""
    manual = SystemManual(**manual_in.dict())
    db.add(manual)
    await db.commit()
    await db.refresh(manual)
    return manual

@router.put("/{manual_id}", response_model=ManualResponse, summary="更新手册", dependencies=[Depends(permission_required([PermissionType.MANAGE_SYSTEM_PROMPTS]))])
async def update_manual(
    manual_id: int,
    manual_in: ManualUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """更新操作手册 (需要系统管理权限)"""
    result = await db.execute(select(SystemManual).where(SystemManual.id == manual_id))
    manual = result.scalar_one_or_none()

    if not manual:
        raise HTTPException(status_code=404, detail="手册不存在")

    update_data = manual_in.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(manual, field, value)

    await db.commit()
    await db.refresh(manual)
    return manual

@router.delete("/{manual_id}", summary="删除手册", dependencies=[Depends(permission_required([PermissionType.MANAGE_SYSTEM_PROMPTS]))])
async def delete_manual(
    manual_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """删除操作手册 (需要系统管理权限)"""
    result = await db.execute(select(SystemManual).where(SystemManual.id == manual_id))
    manual = result.scalar_one_or_none()

    if not manual:
        raise HTTPException(status_code=404, detail="手册不存在")

    await db.delete(manual)
    await db.commit()
    return {"message": "手册删除成功"}
