"""AI and RAG settings — LLM, embedding, reranker, vector search, caching."""


from pydantic_settings import BaseSettings, SettingsConfigDict


def _auto_tier_defaults() -> dict[str, str]:
    """Apply hardware-detected defaults for fields not explicitly set via env."""
    try:
        from app.core.hardware import detect as _detect

        hw = _detect()
    except Exception:
        return {}
    overrides: dict[str, str] = {}
    for field, attr, env_var in [
        ("EMBEDDING_MODEL", "embedding_model", "EMBEDDING_MODEL"),
        ("VECTOR_DIMENSION", "vector_dim", "VECTOR_DIMENSION"),
    ]:
        if env_var not in __import__("os").environ:
            overrides[field] = str(getattr(hw, attr))
    return overrides


class AISettings(BaseSettings):
    """AI model, embedding, reranker, vector, and RAG pipeline settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
        extra="ignore",
    )

    # DeepSeek / LLM
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_API_URL: str = "https://api.deepseek.com"
    DEEPSEEK_MODEL: str = "deepseek-v4-flash"
    AI_MAX_TOKENS: int = 10000
    AI_STREAM_TIMEOUT: int = 120

    # Local LLM (Ollama / LocalAI)
    ENABLE_LOCAL_LLM: bool = False
    LOCAL_LLM_URL: str = "http://localhost:11434/v1"
    LOCAL_LLM_MODEL: str = "llama3"

    # Local embedding model
    ENABLE_LOCAL_EMBEDDING: bool = False
    LOCAL_EMBEDDING_MODEL: str = "nomic-embed-text"

    # Embedding — default to local Ollama (free); set env vars for paid API
    EMBEDDING_API_KEY: str | None = None
    EMBEDDING_API_URL: str = "http://localhost:11434/v1"
    EMBEDDING_BASE_URL: str | None = None
    EMBEDDING_MODEL: str = "nomic-embed-text"

    # Vector search
    VECTOR_DIMENSION: int = 768  # nomic-embed-text
    SIMILARITY_THRESHOLD: float = 0.7
    TOP_K_RESULTS: int = 10

    # RAG retrieval strategy
    RAG_ENABLE_MMR: bool = True
    RAG_MMR_LAMBDA: float = 0.65
    RAG_MMR_CANDIDATE_MULTIPLIER: int = 4
    RAG_HYDE_WEIGHT: float = 0.3

    # Query rewrite (multi-query expansion)
    RAG_ENABLE_QUERY_REWRITE: bool = True
    RAG_QUERY_REWRITE_COUNT: int = 4

    # Reranker
    RAG_ENABLE_RERANKER: bool = True
    RAG_RERANK_TOP_N: int = 20
    RAG_RERANK_TIMEOUT_SECONDS: float = 8.0

    # Local cross-encoder reranker (free, no API cost)
    RERANK_USE_LOCAL: bool = True
    RERANK_LOCAL_MODEL: str = "BAAI/bge-reranker-base"

    # Reranker API fallback — None = disabled, only use local BGE
    RERANK_API_KEY: str | None = None
    RERANK_API_URL: str | None = None
    RERANK_MODEL: str = "rerank"

    # Legacy reranker env var aliases
    RAG_RERANK_MODEL: str | None = None
    RAG_RERANK_API_KEY: str | None = None
    RAG_RERANK_API_URL: str | None = None

    # Retrieval cache + retry
    RAG_ENABLE_CACHE: bool = True
    RAG_CACHE_TTL_SECONDS: int = 600
    RAG_CACHE_MAX_SIZE: int = 1000
    RAG_RETRIEVAL_MAX_RETRIES: int = 2

    # Query decomposition (multi-perspective RAG)
    RAG_ENABLE_QUERY_DECOMPOSITION: bool = True
    RAG_DECOMPOSITION_MAX_SUBQUERIES: int = 4

    # Privacy & security
    ENABLE_PII_MASKING: bool = True

    # Code execution sandbox — "docker" (container-level isolation) or "ast" (in-process)
    SANDBOX_MODE: str = "auto"
    SANDBOX_DOCKER_IMAGE: str = "python:3.11-slim"
    SANDBOX_DOCKER_MEMORY_MB: int = 256
    SANDBOX_DOCKER_NETWORK: str = "none"
    SANDBOX_DOCKER_TIMEOUT_SECONDS: int = 30

    # Observability — Langfuse
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_HOST: str = "https://cloud.langfuse.com"
