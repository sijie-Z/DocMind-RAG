"""可观测性导出字段的统一脱敏（纯函数，只读）。

RunReport / 结构化 trace 会随 SSE 或日志把 query、goal、final_output、
reasoning 等预览字段带出进程。本模块把三道处理收口到一个纯函数：

1. 敏感模式（PII / token / Bearer / 授权头）替换为固定占位符，不可逆；
2. 控制字符经 :func:`app.core.log_safety.log_safe` 转义，保证单行输出；
3. 长度上限截断，避免超长正文灌入日志或导出。

设计约束：不落任何日志正文、不读写文件、不做删除。现有 ``log_safe`` 负责
日志注入防御，本模块在其之上补字段级 PII 脱敏与长度上限。
"""

from __future__ import annotations

import re

from app.core.log_safety import log_safe

# 与现有 preview 字段口径一致：final_output 400、reasoning 300、其余 200。
DEFAULT_PREVIEW_MAX_LEN = 400
_REASONING_MAX_LEN = 300
_FIELD_MAX_LEN = 200

# 占位符不带原文，无法还原；与 error_mapping 的失败细节占位口径区分。
_PII_PLACEHOLDER = "[PII_REDACTED]"
_SECRET_PLACEHOLDER = "[SECRET_REDACTED]"

# log_safe 只覆盖 C0/DEL；Unicode 的行/段分隔符（U+2028/U+2029）、C1 控制符
# 与零宽字符同样会破坏单行导出或造成视觉混淆，统一转义为可见形态。
# 使用显式 \uXXXX 转义，避免源文件里出现不可见字符。
_UNICODE_CONTROL_RE = re.compile("[\u0080-\u009f\u200b-\u200f\u2028\u2029\ufeff]")


def _escape_unicode_controls(text: str) -> str:
    return _UNICODE_CONTROL_RE.sub(lambda m: f"\\u{ord(m.group()):04x}", text)


# 复用 masking_service 的 PII 模式口径（电话/邮箱/身份证/银行卡/IP），外加
# 导出场景常见的凭据形态。按「长匹配、凭据优先」顺序替换。
# 键值型凭据保留键名与分隔符，只把值替换成占位符，便于排障且不泄漏原文。
_KV_SECRET_REPL = r"\g<key>\g<sep>" + _SECRET_PLACEHOLDER

# IPv6 覆盖全展开、结尾 ::、中段 ::、纯 :: 与 ::1；(?![\w:]) 防止把普通
# 十六进制词/时间串误判为地址。
_IPV6_RE = re.compile(
    r"(?<![\w:])(?:"
    r"(?:[0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4}"
    r"|(?:[0-9A-Fa-f]{1,4}:){1,7}:"
    r"|(?:[0-9A-Fa-f]{1,4}:){1,6}:[0-9A-Fa-f]{1,4}"
    r"|(?:[0-9A-Fa-f]{1,4}:){1,5}(?::[0-9A-Fa-f]{1,4}){1,2}"
    r"|(?:[0-9A-Fa-f]{1,4}:){1,4}(?::[0-9A-Fa-f]{1,4}){1,3}"
    r"|(?:[0-9A-Fa-f]{1,4}:){1,3}(?::[0-9A-Fa-f]{1,4}){1,4}"
    r"|(?:[0-9A-Fa-f]{1,4}:){1,2}(?::[0-9A-Fa-f]{1,4}){1,5}"
    r"|[0-9A-Fa-f]{1,4}:(?::[0-9A-Fa-f]{1,4}){1,6}"
    r"|:(?:(?::[0-9A-Fa-f]{1,4}){1,7}|:)"
    r")(?![\w:])"
)

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # 具体 token 前缀先于通用键值掩码，避免 authorization: Bearer ghp_xxx 只盖住前半段。
    (re.compile(r"ghp_[A-Za-z0-9]{12,}"), _SECRET_PLACEHOLDER),
    (re.compile(r"github_pat_[A-Za-z0-9_]{12,}"), _SECRET_PLACEHOLDER),
    (re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"), _SECRET_PLACEHOLDER),
    (re.compile(r"(?i)\b(?:sk-|dm_)[A-Za-z0-9_-]{12,}"), _SECRET_PLACEHOLDER),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), _SECRET_PLACEHOLDER),
    (re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), _SECRET_PLACEHOLDER),
    # password/passwd/pwd/token/secret/api_key/api-key/authorization 后接值。
    # 用 (?<![A-Za-z0-9_]) 代替 \b：中文与 ASCII 都是 \w，\b 会在「密password=」处误判。
    # 值里已经出现的占位符（前面的 token 前缀规则先命中）整体吞掉，避免叠加成
    # 「[SECRET_REDACTED]]」这类畸形输出。
    (
        re.compile(
            r"(?i)(?<![A-Za-z0-9_])"
            r"(?P<key>password|passwd|pwd|token|secret|api[_-]?key|authorization)"
            r"(?P<sep>\s*[:=]\s*)"
            r"(?:\[(?:PII|SECRET)_REDACTED\]|[\"']?[^\s,;，。；、)\]}\"']{1,256})"
        ),
        _KV_SECRET_REPL,
    ),
    (
        re.compile(
            r"(?<!\d)(?:\d{6}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx]|\d{15}(?:\d{2}[0-9xX])?)(?!\d)"
        ),
        _PII_PLACEHOLDER,
    ),
    (re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)"), _PII_PLACEHOLDER),  # US SSN
    (re.compile(r"(?<!\d)\d{16,19}(?!\d)"), _PII_PLACEHOLDER),
    (re.compile(r"(?<!\d)(?:\+86)?1[3-9]\d{9}(?!\d)"), _PII_PLACEHOLDER),
    (
        re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}"),
        _PII_PLACEHOLDER,
    ),
    (
        re.compile(
            r"\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b"
        ),
        _PII_PLACEHOLDER,
    ),
    (_IPV6_RE, _PII_PLACEHOLDER),
)


def mask_observability_text(value: object, max_len: int = DEFAULT_PREVIEW_MAX_LEN) -> str:
    """把单个预览字段转为可安全导出/记录的单行短文本。

    - ``None`` → ``""``；其余先 ``str()``；
    - 敏感模式替换为不可逆占位符；
    - 换行/控制字符经 ``log_safe`` 转义；
    - 最后按 ``max_len`` 截断（含截断标记）。

    纯函数：不修改入参，不产生副作用。
    """
    text = "" if value is None else str(value)
    for pattern, placeholder in _PATTERNS:
        text = pattern.sub(placeholder, text)
    # 先处理 Unicode 控制/零宽字符，再经 log_safe 处理 C0/DEL 与限长。
    text = _escape_unicode_controls(text)
    return log_safe(text, max_len=max(1, max_len))


def mask_run_report_preview(value: object) -> str:
    """query / goal / final_output 等一般预览字段（上限 400）。"""
    return mask_observability_text(value, DEFAULT_PREVIEW_MAX_LEN)


def mask_reasoning_preview(value: object) -> str:
    """decision reasoning 预览（上限 300，与现有口径一致）。"""
    return mask_observability_text(value, _REASONING_MAX_LEN)


def mask_report_field(value: object) -> str:
    """步骤描述 / result_preview / failure 等短字段（上限 200）。"""
    return mask_observability_text(value, _FIELD_MAX_LEN)
