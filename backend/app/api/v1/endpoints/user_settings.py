"""
DocMind AI知识库系统 - 用户设置端点
"""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.models.user_settings import UserSettings
from app.schemas.user_settings import (
    ProviderSettingsUpdate,
    UserSettingsResponse,
    UserSettingsUpdate,
)
from app.services import llm_settings_service

logger = logging.getLogger(__name__)

router = APIRouter()


async def _get_or_create_settings(db: AsyncSession, user_id: int) -> UserSettings:
    """获取用户设置，不存在则创建默认设置"""
    result = await db.execute(select(UserSettings).where(UserSettings.user_id == user_id))
    settings = result.scalar_one_or_none()

    if settings is None:
        settings = UserSettings(user_id=user_id)
        db.add(settings)
        await db.commit()
        await db.refresh(settings)

    return settings


def _sanitize_settings_payload(settings_obj: UserSettings) -> dict:
    """通用 settings 响应也不得携带 AI Provider 的明文/密文 key。

    preferences 是 JSON 透传字段：若不处理，``api_key_encrypted``（或历史明文
    ``api_key``）会原样回给客户端。统一替换为掩码视图——完整配置只经
    GET/PUT /settings/providers 的专用口径暴露。
    """
    data = UserSettingsResponse.model_validate(settings_obj).model_dump()
    prefs = data.get("preferences")
    if isinstance(prefs, dict) and "ai_provider" in prefs:
        prefs = dict(prefs)
        prefs["ai_provider"] = _current_provider_view(prefs.get("ai_provider"))
        data["preferences"] = prefs
    return data


@router.get("/settings", response_model=dict)
async def get_user_settings(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """获取当前用户的个性化设置（语言、主题等）"""
    settings = await _get_or_create_settings(db, current_user.id)
    return {
        "success": True,
        "message": "获取成功",
        "data": _sanitize_settings_payload(settings),
    }


@router.put("/settings", response_model=dict)
async def update_user_settings(
    body: UserSettingsUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """更新当前用户的个性化设置"""
    settings = await _get_or_create_settings(db, current_user.id)

    update_data = body.model_dump(exclude_unset=True, exclude_none=True)
    if "preferences" in update_data and isinstance(update_data["preferences"], dict):
        incoming = dict(update_data["preferences"])
        # AI Provider 配置只能走 PUT /settings/providers（加密 + 掩码回写保护）：
        # 通用通道携带的 ai_provider 一律丢弃；已存的加密配置无条件保留，
        # 避免 preferences 整包替换把个人模型配置静默清掉。
        incoming.pop("ai_provider", None)
        existing_prefs = settings.preferences if isinstance(settings.preferences, dict) else {}
        if "ai_provider" in existing_prefs:
            incoming["ai_provider"] = existing_prefs["ai_provider"]
        update_data["preferences"] = incoming
    for field, value in update_data.items():
        setattr(settings, field, value)

    await db.commit()
    await db.refresh(settings)

    return {
        "success": True,
        "message": "更新成功",
        "data": _sanitize_settings_payload(settings),
    }


# ── AI Provider settings ────────────────────────────────────────
# Provider 目录/加密/解析统一由 llm_settings_service 提供（单一事实源）。

_AVAILABLE_PROVIDERS = llm_settings_service.AVAILABLE_PROVIDERS


def _current_provider_view(raw: dict | None) -> dict:
    """把已存配置投影为 API 响应：只回掩码，绝不回明文 key。"""
    if not isinstance(raw, dict) or not raw.get("provider"):
        return {
            "provider": "deepseek",
            "model": llm_settings_service.server_default_model(),
            "api_key": "",
            "base_url": "",
            "has_api_key": False,
            "source": "server",
        }
    try:
        plain_key = llm_settings_service.read_api_key(raw)
    except llm_settings_service.LLMConfigError:
        plain_key = ""
    return {
        "provider": raw.get("provider", "deepseek"),
        "model": raw.get("model", ""),
        "api_key": llm_settings_service.mask_api_key(plain_key),
        "base_url": raw.get("base_url", "") or "",
        "has_api_key": bool(plain_key),
        "source": "user",
    }


@router.get("/settings/providers", response_model=dict)
async def get_providers(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """返回可用 AI Provider 列表和当前用户选择（key 始终为掩码）。"""
    settings_obj = await _get_or_create_settings(db, current_user.id)
    prefs = settings_obj.preferences if isinstance(settings_obj.preferences, dict) else {}
    return {
        "success": True,
        "providers": _AVAILABLE_PROVIDERS,
        "current": _current_provider_view(prefs.get("ai_provider")),
    }


@router.put("/settings/providers", response_model=dict)
async def update_providers(
    body: ProviderSettingsUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """保存个人 AI Provider 配置（provider/model/api_key/base_url）。

    - 保存后 Chat（RAG）与 Agent 的真实 LLM 调用立即使用该配置；
    - ``use_server_default=True`` 清除个人配置并回退服务端默认；
    - 提交空/掩码占位 key 时保留已存 key（掩码回写保护）；
    - API key 加密落库，响应永不含明文。
    """
    settings_obj = await _get_or_create_settings(db, current_user.id)
    prefs = dict(settings_obj.preferences) if isinstance(settings_obj.preferences, dict) else {}

    if body.use_server_default:
        prefs.pop("ai_provider", None)
        settings_obj.preferences = prefs
        await db.commit()
        await db.refresh(settings_obj)
        return {
            "success": True,
            "message": "已切换为服务端默认模型",
            "current": _current_provider_view(None),
        }

    try:
        provider, model, base_url = llm_settings_service.validate_provider_config(
            body.provider, body.model, body.base_url
        )
    except llm_settings_service.LLMConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    existing_key = ""
    raw_existing = prefs.get("ai_provider")
    if isinstance(raw_existing, dict):
        try:
            existing_key = llm_settings_service.read_api_key(raw_existing)
        except llm_settings_service.LLMConfigError:
            # 旧密文不可解密（SECRET_KEY 轮换）：当作无 key，要求重新填写。
            existing_key = ""

    submitted = (body.api_key or "").strip()
    preserve_existing = not submitted or llm_settings_service.is_masked_placeholder(submitted)
    stored_provider = raw_existing.get("provider") if isinstance(raw_existing, dict) else None
    if (
        preserve_existing
        and llm_settings_service.provider_requires_key(provider)
        and provider != stored_provider
    ):
        # 切换 provider 后旧 key 必然无效：掩码/空值不得把旧 provider 的 key
        # 静默复用到新 provider（否则用户会收到上游 401 却不知原因）。
        raise HTTPException(
            status_code=400,
            detail="切换提供商后请重新填写 API Key",
        )
    api_key = existing_key if preserve_existing else submitted
    if llm_settings_service.provider_requires_key(provider) and not api_key:
        raise HTTPException(
            status_code=400,
            detail="该提供商需要 API Key；如需使用服务端默认，请选择「使用服务端默认」",
        )

    stored: dict = {
        "provider": provider,
        "model": model,
        # 客户端缓存用随机 id（不含密钥指纹）；每次保存都会刷新。
        "config_id": uuid.uuid4().hex,
    }
    if base_url:
        stored["base_url"] = base_url
    if llm_settings_service.provider_requires_key(provider) and api_key:
        # 明文 key 绝不落库；新密文字段优先于历史明文。
        stored["api_key_encrypted"] = llm_settings_service.encrypt_api_key(api_key)
    # 不需要 key 的 provider（如 Ollama）不保留任何历史密钥。
    prefs["ai_provider"] = stored
    settings_obj.preferences = prefs
    await db.commit()
    await db.refresh(settings_obj)

    logger.info("用户 AI Provider 设置已更新（user_id=%s）", current_user.id)
    return {
        "success": True,
        "message": "已保存，后续对话与 Agent 将使用该配置",
        "current": _current_provider_view(stored),
    }
