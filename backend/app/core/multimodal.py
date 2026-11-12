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


# ── 附件路由与消息组装 ────────────────────────────────────────────────
# 附件有两种：document（PDF/Word/…）与 image（PNG/JPG/…）。
# document 永远走解析→chunk→RAG；image 才有多模态直连的可能。

AttachmentKind = Literal["document", "image"]


def plan_attachment_route(kind: AttachmentKind, model: str | None, **kwargs: object) -> ImagePath:
    """该附件本轮走哪条路。

    - `document` → 恒 `"ocr"`（实际上是解析→检索，视觉直连不适用）
    - `image`    → 由 `decide_image_path` 决定（vision 或 ocr 降级）
    """
    if kind == "document":
        return "ocr"
    return decide_image_path(model, **kwargs)  # type: ignore[arg-type]


def build_vision_user_content(text: str, image_urls: list[str]) -> list[dict[str, object]]:
    """把「文本 + 若干图片」组装成 OpenAI 兼容的 user content 数组。

    顺序：图片在前、文本在后 —— 与主流视觉模型的推荐用法一致（先给视觉上下文，
    再给指令）。没有任何图片时返回只含一个 text part 的数组，调用方可直接使用。
    """
    parts: list[dict[str, object]] = [build_image_content_part(u) for u in image_urls]
    parts.append({"type": "text", "text": text})
    return parts


class ImageRequestRejected(ValueError):
    """请求带了图片，但当前配置/模型不允许视觉直连。

    调用方（端点）应把它映射成 400/422 并**如实告知原因**，而不是静默降级 ——
    用户明确传了图，系统却当成"没传"，是这类功能最常见的静默假成功。
    """


def assert_image_request_allowed(
    image_ids: list[str] | None,
    model: str | None,
    **kwargs: object,
) -> None:
    """校验「带图请求」是否可被接受；不可接受时抛 `ImageRequestRejected`。

    - 未带图片 → 直接通过（不影响既有纯文本链路）
    - 带图片但多模态关闭 / 模型不在白名单 → 拒绝，并在消息里给出下一步
    - 带图片且能力满足 → 通过
    """
    ids = image_ids or []
    if not ids:
        return
    if supports_vision(model, **kwargs):  # type: ignore[arg-type]
        return
    raise ImageRequestRejected(
        "当前模型不支持图片直连（MULTIMODAL_ENABLED 未开启或模型不在 "
        "VISION_MODEL_ALLOWLIST 内）。可改为把图片作为文档上传：系统会用 OCR 提取"
        "文字并纳入检索，回答同样带出处。"
    )
