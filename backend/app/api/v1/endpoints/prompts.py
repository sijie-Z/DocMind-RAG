"""API endpoints for prompt template management with version history."""
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user
from app.exceptions import AuthorizationError, ConflictError, NotFoundError
from app.models.prompt import PromptTemplate
from app.models.prompt_version import PromptTemplateVersion
from app.models.user import User
from app.schemas.prompt import (
    PromptTemplateCreate,
    PromptTemplateResponse,
    PromptTemplateUpdateWithNote,
    PromptTemplateVersionResponse,
)

router = APIRouter()


@router.get("/", response_model=list[PromptTemplateResponse])
async def list_prompts(
    db: AsyncSession = Depends(get_db),
    skip: int = 0,
    limit: int = Query(100, ge=1, le=500),
    category: str = None,
    current_user: User = Depends(get_current_user),
):
    """获取提示词模板列表 (所有用户可见)"""
    stmt = select(PromptTemplate)
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
    """创建新的提示词模板 (所有用户可创建)"""
    existing_prompt = await db.execute(
        select(PromptTemplate).filter(PromptTemplate.name == prompt_in.name)
    )
    if existing_prompt.scalars().first():
        raise ConflictError(detail="模板名称已存在")

    db_prompt = PromptTemplate(
        **prompt_in.model_dump(),
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

    return db_prompt


@router.put("/{prompt_id}", response_model=PromptTemplateResponse)
async def update_prompt(
    prompt_id: int,
    prompt_in: PromptTemplateUpdateWithNote,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """更新提示词模板 — 自动创建版本快照 (创建者或管理员可修改)"""
    result = await db.execute(
        select(PromptTemplate).filter(PromptTemplate.id == prompt_id)
    )
    db_prompt = result.scalars().first()
    if not db_prompt:
        raise NotFoundError(detail="模板不存在")

    # Permission check
    if db_prompt.creator_id != current_user.id and current_user.role != "admin":
        raise AuthorizationError(detail="无权修改此模板")

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
    update_data = prompt_in.model_dump(exclude_unset=True, exclude={"change_note"})
    for field, value in update_data.items():
        setattr(db_prompt, field, value)

    db_prompt.version += 1
    db_prompt.updated_at = datetime.now(UTC)

    await db.commit()
    await db.refresh(db_prompt)
    return db_prompt


@router.get("/{prompt_id}/versions", response_model=list[PromptTemplateVersionResponse])
async def get_prompt_versions(
    prompt_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取模板版本历史"""
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
    result = await db.execute(
        select(PromptTemplate).filter(PromptTemplate.id == prompt_id)
    )
    db_prompt = result.scalars().first()
    if not db_prompt:
        raise NotFoundError(detail="模板不存在")

    if db_prompt.creator_id != current_user.id and current_user.role != "admin":
        raise AuthorizationError(detail="无权修改此模板")

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
    """删除提示词模板 (创建者或管理员可删除)"""
    result = await db.execute(
        select(PromptTemplate).filter(PromptTemplate.id == prompt_id)
    )
    db_prompt = result.scalars().first()
    if not db_prompt:
        raise NotFoundError(detail="模板不存在")

    if db_prompt.creator_id != current_user.id and current_user.role != "admin":
        raise AuthorizationError(detail="无权删除此模板")

    await db.delete(db_prompt)
    await db.commit()
    return {"message": "模板删除成功"}


@router.post("/seed", response_model=dict)
async def seed_default_prompts(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """初始化默认提示词模板 (管理员)"""
    if current_user.role != "admin":
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
            db_p = PromptTemplate(**p, creator_id=current_user.id, version=1)
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
