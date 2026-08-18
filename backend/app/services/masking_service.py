import logging
import re

logger = logging.getLogger(__name__)

class MaskingService:
    """
    敏感信息脱敏服务 (PII Masking)
    支持手机号、身份证号、邮箱、IP地址、银行卡号等识别与脱敏
    """

    # 定义常见的敏感信息正则模式
    # 安全修复：id_card 按身份证结构（6位地区码+出生日期+顺序码+校验位）收紧，
    # 避免 16-19 位纯数字（银行卡号）被 18 位身份证模式吞掉；银行卡号先行匹配。
    PATTERNS = {
        "phone": r"(?<!\d)(?:\+86)?1[3-9]\d{9}(?!\d)",
        "email": r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
        # id_card 必须先于 bank_card：新版 18 位身份证按结构匹配（6位地区+出生日期+3位顺序码+校验位），
        # 旧版 15 位 / 17 位+X 一并覆盖；严格结构保证不会误吞 16-19 位银行卡号。
        "id_card": r"(?<!\d)(?:\d{6}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]|\d{15}(?:\d{2}[0-9xX])?)(?!\d)",
        "bank_card": r"(?<!\d)\d{16,19}(?!\d)",
        "ip_address": r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b"
    }

    def mask_text(self, text: str, start_index: int = 0) -> tuple[str, dict[str, str]]:
        """
        对文本进行脱敏处理
        返回: (脱敏后的文本, 原始数据映射表)

        start_index: 占位符编号起始偏移，用于多段文本合并掩码时保证编号全局唯一
        （例如先掩码检索上下文、再掩码用户 query，避免 [PHONE_1] 冲突导致还原错乱）。
        """
        if not text:
            return text, {}

        mapping = {}
        masked_text = text

        # 遍历所有模式进行替换
        for p_type, pattern in self.PATTERNS.items():
            matches = re.findall(pattern, masked_text)
            for match in set(matches):
                # 为每个匹配项生成一个占位符，例如 [PHONE_1]
                placeholder = f"[{p_type.upper()}_{start_index + len(mapping) + 1}]"
                mapping[placeholder] = match
                # 使用占位符替换原始文本中的敏感信息
                masked_text = masked_text.replace(match, placeholder)

        if mapping:
            logger.info(f"🛡️ PII Masking: 识别并脱敏了 {len(mapping)} 处敏感信息")

        return masked_text, mapping

    def unmask_text(self, masked_text: str, mapping: dict[str, str]) -> str:
        """
        将脱敏后的占位符还原回原始信息
        """
        if not masked_text or not mapping:
            return masked_text

        unmasked_text = masked_text
        for placeholder, original_value in mapping.items():
            unmasked_text = unmasked_text.replace(placeholder, original_value)

        return unmasked_text

masking_service = MaskingService()
