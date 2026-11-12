"""多模态输入的能力判定与降级契约（图片）。

设计目标：**换 provider 不改业务**。
- 默认（`MULTIMODAL_ENABLED=false`）：图片走 OCR（rapidocr）→ 文本 → 现有 RAG 管道，
  与今天的行为完全一致；
- 打开开关且模型在白名单内：图片作为 OpenAI 兼容的 `image_url` content part
  直接交给视觉模型；
- 打开开关但模型不在白名单：**仍然降级到 OCR**，不静默失败、也不把图片硬塞给不支持
  的模型（那会得到 400 或幻觉）。

调用方只需要问一个问题：`decide_image_path(model)` 返回 `"vision"` 还是 `"ocr"`。
"""

from __future__ import annotations

import re
from typing import Literal

from app.core.config import settings

ImagePath = Literal["vision", "ocr"]


def parse_allowlist(raw: str | None) -> frozenset[str]:
    """把逗号/空白分隔的白名单解析成小写集合；空串 → 空集合（= 谁都不支持）。"""
    if not raw:
        return frozenset()
    parts = re.split(r"[,\s]+", raw.strip())
    return frozenset(p.lower() for p in parts if p)


def supports_vision(
    model: str | None,
    *,
    enabled: bool | None = None,
    allowlist: str | None = None,
) -> bool:
    """该模型此刻能否直接吃图片。

    三个条件缺一不可：开关打开、模型名非空、模型名命中白名单。
    任何一项不满足都返回 False（调用方据此走 OCR 降级）。
    """
    is_enabled = settings.MULTIMODAL_ENABLED if enabled is None else enabled
    if not is_enabled:
        return False
    name = (model or "").strip().lower()
    if not name:
        return False
    raw = settings.VISION_MODEL_ALLOWLIST if allowlist is None else allowlist
    return name in parse_allowlist(raw)


def decide_image_path(model: str | None, **kwargs: object) -> ImagePath:
    """图片该走视觉直连还是 OCR 降级。这是唯一的对外判定入口。"""
    return "vision" if supports_vision(model, **kwargs) else "ocr"  # type: ignore[arg-type]


def build_image_content_part(url: str) -> dict[str, object]:
    """构造 OpenAI 兼容的图片 content part（data URL 与 http(s) URL 同形）。

    `{"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}`
    """
    if not url or not isinstance(url, str):
        raise ValueError("image url 不能为空")
    return {"type": "image_url", "image_url": {"url": url}}
