"""Context engine — manages conversation context window and compression.

When the conversation approaches the model's token limit, the context engine
compresses older messages by summarizing them with the LLM, preserving key
information while freeing up space for new interactions.

Inspired by hermes-agent's ContextEngine / ContextCompressor pattern.
"""
import logging

import tiktoken

from app.agent.tool_pairs import (
    balance_cut,
    call_ids_of,
    result_call_id_of,
    validate_tool_pairs,
)

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

        # C3a：先验配对完整性——孤儿 result / 悬空 call 显式报错（不静默）。
        validate_tool_pairs(messages)

        total_tokens = estimate_messages_tokens(messages)
        if system_prompt:
            total_tokens += estimate_tokens(system_prompt) + 4

        if total_tokens <= self.max_context_tokens:
            return messages

        if len(messages) <= self.tail_window:
            return self._truncate_messages(messages, self.max_context_tokens)

        # C3a：压缩切点按配对边界回退——call 与其全部 result 必须同侧。
        cut = balance_cut(messages, len(messages) - self.tail_window)
        old_messages = messages[:cut]
        recent_messages = messages[cut:]

        budget_for_old = max(
            200,
            self.max_context_tokens
            - estimate_messages_tokens(recent_messages)
            - (estimate_tokens(system_prompt) + 4 if system_prompt else 0)
            - 50,
        )

        compressed = (
            self._compress_messages_fast(old_messages, budget_for_old)
            if old_messages else []
        )
        result = compressed + recent_messages

        final_tokens = estimate_messages_tokens(result)
        if final_tokens > self.max_context_tokens:
            result = self._truncate_messages(result, self.max_context_tokens)

        return result

    async def fit_async(
        self,
        messages: list[dict[str, str]],
        system_prompt: str | None = None,
        ctx=None,
    ) -> list[dict[str, str]]:
        """Async version: trim/compress messages with LLM summarization."""
        if not messages:
            return messages

        # C3a：先验配对完整性——孤儿 result / 悬空 call 显式报错（不静默）。
        validate_tool_pairs(messages)

        total_tokens = estimate_messages_tokens(messages)
        if system_prompt:
            total_tokens += estimate_tokens(system_prompt) + 4

        if total_tokens <= self.max_context_tokens:
            return messages

        if len(messages) <= self.tail_window:
            return self._truncate_messages(messages, self.max_context_tokens)

        # C3a：压缩切点按配对边界回退——call 与其全部 result 必须同侧。
        cut = balance_cut(messages, len(messages) - self.tail_window)
        old_messages = messages[:cut]
        recent_messages = messages[cut:]

        budget_for_old = max(
            200,
            self.max_context_tokens
            - estimate_messages_tokens(recent_messages)
            - (estimate_tokens(system_prompt) + 4 if system_prompt else 0)
            - 50,
        )

        compressed = await self._compress_messages_async(old_messages, budget_for_old, ctx=ctx) \
            if old_messages else []
        result = compressed + recent_messages

        final_tokens = estimate_messages_tokens(result)
        if final_tokens > self.max_context_tokens:
            result = self._truncate_messages(result, self.max_context_tokens)

        return result

    async def _compress_messages_async(
        self, messages: list[dict[str, str]], token_budget: int, ctx=None
    ) -> list[dict[str, str]]:
        """Compress messages using LLM summarization when available."""
        if not messages:
            return []

        # Try LLM summarization for 4+ messages
        if self._llm_client and len(messages) >= 4:
            try:
                summary = await self._summarize_with_llm(messages, token_budget, ctx=ctx)
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
        self, messages: list[dict[str, str]], token_budget: int, ctx=None
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
            # 用量记账（issue #202 评审 minor-1）：环内第七个调用点——
            # ContextEngine 摘要，经 loop.py 的 fit_async 在产品路径可达
            from app.agent.usage import record_llm_usage

            record_llm_usage(resp, ctx)
            return resp.choices[0].message.content or ""
        except Exception:
            return ""

    def _truncate_messages(
        self, messages: list[dict[str, str]], token_budget: int
    ) -> list[dict[str, str]]:
        """Last resort: truncate individual messages so the total fits the budget.

        This is the final guarantee of the class contract, so it measures in the
        same unit as estimate_tokens (real tokens) rather than in characters —
        mixing the two units let the result overshoot the budget on every pass.

        C3a：按配对分组截断——call 与其 result 是不可拆的整体；预算不足时整组
        丢弃（绝不留下孤儿 result / 悬空 call）。输出前再过一次配对校验。
        """
        result: list[dict[str, str]] = []
        used = 0
        i = 0
        n = len(messages)
        while i < n:
            msg = messages[i]
            # 收集配对组：assistant(tool_calls) + 紧随其后的对应 tool 结果。
            group = [msg]
            pending_ids = set(call_ids_of(msg))
            j = i + 1
            while pending_ids and j < n:
                rid = result_call_id_of(messages[j])
                if rid and rid in pending_ids:
                    group.append(messages[j])
                    pending_ids.discard(rid)
                    j += 1
                else:
                    break
            i = j

            fitted_group: list[dict[str, str]] = []
            group_used = 0
            over_budget = False
            for m in group:
                remaining = token_budget - used - group_used
                if remaining <= 0:
                    over_budget = True
                    break
                fitted = self._fit_one_message(m, remaining)
                if fitted is None:
                    over_budget = True
                    break
                fitted_group.append(fitted)
                group_used += estimate_tokens(fitted.get("content") or "") + 4
            if over_budget:
                if len(group) > 1:
                    # 配对不可拆：整组丢弃；后续消息依赖本组 call 时同样不能留。
                    break
                # 单条消息放不下：维持旧行为（跳过该条，继续尝试后续）。
                continue
            result.extend(fitted_group)
            used += group_used

        validate_tool_pairs(result)
        return result

    @staticmethod
    def _fit_one_message(
        msg: dict[str, str], remaining: int
    ) -> dict[str, str] | None:
        """把单条消息压进 remaining token；放不下 → None（旧语义的 continue）。"""
        content = msg.get("content") or ""
        if estimate_tokens(content) <= remaining:
            return {**msg}
        # Cut to the token budget directly; chars ≈ tokens * CHARS_PER_TOKEN
        # is only a starting guess, so re-encode and trim until it fits.
        keep = max(1, int(remaining * CHARS_PER_TOKEN))
        content = content[:keep] + "...[truncated]"
        while estimate_tokens(content) > remaining and keep > 1:
            keep = int(keep * 0.8)
            content = content[:keep] + "...[truncated]"
        if estimate_tokens(content) > remaining:
            return None
        return {**msg, "content": content}

    def inject_tool_results(
        self,
        messages: list[dict[str, str]],
        tool_results: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        """Append tool results to messages, respecting token budget."""
        combined = messages + tool_results
        return self.fit(combined)
