from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user
from app.exceptions import AuthorizationError, ConflictError, NotFoundError
from app.models.prompt import PromptTemplate
from app.models.user import User
from app.schemas.prompt import PromptTemplateCreate, PromptTemplateResponse, PromptTemplateUpdate

router = APIRouter()

@router.get("/", response_model=list[PromptTemplateResponse])
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
        raise ConflictError(detail="模板名称已存在")

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
        raise NotFoundError(detail="模板不存在")

    # 权限检查: 创建者或管理员可以修改
    if db_prompt.creator_id != current_user.id and current_user.role != "admin":
        raise AuthorizationError(detail="无权修改此模板")

    if prompt_in.name and prompt_in.name != db_prompt.name:
        existing_prompt = await db.execute(select(PromptTemplate).filter(PromptTemplate.name == prompt_in.name))
        if existing_prompt.scalars().first():
            raise ConflictError(detail="模板名称已存在")

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
        raise NotFoundError(detail="模板不存在")

    # 权限检查: 创建者或管理员可以删除
    if db_prompt.creator_id != current_user.id and current_user.role != "admin":
        raise AuthorizationError(detail="无权删除此模板")

    await db.delete(db_prompt)
    await db.commit()
    return {"message": "模板删除成功"}

@router.post("/seed", response_model=dict)
async def seed_default_prompts(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
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
            "category": "general"
        },
        {
            "name": "精准检索",
            "content": "你是一个基于知识库的问答助手。请严格根据提供的参考文档回答。不要胡乱猜测。回答时使用 [n] 格式标注引用来源。",
            "description": "强制要求根据知识库内容回答，附带引用",
            "is_system": True,
            "category": "rag"
        },
        {
            "name": "文档摘要专家",
            "content": "你是文档摘要专家。请为以下文档生成结构化摘要，包含：\n1. **主题**：一句话概括文档核心内容\n2. **关键要点**：3-5 条主要观点\n3. **结论**：文档的最终结论或建议\n\n请使用简体中文，保持客观准确。",
            "description": "生成结构化文档摘要",
            "is_system": True,
            "category": "summary"
        },
        {
            "name": "对比分析师",
            "content": "你是对比分析专家。请根据提供的多个文档，从以下维度进行对比分析：\n1. **共同点**：各文档的共识\n2. **差异点**：不同文档的分歧\n3. **优劣势**：各自的优缺点\n4. **建议**：基于分析的行动建议\n\n请用表格形式呈现对比结果。",
            "description": "多文档对比分析",
            "is_system": True,
            "category": "analysis"
        },
        {
            "name": "技术文档翻译",
            "content": "你是专业的技术文档翻译。请将以下内容翻译为指定语言，注意：\n- 专业术语保持一致\n- 代码块不翻译\n- 保留原文格式\n- 如有歧义，在括号中注明原文",
            "description": "技术文档多语言翻译",
            "is_system": True,
            "category": "translation"
        },
        {
            "name": "FAQ 生成器",
            "content": "你是 FAQ 生成专家。请根据以下文档内容，生成常见问题解答：\n1. 提取文档中的核心知识点\n2. 针对每个知识点，生成用户最可能问的问题\n3. 基于文档内容给出准确回答\n4. 每个回答附带引用来源\n\n输出格式：Q: 问题\\nA: 回答 [来源]",
            "description": "从文档自动生成 FAQ",
            "is_system": True,
            "category": "generation"
        },
        {
            "name": "风险评估师",
            "content": "你是风险评估专家。请根据提供的文档，识别并评估潜在风险：\n1. **风险识别**：列出所有潜在风险点\n2. **影响评估**：高/中/低 三级\n3. **发生概率**：高/中/低 三级\n4. **应对建议**：针对每个风险的缓解措施\n\n请以结构化格式输出。",
            "description": "文档风险评估分析",
            "is_system": True,
            "category": "analysis"
        },
        {
            "name": "知识图谱提取",
            "content": "你是知识图谱提取专家。请从以下文档中提取：\n1. **实体**：人物、组织、地点、产品、技术\n2. **关系**：实体之间的关系（如：属于、使用、创建）\n3. **属性**：实体的关键属性\n\n输出格式为 JSON 数组。",
            "description": "从文档提取实体和关系",
            "is_system": True,
            "category": "extraction"
        },
    ]

    for p in default_prompts:
        existing_prompt = await db.execute(select(PromptTemplate).filter(PromptTemplate.name == p["name"]))
        if not existing_prompt.scalars().first():
            db_p = PromptTemplate(**p, creator_id=current_user.id)
            db.add(db_p)

    await db.commit()
    return {"message": "Default prompts seeded"}
