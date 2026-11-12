"""多模态降级契约（services/multimodal.py）的单元测试。

判别点：**默认配置下图片必须走 OCR**；打开开关但模型不在白名单也必须走 OCR；
只有"开关开 + 模型命中白名单"才允许视觉直连。任何一条被破坏都会让图片在
非视觉模型上被硬塞、返回 400 或幻觉。
"""

from __future__ import annotations

import pytest

from app.services.multimodal import (
    build_image_content_part,
    decide_image_path,
    parse_allowlist,
    supports_vision,
)


class TestParseAllowlist:
    def test_empty_means_nobody(self) -> None:
        assert parse_allowlist("") == frozenset()
        assert parse_allowlist(None) == frozenset()

    def test_comma_and_space_separated(self) -> None:
        assert parse_allowlist("a, b ,c") == frozenset({"a", "b", "c"})

    def test_lowercased(self) -> None:
        assert parse_allowlist("DeepSeek-VL2") == frozenset({"deepseek-vl2"})


class TestSupportsVision:
    def test_disabled_by_default(self) -> None:
        assert (
            supports_vision("deepseek-v4-flash", enabled=False, allowlist="deepseek-v4-flash")
            is False
        )

    def test_enabled_but_model_not_allowlisted(self) -> None:
        assert (
            supports_vision("deepseek-v4-flash", enabled=True, allowlist="qwen2.5-vl-7b") is False
        )

    def test_enabled_and_allowlisted(self) -> None:
        assert supports_vision("qwen2.5-vl-7b", enabled=True, allowlist="qwen2.5-vl-7b") is True

    def test_case_insensitive_match(self) -> None:
        assert supports_vision("Qwen2.5-VL-7B", enabled=True, allowlist="qwen2.5-vl-7b") is True

    def test_empty_model_is_never_vision(self) -> None:
        assert supports_vision("", enabled=True, allowlist="qwen2.5-vl-7b") is False
        assert supports_vision(None, enabled=True, allowlist="qwen2.5-vl-7b") is False

    def test_enabled_but_empty_allowlist(self) -> None:
        assert supports_vision("qwen2.5-vl-7b", enabled=True, allowlist="") is False


class TestDecideImagePath:
    @pytest.mark.parametrize(
        ("model", "enabled", "allowlist", "expected"),
        [
            ("deepseek-v4-flash", False, "", "ocr"),
            ("deepseek-v4-flash", True, "", "ocr"),
            ("deepseek-v4-flash", True, "qwen2.5-vl-7b", "ocr"),
            ("qwen2.5-vl-7b", True, "qwen2.5-vl-7b", "vision"),
        ],
    )
    def test_matrix(self, model: str, enabled: bool, allowlist: str, expected: str) -> None:
        assert decide_image_path(model, enabled=enabled, allowlist=allowlist) == expected

    def test_defaults_come_from_settings(self) -> None:
        # 默认配置（MULTIMODAL_ENABLED=false）下，任何模型都只能走 OCR
        assert decide_image_path("qwen2.5-vl-7b") == "ocr"


class TestBuildImageContentPart:
    def test_openai_compatible_shape(self) -> None:
        part = build_image_content_part("data:image/png;base64,AAAA")
        assert part == {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}

    def test_http_url(self) -> None:
        part = build_image_content_part("https://example.com/a.png")
        assert part["image_url"] == {"url": "https://example.com/a.png"}  # type: ignore[index]

    def test_empty_url_rejected(self) -> None:
        with pytest.raises(ValueError):
            build_image_content_part("")
