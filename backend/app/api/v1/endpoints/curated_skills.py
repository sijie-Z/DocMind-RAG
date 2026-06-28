"""Curated Skills API endpoints — SKILL.md file-based curated skills.

Endpoints:
    GET  /api/v1/curated-skills              — list all skill summaries
    GET  /api/v1/curated-skills/{name}        — get skill detail
    GET  /api/v1/curated-skills/{name}/references/{ref_name} — get reference content
    POST /api/v1/curated-skills/reload         — force rescan skills directory
"""

import logging

from fastapi import APIRouter, Depends

from app.agent.curated_skills import curated_skill_registry
from app.core.security import get_current_user
from app.exceptions import NotFoundError
from app.models.user import User

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("")
async def list_curated_skills(
    current_user: User = Depends(get_current_user),
):
    """List all curated skill summaries (name + description)."""
    # Ensure loaded
    curated_skill_registry.load_all()
    summaries = curated_skill_registry.list_summaries()
    return {
        "success": True,
        "data": summaries,
        "total": len(summaries),
    }


@router.get("/{skill_name}")
async def get_curated_skill(
    skill_name: str,
    current_user: User = Depends(get_current_user),
):
    """Get full detail of a curated skill."""
    curated_skill_registry.load_all()
    detail = curated_skill_registry.get_detail(skill_name)
    if not detail:
        raise NotFoundError(f"技能 '{skill_name}' 不存在")
    return {"success": True, "data": detail}


@router.get("/{skill_name}/references/{ref_name}")
async def get_skill_reference(
    skill_name: str,
    ref_name: str,
    current_user: User = Depends(get_current_user),
):
    """Get a reference document for a curated skill."""
    curated_skill_registry.load_all()
    content = curated_skill_registry.load_reference(skill_name, ref_name)
    if content is None:
        raise NotFoundError(f"参考文档 '{ref_name}' 不存在于技能 '{skill_name}' 中")
    return {
        "success": True,
        "data": {
            "skill_name": skill_name,
            "reference_name": ref_name,
            "content": content,
        },
    }


@router.post("/reload")
async def reload_curated_skills(
    current_user: User = Depends(get_current_user),
):
    """Force reload all curated skills from the filesystem."""
    count = curated_skill_registry.reload()
    return {
        "success": True,
        "message": f"已重新加载 {count} 个技能",
        "total": count,
    }
