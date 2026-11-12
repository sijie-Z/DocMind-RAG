from __future__ import annotations

from app.core.multimodal import build_vision_user_content, plan_attachment_route


class TestPlanAttachmentRoute:
    def test_document_never_goes_vision(self) -> None:
        assert (
            plan_attachment_route(
                "document", "qwen2.5-vl-7b", enabled=True, allowlist="qwen2.5-vl-7b"
            )
            == "ocr"
        )

    def test_image_follows_model_capability(self) -> None:
        assert (
            plan_attachment_route("image", "qwen2.5-vl-7b", enabled=True, allowlist="qwen2.5-vl-7b")
            == "vision"
        )
        assert (
            plan_attachment_route(
                "image", "deepseek-v4-flash", enabled=True, allowlist="qwen2.5-vl-7b"
            )
            == "ocr"
        )

    def test_image_defaults_to_ocr(self) -> None:
        assert plan_attachment_route("image", "qwen2.5-vl-7b") == "ocr"


class TestBuildVisionUserContent:
    def test_images_before_text(self) -> None:
        parts = build_vision_user_content(
            "这张发票金额是多少？", ["data:image/png;base64,AA", "https://x/b.jpg"]
        )
        assert [p["type"] for p in parts] == ["image_url", "image_url", "text"]
        assert parts[-1] == {"type": "text", "text": "这张发票金额是多少？"}

    def test_no_images_yields_text_only(self) -> None:
        parts = build_vision_user_content("纯文本", [])
        assert parts == [{"type": "text", "text": "纯文本"}]

    def test_empty_image_url_rejected(self) -> None:
        import pytest

        with pytest.raises(ValueError):
            build_vision_user_content("x", [""])
