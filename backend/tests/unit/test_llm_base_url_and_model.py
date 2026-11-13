"""OpenAI 兼容基址与模型名必须来自配置，而不是写死厂商型号。

事故背景（2026-11-13）：接本地 Ollama（http://localhost:11434/v1）时，
1) `_normalize_base_url` 把 `/v1` 剥掉 → SDK 拼出 `/chat/completions` → 404 page not found；
2) `AgentConfig.model` 写死 `deepseek-v4-flash` → 即便改 base_url 仍拿厂商型号去请求 → 404 model not found。
这两条让"换任意 OpenAI 兼容端点"实际上不可用。
"""

from __future__ import annotations

from app.agent.config import AgentConfig
from app.core.config import settings
from app.dependencies import _normalize_base_url

KEEP_V1 = ["/chat/completions"]


class TestBaseUrlNormalization:
    def test_ollama_v1_is_preserved(self) -> None:
        assert _normalize_base_url("http://localhost:11434/v1", strip_suffixes=KEEP_V1) == (
            "http://localhost:11434/v1"
        )

    def test_trailing_slash_trimmed_but_v1_kept(self) -> None:
        assert _normalize_base_url("http://localhost:11434/v1/", strip_suffixes=KEEP_V1) == (
            "http://localhost:11434/v1"
        )

    def test_full_endpoint_still_stripped(self) -> None:
        assert (
            _normalize_base_url(
                "http://localhost:11434/v1/chat/completions", strip_suffixes=KEEP_V1
            )
            == "http://localhost:11434/v1"
        )

    def test_deepseek_bare_host_unchanged(self) -> None:
        assert _normalize_base_url("https://api.deepseek.com", strip_suffixes=KEEP_V1) == (
            "https://api.deepseek.com"
        )


class TestAgentModelFollowsSettings:
    def test_default_model_comes_from_settings(self) -> None:
        assert AgentConfig().model == settings.DEEPSEEK_MODEL
