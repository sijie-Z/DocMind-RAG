"""条号提取与命中判定 —— ClauseHit@k 的核心规则（Phase 1，纯标准库）。

统一口径（与 backend/docs/eval-gate-phase1.md 一致）：

1. **条号（中文条款）**：形如 ``第<数字>条``，数字支持中文数字
   （一/二/…/十/百/千、零〇、两）与阿拉伯数字，允许中间有空格。
   一律归一化为 ``第N条``（N 为阿拉伯数字）——「第七条」「第 7 条」「第7条」
   是同一个条号。

2. **章节编号**：形如 ``X.Y`` / ``X.Y.Z`` 的十进制编号（至少一段小数点），
   归一化为 ``§X.Y``。

3. **命中判定采用「定义位」（definition site）口径**：
   条号 / 章节编号必须出现在**行首**（允许前置最多 3 个空白与 markdown
   标题符号 ``#``，以及编号后的空格/点号/冒号等分隔符）才算
   「该 chunk 定义了第 N 条 / §X.Y」。
   正文中间的引用（如「…与第 7 条所列规则冲突的…」「依据 §2.3」）
   **不计入命中** —— 避免「引用了某条号的 chunk」被误判成
   「命中了该条」，这正是「问第七条召回第十二条」要防住的形态。

4. 一个 chunk 可同时定义多个条号（全部展开）；同一 chunk 命中多条只记一次
   （度量在 metrics.py 按名次判定，命中集合本身是去重 set）。

用途分工：
- ``clause_definitions``：命中判定与 golden set 自洽校验（唯一权威）。
- ``clause_mentions``：仅诊断用（报告里展示 chunk 提及了哪些条号），
  不参与通过/失败判定。
"""

from __future__ import annotations

import re

# 条号允许的字符：阿拉伯数字 + 中文数词（含「零〇两」）
_NUM_CHARS = "0-9〇零一二三四五六七八九十百千两"

_CN_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000}

# 行首条号定义位；允许 markdown 前缀 #、空白与行首缩进（最多 3 空格）
_CLAUSE_DEF_RE = re.compile(rf"^[ \t]{{0,3}}(?:#{{1,6}}[ \t]*)?第[ \t]*([{_NUM_CHARS}]+)[ \t]*条")
# 任意位置条号（诊断用）
_CLAUSE_ANY_RE = re.compile(rf"第[ \t]*([{_NUM_CHARS}]+)[ \t]*条")

# 行首章节编号定义位（至少一个小数点）；编号后必须跟分隔符或行尾
_SECTION_DEF_RE = re.compile(
    r"^[ \t]{0,3}(?:#{1,6}[ \t]*)?(\d+(?:\.\d+)+)(?![\d.])(?=[ \t.、:：)）]|$)",
    re.MULTILINE,
)
# § 引用形式（诊断用；也接受「§2.3」紧贴）
_SECTION_REF_RE = re.compile(r"§[ \t]*(\d+(?:\.\d+)+)(?![\d.])")

# golden set 中条号引用的完整形式
_CLAUSE_REF_FULL_RE = re.compile(rf"^第[ \t]*([{_NUM_CHARS}]+)[ \t]*条$")
_SECTION_REF_FULL_RE = re.compile(r"^§?[ \t]*(\d+(?:\.\d+)+)$")


def parse_cn_number(text: str) -> int | None:
    """把中文数字/阿拉伯数字串转成 int；无法解析返回 None。

    支持：七→7、十七→17、七十→70、七十二→72、一百零五→105、两千→2000。
    """
    if not text:
        return None
    if text.isdigit():  # 纯阿拉伯数字（含全角，int() 可直读）
        try:
            return int(text)
        except ValueError:
            return None
    total = 0
    current = 0
    for ch in text:
        if ch in _CN_DIGITS:
            current = _CN_DIGITS[ch]
        elif ch in _CN_UNITS:
            total += (current or 1) * _CN_UNITS[ch]
            current = 0
        else:
            return None
    return total + current


def normalize_clause_ref(raw: str) -> str | None:
    """把 golden set 里手写的条号引用归一化为规范形式。

    接受：``第七条`` / ``第7条`` / ``第 7 条`` → ``第7条``；
    ``§2.3`` / ``2.3`` → ``§2.3``。其余（如裸整数 ``7``）返回 None，
    由加载器给出明确报错（避免「7 是条号还是章节号」的歧义）。
    """
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if s.startswith("第"):
        m = _CLAUSE_REF_FULL_RE.match(s)
        if m:
            n = parse_cn_number(m.group(1))
            return f"第{n}条" if n is not None else None
        return None  # 「第…」开头但不符合条号格式（如「第7.5条」）→ 明确拒绝
    m = _SECTION_REF_FULL_RE.match(s)
    if m:
        return f"§{m.group(1)}"
    return None


def clause_definitions(text: str) -> set[str]:
    """提取文本中**行首定义位**的条号/章节编号（命中判定的权威口径）。"""
    ids: set[str] = set()
    if not text:
        return ids
    for line in text.splitlines():
        m = _CLAUSE_DEF_RE.match(line)
        if m:
            n = parse_cn_number(m.group(1))
            if n is not None:
                ids.add(f"第{n}条")
        m = _SECTION_DEF_RE.match(line)
        if m:
            ids.add(f"§{m.group(1)}")
    return ids


def clause_mentions(text: str) -> set[str]:
    """提取文本中所有条号/章节引用（含正文引用；仅诊断用，不参与判定）。"""
    ids: set[str] = set(clause_definitions(text))
    if not text:
        return ids
    for m in _CLAUSE_ANY_RE.finditer(text):
        n = parse_cn_number(m.group(1))
        if n is not None:
            ids.add(f"第{n}条")
    for m in _SECTION_REF_RE.finditer(text):
        ids.add(f"§{m.group(1)}")
    return ids
