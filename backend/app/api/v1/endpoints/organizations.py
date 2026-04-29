# -*- coding: utf-8 -*-
"""
派聪明AI知识库系统 - 组织管理端点
"""

from typing import List, Optional, Any
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, update, delete

from app.core.database import get_db
from app.models.user import User
from app.models.organization import Organization
from app.services.organization_service import organization_service
from app.schemas.organization import OrganizationCreate, OrganizationUpdate, OrganizationOut
from app.core.security import get_current_user, permission_required
from app.models.rbac import PermissionType
from app.api.v1.endpoints.notifications import create_notification
from app.services.audit_service import audit_service

router = APIRouter()

@router.get("/tree", summary="获取组织架构树", dependencies=[Depends(permission_required([PermissionType.VIEW_ORGANIZATION]))])
async def get_organization_tree(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """获取组织架构树，包含层级关系和基础信息"""
    try:
        # ✅ 修复：直接传整个 current_user 对象进去
        tree = await organization_service.get_organization_tree(db, current_user)
        return {
            "success": True,
            "message": "获取成功",
            "data": tree
        }
    except Exception as e:
        # 打印详细错误到终端，方便我们看
        import traceback
        traceback.print_exc() 
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取组织架构树失败: {str(e)}"
        )

@router.post("/", summary="创建组织", dependencies=[Depends(permission_required([PermissionType.CREATE_ORGANIZATION]))])
async def create_organization(
    org_in: OrganizationCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """创建新的组织或部门"""
    try:
        # 检查重名
        existing = await organization_service.get_organization_by_name(db, org_in.name)
        if existing:
            raise HTTPException(status_code=400, detail="组织名称已存在")
            
        new_org = await organization_service.create_organization(
            db, org_in.model_dump(), current_user.id
        )
        
        return {
            "success": True,
            "message": "创建成功",
            "data": {
                "id": new_org.id,
                "name": new_org.name
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"创建组织失败: {str(e)}"
        )

@router.get("/{org_id}/stats", summary="获取组织统计信息", dependencies=[Depends(permission_required([PermissionType.VIEW_ORGANIZATION], organization_id_param='org_id'))])
async def get_organization_stats(
    org_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """获取组织的成员数、文档数等统计数据"""
    stats = await organization_service.get_org_stats(db, org_id)
    return {
        "success": True,
        "data": stats
    }

@router.get("/", summary="获取组织列表", dependencies=[Depends(permission_required([PermissionType.VIEW_ORGANIZATION]))])
async def get_organizations(
    page: int = 1,
    page_size: int = 10,
    search: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """获取组织列表"""
    try:
        skip = (page - 1) * page_size
        limit = page_size
        
        conditions = []
        if search:
            conditions.append(Organization.name.contains(search))
            
        # 查询总数
        total_query = select(func.count(Organization.id)).where(*conditions)
        total_result = await db.execute(total_query)
        total = total_result.scalar() or 0
        
        # 查询分页数据
        query = select(Organization).where(*conditions).order_by(Organization.sort_order.asc()).offset(skip).limit(limit)
        result = await db.execute(query)
        organizations = result.scalars().all()
        
        org_list = []
        for org in organizations:
            org_list.append({
                "id": org.id,
                "name": org.name,
                "description": org.description,
                "color": org.color,
                "is_private": org.is_private,
                "parent_id": org.parent_id,
                "level": org.level,
                "sort_order": org.sort_order,
                "owner_id": org.owner_id,
                "created_at": org.created_at.isoformat() if org.created_at else None
            })
            
        return {
            "success": True,
            "message": "获取成功",
            "data": {
                "data": org_list,
                "total": total,
                "page": page,
                "page_size": page_size
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取组织列表失败: {str(e)}")

@router.get("/{org_id}", summary="获取组织信息", dependencies=[Depends(permission_required([PermissionType.VIEW_ORGANIZATION], organization_id_param='org_id'))])
async def get_organization(
    org_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """获取组织信息"""
    query = select(Organization).where(Organization.id == org_id)
    result = await db.execute(query)
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail="组织不存在")
    return {"success": True, "data": org}

@router.put("/{org_id}", summary="更新组织信息", dependencies=[Depends(permission_required([PermissionType.UPDATE_ORGANIZATION], organization_id_param='org_id'))])
async def update_organization(
    org_id: int,
    org_in: OrganizationUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """更新组织信息，包括拖拽引起的父级和排序变更"""
    try:
        updated_org = await organization_service.update_organization(
            db, org_id, org_in.model_dump(exclude_unset=True)
        )
        if not updated_org:
            raise HTTPException(status_code=404, detail="组织不存在")
            
        return {"success": True, "message": "更新成功"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"更新失败: {str(e)}")

@router.delete("/batch", summary="批量删除组织", dependencies=[Depends(permission_required([PermissionType.DELETE_ORGANIZATION]))])
async def batch_delete_organizations(
    data: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """批量删除选中的组织及其子组织"""
    ids = data.get("ids", [])
    if not ids:
        return {"success": True, "message": "无选中项"}
        
    success = await organization_service.batch_delete_organizations(db, ids)
    if success:
        await audit_service.log_activity(
            user_id=current_user.id,
            action="delete_organization",
            target_type="organization",
            target_id=",".join(str(i) for i in ids),
            detail=f"批量删除 {len(ids)} 个组织",
            db=db
        )
        return {"success": True, "message": f"成功删除 {len(ids)} 个组织"}
    else:
        raise HTTPException(status_code=500, detail="批量删除失败")

@router.delete("/{org_id}", summary="删除组织", dependencies=[Depends(permission_required([PermissionType.DELETE_ORGANIZATION], organization_id_param='org_id'))])
async def delete_organization(
    org_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """彻底删除组织及其所有子组织"""
    success = await organization_service.delete_organization_thoroughly(db, org_id)
    if success:
        await audit_service.log_activity(
            user_id=current_user.id,
            action="delete_organization",
            target_type="organization",
            target_id=str(org_id),
            detail=f"删除组织 ID={org_id}",
            db=db
        )
        return {"success": True, "message": "删除成功"}
    else:
        raise HTTPException(status_code=500, detail="删除组织失败")

@router.get("/{org_id}/members", summary="获取组织成员列表", dependencies=[Depends(permission_required([PermissionType.VIEW_ORGANIZATION], organization_id_param='org_id'))])
async def get_organization_members(
    org_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """获取组织成员列表"""
    try:
        # 检查组织是否存在
        org_query = select(Organization).where(Organization.id == org_id)
        org_result = await db.execute(org_query)
        if not org_result.scalar_one_or_none():
            raise HTTPException(status_code=404, detail="组织不存在")
            
        # 查询属于该组织的用户
        user_query = select(User).where(User.organization_id == org_id)
        user_result = await db.execute(user_query)
        users = user_result.scalars().all()
        
        member_list = []
        for user in users:
            member_list.append({
                "id": user.id,
                "username": user.username,
                "nickname": user.full_name or user.username,
                "email": user.email,
                "role": user.role,
                "is_active": user.is_active,
                "last_login_at": user.last_login_at.isoformat() if user.last_login_at else None
            })
            
        return {"success": True, "data": member_list}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取成员列表失败: {str(e)}")

@router.post("/{org_id}/members/{user_id}", summary="添加成员到组织", dependencies=[Depends(permission_required([PermissionType.UPDATE_ORGANIZATION], organization_id_param='org_id'))])
async def add_organization_member(
    org_id: int,
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    org = (await db.execute(select(Organization).where(Organization.id == org_id))).scalar_one_or_none()
    if not org:
        raise HTTPException(status_code=404, detail="组织不存在")

    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    user_obj: Any = user
    old_org_id = user_obj.organization_id
    user_obj.organization_id = org_id
    await db.commit()

    await create_notification(
        db,
        user_id=user_id,
        title="组织成员变更",
        content=f"您已被加入组织《{org.name}》。",
        type="organization",
        target_route="Organizations",
        target_id=str(org_id),
    )

    if old_org_id and old_org_id != org_id:
        await create_notification(
            db,
            user_id=current_user.id,
            title="组织成员已调整",
            content=f"成员 {user.username} 已从组织 {old_org_id} 调整到《{org.name}》。",
            type="organization",
            target_route="Organizations",
            target_id=str(org_id),
        )

    return {"success": True, "message": "成员已加入组织"}

@router.patch("/{org_id}/members/{user_id}/role", summary="更新组织成员角色", dependencies=[Depends(permission_required([PermissionType.UPDATE_ORGANIZATION], organization_id_param='org_id'))])
async def update_organization_member_role(
    org_id: int,
    user_id: int,
    role: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    if role not in ["user", "admin"]:
        raise HTTPException(status_code=400, detail="无效角色")

    user = (await db.execute(select(User).where(User.id == user_id, User.organization_id == org_id))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="组织成员不存在")

    user_obj: Any = user
    user_obj.role = role
    await db.commit()

    await create_notification(
        db,
        user_id=user_id,
        title="组织角色变更",
        content=f"您在组织中的角色已调整为 {role}。",
        type="organization",
        target_route="Organizations",
        target_id=str(org_id),
    )

    return {"success": True, "message": "角色更新成功"}

@router.delete("/{org_id}/members/{user_id}", summary="移除组织成员", dependencies=[Depends(permission_required([PermissionType.UPDATE_ORGANIZATION], organization_id_param='org_id'))])
async def remove_organization_member(
    org_id: int,
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    user = (await db.execute(select(User).where(User.id == user_id, User.organization_id == org_id))).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="组织成员不存在")

    user_obj: Any = user
    user_obj.organization_id = None
    await db.commit()

    await create_notification(
        db,
        user_id=user_id,
        title="组织成员变更",
        content="您已被移出当前组织。",
        type="organization",
        target_route="Organizations",
        target_id=str(org_id),
    )

    return {"success": True, "message": "成员已移除"}

@router.get("/{org_id}/documents", summary="获取组织关联文档", dependencies=[Depends(permission_required([PermissionType.VIEW_ORGANIZATION], organization_id_param='org_id'))])
async def get_organization_documents(
    org_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """获取组织关联的知识库文档"""
    try:
        from app.models.document import Document
        query = select(Document).where(Document.organization_id == org_id)
        result = await db.execute(query)
        documents = result.scalars().all()
        
        doc_list = []
        for doc in documents:
            doc_list.append({
                "id": doc.id,
                "title": doc.title,
                "file_type": doc.file_type,
                "status": doc.status,
                "created_at": doc.created_at.isoformat() if doc.created_at else None
            })
            
        return {"success": True, "data": doc_list}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取关联文档失败: {str(e)}")

