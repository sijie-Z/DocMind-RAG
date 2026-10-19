"""
DocMind AI知识库系统 - 组织管理端点
"""

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.endpoints.notifications import create_notification
from app.core.database import get_db
from app.core.security import (
    get_current_user,
    get_user_org_id,
    is_platform_admin,
    permission_required,
)
from app.exceptions import (
    AppError,
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.models.organization import Organization, user_organization
from app.models.rbac import PermissionType, user_organization_role_association
from app.models.user import User
from app.schemas.organization import OrganizationCreate, OrganizationUpdate
from app.services.audit_service import audit_service
from app.services.organization_service import organization_service
from app.services.permission_service import permission_service

router = APIRouter()


def _ensure_org_membership(current_user: User, org_id: int) -> None:
    """S1 安全加固：非超管必须属于目标组织，否则抛出 403。

    W0-1（ADR-001 D3）：权限依赖按 per-org 角色判定（权限只在该组织内成立），
    成员资格仍需在这里显式校验，二者缺一不可。
    """
    if getattr(current_user, "is_superuser", False):
        return
    if current_user.organization_id != org_id:
        raise AuthorizationError("无权访问该组织的数据")

@router.get("/tree", response_model=dict, summary="获取组织架构树", dependencies=[Depends(permission_required([PermissionType.VIEW_ORGANIZATION]))])
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
    except AppError:
        # 领域异常自带正确的状态码（如 AuthenticationError → 401，issue #82）。
        # 不先放行的话，它会被下面的 `except Exception` 重新包装成 500 —— 与
        # issue #90 是同一修法。本文件其余端点（如 create_organization）同理。
        raise
    except Exception as e:
        raise AppError(f"获取组织架构树失败: {str(e)}")

@router.post("/", response_model=dict, summary="创建组织", dependencies=[Depends(permission_required([PermissionType.CREATE_ORGANIZATION]))])
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
            raise ValidationError("组织名称已存在")

        new_org = await organization_service.create_organization(
            db, org_in.model_dump(), current_user.id
        )
        await db.commit()

        return {
            "success": True,
            "message": "创建成功",
            "data": {
                "id": new_org.id,
                "name": new_org.name
            }
        }
    except (AppError, NotFoundError, ValidationError, AuthorizationError, ConflictError):
        raise
    except Exception as e:
        raise AppError(f"创建组织失败: {str(e)}")

@router.get("/{org_id}/stats", response_model=dict, summary="获取组织统计信息", dependencies=[Depends(permission_required([PermissionType.VIEW_ORGANIZATION], organization_id_param='org_id'))])
async def get_organization_stats(
    org_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """获取组织的成员数、文档数等统计数据"""
    _ensure_org_membership(current_user, org_id)
    stats = await organization_service.get_org_stats(db, org_id)
    return {
        "success": True,
        "data": stats
    }

@router.get("/", response_model=dict, summary="获取组织列表", dependencies=[Depends(permission_required([PermissionType.VIEW_ORGANIZATION]))])
async def get_organizations(
    page: int = 1,
    page_size: int = 10,
    search: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """获取组织列表"""
    try:
        skip = (page - 1) * page_size
        limit = page_size

        conditions = []
        # W0-1：组织列表是跨租户信息面——平台管理员可看全部；组织管理员/
        # 普通账号只能看到自己的组织（无组织 fail closed：403，不做任何兜底）。
        if not is_platform_admin(current_user):
            org_id = get_user_org_id(current_user)
            if org_id is None:
                raise AuthorizationError("当前账号无组织上下文，无法查看组织列表")
            conditions.append(Organization.id == org_id)
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
    except AppError:
        # 放行：上面 fail-closed 的 403（以及其它业务异常）不得被降级为 500。
        raise
    except Exception as e:
        raise AppError(f"获取组织列表失败: {str(e)}")

@router.get("/{org_id}", response_model=dict, summary="获取组织信息", dependencies=[Depends(permission_required([PermissionType.VIEW_ORGANIZATION], organization_id_param='org_id'))])
async def get_organization(
    org_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """获取组织信息"""
    _ensure_org_membership(current_user, org_id)
    query = select(Organization).where(Organization.id == org_id)
    result = await db.execute(query)
    org = result.scalar_one_or_none()
    if not org:
        raise NotFoundError("组织不存在")
    # issue #169：这里曾直接外传 SQLAlchemy ORM 实例，FastAPI 序列化时抛
    # PydanticSerializationError → 必然 500。改为与 tree / list 端点同源的
    # 手工字典（_org_to_dict），只暴露前端 Organization 接口需要的字段，
    # 不顺带把 ORM 全字段（owner_id / updated_at 等）倒给调用方。
    return {"success": True, "data": organization_service._org_to_dict(org)}

@router.put("/{org_id}", response_model=dict, summary="更新组织信息", dependencies=[Depends(permission_required([PermissionType.UPDATE_ORGANIZATION], organization_id_param='org_id'))])
async def update_organization(
    org_id: int,
    org_in: OrganizationUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """更新组织信息，包括拖拽引起的父级和排序变更"""
    _ensure_org_membership(current_user, org_id)
    updates = org_in.model_dump(exclude_unset=True)
    # W0-1：组织层级（parent_id）是跨租户结构写——组织管理员可改本组织
    # 展示字段与排序，但不得把组织挂到其他组织下；层级调整仅平台管理员。
    # 只在「值确实变化」时拒绝：前端编辑/拖拽会回传未变化的 parent_id。
    if "parent_id" in updates and not is_platform_admin(current_user):
        current_org = await db.get(Organization, org_id)
        if current_org is None:
            raise NotFoundError("组织不存在")
        if updates["parent_id"] != current_org.parent_id:
            raise AuthorizationError("仅平台管理员可调整组织层级")
    try:
        updated_org = await organization_service.update_organization(
            db, org_id, updates
        )
        if not updated_org:
            raise NotFoundError("组织不存在")

        await db.commit()
        return {"success": True, "message": "更新成功"}
    except AppError:
        # issue #98：NotFoundError 等控制流异常自带正确状态码，先放行，
        # 否则会被下面的 except Exception 重新包装成 500。
        raise
    except ValueError as e:
        raise ValidationError(str(e))
    except Exception as e:
        raise AppError(f"更新失败: {str(e)}")

@router.delete("/batch", response_model=dict, summary="批量删除组织", dependencies=[Depends(permission_required([PermissionType.DELETE_ORGANIZATION]))])
async def batch_delete_organizations(
    data: dict,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """批量删除选中的组织及其子组织"""
    ids = data.get("ids", [])
    if not ids:
        return {"success": True, "message": "无选中项"}

    # S1 安全加固：非超管必须对每一个待删除组织都有成员资格，杜绝跨组织批量删除
    try:
        org_ids = [int(i) for i in ids]
    except (TypeError, ValueError):
        raise ValidationError("无效的组织ID")
    for target_org_id in org_ids:
        _ensure_org_membership(current_user, target_org_id)

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
        await db.commit()
        return {"success": True, "message": f"成功删除 {len(ids)} 个组织"}
    raise AppError("批量删除失败")

@router.delete("/{org_id}", response_model=dict, summary="删除组织", dependencies=[Depends(permission_required([PermissionType.DELETE_ORGANIZATION], organization_id_param='org_id'))])
async def delete_organization(
    org_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """彻底删除组织及其所有子组织"""
    _ensure_org_membership(current_user, org_id)
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
        await db.commit()
        return {"success": True, "message": "删除成功"}
    raise AppError("删除组织失败")

@router.get("/{org_id}/members", response_model=dict, summary="获取组织成员列表", dependencies=[Depends(permission_required([PermissionType.VIEW_ORGANIZATION], organization_id_param='org_id'))])
async def get_organization_members(
    org_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """获取组织成员列表"""
    _ensure_org_membership(current_user, org_id)
    try:
        # 检查组织是否存在
        org_query = select(Organization).where(Organization.id == org_id)
        org_result = await db.execute(org_query)
        if not org_result.scalar_one_or_none():
            raise NotFoundError("组织不存在")

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
    except AppError:
        # issue #98：NotFoundError 自带 404，先放行，避免被下面的 except Exception 降级为 500。
        raise
    except Exception as e:
        raise AppError(f"获取成员列表失败: {str(e)}")

@router.post("/{org_id}/members/{user_id}", response_model=dict, summary="添加成员到组织", dependencies=[Depends(permission_required([PermissionType.UPDATE_ORGANIZATION], organization_id_param='org_id'))])
async def add_organization_member(
    org_id: int,
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    _ensure_org_membership(current_user, org_id)
    org = (await db.execute(select(Organization).where(Organization.id == org_id))).scalar_one_or_none()
    if not org:
        raise NotFoundError("组织不存在")

    user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if not user:
        raise NotFoundError("用户不存在")

    user_obj: Any = user
    # W0-1（blocker）：非平台管理员不得跨组织拉人，也不得操作平台超管。
    # 先判跨组织归属（对所有非本组织目标同一响应），再判超管，避免枚举 oracle。
    if (
        not current_user.is_superuser
        and user_obj.organization_id not in (None, org_id)
    ):
        raise AuthorizationError("无权将其他组织的用户加入本组织")
    if user_obj.is_superuser and not current_user.is_superuser:
        raise AuthorizationError("无权将其他组织的用户加入本组织")

    old_org_id = user_obj.organization_id
    user_obj.organization_id = org_id
    # W0-1：调离组织时撤销旧组织的 OrgAdmin 关联，避免遗留跨组织管理权。
    if old_org_id is not None and old_org_id != org_id:
        await permission_service.set_org_admin(db, user_id, old_org_id, False)
        user_obj.role = "user"
    # A2 P0：成员表是 token 组织回退的权威依据——加入组织必须同步写入
    # user_organization 成员关系，否则成员被移出后旧 token 仍会被成员校验放行。
    await organization_service.add_user_to_organization(db, user_obj, org)
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

@router.patch("/{org_id}/members/{user_id}/role", response_model=dict, summary="更新组织成员角色", dependencies=[Depends(permission_required([PermissionType.UPDATE_ORGANIZATION], organization_id_param='org_id'))])
async def update_organization_member_role(
    org_id: int,
    user_id: int,
    role: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    _ensure_org_membership(current_user, org_id)
    if role not in ["user", "admin"]:
        raise ValidationError("无效角色")

    user = (await db.execute(select(User).where(User.id == user_id, User.organization_id == org_id))).scalar_one_or_none()
    if not user:
        raise NotFoundError("组织成员不存在")

    user_obj: Any = user
    # W0-1：非平台管理员不得改动平台超管的组织角色。
    if user_obj.is_superuser and not current_user.is_superuser:
        raise AuthorizationError("无权操作平台管理员账号")
    # W0-1：组织管理员权限的唯一权威是 org 角色关联；users.role 仅保留为
    # 展示标签（鉴权路径不再读取，见 CI 门禁 check_rbac_admin_bypass.py）。
    await permission_service.set_org_admin(db, user_id, org_id, role == "admin")
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

@router.delete("/{org_id}/members/{user_id}", response_model=dict, summary="移除组织成员", dependencies=[Depends(permission_required([PermissionType.UPDATE_ORGANIZATION], organization_id_param='org_id'))])
async def remove_organization_member(
    org_id: int,
    user_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    _ensure_org_membership(current_user, org_id)
    user = (await db.execute(select(User).where(User.id == user_id, User.organization_id == org_id))).scalar_one_or_none()
    if not user:
        raise NotFoundError("组织成员不存在")

    user_obj: Any = user
    # W0-1：非平台管理员不得移除平台超管（防组织管理员把超管踢出组织）。
    if user_obj.is_superuser and not current_user.is_superuser:
        raise AuthorizationError("无权操作平台管理员账号")
    # W0-1：移除成员即失去该组织的管理身份；users.role 展示标签同步降级
    # （权限判定只认角色关联，标签不参与鉴权）。
    user_obj.role = "user"
    # A2（评审 m-4）：仅当用户的主组织正是被移除的组织时才清空主组织字段；
    # 用户若同时属于其他组织（主组织指向别处），不得被这次移除连带清空。
    if user_obj.organization_id == org_id:
        user_obj.organization_id = None
    # A2 P0：删除成员关系，使被移出成员的旧 token 组织回退立即失效（403），
    # 不等待 token 过期。同时清理该用户在本组织的角色关联（重新加入不继承旧角色，
    # 也避免组织删除时因角色关联外键触发 1451）。
    await db.execute(
        delete(user_organization_role_association).where(
            and_(
                user_organization_role_association.c.user_id == user_id,
                user_organization_role_association.c.organization_id == org_id,
            )
        )
    )
    await db.execute(
        delete(user_organization).where(
            and_(
                user_organization.c.user_id == user_id,
                user_organization.c.organization_id == org_id,
            )
        )
    )
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

@router.get("/{org_id}/documents", response_model=dict, summary="获取组织关联文档", dependencies=[Depends(permission_required([PermissionType.VIEW_ORGANIZATION], organization_id_param='org_id'))])
async def get_organization_documents(
    org_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """获取组织关联的知识库文档"""
    _ensure_org_membership(current_user, org_id)
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
        raise AppError(f"获取关联文档失败: {str(e)}")

