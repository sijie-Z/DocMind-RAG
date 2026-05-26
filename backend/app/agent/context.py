"""Context engine — manages conversation context window and compression.

When the conversation approaches the model's token limit, the context engine
compresses older messages by summarizing them with the LLM, preserving key
information while freeing up space for new interactions.

Inspired by hermes-agent's ContextEngine / ContextCompressor pattern.
"""
import logging

import tiktoken

logger = logging.getLogger(__name__)

CHARS_PER_TOKEN = 2.5  # rough approximation: 1 token ≈ 2.5 chars for CJK+EN mixed

try:
    _ENCODER = tiktoken.get_encoding("cl100k_base")
except Exception:
    _ENCODER = None
    logger.warning("tiktoken encoding unavailable, falling back to heuristic token counting")


def estimate_tokens(text: str) -> int:
    """Token count estimate using tiktoken when available."""
    if _ENCODER:
        return max(1, len(_ENCODER.encode(text)))
    return max(1, int(len(text) / 2.5))


def estimate_messages_tokens(messages: list[dict[str, str]]) -> int:
    """Estimate total tokens in a message list."""
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        total += estimate_tokens(content) + 4  # role overhead
    return total


class ContextEngine:
    """Manages conversation context within a token budget.

    Strategy:
    1. System prompt is always preserved (pinned).
    2. Recent N messages are always preserved (tail window).
    3. Older messages are compressed: summarized via LLM (preferred) or truncated.
    """

    def __init__(
        self,
        max_context_tokens: int = 8000,
        tail_window: int = 6,
        compression_ratio: float = 0.3,
    ):
        self.max_context_tokens = max_context_tokens
        self.tail_window = tail_window
        self.compression_ratio = compression_ratio
        self._llm_client = None  # set via set_llm_client() for intelligent summarization

    def set_llm_client(self, client) -> None:
        """Set the LLM client for intelligent message summarization."""
        self._llm_client = client

    def fit(
        self,
        messages: list[dict[str, str]],
        system_prompt: str | None = None,
    ) -> list[dict[str, str]]:
        """Trim/compress messages to fit within the token budget (sync fallback).

        Prefer fit_async() when an event loop is available.
        """
        if not messages:
            return messages

        total_tokens = estimate_messages_tokens(messages)
        if system_prompt:
            total_tokens += estimate_tokens(system_prompt) + 4

        if total_tokens <= self.max_context_tokens:
            return messages

        if len(messages) <= self.tail_window:
            return self._truncate_messages(messages, self.max_context_tokens)

        old_messages = messages[:-self.tail_window]
        recent_messages = messages[-self.tail_window:]

        budget_for_old = max(
            200,
            self.max_context_tokens
            - estimate_messages_tokens(recent_messages)
            - (estimate_tokens(system_prompt) + 4 if system_prompt else 0)
            - 50,
        )

        compressed = self._compress_messages_fast(old_messages, budget_for_old)
        result = compressed + recent_messages

        final_tokens = estimate_messages_tokens(result)
        if final_tokens > self.max_context_tokens:
            result = self._truncate_messages(result, self.max_context_tokens)

        return result

    async def fit_async(
        self,
        messages: list[dict[str, str]],
        system_prompt: str | None = None,
    ) -> list[dict[str, str]]:
        """Async version: trim/compress messages with LLM summarization."""
        if not messages:
            return messages

        total_tokens = estimate_messages_tokens(messages)
        if system_prompt:
            total_tokens += estimate_tokens(system_prompt) + 4

        if total_tokens <= self.max_context_tokens:
            return messages

        if len(messages) <= self.tail_window:
            return self._truncate_messages(messages, self.max_context_tokens)

        old_messages = messages[:-self.tail_window]
        recent_messages = messages[-self.tail_window:]

        budget_for_old = max(
            200,
            self.max_context_tokens
            - estimate_messages_tokens(recent_messages)
            - (estimate_tokens(system_prompt) + 4 if system_prompt else 0)
            - 50,
        )

        compressed = await self._compress_messages_async(old_messages, budget_for_old)
        result = compressed + recent_messages

        final_tokens = estimate_messages_tokens(result)
        if final_tokens > self.max_context_tokens:
            result = self._truncate_messages(result, self.max_context_tokens)

        return result

    async def _compress_messages_async(
        self, messages: list[dict[str, str]], token_budget: int
    ) -> list[dict[str, str]]:
        """Compress messages using LLM summarization when available."""
        if not messages:
            return []

        # Try LLM summarization for 4+ messages
        if self._llm_client and len(messages) >= 4:
            try:
                summary = await self._summarize_with_llm(messages, token_budget)
                if summary:
                    return [{"role": "system", "content": "[Conversation summary]\n" + summary}]
            except Exception:
                logger.debug("LLM summarization failed, falling back to truncation", exc_info=True)

        # Fallback: truncated concatenation
        return self._compress_messages_fast(messages, token_budget)

    def _compress_messages(
        self, messages: list[dict[str, str]], token_budget: int
    ) -> list[dict[str, str]]:
        """Synchronous fallback: compress a list of messages into a single summary message."""
        return self._compress_messages_fast(messages, token_budget)

    def _compress_messages_fast(
        self, messages: list[dict[str, str]], token_budget: int
    ) -> list[dict[str, str]]:
        """Truncation-based compression (fast, no LLM call)."""
        parts = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if not content:
                continue
            truncated = content[:200] + ("..." if len(content) > 200 else "")
            parts.append(f"[{role}]: {truncated}")

        summary_text = "[Earlier conversation]\n" + "\n".join(parts)
        max_chars = int(token_budget * CHARS_PER_TOKEN)
        if len(summary_text) > max_chars:
            summary_text = summary_text[:max_chars - 20] + "...[truncated]"

        return [{"role": "system", "content": summary_text}]

    async def _summarize_with_llm(
        self, messages: list[dict[str, str]], token_budget: int
    ) -> str:
        """Use the LLM to produce a concise summary of older messages."""
        if not self._llm_client:
            return ""

        conversation_text = "\n".join(
            f"[{m.get('role', '?')}]: {m.get('content', '')[:300]}"
            for m in messages[-20:]  # last 20 messages at most
        )
        max_chars = int(token_budget * CHARS_PER_TOKEN)

        try:
            from app.core.config import settings
            resp = await self._llm_client.chat.completions.create(
                model=settings.DEEPSEEK_MODEL,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Summarize the following conversation into a concise paragraph "
                            "(under 300 words). Preserve key facts, decisions, and action items. "
                            "Use the same language as the conversation."
                        ),
                    },
                    {"role": "user", "content": conversation_text[:4000]},
                ],
                temperature=0.0,
                max_tokens=min(300, max_chars // 2),
            )
            return resp.choices[0].message.content or ""
        except Exception:
            return ""

    def _truncate_messages(
        self, messages: list[dict[str, str]], token_budget: int
    ) -> list[dict[str, str]]:
        """Last resort: truncate individual messages to fit budget."""
        max_chars = int(token_budget * CHARS_PER_TOKEN)
        result = []
        used = 0
        for msg in messages:
            content = msg.get("content", "")
            remaining = max_chars - used
            if remaining <= 0:
                break
            if len(content) > remaining:
                content = content[:remaining - 10] + "...[truncated]"
            result.append({**msg, "content": content})
            used += len(content) + 4
        return result

    def inject_tool_results(
        self,
        messages: list[dict[str, str]],
        tool_results: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        """Append tool results to messages, respecting token budget."""
        combined = messages + tool_results
        return self.fit(combined)
