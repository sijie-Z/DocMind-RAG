"""AI and RAG settings — LLM, embedding, reranker, vector search, caching."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _auto_tier_defaults(
    *,
    explicitly_set: set[str] | frozenset[str] | None = None,
    local_embedding: bool = False,
) -> dict[str, object]:
    """Apply hardware-detected defaults for fields the user did NOT set anywhere.

    ``explicitly_set`` 应传 pydantic 的 ``settings.model_fields_set`` —— 它包含
    **所有显式来源**（真实环境变量 / `.env` 文件 / 初始化参数）。**不能只查
    `os.environ`**：`.env` 的值不在 `os.environ` 里，曾经因此把 `.env` 里显式
    配置的字段静默覆盖成硬件档位默认值（issue #232：`.env` 写
    `EMBEDDING_MODEL=qwen3-embedding:0.6b`，运行时被覆盖成 `bge-m3`，
    worker 报 `404 model not found`，排查代价极高）。

    ``local_embedding=True``（``ENABLE_LOCAL_EMBEDDING``）时**不套用硬件档**：
    本地 embedding 的模型由 ``LOCAL_EMBEDDING_MODEL`` 决定（默认
    ``nomic-embed-text`` = 768 维），而硬件档面向的是在线模型
    （bge-m3/1024、Qwen3/2048+）。混用会制造「模型 nomic、维度 1024」的固有
    冲突（issue #196 的启动维度校验会拒绝启动）。此时保留字段默认值
    （768），用户换了本地模型时须显式设置 ``VECTOR_DIMENSION``——启动校验会
    给出可读错误，而不是静默写坏数据。

    **不要在这里对返回值做 `str()`。** 调用方（`app/core/config/__init__.py`）用
    `object.__setattr__` 写进 settings，**绕过 pydantic 的类型校验与转换** ——
    所以这里给的类型就是最终生效的类型。

    曾经这里写的是 `str(getattr(hw, attr))`，于是 `VECTOR_DIMENSION`（int 字段）
    在运行时变成字符串 `'1024'`，而它被直接塞进 ES 的索引 mapping，
    ES 以 `400 mapper_parsing_exception: Property [dims] must be an integer` 拒绝 ——
    **在没把 VECTOR_DIMENSION 写进环境变量的机器上（即全新部署的默认路径），
    索引根本建不出来。**
    """
    if local_embedding:
        return {}
    try:
        from app.core.hardware import detect as _detect

        hw = _detect()
    except Exception:
        return {}
    overrides: dict[str, object] = {}
    explicit = explicitly_set or set()
    for field, attr, env_var in [
        ("EMBEDDING_MODEL", "embedding_model", "EMBEDDING_MODEL"),
        ("VECTOR_DIMENSION", "vector_dim", "VECTOR_DIMENSION"),
    ]:
        if field in explicit or env_var in __import__("os").environ:
            continue
        overrides[field] = getattr(hw, attr)
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

    # Workflow LLM 节点的环境默认 key（services/workflow_engine.py 的 get_llm）。
    # issue #273：此前引擎用 getattr(settings, 'OPENAI_API_KEY', '')（单引号写法）
    # 兜底读取，但 Settings 无对应字段——extra="ignore" 下 .env 配置被静默吞掉，
    # key 恒为空串（与 #259 同族反向缺口，前两轮全仓扫描因单引号口径漏网）。
    # deepseek 节点复用上方既有 DEEPSEEK_API_KEY，引擎侧已同口径直读。
    OPENAI_API_KEY: str = ""
    DASHSCOPE_API_KEY: str = ""

    # Local LLM (Ollama / LocalAI)
    ENABLE_LOCAL_LLM: bool = False
    LOCAL_LLM_URL: str = "http://localhost:11434/v1"
    LOCAL_LLM_MODEL: str = "llama3"

    # Local embedding model
    ENABLE_LOCAL_EMBEDDING: bool = False
    LOCAL_EMBEDDING_MODEL: str = "nomic-embed-text"

    # Embedding — default to local Ollama (free); set env vars for paid API
    # 注：EMBEDDING_BASE_URL 曾是零消费的重复旋钮（真正的旋钮是上方 EMBEDDING_API_URL，
    # 与 #241 已删的 DEEPSEEK_BASE_URL 同形态，issue #242 已删除）。
    EMBEDDING_API_KEY: str | None = None
    EMBEDDING_API_URL: str = "http://localhost:11434/v1"
    EMBEDDING_MODEL: str = "nomic-embed-text"

    # issue #196：embedding 一致性契约。
    # - SEND_DIMENSIONS 默认关闭：请求服务端截断会掩盖「VECTOR_DIMENSION 已过期」
    #   的漂移（截断后维度“恰好”匹配 mapping，但模型已经换了）。只有确认
    #   provider 支持 dimensions 截断且确实想用截断时才开启。
    # - 启动 probe：服务可达但返回维度 ≠ VECTOR_DIMENSION → 拒绝启动；
    #   服务不可达 → 显式告警后继续（离线开发属预期）。
    EMBEDDING_SEND_DIMENSIONS: bool = False
    EMBEDDING_STARTUP_PROBE: bool = True
    EMBEDDING_PROBE_TIMEOUT_SECONDS: float = Field(default=5.0, gt=0.0)

    # Vector search
    VECTOR_DIMENSION: int = 768  # nomic-embed-text
    # 注：SIMILARITY_THRESHOLD 曾是 README:108 的承诺（默认 0.7），但实现从未存在
    # 对应机制（真实过滤是 retriever.py 的两处 min_score 硬编码），issue #242 已
    # 删除字段与文档承诺。未来若真做「最低相似度」旋钮，应映射 min_score 空间，
    # 属新功能而不是接线。
    # 搜索结果默认数量（issue #242 接线）：供 KnowledgeSearchRequest.top_k 的
    # 默认值使用。注意 chat 检索链路的独立默认值是 5（rag/pipeline.py），
    # 本字段只承诺 README 所说的「搜索」层。
    TOP_K_RESULTS: int = 10

    # RAG retrieval strategy
    RAG_ENABLE_MMR: bool = True
    RAG_MMR_LAMBDA: float = 0.65
    RAG_MMR_CANDIDATE_MULTIPLIER: int = 4
    RAG_HYDE_WEIGHT: float = 0.3

    # #186：提示词正文上限（字符）与展示 snippet 上限（字符）。
    # 提示词正文单一来源是 `prompt_text`（完整 chunk）；超过上限时按
    # context_compressor 的规则式句子抽取截断，不硬切头部。默认值与原硬编码
    # （3000 / 260）一致，行为只在「超限且关键句不在头部」时改善。
    RAG_PROMPT_TEXT_MAX_CHARS: int = Field(default=3000, ge=1)
    RAG_SNIPPET_MAX_CHARS: int = Field(default=260, ge=1)

    # Query rewrite (multi-query expansion)
    RAG_ENABLE_QUERY_REWRITE: bool = True
    RAG_QUERY_REWRITE_COUNT: int = 4
    # LLM 改写等待超时（秒）。issue #259：此前 retriever 经 getattr 兜底读取但
    # Settings 无此字段，extra="ignore" 下 .env 配置被静默忽略；现补字段可覆盖。
    # issue #273 顺带：补下界（负数此前可入 Settings）。0 是合法显式值——消费端
    # retriever 以 max(0.2, ·) 钳下限（该偏离由常驻用例钉住）。
    RAG_QUERY_REWRITE_TIMEOUT_SECONDS: float = Field(default=1.2, ge=0.0)

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

    # Elasticsearch
    ELASTICSEARCH_VERIFY_CERTS: bool = False
    ELASTICSEARCH_REPLICAS: int = 0

    # Retrieval cache + retry
    RAG_ENABLE_CACHE: bool = True
    RAG_CACHE_TTL_SECONDS: int = 600
    RAG_CACHE_MAX_SIZE: int = 1000
    RAG_RETRIEVAL_MAX_RETRIES: int = 2

    # Semantic cache（rag/cache.py 的 SemanticCache）。issue #259：此前经
    # getattr(settings, …) 兜底读取但 Settings 无字段，.env 配置被静默忽略；
    # 现补字段，类型/默认值与原 getattr 兜底一致。issue #273 顺带：补合理性校验
    # （阈值 [0,1]；TTL ≥0——负值此前可入 Settings，setex 报 invalid expire）。
    SEMANTIC_CACHE_THRESHOLD: float = Field(default=0.92, ge=0.0, le=1.0)
    SEMANTIC_CACHE_TTL_SECONDS: int = Field(default=3600, ge=0)
    # issue #195：阈值 0.92 只能做粗筛——相邻条款（第七条 vs 第十三条）或
    # 同句式换实体（北京 vs 上海）的向量余弦也在阈值内，会跨问题返回他人的
    # sources（检索链）/ 完整答案（问答链）。两个语义缓存命名空间因此默认
    # 关闭：检索 sources（精确缓存 RetrievalCache 已按 query+org+top_k 足够）
    # 与问答 answer。显式开启后，命中仍必须通过条号/数字/实体等价判定
    # （rag/cache.py::queries_equivalent），阈值不是唯一判据。
    SEMANTIC_CACHE_RETRIEVAL_ENABLED: bool = False
    SEMANTIC_CACHE_ANSWER_ENABLED: bool = False

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
