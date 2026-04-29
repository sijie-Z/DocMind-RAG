# -*- coding: utf-8 -*-
from typing import List, Any
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.core.database import get_db
from app.models.prompt import PromptTemplate
from app.schemas.prompt import PromptTemplateCreate, PromptTemplateUpdate, PromptTemplateResponse
from app.core.security import get_current_user
from app.models.user import User

router = APIRouter()

@router.get("/", response_model=List[PromptTemplateResponse])
async def list_prompts(
    db: AsyncSession = Depends(get_db),
    skip: int = 0,
    limit: int = 100,
    category: str = None,
    current_user: User = Depends(get_current_user)
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
    current_user: User = Depends(get_current_user)
):
    """创建新的提示词模板 (所有用户可创建)"""
    existing_prompt = await db.execute(select(PromptTemplate).filter(PromptTemplate.name == prompt_in.name))
    if existing_prompt.scalars().first():
        raise HTTPException(status_code=400, detail="模板名称已存在")

    db_prompt = PromptTemplate(
        **prompt_in.model_dump(),
        creator_id=current_user.id
    )
    db.add(db_prompt)
    await db.commit()
    await db.refresh(db_prompt)
    return db_prompt

@router.put("/{prompt_id}", response_model=PromptTemplateResponse)
async def update_prompt(
    prompt_id: int,
    prompt_in: PromptTemplateUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """更新提示词模板 (创建者或管理员可修改)"""
    result = await db.execute(select(PromptTemplate).filter(PromptTemplate.id == prompt_id))
    db_prompt = result.scalars().first()
    if not db_prompt:
        raise HTTPException(status_code=404, detail="模板不存在")

    # 权限检查: 创建者或管理员可以修改
    if db_prompt.creator_id != current_user.id and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="无权修改此模板")

    if prompt_in.name and prompt_in.name != db_prompt.name:
        existing_prompt = await db.execute(select(PromptTemplate).filter(PromptTemplate.name == prompt_in.name))
        if existing_prompt.scalars().first():
            raise HTTPException(status_code=400, detail="模板名称已存在")

    for field, value in prompt_in.model_dump(exclude_unset=True).items():
        setattr(db_prompt, field, value)

    await db.commit()
    await db.refresh(db_prompt)
    return db_prompt

@router.delete("/{prompt_id}", response_model=dict)
async def delete_prompt(
    prompt_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """删除提示词模板 (创建者或管理员可删除)"""
    result = await db.execute(select(PromptTemplate).filter(PromptTemplate.id == prompt_id))
    db_prompt = result.scalars().first()
    if not db_prompt:
        raise HTTPException(status_code=404, detail="模板不存在")

    # 权限检查: 创建者或管理员可以删除
    if db_prompt.creator_id != current_user.id and current_user.role != "admin":
        raise HTTPException(status_code=403, detail="无权删除此模板")

    await db.delete(db_prompt)
    await db.commit()
    return {"message": "模板删除成功"}

@router.post("/seed")
async def seed_default_prompts(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """初始化默认提示词模板 (管理员)"""
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")

    default_prompts = [
        {
            "name": "通用助手",
            "content": "你是一个专业的AI助手，请根据提供的信息回答用户问题。如果信息不足，请委婉告知。",
            "description": "默认的通用问答提示词",
            "is_system": True,
            "category": "general"
        },
        {
            "name": "精准检索",
            "content": "你是一个基于知识库的问答助手。请严格根据提供的参考文档回答。不要胡乱猜测。",
            "description": "强制要求根据知识库内容回答",
            "is_system": True,
            "category": "rag"
        }
    ]

    for p in default_prompts:
        existing_prompt = await db.execute(select(PromptTemplate).filter(PromptTemplate.name == p["name"]))
        if not existing_prompt.scalars().first():
            db_p = PromptTemplate(**p, creator_id=current_user.id)
            db.add(db_p)

    await db.commit()
    return {"message": "Default prompts seeded"}
