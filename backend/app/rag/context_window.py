"""Multi-turn context window management for RAG chat.

Manages the token budget across system prompt, retrieved context, and
conversation history. When the budget is exceeded, older messages are
compressed or dropped to make room for new interactions.
"""
import logging

logger = logging.getLogger(__name__)

# tiktoken-based token counting (cl100k_base is a good approximation for most LLMs)
try:
    import tiktoken
    _ENCODER = tiktoken.get_encoding("cl100k_base")
except ImportError:
    _ENCODER = None
    logger.warning("tiktoken not installed, falling back to heuristic token counting")


def estimate_tokens(text: str) -> int:
    if _ENCODER:
        return max(1, len(_ENCODER.encode(text)))
    # Fallback heuristic: CJK ~1.5, EN ~4, mixed ~2.5
    return max(1, int(len(text) / 2.5))


def estimate_message_tokens(msg: dict[str, str]) -> int:
    content = msg.get("content", "")
    return estimate_tokens(content) + 4  # role/formatting overhead


class ChatContextWindow:
    """Manages the context window for multi-turn RAG conversations.

    Token budget allocation:
    - system_prompt: always preserved (pinned)
    - context_docs: always preserved (pinned, comes from retrieval)
    - history: fills remaining budget, newest first
      - Recent N messages are preserved verbatim (tail window)
      - Older messages are compressed to a summary line
    """

    def __init__(
        self,
        max_tokens: int = 8000,
        tail_window: int = 6,
        max_history_messages: int = 20,
    ):
        self.max_tokens = max_tokens
        self.tail_window = tail_window
        self.max_history_messages = max_history_messages

    def fit_messages(
        self,
        system_prompt: str,
        context_docs: str,
        history: list[dict[str, str]],
        user_query: str,
    ) -> list[dict[str, str]]:
        """Build a message list that fits within the token budget.

        Returns: [system, ...history_tail, user_message]
        """
        system_tokens = estimate_tokens(system_prompt) + estimate_tokens(context_docs) + 8
        query_tokens = estimate_tokens(user_query) + 4
        remaining_budget = self.max_tokens - system_tokens - query_tokens

        if remaining_budget < 200:
            logger.warning(f"Context budget very tight: {remaining_budget} tokens remaining")
            remaining_budget = 200

        # Cap history
        capped_history = history[-self.max_history_messages:]

        # Split into old (compressible) and recent (pinned)
        if len(capped_history) <= self.tail_window:
            # All messages fit in the tail window
            fitted = self._fit_tail(capped_history, remaining_budget)
        else:
            old_msgs = capped_history[:-self.tail_window]
            recent_msgs = capped_history[-self.tail_window:]

            # Compress old messages
            compressed = self._compress_old(old_msgs)

            # Check if compressed + recent fits
            compressed_tokens = estimate_message_tokens(compressed) if compressed else 0
            tail_budget = remaining_budget - compressed_tokens

            fitted_recent = self._fit_tail(recent_msgs, tail_budget)

            fitted = []
            if compressed:
                fitted.append(compressed)
            fitted.extend(fitted_recent)

        return fitted

    def _fit_tail(
        self, messages: list[dict[str, str]], budget: int
    ) -> list[dict[str, str]]:
        """Fit as many recent messages as possible within budget (newest first)."""
        result = []
        used = 0
        for msg in reversed(messages):
            cost = estimate_message_tokens(msg)
            if used + cost > budget:
                break
            result.append(msg)
            used += cost
        result.reverse()
        return result

    def _compress_old(self, messages: list[dict[str, str]]) -> dict[str, str] | None:
        """Compress old messages using multi-layer cascade strategy.

        L1: Extract topic snippets (fast, no LLM needed)
        L2: Merge Q&A pairs into structured summaries
        L3: Keep role-exchange pairs with key information preserved
        """
        if not messages:
            return None

        # Layer 1: Extract Q&A pairs and topics
        qa_pairs = []
        current_q = None
        for msg in messages:
            role = msg.get("role", "user")
            content = (msg.get("content") or "").strip()
            if not content:
                continue

            if role == "user":
                current_q = content
            elif role == "assistant" and current_q:
                qa_pairs.append((current_q, content))
                current_q = None
            elif role == "system":
                # Preserve system messages as context hints
                qa_pairs.append(("[System]", content))

        if not qa_pairs:
            # Fallback: just extract topic snippets
            topics = []
            for msg in messages:
                content = (msg.get("content") or "").strip()
                if content:
                    snippet = content[:40].replace("\n", " ")
                    if len(content) > 40:
                        snippet += "..."
                    topics.append(snippet)
            if not topics:
                return None
            summary = f"[Earlier conversation: {len(messages)} messages. Topics: {'; '.join(topics[:5])}]"
            return {"role": "system", "content": summary}

        # Layer 2: Build structured summary from Q&A pairs
        summary_parts = []
        for q, a in qa_pairs[:8]:  # Keep up to 8 pairs
            q_short = q[:60].replace("\n", " ")
            if len(q) > 60:
                q_short += "..."
            a_short = a[:80].replace("\n", " ")
            if len(a) > 80:
                a_short += "..."
            summary_parts.append(f"Q: {q_short}\nA: {a_short}")

        remaining = len(qa_pairs) - 8
        if remaining > 0:
            summary_parts.append(f"... and {remaining} more exchanges")

        summary = "[Conversation History]\n" + "\n---\n".join(summary_parts)
        return {"role": "system", "content": summary}


def build_rag_messages(
    system_prompt: str,
    context_docs: str,
    history: list[dict[str, str]],
    user_query: str,
    max_tokens: int = 8000,
) -> list[dict[str, str]]:
    """Convenience function: build a token-budget-aware message list for RAG chat."""
    window = ChatContextWindow(max_tokens=max_tokens)
    fitted_history = window.fit_messages(system_prompt, context_docs, history, user_query)

    # Build final message list
    messages: list[dict[str, str]] = [
        {"role": "system", "content": system_prompt},
    ]
    messages.extend(fitted_history)
    messages.append({"role": "user", "content": f"【参考文档】：\n{context_docs}\n\n【问题】：{user_query}"})
    return messages
