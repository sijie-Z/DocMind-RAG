"""AI and RAG settings — LLM, embedding, reranker, vector search, caching."""


from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # Embedding
    EMBEDDING_API_KEY: str | None = None
    EMBEDDING_API_URL: str = "https://open.bigmodel.cn/api/paas/v4/"
    EMBEDDING_BASE_URL: str | None = None
    EMBEDDING_MODEL: str = "embedding-3"

    # Vector search
    VECTOR_DIMENSION: int = 2048
    SIMILARITY_THRESHOLD: float = 0.7
    TOP_K_RESULTS: int = 10

    # RAG retrieval strategy
    RAG_ENABLE_MMR: bool = True
    RAG_MMR_LAMBDA: float = 0.65
    RAG_MMR_CANDIDATE_MULTIPLIER: int = 4

    # Query rewrite (multi-query expansion)
    RAG_ENABLE_QUERY_REWRITE: bool = True
    RAG_QUERY_REWRITE_COUNT: int = 4

    # Reranker
    RAG_ENABLE_RERANKER: bool = True
    RAG_RERANK_TOP_N: int = 20
    RAG_RERANK_TIMEOUT_SECONDS: float = 8.0

    # Reranker API (supports alternate env var names)
    RERANK_API_KEY: str | None = None
    RERANK_API_URL: str | None = "https://open.bigmodel.cn/api/paas/v4/"
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

    # Privacy & security
    ENABLE_PII_MASKING: bool = True
