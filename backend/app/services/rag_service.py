"""RAG service facade — delegates to the new RAGPipeline.

This module exists for backward compatibility. New code should import
from `app.rag.pipeline` or use `app.dependencies.get_rag_pipeline()`.
"""
import logging
from collections.abc import AsyncGenerator
from typing import Any

from app.dependencies import get_rag_pipeline

logger = logging.getLogger(__name__)


class RagServiceFacade:
    """Thin wrapper that delegates to RAGPipeline."""

    @property
    def _pipeline(self):
        return get_rag_pipeline()

    # ---- Delegated methods ----

    async def get_embedding(self, text: str) -> list[float]:
        return await self._pipeline.get_embedding(text)

    async def search_knowledge_base(
        self,
        query: str,
        organization_id: int,
        top_k: int = 5,
        document_ids: list[str] | None = None,
        search_mode: str | None = None,
    ) -> list[dict[str, Any]]:
        return await self._pipeline.search_knowledge_base(query, organization_id, top_k, document_ids)

    def report_grounded(self, has_sources: bool) -> None:
        self._pipeline.report_grounded(has_sources)

    def report_tokens(self, input_tokens: int, output_tokens: int) -> None:
        self._pipeline.report_tokens(input_tokens, output_tokens)

    def get_metrics(self, window_seconds: int = 0) -> dict[str, Any]:
        return self._pipeline.get_metrics(window_seconds)

    async def chat_stream(
        self,
        query: str,
        context: list[dict[str, Any]],
        history: list[dict[str, str]] = None,
        system_prompt_override: str | None = None,
        enable_compression: bool = True,
        enable_masking: bool = True,
    ) -> AsyncGenerator[str, None]:
        async for chunk in self._pipeline.chat_stream(
            query, context, history, system_prompt_override, enable_compression, enable_masking
        ):
            yield chunk

    # Legacy compatibility — these are no-ops or delegations
    def reset_metrics(self) -> None:
        self._pipeline.metrics.reset()

    @property
    def openai_client(self):
        return self._pipeline.openai_client

    @property
    def embedding_client(self):
        return self._pipeline.retriever.embedding_client


# Singleton for backward compatibility
rag_service = RagServiceFacade()
