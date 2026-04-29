# -*- coding: utf-8 -*-
"""
派聪明AI知识库系统 - 权限服务
"""

import logging
from typing import List, Optional, Set

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from sqlalchemy.orm import selectinload

from app.models.rbac import Permission, Role, PermissionType, user_organization_role_association
from app.models.user import User
from app.core.database import AsyncSessionLocal

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

class PermissionService:
    """权限服务类 - 提供RBAC核心逻辑"""

    async def get_user_permissions(self, db: AsyncSession, user: User, organization_id: Optional[int] = None) -> Set[str]:
        """获取用户拥有的所有权限名称集合"""
        # 确保传入的是对象而不是ID
        if isinstance(user, int):
            user = await db.get(User, user)

        permissions_set = set()

        # 1. 超级管理员特权：直接返回所有已知权限
        if user.is_superuser:
            result = await db.execute(select(Permission.name))
            return set(result.scalars().all())
        
        # 2. 获取当前用户对应的系统级角色权限
        system_role_names = ["SysAdmin"] if (getattr(user, "role", "") or "").lower() == "admin" else ["User"]
        system_roles_query = select(Role).where(
            and_(
                Role.is_system_role == True,
                Role.name.in_(system_role_names)
            )
        ).options(selectinload(Role.permissions))
        system_roles_result = await db.execute(system_roles_query)
        for role in system_roles_result.scalars().all():
            for perm in role.permissions:
                permissions_set.add(perm.name)

        # 3. 获取组织级角色权限
        if organization_id:
            user_org_roles_query = select(Role).join(
                user_organization_role_association, Role.id == user_organization_role_association.c.role_id
            ).where(
                and_(
                    user_organization_role_association.c.user_id == user.id,
                    user_organization_role_association.c.organization_id == organization_id
                )
            ).options(selectinload(Role.permissions))
            
            user_org_roles_result = await db.execute(user_org_roles_query)
            for role in user_org_roles_result.scalars().all():
                for perm in role.permissions:
                    permissions_set.add(perm.name)

        return permissions_set

    async def initialize_default_permissions_and_roles(self):
        """初始化基础权限和角色数据"""
        async with AsyncSessionLocal() as db:
            logger.debug("开始初始化默认权限和角色...")

            # 1. 定义核心权限名 (必须与代码中 PermissionType 枚举的值完全一致)
            default_permissions_data = [
                {"name": PermissionType.VIEW_USER_DETAIL.value, "description": "查看用户详情"},
                {"name": PermissionType.MANAGE_USER_ROLES.value, "description": "管理用户角色"},
                {"name": PermissionType.DELETE_USER.value, "description": "删除用户"},
                {"name": PermissionType.VIEW_ORGANIZATION.value, "description": "查看组织"},
                {"name": PermissionType.CREATE_ORGANIZATION.value, "description": "创建组织"},
                {"name": PermissionType.UPDATE_ORGANIZATION.value, "description": "更新组织"},
                {"name": PermissionType.DELETE_ORGANIZATION.value, "description": "删除组织"},
                {"name": PermissionType.MANAGE_ORGANIZATION_MEMBERS.value, "description": "管理组织成员"},
                {"name": PermissionType.DOCUMENT_UPLOAD.value, "description": "上传文档"},
                {"name": PermissionType.DOCUMENT_READ.value, "description": "查看文档"},
                {"name": PermissionType.DOCUMENT_UPDATE.value, "description": "更新文档"},
                {"name": PermissionType.DOCUMENT_DELETE.value, "description": "删除文档"},
                {"name": PermissionType.VIEW_KNOWLEDGE_BASE.value, "description": "查看知识库"},
                {"name": PermissionType.SEARCH_KNOWLEDGE_BASE.value, "description": "检索知识库"},
                {"name": PermissionType.MANAGE_KNOWLEDGE_BASE.value, "description": "管理知识库"},
                {"name": PermissionType.REBUILD_DOCUMENT_INDEX.value, "description": "重建知识库索引"},
                {"name": PermissionType.VIEW_PROMPT.value, "description": "查看提示词"},
                {"name": PermissionType.CREATE_PROMPT.value, "description": "创建提示词"},
                {"name": PermissionType.UPDATE_PROMPT.value, "description": "更新提示词"},
                {"name": PermissionType.DELETE_PROMPT.value, "description": "删除提示词"},
                {"name": PermissionType.MANAGE_SYSTEM_PROMPTS.value, "description": "管理系统提示词"},
                {"name": PermissionType.VIEW_SYSTEM_HEALTH.value, "description": "查看系统健康"},
                {"name": PermissionType.USE_EMBEDDING_SERVICE.value, "description": "使用向量服务"}
            ]

            # 写入权限表
            for p_data in default_permissions_data:
                result = await db.execute(select(Permission).where(Permission.name == p_data["name"]))
                if not result.scalar_one_or_none():
                    db.add(Permission(**p_data))
            await db.commit()

            # 加载所有权限对象供角色关联
            result = await db.execute(select(Permission))
            all_perms_map = {p.name: p for p in result.scalars().all()}

            # 2. 定义角色及其拥有的权限
            default_roles_data = [
                {
                    "name": "SysAdmin", "description": "系统管理员", "is_system_role": True,
                    "permissions": list(all_perms_map.keys()) # 拥有所有权限
                },
                {
                    "name": "User", "description": "普通用户", "is_system_role": True,
                    "permissions": [
                        PermissionType.DOCUMENT_READ.value,
                        PermissionType.DOCUMENT_UPLOAD.value,
                        PermissionType.VIEW_KNOWLEDGE_BASE.value,
                        PermissionType.SEARCH_KNOWLEDGE_BASE.value,
                        PermissionType.VIEW_PROMPT.value
                    ]
                }
            ]

            # 写入角色并关联权限
            for r_data in default_roles_data:
                stmt = select(Role).where(Role.name == r_data["name"]).options(selectinload(Role.permissions))
                result = await db.execute(stmt)
                role = result.scalar_one_or_none()

                if not role:
                    role = Role(name=r_data["name"], description=r_data["description"], is_system_role=r_data["is_system_role"])
                    db.add(role)
                    await db.flush()
                
                await db.refresh(role, ["permissions"])
                
                # 同步权限关联
                current_perms = {p.name for p in role.permissions}
                for p_name in r_data["permissions"]:
                    if p_name in all_perms_map and p_name not in current_perms:
                        role.permissions.append(all_perms_map[p_name])
            
            await db.commit()
            logger.info("默认权限和角色初始化完成。")

permission_service = PermissionService()
