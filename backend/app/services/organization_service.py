# -*- coding: utf-8 -*-
"""
派聪明AI知识库系统 - 组织服务
"""

import logging
from typing import Optional, List, Dict, Any, cast
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete, and_
from sqlalchemy.orm import selectinload

from app.models.organization import Organization, user_organization
from app.models.user import User
from app.models.document import Document

logger = logging.getLogger(__name__)

class OrganizationService:
    """组织服务类 - 提供企业级组织架构管理功能"""
    
    async def get_organization_tree(self, db: AsyncSession, user: User) -> List[Dict]:
        """
        获取组织树结构
        
        Args:
            db: 数据库会话
            user: 当前用户对象 (User)
            
        Returns:
            树形结构的组织列表
        """
        try:
            # 1. 获取所有组织
            # 这里可以利用刚才设置的超管特权
            if user.is_superuser:
                # 超管看到完整的树
                stmt = select(Organization).options(selectinload(Organization.children)).order_by(Organization.level, Organization.sort_order)
            else:
                # 普通用户只能看到自己所在组织及其下属（简化处理：看到所有非私有组织）
                stmt = select(Organization).where(Organization.is_private == False).options(selectinload(Organization.children)).order_by(Organization.level, Organization.sort_order)
            
            result = await db.execute(stmt)
            all_orgs = result.scalars().all()
            
            # 2. 构建树形结构 (后续逻辑不变)
            org_map = {org.id: self._org_to_dict(org) for org in all_orgs}
            tree = []
            
            for org in all_orgs:
                org_dict = org_map[org.id]
                if org.parent_id is None:
                    tree.append(org_dict)
                else:
                    parent = org_map.get(org.parent_id)
                    if parent:
                        if 'children' not in parent:
                            parent['children'] = []
                        parent['children'].append(org_dict)
            
            return tree
            
        except Exception as e:
            logger.error(f"获取组织树失败: {str(e)}")
            return []

    async def update_organization(self, db: AsyncSession, org_id: int, data: Dict[str, Any]) -> Optional[Organization]:
        """
        更新组织信息，自动处理层级变更
        小白讲解：当一个部门搬到另一个部门下面时，我们会自动计算它现在是第几级。
        """
        try:
            # 1. 获取现有组织
            result = await db.execute(select(Organization).where(Organization.id == org_id))
            org = result.scalar_one_or_none()
            if not org:
                return None
            
            # 2. 检查是否变更了父级
            old_parent_id = org.parent_id
            new_parent_id = data.get("parent_id")
            
            # 3. 如果父级变了，重新计算 level
            if "parent_id" in data and new_parent_id != old_parent_id:
                # 防止循环引用
                if new_parent_id == org_id:
                    raise ValueError("不能将组织设置为自己的子组织")
                
                level: int = 1
                if new_parent_id:
                    parent_result = await db.execute(select(Organization).where(Organization.id == new_parent_id))
                    parent = parent_result.scalar_one_or_none()
                    if parent:
                        level = int(cast(int, parent.level)) + 1

                org.level = int(level)  # type: ignore
                # 递归更新所有子组织的 level
                await self._update_children_levels(db, org)
            
            # 4. 更新其他字段
            for field, value in data.items():
                if hasattr(org, field):
                    setattr(org, field, value)
            
            await db.commit()
            await db.refresh(org)
            return org
            
        except Exception as e:
            await db.rollback()
            logger.error(f"更新组织失败: {str(e)}")
            raise

    async def _update_children_levels(self, db: AsyncSession, parent_org: Organization):
        """递归更新子组织的层级"""
        result = await db.execute(select(Organization).where(Organization.parent_id == parent_org.id))
        children = result.scalars().all()
        
        for child in children:
            child.level = int(cast(int, parent_org.level)) + 1  # type: ignore
            await self._update_children_levels(db, child)

    async def batch_delete_organizations(self, db: AsyncSession, org_ids: List[int]) -> bool:
        """批量删除组织及其关联数据"""
        try:
            for org_id in org_ids:
                await self.delete_organization_thoroughly(db, org_id)
            return True
        except Exception as e:
            logger.error(f"批量删除组织失败: {str(e)}")
            return False

    def _org_to_dict(self, org: Organization) -> Dict:
        """将组织对象转换为字典，并包含基础统计信息"""
        return {
            "id": org.id,
            "name": org.name,
            "description": org.description,
            "color": org.color,
            "is_private": org.is_private,
            "parent_id": org.parent_id,
            "level": org.level,
            "sort_order": org.sort_order,
            "created_at": org.created_at.isoformat() if org.created_at else None,
            "children": []
        }

    async def create_organization(self, db: AsyncSession, data: Dict, owner_id: int) -> Organization:
        """
        创建新组织
        """
        try:
            # 处理层级
            level = 1
            if data.get("parent_id"):
                parent_result = await db.execute(select(Organization).where(Organization.id == data["parent_id"]))
                parent = parent_result.scalar_one_or_none()
                if parent:
                    level = int(cast(int, parent.level)) + 1

            organization = Organization(
                name=data["name"],
                description=data.get("description"),
                color=data.get("color", "#18a058"),
                parent_id=data.get("parent_id"),
                level=level,  # type: ignore
                sort_order=data.get("sort_order", 0),
                owner_id=owner_id,
                is_private=data.get("is_private", False)
            )
            
            db.add(organization)
            await db.commit()
            await db.refresh(organization)
            
            # 自动将所有者添加为成员
            user_result = await db.execute(select(User).where(User.id == owner_id))
            user = user_result.scalar_one_or_none()
            if user:
                await self.add_user_to_organization(db, user, organization)
                
            return organization
        except Exception as e:
            await db.rollback()
            logger.error(f"创建组织失败: {str(e)}")
            raise

    async def get_org_stats(self, db: AsyncSession, org_id: int) -> Dict:
        """获取组织统计数据"""
        try:
            # 统计成员数
            member_count_result = await db.execute(
                select(func.count(user_organization.c.user_id))
                .where(user_organization.c.organization_id == org_id)
            )
            member_count = member_count_result.scalar() or 0
            
            # 统计文档数
            doc_count_result = await db.execute(
                select(func.count(Document.id))
                .where(Document.organization_id == org_id)
            )
            doc_count = doc_count_result.scalar() or 0
            
            return {
                "member_count": member_count,
                "document_count": doc_count
            }
        except Exception as e:
            logger.error(f"获取组织统计失败: {str(e)}")
            return {"member_count": 0, "document_count": 0}

    async def delete_organization_thoroughly(self, db: AsyncSession, org_id: int) -> bool:
        """
        彻底删除组织及其所有子组织（递归删除）
        """
        try:
            # 1. 获取组织及其所有子组织ID
            orgs_to_delete = await self._get_all_children_ids(db, org_id)
            orgs_to_delete.append(org_id)
            
            # 2. 删除成员关联
            await db.execute(
                delete(user_organization).where(user_organization.c.organization_id.in_(orgs_to_delete))
            )
            
            # 3. 删除组织本身
            await db.execute(
                delete(Organization).where(Organization.id.in_(orgs_to_delete))
            )
            
            await db.commit()
            return True
        except Exception as e:
            await db.rollback()
            logger.error(f"彻底删除组织失败: {str(e)}")
            return False

    async def _get_all_children_ids(self, db: AsyncSession, parent_id: int) -> List[int]:
        """递归获取所有子组织ID"""
        result = await db.execute(select(Organization.id).where(Organization.parent_id == parent_id))
        children_ids = list(result.scalars().all())
        
        all_ids = []
        for child_id in children_ids:
            all_ids.append(child_id)
            sub_ids = await self._get_all_children_ids(db, child_id)
            all_ids.extend(sub_ids)
            
        return all_ids

    async def create_private_organization(self, db: AsyncSession, user: User) -> Organization:
        """
        为用户创建私有组织标签
        
        Args:
            db: 数据库会话
            user: 用户对象
            
        Returns:
            创建的私有组织
        """
        try:
            # 创建私有组织，格式为 PRIVATE_username
            org_name = f"PRIVATE_{user.username}"
            
            # 检查是否已存在
            existing = await self.get_organization_by_name(db, org_name)
            if existing:
                return existing

            organization = Organization(
                name=org_name,
                description=f"{user.username} 的私有组织",
                is_private=True,
                owner_id=user.id
            )
            
            db.add(organization)
            await db.commit()
            await db.refresh(organization)
            
            # 添加用户为成员
            await self.add_user_to_organization(db, user, organization)
            
            logger.info(f"为用户 {user.username} 创建私有组织: {org_name}")
            return organization
            
        except Exception as e:
            await db.rollback()
            logger.error(f"创建私有组织失败: {str(e)}")
            raise
    
    async def get_organization_by_name(self, db: AsyncSession, name: str) -> Optional[Organization]:
        """根据名称获取组织"""
        try:
            result = await db.execute(select(Organization).where(Organization.name == name))
            return result.scalar_one_or_none()
        except Exception as e:
            logger.error(f"根据名称获取组织失败: {str(e)}")
            return None
    
    async def add_user_to_organization(self, db: AsyncSession, user: User, organization: Organization) -> bool:
        """
        添加用户到组织
        """
        try:
            # 检查是否已经是成员
            result = await db.execute(
                select(user_organization)
                .where(and_(
                    user_organization.c.user_id == user.id,
                    user_organization.c.organization_id == organization.id
                ))
            )
            if result.scalar_one_or_none():
                return False

            await db.execute(
                user_organization.insert().values(
                    user_id=user.id,
                    organization_id=organization.id
                )
            )
            await db.commit()
            logger.info(f"将用户 {user.username} 添加到组织 {organization.name}")
            return True
        except Exception as e:
            await db.rollback()
            logger.error(f"添加用户到组织失败: {str(e)}")
            return False
    
    async def get_user_organizations(self, db: AsyncSession, user: User) -> List[Organization]:
        """获取用户的所有组织"""
        try:
            # 这里的 user.organizations 需要在 User 模型中正确配置 secondary 关系
            result = await db.execute(
                select(Organization)
                .join(user_organization)
                .where(user_organization.c.user_id == user.id)
            )
            return list(result.scalars().all())
        except Exception as e:
            logger.error(f"获取用户组织失败: {str(e)}")
            return []
    
    async def get_organization_members(self, db: AsyncSession, organization_id: int) -> List[User]:
        """获取组织的所有成员"""
        try:
            result = await db.execute(
                select(User)
                .join(user_organization)
                .where(user_organization.c.organization_id == organization_id)
            )
            return list(result.scalars().all())
        except Exception as e:
            logger.error(f"获取组织成员失败: {str(e)}")
            return []

# 创建服务实例
organization_service = OrganizationService()
