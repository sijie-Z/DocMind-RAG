# -*- coding: utf-8 -*-
"""
派聪明AI知识库系统 - 安全工具
"""

# backend/app/core/security.py
from typing import List, Optional, Set
from fastapi import Depends, Request, HTTPException, status 
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.models.user import User
from app.services.auth_service import auth_service
from app.services.permission_service import permission_service
from app.models.rbac import PermissionType

async def get_current_user(
    current_user: User = Depends(auth_service.get_current_user),
    db: AsyncSession = Depends(get_db)
) -> User:
    return current_user

def permission_required(required_permissions: List[PermissionType], organization_id_param: str = None):
    async def check_permissions(
        request: Request,
        current_user: User = Depends(get_current_user),
        db: AsyncSession = Depends(get_db)
    ):
        # 1. 核心修复：上帝模式（优先判断超管） 
        # 必须先从数据库 reload 一下，确保 is_superuser 状态是最新的
        user = await db.get(User, current_user.id)
        if user and user.is_superuser:
            return True

        # 2. 提取组织 ID
        organization_id = None
        if organization_id_param:
            organization_id = request.path_params.get(organization_id_param)
            if organization_id is None:
                if request.method == "GET":
                    organization_id = request.query_params.get(organization_id_param)
                elif request.method == "POST":
                    try:
                        form = await request.form()
                        organization_id = form.get(organization_id_param)
                    except Exception:
                        pass
                    if organization_id is None:
                        try:
                            payload = await request.json()
                            if isinstance(payload, dict):
                                organization_id = payload.get(organization_id_param)
                        except Exception:
                            pass

        # 3. 获取用户权限集合
        # ✅ 修正：传递 user 对象，而不是 user.id
        user_permissions = await permission_service.get_user_permissions(db, user, organization_id)

        # 4. 校验权限名 (对比字符串)
        for perm in required_permissions:
            if perm.value not in user_permissions:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"用户无此权限: {perm.value}"
                )
        return True 
    return check_permissions
    
