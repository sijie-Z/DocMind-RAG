"""Agent 记忆系统 API 端点"""
from typing import Any

from fastapi import APIRouter, Depends

from app.core.security import get_current_user
from app.models.user import User
from app.schemas.memory import (
    ExperienceRequest,
    ImportMemoriesRequest,
    InteractionRequest,
    RecallRequest,
    RememberRequest,
)
from app.services.memory_service import get_memory_system

router = APIRouter()


@router.get("/{agent_id}", response_model=dict[str, Any])
async def get_agent_memory(
    agent_id: str = "default",
    current_user: User = Depends(get_current_user)
):
    """获取 Agent 的完整记忆"""
    memory_system = get_memory_system(agent_id)
    return {"success": True, "data": memory_system.export()}


@router.post("/{agent_id}/remember", response_model=dict[str, Any])
async def store_memory(
    agent_id: str,
    body: RememberRequest,
    current_user: User = Depends(get_current_user)
):
    """存储记忆"""
    memory_system = get_memory_system(agent_id)
    item = await memory_system.remember(
        content=body.content,
        memory_type=body.memory_type,
        importance=body.importance,
        metadata=body.metadata
    )
    return {
        "success": True,
        "data": {
            "id": item.id,
            "content": item.content,
            "memory_type": item.memory_type,
            "importance": item.importance
        }
    }


@router.post("/{agent_id}/recall", response_model=dict[str, Any])
async def recall_memory(
    agent_id: str,
    body: RecallRequest,
    current_user: User = Depends(get_current_user)
):
    """检索记忆"""
    memory_system = get_memory_system(agent_id)
    results = await memory_system.recall(
        query=body.query,
        memory_types=body.memory_types,
        top_k=body.top_k
    )
    return {
        "success": True,
        "data": {"query": body.query, "results": results, "count": len(results)}
    }


@router.post("/{agent_id}/interaction", response_model=dict[str, Any])
async def store_interaction(
    agent_id: str,
    body: InteractionRequest,
    current_user: User = Depends(get_current_user)
):
    """存储交互记录"""
    memory_system = get_memory_system(agent_id)
    await memory_system.store_interaction(body.user_input, body.assistant_response)
    return {"success": True, "message": "交互已存储"}


@router.post("/{agent_id}/experience", response_model=dict[str, Any])
async def store_experience(
    agent_id: str,
    body: ExperienceRequest,
    current_user: User = Depends(get_current_user)
):
    """存储经验"""
    memory_system = get_memory_system(agent_id)
    await memory_system.store_experience(
        success=body.success,
        action=body.action,
        result=body.result,
        context=body.context or ""
    )
    return {"success": True, "message": "经验已存储"}


@router.get("/{agent_id}/context", response_model=dict[str, Any])
async def get_memory_context(
    agent_id: str,
    query: str,
    current_user: User = Depends(get_current_user)
):
    """获取记忆上下文（用于LLM输入）"""
    memory_system = get_memory_system(agent_id)
    context = await memory_system.get_context(query)
    return {"success": True, "data": {"context": context}}


@router.delete("/{agent_id}", response_model=dict[str, Any])
async def clear_memory(
    agent_id: str,
    memory_type: str | None = None,
    current_user: User = Depends(get_current_user)
):
    """清空记忆"""
    memory_system = get_memory_system(agent_id)

    if memory_type:
        if memory_type == "short_term":
            memory_system.short_term.clear()
        elif memory_type == "working":
            memory_system.working.clear()
        elif memory_type == "reflective":
            memory_system.reflective.insights.clear()
            memory_system.reflective.patterns.clear()
            memory_system.reflective.lessons.clear()
    else:
        memory_system.short_term.clear()
        memory_system.working.clear()
        memory_system.reflective.insights.clear()
        memory_system.reflective.patterns.clear()
        memory_system.reflective.lessons.clear()
        memory_system.long_term.memories.clear()
        memory_system.long_term.index.clear()

    return {"success": True, "message": f"记忆已清空: {memory_type or '全部'}"}


@router.post("/{agent_id}/import", response_model=dict[str, Any])
async def import_memory(
    agent_id: str,
    body: ImportMemoriesRequest,
    current_user: User = Depends(get_current_user)
):
    """导入记忆数据"""
    memory_system = get_memory_system(agent_id)
    memory_system.import_data(body.data)
    return {"success": True, "message": "记忆导入成功"}


@router.get("/{agent_id}/insights", response_model=dict[str, Any])
async def get_insights(
    agent_id: str,
    context: str | None = None,
    top_k: int = 10,
    current_user: User = Depends(get_current_user)
):
    """获取洞察"""
    memory_system = get_memory_system(agent_id)
    if context:
        insights = memory_system.reflective.get_relevant_insights(context, top_k)
    else:
        insights = [i["content"] for i in memory_system.reflective.insights[-top_k:]]
    return {"success": True, "data": {"insights": insights}}


@router.get("/{agent_id}/lessons", response_model=dict[str, Any])
async def get_lessons(
    agent_id: str,
    situation: str | None = None,
    current_user: User = Depends(get_current_user)
):
    """获取经验教训"""
    memory_system = get_memory_system(agent_id)
    if situation:
        lessons = memory_system.reflective.get_lessons_for_situation(situation)
    else:
        lessons = memory_system.reflective.lessons
    return {"success": True, "data": {"lessons": lessons}}
