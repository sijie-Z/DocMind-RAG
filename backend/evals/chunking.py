"""固定语料 → 确定性分块（纯标准库）。

Phase 1 的语料是仓库内提交的 fixtures，采用**确定性切块**：
遇到「条款起始行」或「章节标题行」即开新块，其余行进当前块。
这样每个 chunk 与其条号一一对应（一事实一 chunk），把
「切分粒度」这个变量从 Phase 1 的测量里固定住——
检索链本身的差异才是被测量的对象。切分策略实验属于 Phase 2。

fixture 约定（corpus/ 下的文件必须遵守，测试会校验）：
- 条式文档：每个 ``第N条`` 独占行首，条款正文在同一行或后续行；
- 节式文档：章节编号写作 markdown 标题 ``## X.Y 标题``。
"""

from __future__ import annotations

import re

# 条款起始行：行首「第N条」（与 clauses.py 的定义位口径一致）
_CN_CLAUSE_START_RE = re.compile(r"^第[ \t]*[0-9〇零一二三四五六七八九十百千两]+[ \t]*条")
# 章节标题行：markdown 标题 + 十进制编号
_SECTION_HEADING_RE = re.compile(r"^#{1,6}[ \t]*\d+(?:\.\d+)+[ \t.、:：)）]")


def is_chunk_start(line: str) -> bool:
    """该行是否开启一个新的 chunk（条款起始行 / 章节标题行）。"""
    return bool(_CN_CLAUSE_START_RE.match(line) or _SECTION_HEADING_RE.match(line))


def split_corpus_file(text: str) -> list[str]:
    """把一份 fixture 文本切为确定性 chunk 列表（保序、零丢失、去空块）。"""
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in text.splitlines():
        if is_chunk_start(line) and any(ln.strip() for ln in current):
            blocks.append(current)
            current = [line]
        else:
            current.append(line)
    if any(ln.strip() for ln in current):
        blocks.append(current)
    chunks: list[str] = []
    for block in blocks:
        chunk = "\n".join(block).strip()
        if chunk:
            chunks.append(chunk)
    return chunks
