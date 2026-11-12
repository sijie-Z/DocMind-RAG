"""守卫：多模态默认必须是关闭的。

判别力：把 ai.py 里 MULTIMODAL_ENABLED 默认改成 True，或给
VISION_MODEL_ALLOWLIST 预置任何模型 → 本用例红。默认一旦被悄悄打开，
所有图片请求会突然改走视觉直连，是典型的高危静默行为变更。
"""

from __future__ import annotations

from app.core.config import settings
from app.core.multimodal import decide_image_path


def test_multimodal_disabled_by_default() -> None:
    assert settings.MULTIMODAL_ENABLED is False


def test_vision_allowlist_empty_by_default() -> None:
    assert settings.VISION_MODEL_ALLOWLIST.strip() == ""


def test_default_model_routes_to_ocr() -> None:
    assert decide_image_path(settings.DEEPSEEK_MODEL) == "ocr"
