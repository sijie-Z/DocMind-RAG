"""Dependency injection container — wires RAG pipeline and other services.

All infrastructure clients are constructed once at startup with validated URLs.
Services receive their collaborators via constructor injection.
"""

import logging
from functools import lru_cache
from urllib.parse import urlparse

from openai import AsyncOpenAI

from app.core.config import settings
from app.rag.errors import EmbeddingConfigError
from app.rag.pipeline import RAGPipeline

logger = logging.getLogger(__name__)


def _normalize_base_url(url: str, strip_suffixes: list[str] | None = None) -> str:
    """Normalize an API base URL by stripping known endpoint suffixes and validating format."""
    if not url:
        raise ValueError("API URL cannot be empty")
    for suffix in strip_suffixes or []:
        if url.endswith(suffix):
            url = url[: -len(suffix)]
    url = url.rstrip("/")
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(
            f"Invalid API URL: '{url}' — must include scheme and host "
            f"(e.g. https://api.deepseek.com)"
        )
    return url


@lru_cache
def _build_openai_client() -> AsyncOpenAI | None:
    if settings.ENABLE_LOCAL_LLM:
        return AsyncOpenAI(api_key="ollama", base_url=settings.LOCAL_LLM_URL)
    if settings.DEEPSEEK_API_KEY:
        try:
            base_url = _normalize_base_url(
                settings.DEEPSEEK_API_URL,
                strip_suffixes=["/chat/completions", "/v1"],
            )
        except ValueError as e:
            logger.error(f"Invalid DEEPSEEK_API_URL: {e}")
            return None
        return AsyncOpenAI(api_key=settings.DEEPSEEK_API_KEY, base_url=base_url)
    return None


@lru_cache
def _build_rerank_client() -> AsyncOpenAI | None:
    key = settings.RERANK_API_KEY or settings.RAG_RERANK_API_KEY or settings.DEEPSEEK_API_KEY
    url = settings.RERANK_API_URL or settings.RAG_RERANK_API_URL or settings.DEEPSEEK_API_URL
    if key and url:
        try:
            base = _normalize_base_url(url, strip_suffixes=["/chat/completions", "/v1"])
        except ValueError as e:
            logger.error(f"Invalid RERANK_API_URL: {e}")
            return None
        return AsyncOpenAI(api_key=key, base_url=base)
    return None


@lru_cache
def get_rag_pipeline() -> RAGPipeline:
    """Singleton RAG pipeline instance.

    embedding 客户端**不在这里构造**：检索与索引共用
    ``app.services.embedding_service.embedding_service``（issue #196 单一真源）。
    旧实现此处另建 ``_build_embedding_client()``（忽略 ENABLE_LOCAL_EMBEDDING），
    曾造成索引/查询模型漂移，勿恢复。

    无 embedding 配置（在线模式缺 key 且未启用本地 embedding）时，单例模块
    导入会抛 ``EmbeddingConfigError``（RuntimeError 子类）——这与基线
    ``_build_embedding_client()`` 返回 ``None`` 的降级语义对齐：**管线照常
    构造，仅向量臂关闭**。LLM/关键词能力与只依赖 ``rag_service.openai_client``
    的工具（如图谱回填脚本）不受影响。
    """
    try:
        from app.services.embedding_service import embedding_service
    except EmbeddingConfigError as e:
        logger.warning(
            "Embedding 未配置（%s）——RAG 管线以向量臂关闭模式构造；关键词检索与 LLM 能力不受影响。",
            e,
            exc_info=True,
        )
        embedding_service = None

    return RAGPipeline(
        openai_client=_build_openai_client(),
        embedding_provider=embedding_service,
        rerank_client=_build_rerank_client(),
        # LLM 重排兜底使用 chat LLM 客户端（与聊天同源），而非 /rerank 的智谱客户端
        llm_client=_build_openai_client(),
    )


async def wire_memory_embedding_provider() -> None:
    """Wire the RAG pipeline's embedding provider into the agent memory system.

    Called at startup so that semantic memory search works.
    """
    try:
        pipeline = get_rag_pipeline()
        # Import here to avoid circular import at module level
        from app.services.memory_service import memory_systems

        for _agent_id, ms in memory_systems.items():
            if ms._embedding_provider is None:
                ms.set_embedding_provider(pipeline.get_embedding)
        logger.info("Memory embedding provider wired")
    except Exception as e:
        logger.warning(f"Failed to wire memory embedding provider: {e}")
