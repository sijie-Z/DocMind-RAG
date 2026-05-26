"""Context window management tests."""
from app.rag.context_window import (
    ChatContextWindow,
    build_rag_messages,
    estimate_message_tokens,
    estimate_tokens,
)


class TestEstimateTokens:
    def test_empty_string(self):
        assert estimate_tokens("") == 1

    def test_short_text(self):
        assert estimate_tokens("hello") >= 1

    def test_longer_text(self):
        tokens = estimate_tokens("a" * 100)
        # tiktoken cl100k_base: ~25 tokens for 100 ASCII chars (much more accurate than old heuristic)
        assert 10 <= tokens <= 50


class TestEstimateMessageTokens:
    def test_includes_overhead(self):
        msg = {"role": "user", "content": "hello"}
        tokens = estimate_message_tokens(msg)
        assert tokens >= 5  # content + 4 overhead

    def test_empty_content(self):
        msg = {"role": "system", "content": ""}
        tokens = estimate_message_tokens(msg)
        assert tokens >= 4  # overhead


class TestChatContextWindow:
    def test_short_history_preserved_fully(self):
        window = ChatContextWindow(max_tokens=8000, tail_window=6)
        history = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
        result = window.fit_messages("system", "context docs", history, "query")
        # All history should be preserved
        assert len(result) == 2

    def test_long_history_tails_only(self):
        window = ChatContextWindow(max_tokens=500, tail_window=4)
        history = [{"role": "user", "content": f"message {i}"} for i in range(20)]
        result = window.fit_messages("system prompt", "context", history, "query")
        # Should be capped, not all 20
        assert len(result) < 20
        # Most recent messages should be preserved
        assert any("message 19" in m.get("content", "") for m in result)

    def test_compressed_old_messages(self):
        window = ChatContextWindow(max_tokens=500, tail_window=2)
        history = [{"role": "user", "content": f"topic {i}"} for i in range(10)]
        result = window.fit_messages("system", "context", history, "query")
        # Should have a compressed summary + recent messages
        roles = [m["role"] for m in result]
        assert "system" in roles  # summary message

    def test_very_tight_budget(self):
        window = ChatContextWindow(max_tokens=200, tail_window=2)
        history = [{"role": "user", "content": "a" * 500}] * 10
        result = window.fit_messages("system", "context", history, "query")
        # Should still return something, not crash
        assert isinstance(result, list)

    def test_empty_history(self):
        window = ChatContextWindow(max_tokens=8000)
        result = window.fit_messages("system", "context", [], "query")
        assert result == []


class TestBuildRagMessages:
    def test_includes_system_and_user(self):
        messages = build_rag_messages(
            system_prompt="You are helpful",
            context_docs="doc content",
            history=[{"role": "user", "content": "hi"}],
            user_query="what?",
        )
        assert messages[0]["role"] == "system"
        assert messages[-1]["role"] == "user"
        assert "what?" in messages[-1]["content"]

    def test_history_in_middle(self):
        messages = build_rag_messages(
            system_prompt="sys",
            context_docs="ctx",
            history=[
                {"role": "user", "content": "q1"},
                {"role": "assistant", "content": "a1"},
            ],
            user_query="q2",
        )
        # system + history(2) + user = 4
        assert len(messages) == 4
        assert messages[1]["content"] == "q1"
        assert messages[2]["content"] == "a1"
