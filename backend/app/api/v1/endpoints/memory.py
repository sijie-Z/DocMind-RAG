"""Agent 记忆系统 API 端点"""

import re
from typing import Any

from fastapi import APIRouter, Depends

from app.core.security import get_current_user, get_user_org_id
from app.exceptions import AuthorizationError, ValidationError
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

# agent_id 会拼入全局 memory dict 的 key，必须严格限制字符与长度，防止内存 DoS。
# 允许冒号：会话作用域的 agent_id 形如 "session:<uuid>"，
# 操作员需要用它来清理单个会话（而非该用户全部会话）的记忆。
_AGENT_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_:-]{1,64}$")


def _normalize_agent_id(agent_id: str) -> str:
    """校验并规范化 agent_id：空值回退为 "default"，非法字符抛 ValidationError"""
    agent_id = (agent_id or "").strip()
    if not agent_id:
        return "default"
    if not _AGENT_ID_PATTERN.match(agent_id):
        raise ValidationError("agent_id 仅允许字母、数字、下划线、连字符、冒号，长度 1-64")
    return agent_id


async def _get_user_memory_system(current_user: User, agent_id: str):
    """Scope agent memory to the current user's org+user namespace."""
    # 安全加固（issue #268）：此前 `organization_id or 1` 把无组织用户的命名空间
    # 标成 1 号组织（仅标签错——键里另有 user_id 段，无跨租户读）。现在用真实的
    # org/user 键：无组织时该段如实为 None，绝不冒充 1 号组织。
    # W0-6：无组织不再生成 "None:..." 命名空间——显式 403 拒绝（fail-closed）。
    org_id = get_user_org_id(current_user)
    if org_id is None:
        raise AuthorizationError("当前用户未加入任何组织，无法访问 Agent 记忆")
    # A1（记忆 IDOR）：命名空间由 get_memory_system 按显式租户上下文拼装，
    # 端点不再自行拼接全局 key。
    system = get_memory_system(org_id, current_user.id, agent_id)
    await system._load_from_redis()
    return system


@router.get("/{agent_id}", response_model=dict[str, Any])
async def get_agent_memory(
    agent_id: str = "default", current_user: User = Depends(get_current_user)
):
    """获取 Agent 的完整记忆"""
    agent_id = _normalize_agent_id(agent_id)
    memory_system = await _get_user_memory_system(current_user, agent_id)
    return {"success": True, "data": memory_system.export()}


@router.post("/{agent_id}/remember", response_model=dict[str, Any])
async def store_memory(
    agent_id: str, body: RememberRequest, current_user: User = Depends(get_current_user)
):
    """存储记忆"""
    agent_id = _normalize_agent_id(agent_id)
    memory_system = await _get_user_memory_system(current_user, agent_id)
    item = await memory_system.remember(
        content=body.content,
        memory_type=body.memory_type,
        importance=body.importance,
        metadata=body.metadata,
    )
    return {
        "success": True,
        "data": {
            "id": item.id,
            "content": item.content,
            "memory_type": item.memory_type,
            "importance": item.importance,
        },
    }


@router.post("/{agent_id}/recall", response_model=dict[str, Any])
async def recall_memory(
    agent_id: str, body: RecallRequest, current_user: User = Depends(get_current_user)
):
    """检索记忆"""
    agent_id = _normalize_agent_id(agent_id)
    memory_system = await _get_user_memory_system(current_user, agent_id)
    results = await memory_system.recall(
        query=body.query, memory_types=body.memory_types, top_k=body.top_k
    )
    return {
        "success": True,
        "data": {"query": body.query, "results": results, "count": len(results)},
    }


@router.post("/{agent_id}/interaction", response_model=dict[str, Any])
async def store_interaction(
    agent_id: str, body: InteractionRequest, current_user: User = Depends(get_current_user)
):
    """存储交互记录"""
    agent_id = _normalize_agent_id(agent_id)
    memory_system = await _get_user_memory_system(current_user, agent_id)
    await memory_system.store_interaction(body.user_input, body.assistant_response)
    return {"success": True, "message": "交互已存储"}


@router.post("/{agent_id}/experience", response_model=dict[str, Any])
async def store_experience(
    agent_id: str, body: ExperienceRequest, current_user: User = Depends(get_current_user)
):
    """存储经验"""
    agent_id = _normalize_agent_id(agent_id)
    memory_system = await _get_user_memory_system(current_user, agent_id)
    await memory_system.store_experience(
        success=body.success, action=body.action, result=body.result, context=body.context or ""
    )
    return {"success": True, "message": "经验已存储"}


@router.get("/{agent_id}/context", response_model=dict[str, Any])
async def get_memory_context(
    agent_id: str, query: str, current_user: User = Depends(get_current_user)
):
    """获取记忆上下文（用于LLM输入）"""
    agent_id = _normalize_agent_id(agent_id)
    memory_system = await _get_user_memory_system(current_user, agent_id)
    context = await memory_system.get_context(query)
    return {"success": True, "data": {"context": context}}


@router.delete("/{agent_id}", response_model=dict[str, Any])
async def clear_memory(
    agent_id: str, memory_type: str | None = None, current_user: User = Depends(get_current_user)
):
    """清空记忆"""
    agent_id = _normalize_agent_id(agent_id)
    memory_system = await _get_user_memory_system(current_user, agent_id)

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

    await memory_system._save_to_redis()
    return {"success": True, "message": f"记忆已清空: {memory_type or '全部'}"}


@router.post("/{agent_id}/import", response_model=dict[str, Any])
async def import_memory(
    agent_id: str, body: ImportMemoriesRequest, current_user: User = Depends(get_current_user)
):
    """导入记忆数据"""
    agent_id = _normalize_agent_id(agent_id)
    memory_system = await _get_user_memory_system(current_user, agent_id)
    memory_system.import_data(body.data)
    await memory_system._save_to_redis()
    return {"success": True, "message": "记忆导入成功"}


@router.get("/{agent_id}/insights", response_model=dict[str, Any])
async def get_insights(
    agent_id: str,
    context: str | None = None,
    top_k: int = 10,
    current_user: User = Depends(get_current_user),
):
    """获取洞察"""
    agent_id = _normalize_agent_id(agent_id)
    memory_system = await _get_user_memory_system(current_user, agent_id)
    if context:
        insights = memory_system.reflective.get_relevant_insights(context, top_k)
    else:
        insights = [i["content"] for i in memory_system.reflective.insights[-top_k:]]
    return {"success": True, "data": {"insights": insights}}


@router.get("/{agent_id}/lessons", response_model=dict[str, Any])
async def get_lessons(
    agent_id: str, situation: str | None = None, current_user: User = Depends(get_current_user)
):
    """获取经验教训"""
    agent_id = _normalize_agent_id(agent_id)
    # issue #169：此处漏了 await —— memory_system 拿到的是 coroutine，
    # 下一行 .reflective 直接 AttributeError → 必然 500（且伴随
    # "coroutine ... was never awaited" RuntimeWarning）。同文件其余 9 处
    # 调用均带 await，仅此一处漏。
    memory_system = await _get_user_memory_system(current_user, agent_id)
    if situation:
        lessons = memory_system.reflective.get_lessons_for_situation(situation)
    else:
        lessons = memory_system.reflective.lessons
    return {"success": True, "data": {"lessons": lessons}}
