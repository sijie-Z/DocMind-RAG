"""Context compression — truncate and compress retrieved context for LLM input."""

import re
from typing import Any

from app.rag.query_processor import extract_query_terms
from app.rag.source_provenance import PARSER_ORIGINAL, original_text_of, provenance_of


def get_prompt_text(item: dict[str, Any]) -> str:
    """提示词正文的唯一读取入口（issue #186）。

    检索结果同时携带 `prompt_text`（完整 chunk 正文，LLM 输入的唯一来源）与
    `snippet`（展示用短片段）。修前各消费点写 `snippet or text`，snippet 非空
    即短路全文压缩器——`compress_context_list` 的产物从未进入提示词。

    PR2/R2：有真实 original_text 时永远直接返回 O，预填 prompt_text 无论是
    空串、E 或其它内容都不得覆盖原文。marker 为 parser_original 但 O 缺失时
    同样禁止回退 E；只有明确 legacy 载荷才保留旧的兼容 fallback。
    """
    original = original_text_of(item)
    if original is not None:
        return original
    if provenance_of(item) == PARSER_ORIGINAL:
        return ""
    return (
        item.get("prompt_text")
        or item.get("text")
        or item.get("content")
        or item.get("snippet")
        or ""
    )


def compress(text: str, query: str, max_chars: int = 800) -> str:
    """Compress text by keeping only query-relevant sentences.

    规则式、无 LLM 调用（README 的压缩器口径）。超限时保留开头少量句子建立
    上下文，再按查询术语逐句挑选相关句——不做无差别头部硬切，因此关键句在
    chunk 后半部分时仍可能被保留（issue #186 的 snippet / 提示词第二段上限
    也复用本函数）。查询术语全不命中时按句界尽量填满预算（不退化成 30% 头部）。
    注意：句子按 `[。！？\\n]` 切分，重连统一使用「。」（既有口径，展示层与
    提示词一致）。
    """
    if not text or len(text) <= max_chars:
        return text
    max_chars = max(1, int(max_chars))
    terms = extract_query_terms(query)
    if not terms:
        return text[:max_chars]

    sentences = re.split(r"[。！？\n]", text)
    parts = []
    current_len = 0
    any_relevant = False
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        relevance = sum(1 for t in terms if t in sentence.lower())
        if relevance > 0 or current_len < max_chars * 0.3:
            if current_len + len(sentence) + 1 <= max_chars:
                parts.append(sentence)
                current_len += len(sentence) + 1
                any_relevant = any_relevant or relevance > 0
            elif relevance >= len(terms) * 0.5:
                # 极端小上限（max_chars<4）时 room<=0，不放省略号，避免负切片越界。
                room = max_chars - current_len - 3
                if room > 0:
                    truncated = sentence[:room]
                    if truncated:
                        parts.append(truncated + "...")
                        any_relevant = True
                        break
    if not parts:
        return text[:max_chars]
    if not any_relevant:
        # 查询术语在全文零命中：没有「语义句」可挑，按句界尽量填满预算，
        # 而不是只留 30% 头部（issue #186 评审 m2）。
        head: list[str] = []
        used = 0
        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue
            if used + len(sentence) + 1 > max_chars:
                break
            head.append(sentence)
            used += len(sentence) + 1
        if head:
            return "。".join(head)[:max_chars]
        return text[:max_chars]
    result = "。".join(parts)
    if len(result) > max_chars:
        # max_chars < 4 时没有空间放省略号，直接硬限长（极端配置兜底）。
        if max_chars > 3:
            result = result[: max_chars - 3] + "..."
        else:
            result = result[:max_chars]
    return result


def compress_context_list(
    contexts: list[dict[str, Any]], query: str, max_context_chars: int = 2000
) -> list[dict[str, Any]]:
    """Compress a list of context items, keeping query-relevant parts.

    issue #186：压缩对象是 `prompt_text`（完整正文），结果写回 `prompt_text`；
    `text` 同步为压缩结果以兼容旧调用方，`snippet` 保持不动（展示层字段）。
    """
    compressed = []
    total_chars = 0
    per_item_max = max(200, int(max_context_chars / max(1, len(contexts))))
    for ctx in contexts:
        text = get_prompt_text(ctx)
        original_len = len(text)
        compressed_text = compress(text, query, max_chars=per_item_max)
        ctx_copy = dict(ctx)
        ctx_copy["original_length"] = original_len
        ctx_copy["prompt_text"] = compressed_text
        ctx_copy["text"] = compressed_text
        ctx_copy["is_compressed"] = len(compressed_text) < original_len
        compressed.append(ctx_copy)
        total_chars += len(compressed_text)
        if total_chars > max_context_chars:
            break
    return compressed
