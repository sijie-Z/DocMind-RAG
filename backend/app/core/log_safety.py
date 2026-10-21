"""日志安全化工具（CodeQL py/log-injection / CWE-117）。

日志里的**用户来源值**（URL 参数、查询、异常文本、外部数据）可能携带换行与
控制字符——不转义直接落日志，攻击者可以伪造日志行（log forging），污染审计与
告警。`log_safe()` 把控制字符转义成可见形态，保证单条日志**永远是单物理行**。

实现口径与 CodeQL 的识别一致：`str.replace("\\r\\n", ...).replace("\\n", ...)`
这类"替换换行"调用即被 `py/log-injection` 视为 sanitizer——本模块把这条替换
放在函数体内，凡是经过 log_safe 的值都不再携带可伪造日志行的字符。
"""

from __future__ import annotations

from typing import Any

# 单条值的长度上限（防日志洪水；超出截断并标注）。
_MAX_LEN = 512


def log_safe(value: Any, max_len: int = _MAX_LEN) -> str:
    """把 value 转为适合单行日志的文本：控制字符转义 + 限长。

    - None → ""；其余先 str()；
    - 换行/回车/制表符及所有其它 C0/DEL 控制符转义为``\\n``/``\\r``/``\\t``/
      ``\\xNN`` 可见形态——这是防"伪造日志行"的关键（任何输入都无法在日志里
      制造真实换行）；
    - 超长值截断并加 ``...[truncated]`` 标记。
    """
    text = "" if value is None else str(value)
    # 先做换行的显式替换（CodeQL 的 sanitizer 识别口径；语义上也是关键一步）
    text = (
        text.replace("\r\n", "\\r\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\t", "\\t")
    )
    # 其余控制字符（C0 / DEL）转义为可见形态
    text = "".join(ch if ord(ch) >= 32 and ord(ch) != 127 else f"\\x{ord(ch):02x}" for ch in text)
    if len(text) > max_len:
        text = text[:max_len] + "...[truncated]"
    return text
