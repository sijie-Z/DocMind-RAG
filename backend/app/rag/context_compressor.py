"""Context compression — truncate and compress retrieved context for LLM input."""
import re
from typing import Any

from app.rag.query_processor import extract_query_terms


def compress(text: str, query: str, max_chars: int = 800) -> str:
    """Compress text by keeping only query-relevant sentences."""
    if not text or len(text) <= max_chars:
        return text
    terms = extract_query_terms(query)
    if not terms:
        return text[:max_chars]

    sentences = re.split(r'[。！？\n]', text)
    parts = []
    current_len = 0
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        relevance = sum(1 for t in terms if t in sentence.lower())
        if relevance > 0 or current_len < max_chars * 0.3:
            if current_len + len(sentence) + 1 <= max_chars:
                parts.append(sentence)
                current_len += len(sentence) + 1
            elif relevance >= len(terms) * 0.5:
                truncated = sentence[:max_chars - current_len - 3]
                if truncated:
                    parts.append(truncated + "...")
                    break
    if not parts:
        return text[:max_chars]
    result = "。".join(parts)
    return result[:max_chars - 3] + "..." if len(result) > max_chars else result


def compress_context_list(
    contexts: list[dict[str, Any]], query: str, max_context_chars: int = 2000
) -> list[dict[str, Any]]:
    """Compress a list of context items, keeping query-relevant parts."""
    compressed = []
    total_chars = 0
    per_item_max = max(200, int(max_context_chars / max(1, len(contexts))))
    for ctx in contexts:
        text = ctx.get("text", "") or ctx.get("content", "")
        original_len = len(text)
        compressed_text = compress(text, query, max_chars=per_item_max)
        ctx_copy = dict(ctx)
        ctx_copy["original_length"] = original_len
        ctx_copy["text"] = compressed_text
        ctx_copy["is_compressed"] = len(compressed_text) < original_len
        compressed.append(ctx_copy)
        total_chars += len(compressed_text)
        if total_chars > max_context_chars:
            break
    return compressed
