from __future__ import annotations

import pytest

from app.services.multimodal import ImageRequestRejected, assert_image_request_allowed


class TestAssertImageRequestAllowed:
    def test_no_images_always_passes(self) -> None:
        assert_image_request_allowed(None, "deepseek-v4-flash")
        assert_image_request_allowed([], "deepseek-v4-flash")

    def test_images_without_vision_are_rejected_with_actionable_message(self) -> None:
        with pytest.raises(ImageRequestRejected) as err:
            assert_image_request_allowed(["f1"], "deepseek-v4-flash", enabled=False, allowlist="")
        msg = str(err.value)
        assert "OCR" in msg and "上传" in msg

    def test_images_with_vision_pass(self) -> None:
        assert_image_request_allowed(
            ["f1"], "qwen2.5-vl-7b", enabled=True, allowlist="qwen2.5-vl-7b"
        )
