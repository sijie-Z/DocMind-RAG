"""API endpoints for prompt template management with version history."""
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import (
    get_current_user,
    is_org_admin,
    is_platform_admin,
    require_organization,
)
from app.exceptions import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from app.models.prompt import PromptTemplate
from app.models.prompt_version import PromptTemplateVersion
from app.models.user import User
from app.schemas.prompt import (
    PromptTemplateCreate,
    PromptTemplateResponse,
    PromptTemplateUpdateWithNote,
    PromptTemplateVersionResponse,
)
from app.services.audit_service import audit_service

router = APIRouter()


async def _resolve_prompt_org(current_user: User, db: AsyncSession) -> int | None:
    """读取侧组织上下文：超管不限组织；其余用户 fail-closed（无组织 403）。"""
    if current_user.is_superuser:
        return None
    return await require_organization(current_user, db)


def _scope_prompt_query(stmt, current_user: User, org_id: int | None):
    """租户边界（A2）+ 三层可见性（B4/D6）。

    - 系统模板：``is_system=True AND organization_id IS NULL``，全组织只读可见；
    - 组织模板：``organization_id == 本组织 AND scope='organization'``，成员可见；
    - 个人模板：``organization_id == 本组织 AND scope='personal'
      AND creator_id == 当前用户``，仅创建者可见（只影响自己）。

    迁移无法回填归属的历史非系统行（organization_id IS NULL, is_system=False）
    不在此列：它们只对超管可见（隔离），绝不再被所有租户当成系统模板读取。
    """
    if current_user.is_superuser:
        return stmt
    return stmt.where(
        or_(
            and_(
                PromptTemplate.is_system.is_(True),
                PromptTemplate.organization_id.is_(None),
            ),
            and_(
                PromptTemplate.organization_id == org_id,
                or_(
                    PromptTemplate.scope == "organization",
                    and_(
                        PromptTemplate.scope == "personal",
                        PromptTemplate.creator_id == current_user.id,
                    ),
                ),
            ),
        )
    )


async def _load_prompt_in_scope(
    db: AsyncSession,
    current_user: User,
    prompt_id: int,
    org_id: int | None,
) -> PromptTemplate:
    """按可见范围加载模板；跨组织一律 404（不泄露存在性）。"""
    stmt = _scope_prompt_query(
        select(PromptTemplate).where(PromptTemplate.id == prompt_id),
        current_user,
        org_id,
    )
    db_prompt = (await db.execute(stmt)).scalars().first()
    if not db_prompt:
        raise NotFoundError(detail="模板不存在")
    return db_prompt


async def _ensure_prompt_writable(
    db: AsyncSession, current_user: User, db_prompt: PromptTemplate
) -> None:
    """按层级校验写权限（组织边界由 _load_prompt_in_scope 保证）。

    - 系统模板：只读；
    - 个人模板：仅创建者（超管可修正）；
    - 组织模板：创建者或**本组织**的管理员（OrgAdmin 组织角色，W0-1）。
    """
    if db_prompt.is_system or db_prompt.scope == "system":
        raise AuthorizationError(detail="系统内置模板为只读，不允许修改或删除")
    if current_user.is_superuser:
        return
    if db_prompt.organization_id is None:
        # 历史孤儿行（organization_id=NULL 且非系统）：仅超管可达。
        raise AuthorizationError(detail="无权修改此模板")
    if db_prompt.scope == "personal":
        if db_prompt.creator_id != current_user.id:
            raise AuthorizationError(detail="个人模板仅创建者可修改")
        return
    # B4（D6 评审 B4-02）：组织模板一旦被管理员激活，就成为全组织运行配置；
    # 非管理员（即使创建者）不得再改内容/名称/描述或删除/回滚，避免绕过
    # 「管理员激活」门改动线上提示词。草稿态（is_active=False）仍由创建者维护。
    if db_prompt.scope == "organization" and db_prompt.is_active:
        if not await is_org_admin(db, current_user, db_prompt.organization_id):
            raise AuthorizationError(
                detail="组织模板激活后仅组织管理员可修改、删除或回滚"
            )
        return
    if (
        db_prompt.creator_id != current_user.id
        and not await is_org_admin(db, current_user, db_prompt.organization_id)
    ):
        raise AuthorizationError(detail="无权修改此模板")


@router.get("/", response_model=list[PromptTemplateResponse])
async def list_prompts(
    db: AsyncSession = Depends(get_db),
    skip: int = 0,
    limit: int = Query(100, ge=1, le=500),
    category: str = None,
    current_user: User = Depends(get_current_user),
):
    """获取提示词模板列表（本组织模板 + 系统内置模板）"""
    org_id = await _resolve_prompt_org(current_user, db)
    stmt = _scope_prompt_query(select(PromptTemplate), current_user, org_id)
    if category:
        stmt = stmt.filter(PromptTemplate.category == category)

    result = await db.execute(stmt.offset(skip).limit(limit))
    return result.scalars().all()


@router.post("/", response_model=PromptTemplateResponse)
async def create_prompt(
    prompt_in: PromptTemplateCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """创建新的提示词模板（落在调用者组织；客户端不得伪造系统模板）

    B4（D6）：``scope`` 只接受 organization（默认）/ personal；
    personal 模板仅创建者可见、可激活（只影响自己）。
    """
    org_id = await require_organization(current_user, db)
    requested_scope = (prompt_in.scope or "organization").strip().lower()
    if requested_scope == "system":
        raise AuthorizationError(detail="系统内置模板只能由系统初始化写入")
    if requested_scope not in ("organization", "personal"):
        raise ValidationError("模板层级非法：仅支持 organization / personal")
    # 重名检查按「可见范围」：本组织模板 + 系统内置模板；其他组织的同名模板不冲突。
    existing_prompt = await db.execute(
        select(PromptTemplate).filter(
            PromptTemplate.name == prompt_in.name,
            or_(
                PromptTemplate.organization_id == org_id,
                PromptTemplate.organization_id.is_(None),
            ),
        )
    )
    if existing_prompt.scalars().first():
        raise ConflictError(detail="模板名称已存在")

    data = prompt_in.model_dump()
    # 安全加固（A2）：is_system 只能由 seed 端点（管理员）写入；
    # 普通创建一律为组织/个人模板，防止伪造全组织可见的「系统模板」。
    data["is_system"] = False
    data["scope"] = requested_scope
    # B4（D6）：组织模板的激活是组织管理员动作。普通创建者若可「创建即激活」，
    # 就绕过了管理员激活门与审计——统一落草稿（is_active=False），由管理员激活。
    creator_can_activate_org = await is_org_admin(db, current_user, org_id)
    if requested_scope == "organization" and not creator_can_activate_org:
        data["is_active"] = False
    db_prompt = PromptTemplate(
        **data,
        organization_id=org_id,
        creator_id=current_user.id,
        version=1,
    )
    db.add(db_prompt)
    await db.commit()
    await db.refresh(db_prompt)

    # Auto-create initial version
    version = PromptTemplateVersion(
        prompt_id=db_prompt.id,
        version=1,
        name=db_prompt.name,
        content=db_prompt.content,
        description=db_prompt.description,
        change_note="初始版本",
        creator_id=current_user.id,
    )
    db.add(version)
    await db.commit()

    # 「激活即审计」：管理员创建即激活的组织模板同样是一次激活动作。
    if requested_scope == "organization" and db_prompt.is_active:
        await audit_service.log_activity(
            user_id=current_user.id,
            action="activate_prompt",
            target_type="prompt_template",
            target_id=str(db_prompt.id),
            detail=f"创建并激活组织提示词模板《{db_prompt.name}》（organization）",
        )

    return db_prompt


@router.put("/{prompt_id}", response_model=PromptTemplateResponse)
async def update_prompt(
    prompt_id: int,
    prompt_in: PromptTemplateUpdateWithNote,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """更新提示词模板 — 自动创建版本快照 (创建者或组织管理员可修改)"""
    org_id = await _resolve_prompt_org(current_user, db)
    db_prompt = await _load_prompt_in_scope(db, current_user, prompt_id, org_id)
    await _ensure_prompt_writable(db, current_user, db_prompt)

    update_data = prompt_in.model_dump(exclude_unset=True, exclude={"change_note"})

    # 安全加固（A2）+ B4（D6）：激活/停用必须审计；
    # 组织模板仅组织管理员可切换；个人模板由创建者自己切换（只影响自己）。
    # 前端编辑表单会回传未变化的 is_active，因此只在值真正变化时触发。
    is_active_change = (
        "is_active" in update_data
        and update_data["is_active"] is not None
        and update_data["is_active"] != db_prompt.is_active
    )
    if is_active_change:
        if current_user.is_superuser:
            pass
        elif db_prompt.scope == "personal":
            if db_prompt.creator_id != current_user.id:
                raise AuthorizationError(detail="个人模板仅创建者可激活")
        elif not await is_org_admin(db, current_user, db_prompt.organization_id):
            raise AuthorizationError(detail="激活或停用组织模板需要管理员权限")

    # Snapshot current state as a version BEFORE applying changes
    old_version = PromptTemplateVersion(
        prompt_id=db_prompt.id,
        version=db_prompt.version + 1,
        name=db_prompt.name,
        content=db_prompt.content,
        description=db_prompt.description,
        change_note=prompt_in.change_note or f"更新至 v{db_prompt.version + 1}",
        creator_id=current_user.id,
    )
    db.add(old_version)

    # Apply changes
    for field, value in update_data.items():
        setattr(db_prompt, field, value)

    db_prompt.version += 1
    db_prompt.updated_at = datetime.now(UTC)

    await db.commit()
    await db.refresh(db_prompt)

    if is_active_change:
        activated = bool(db_prompt.is_active)
        # 审计为旁路（独立 session，失败只记日志）：不得回滚已生效的激活状态。
        await audit_service.log_activity(
            user_id=current_user.id,
            action="activate_prompt" if activated else "deactivate_prompt",
            target_type="prompt_template",
            target_id=str(db_prompt.id),
            detail=(
                f"{'激活' if activated else '停用'}提示词模板《{db_prompt.name}》"
                f"（{db_prompt.scope}）"
            ),
            db=db,
        )
    return db_prompt


@router.get("/{prompt_id}/versions", response_model=list[PromptTemplateVersionResponse])
async def get_prompt_versions(
    prompt_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取模板版本历史"""
    org_id = await _resolve_prompt_org(current_user, db)
    await _load_prompt_in_scope(db, current_user, prompt_id, org_id)
    result = await db.execute(
        select(PromptTemplateVersion)
        .filter(PromptTemplateVersion.prompt_id == prompt_id)
        .order_by(desc(PromptTemplateVersion.version))
    )
    return result.scalars().all()


@router.post("/{prompt_id}/restore/{version}", response_model=PromptTemplateResponse)
async def restore_prompt_version(
    prompt_id: int,
    version: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """将模板恢复到指定版本"""
    org_id = await _resolve_prompt_org(current_user, db)
    db_prompt = await _load_prompt_in_scope(db, current_user, prompt_id, org_id)
    await _ensure_prompt_writable(db, current_user, db_prompt)

    # Find the version to restore
    version_result = await db.execute(
        select(PromptTemplateVersion).filter(
            PromptTemplateVersion.prompt_id == prompt_id,
            PromptTemplateVersion.version == version,
        )
    )
    ver = version_result.scalars().first()
    if not ver:
        raise NotFoundError(detail="版本不存在")

    # Snapshot current state before restoring
    old_version = PromptTemplateVersion(
        prompt_id=db_prompt.id,
        version=db_prompt.version + 1,
        name=db_prompt.name,
        content=db_prompt.content,
        description=db_prompt.description,
        change_note=f"回滚到 v{version}",
        creator_id=current_user.id,
    )
    db.add(old_version)

    # Restore from version
    db_prompt.name = ver.name
    db_prompt.content = ver.content
    db_prompt.description = ver.description
    db_prompt.version += 1
    db_prompt.updated_at = datetime.now(UTC)

    await db.commit()
    await db.refresh(db_prompt)
    return db_prompt


@router.get("/{prompt_id}/version/{version}", response_model=PromptTemplateVersionResponse)
async def get_prompt_version_detail(
    prompt_id: int,
    version: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取指定版本的详情"""
    org_id = await _resolve_prompt_org(current_user, db)
    await _load_prompt_in_scope(db, current_user, prompt_id, org_id)
    result = await db.execute(
        select(PromptTemplateVersion).filter(
            PromptTemplateVersion.prompt_id == prompt_id,
            PromptTemplateVersion.version == version,
        )
    )
    ver = result.scalar_one_or_none()
    if not ver:
        raise NotFoundError(detail="版本不存在")
    return ver


@router.delete("/{prompt_id}", response_model=dict)
async def delete_prompt(
    prompt_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """删除提示词模板 (创建者或组织管理员可删除)"""
    org_id = await _resolve_prompt_org(current_user, db)
    db_prompt = await _load_prompt_in_scope(db, current_user, prompt_id, org_id)
    await _ensure_prompt_writable(db, current_user, db_prompt)

    await db.delete(db_prompt)
    await db.commit()
    return {"message": "模板删除成功"}


@router.post("/seed", response_model=dict)
async def seed_default_prompts(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """初始化默认提示词模板 (平台管理员；写入系统内置模板：organization_id=NULL，只读)"""
    # W0-1：系统内置模板是平台级数据 —— 只认显式 is_superuser。
    if not is_platform_admin(current_user):
        raise AuthorizationError(detail="需要管理员权限")

    default_prompts = [
        {
            "name": "通用助手",
            "content": "你是一个专业的AI助手，请根据提供的信息回答用户问题。如果信息不足，请委婉告知。",
            "description": "默认的通用问答提示词",
            "is_system": True,
            "category": "general",
        },
        {
            "name": "精准检索",
            "content": "你是一个基于知识库的问答助手。请严格根据提供的参考文档回答。不要胡乱猜测。回答时使用 [n] 格式标注引用来源。",
            "description": "强制要求根据知识库内容回答，附带引用",
            "is_system": True,
            "category": "rag",
        },
        {
            "name": "文档摘要专家",
            "content": "你是文档摘要专家。请为以下文档生成结构化摘要，包含：\n1. **主题**：一句话概括文档核心内容\n2. **关键要点**：3-5 条主要观点\n3. **结论**：文档的最终结论或建议\n\n请使用简体中文，保持客观准确。",
            "description": "生成结构化文档摘要",
            "is_system": True,
            "category": "summary",
        },
        {
            "name": "对比分析师",
            "content": "你是对比分析专家。请根据提供的多个文档，从以下维度进行对比分析：\n1. **共同点**：各文档的共识\n2. **差异点**：不同文档的分歧\n3. **优劣势**：各自的优缺点\n4. **建议**：基于分析的行动建议\n\n请用表格形式呈现对比结果。",
            "description": "多文档对比分析",
            "is_system": True,
            "category": "analysis",
        },
        {
            "name": "技术文档翻译",
            "content": "你是专业的技术文档翻译。请将以下内容翻译为指定语言，注意：\n- 专业术语保持一致\n- 代码块不翻译\n- 保留原文格式\n- 如有歧义，在括号中注明原文",
            "description": "技术文档多语言翻译",
            "is_system": True,
            "category": "translation",
        },
    ]

    for p in default_prompts:
        existing_prompt = await db.execute(
            select(PromptTemplate).filter(PromptTemplate.name == p["name"])
        )
        if not existing_prompt.scalars().first():
            db_p = PromptTemplate(
                **p,
                organization_id=None,  # 系统内置：全组织只读可见
                scope="system",  # B4（D6）：三层语义的显式标记
                creator_id=current_user.id,
                version=1,
            )
            db.add(db_p)
            await db.flush()
            # Create initial version
            ver = PromptTemplateVersion(
                prompt_id=db_p.id,
                version=1,
                name=p["name"],
                content=p["content"],
                description=p.get("description"),
                change_note="初始版本",
                creator_id=current_user.id,
            )
            db.add(ver)

    await db.commit()
    return {"message": "Default prompts seeded"}
