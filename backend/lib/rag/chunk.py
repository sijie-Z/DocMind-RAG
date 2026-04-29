import re


def split_text_into_chunks(text: str, max_chars: int = 500, overlap: int = 100) -> list[str]:
    """
    功能：把一整段长文本切成多个小块（chunk），并且每块之间保留一定重叠。
    为什么要这么做：长文档直接检索不准，把它切小后能更精确地命中知识点；
    有重叠可以减少“跨段信息被切断”的问题。

    参数解释（小白版）：
    - max_chars：每个文本块的最大长度，数字越大块越大；
    - overlap：相邻块之间的重叠字符数，保证跨段信息连续。

    返回：由多个文本块组成的列表。
    """
    if not text:
        return []

    # 先用简单方式把连续空白压缩，避免无意义的字符占长度
    cleaned = re.sub(r"\s+", " ", text).strip()

    chunks: list[str] = []
    start = 0
    n = len(cleaned)
    while start < n:
        end = min(start + max_chars, n)
        chunk = cleaned[start:end]
        chunks.append(chunk)

        if end >= n:
            break

        # 下一块的起点往前回退 overlap 个字符，制造重叠
        start = end - overlap if end - overlap > start else end

    return chunks