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
        debug: bool = False,
    ) -> tuple[list[dict[str, Any]], dict | None]:
        # issue #225 决策 7A：search_mode 此前收下即丢弃（门面静默吞参）——
        # 现在如实透传给 pipeline；None = 自动路由（行为不变）。
        return await self._pipeline.search_knowledge_base(
            query, organization_id, top_k, document_ids, debug=debug,
            search_mode=search_mode,
        )

    def report_grounded(self, has_sources: bool) -> None:
        self._pipeline.report_grounded(has_sources)

    def report_tokens(self, input_tokens: int, output_tokens: int) -> None:
        self._pipeline.report_tokens(input_tokens, output_tokens)

    def get_last_token_usage(self) -> tuple[int, int] | None:
        return self._pipeline.get_last_token_usage()

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
        mask_mapping_sink: dict | None = None,
        model_override: str | None = None,
    ) -> AsyncGenerator[str, None]:
        async for chunk in self._pipeline.chat_stream(
            query, context, history, system_prompt_override,
            enable_compression, enable_masking, mask_mapping_sink,
            model_override=model_override,
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
        # issue #196：检索期不再持有独立客户端；底层客户端来自
        # embedding_provider（索引期同一 embedding_service 实例）。
        return self._pipeline.retriever.embedding_client


# Singleton for backward compatibility
rag_service = RagServiceFacade()
