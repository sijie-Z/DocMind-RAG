"""多知识库实体 CRUD 端点（P2）。

所有端点都以 ``require_organization`` 解析的有效组织为租户边界：
- 读：knowledge:read + knowledge_base:read；
- 写：knowledge:write + knowledge_base:manage；组织管理员或超管可写。
非超管不得通过请求体指定 organization_id；跨组织 kb id 一律 404。
"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import (
    get_current_user,
    permission_required,
    require_org_admin,
    require_organization,
    require_scope,
)
from app.models.rbac import PermissionType
from app.models.user import User
from app.schemas.knowledge_base import (
    KnowledgeBaseCreate,
    KnowledgeBaseListResponse,
    KnowledgeBaseResponse,
    KnowledgeBaseUpdate,
)
from app.services.audit_service import audit_service
from app.services.knowledge_base_service import knowledge_base_service

router = APIRouter()


@router.get(
    "/",
    response_model=KnowledgeBaseListResponse,
    dependencies=[
        Depends(require_scope("knowledge:read")),
        Depends(permission_required([PermissionType.VIEW_KNOWLEDGE_BASE])),
    ],
)
async def list_knowledge_bases(
    page: int = 1,
    page_size: int = 10,
    search: str | None = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    org_id = await require_organization(current_user, db)
    items, total = await knowledge_base_service.list(
        db, org_id, page=page, page_size=page_size, search=search
    )
    return KnowledgeBaseListResponse(
        data=[KnowledgeBaseResponse.model_validate(item) for item in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post(
    "/",
    response_model=KnowledgeBaseResponse,
    status_code=201,
    dependencies=[
        Depends(require_scope("knowledge:write")),
        Depends(permission_required([PermissionType.MANAGE_KNOWLEDGE_BASE])),
    ],
)
async def create_knowledge_base(
    payload: KnowledgeBaseCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    org_id = await require_organization(current_user, db)
    await require_org_admin(db, current_user, org_id)
    kb = await knowledge_base_service.create(
        db,
        org_id,
        name=payload.name,
        description=payload.description,
        created_by=current_user.id,
    )
    await audit_service.log_activity(
        user_id=current_user.id,
        action="create_knowledge_base",
        target_type="knowledge_base",
        target_id=kb.id,
        detail=f"创建知识库: {kb.name}",
        db=db,
    )
    return KnowledgeBaseResponse.model_validate(kb)


@router.get(
    "/{knowledge_base_id}",
    response_model=KnowledgeBaseResponse,
    dependencies=[
        Depends(require_scope("knowledge:read")),
        Depends(permission_required([PermissionType.VIEW_KNOWLEDGE_BASE])),
    ],
)
async def get_knowledge_base(
    knowledge_base_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    org_id = await require_organization(current_user, db)
    kb = await knowledge_base_service.get(db, org_id, knowledge_base_id)
    return KnowledgeBaseResponse.model_validate(kb)


@router.put(
    "/{knowledge_base_id}",
    response_model=KnowledgeBaseResponse,
    dependencies=[
        Depends(require_scope("knowledge:write")),
        Depends(permission_required([PermissionType.MANAGE_KNOWLEDGE_BASE])),
    ],
)
async def update_knowledge_base(
    knowledge_base_id: str,
    payload: KnowledgeBaseUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    org_id = await require_organization(current_user, db)
    await require_org_admin(db, current_user, org_id)
    kb = await knowledge_base_service.update(
        db,
        org_id,
        knowledge_base_id,
        name=payload.name,
        description=payload.description,
    )
    await audit_service.log_activity(
        user_id=current_user.id,
        action="update_knowledge_base",
        target_type="knowledge_base",
        target_id=kb.id,
        detail=f"更新知识库: {kb.name}",
        db=db,
    )
    return KnowledgeBaseResponse.model_validate(kb)


@router.delete(
    "/{knowledge_base_id}",
    response_model=dict,
    dependencies=[
        Depends(require_scope("knowledge:write")),
        Depends(permission_required([PermissionType.MANAGE_KNOWLEDGE_BASE])),
    ],
)
async def delete_knowledge_base(
    knowledge_base_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    org_id = await require_organization(current_user, db)
    await require_org_admin(db, current_user, org_id)
    await knowledge_base_service.delete(db, org_id, knowledge_base_id)
    await audit_service.log_activity(
        user_id=current_user.id,
        action="delete_knowledge_base",
        target_type="knowledge_base",
        target_id=knowledge_base_id,
        detail="删除知识库（文档保留并解除关联；该库的公开链接一并失效）",
        db=db,
    )
    return {"success": True, "message": "知识库已删除；原库内文档已回到组织级默认库"}
