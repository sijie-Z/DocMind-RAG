# -*- coding: utf-8 -*-
"""MaskingService (PII 脱敏) 单元测试。"""
import pytest
from app.services.masking_service import MaskingService


@pytest.fixture
def svc():
    return MaskingService()


class TestMaskText:
    def test_empty_input(self, svc: MaskingService):
        text, mapping = svc.mask_text("")
        assert text == ""
        assert mapping == {}

    def test_none_input(self, svc: MaskingService):
        text, mapping = svc.mask_text(None)
        assert text is None
        assert mapping == {}

    def test_mask_phone_number(self, svc: MaskingService):
        text, mapping = svc.mask_text("请联系 13812345678 或 15900001111")
        assert "13812345678" not in text
        assert "15900001111" not in text
        assert "[PHONE_" in text
        assert len(mapping) == 2

    def test_mask_email(self, svc: MaskingService):
        text, mapping = svc.mask_text("邮箱: test@example.com")
        assert "test@example.com" not in text
        assert "[EMAIL_" in text

    def test_mask_id_card(self, svc: MaskingService):
        # Use an ID card where no 11-char substring matches phone regex (1[3-9]\d{9})
        # "11010120000101001X" → embedded digits: 12, 20, 00, 00, 01, 01, 00, 01X — no 1[3-9] match
        text, mapping = svc.mask_text("身份证: 11010120000101001X")
        assert "11010120000101001X" not in text
        assert "[ID_CARD_" in text

    def test_mask_ip_address(self, svc: MaskingService):
        text, mapping = svc.mask_text("服务器地址: 192.168.1.100")
        assert "192.168.1.100" not in text
        assert "[IP_ADDRESS_" in text

    def test_mask_multiple_types(self, svc: MaskingService):
        text = "用户 13812345678 邮箱 test@example.com IP 10.0.0.1"
        masked, mapping = svc.mask_text(text)
        assert "13812345678" not in masked
        assert "test@example.com" not in masked
        assert "10.0.0.1" not in masked
        assert len(mapping) >= 3

    def test_no_sensitive_info(self, svc: MaskingService):
        text = "这是一段普通文本，没有敏感信息。"
        masked, mapping = svc.mask_text(text)
        assert masked == text
        assert mapping == {}

    def test_mapping_keys_are_unique(self, svc: MaskingService):
        text = "13800001111 13800002222 13800003333"
        _, mapping = svc.mask_text(text)
        assert len(mapping) == len(set(mapping.keys()))


class TestUnmaskText:
    def test_roundtrip(self, svc: MaskingService):
        original = "联系人 13812345678 邮箱 admin@corp.com"
        masked, mapping = svc.mask_text(original)
        restored = svc.unmask_text(masked, mapping)
        assert restored == original

    def test_empty_masked_text(self, svc: MaskingService):
        assert svc.unmask_text("", {"[PHONE_1]": "123"}) == ""

    def test_empty_mapping(self, svc: MaskingService):
        assert svc.unmask_text("[PHONE_1]", {}) == "[PHONE_1]"

    def test_none_inputs(self, svc: MaskingService):
        assert svc.unmask_text(None, {"a": "b"}) is None
        assert svc.unmask_text("text", None) == "text"

    def test_partial_mapping(self, svc: MaskingService):
        """If mapping is missing a placeholder, it stays as-is."""
        assert svc.unmask_text("call [PHONE_1] now", {}) == "call [PHONE_1] now"
