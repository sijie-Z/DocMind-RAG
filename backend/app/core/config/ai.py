"""AI and RAG settings — LLM, embedding, reranker, vector search, caching."""

from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def unified_final_enabled() -> bool:
    """内部灰度门：阶段 2 覆盖单一最终综合；默认关闭，不启用阶段 3 恢复语义。"""
    from app.core.config import settings

    return bool(getattr(settings, "AGENT_UNIFIED_FINAL_ENABLED", False))


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

    # issue #467：LLM 连接级快速失败（RAG 查询改写 / 查询分解 / HyDE / 重排 LLM
    # 兜底 / 流式生成共用 `app.core.llm_failfast`）。连接阶段独立短超时：死主机
    # （SYN 被丢弃）不再拖满读超时；连接被拒 / DNS 失败由 APIConnectionError 直接
    # 判定。**读/写阶段的慢与连接建立后的瞬时中断不在此快速失败**——那两类属
    # 可恢复故障，仍走调用方既有超时、重试与降级。默认 1.0s 低于改写站 1.2s
    # 外层预算，保证首个串行调用点也能判定并写入负缓存（<3s 上界的结构前提）；
    # 高延迟链路可上调，但会同比抬高不可达的暴露时间。
    LLM_CONNECT_TIMEOUT_SECONDS: float = Field(default=1.0, gt=0.0)
    # 瞬时故障（408/409/429/5xx 与连接建立后的传输中断）的重试次数。连接类
    # 不可达不在此列——零重试；读/写超时也不重试（已花掉完整读预算，重试只会
    # 成倍放大失败路径耗时）。
    LLM_TRANSIENT_MAX_RETRIES: int = Field(default=1, ge=0, le=5)
    LLM_TRANSIENT_RETRY_BACKOFF_SECONDS: float = Field(default=0.4, ge=0.0)
    # 连接级不可达的进程级负缓存（秒），**按端点（client.base_url）区分**：
    # 一次判定不可达后，窗口内同一端点的调用直接快速失败（任一调用成功即清除）；
    # 不同端点互不封锁。0 = 关闭负缓存。
    LLM_UNREACHABLE_COOLDOWN_SECONDS: float = Field(default=5.0, ge=0.0)

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

    # issue #209：检索不合格 → 拒答闸门（默认关闭，行为与修前一致）。
    # 消费点只在 chat 生成链路（services/chat_service.run_rag_pipeline 检索后、
    # 生成前），**不**进共享的 search_knowledge_base（REST/agent/workflow 共用，
    # 静默截断会是回归）。阈值与重排绝对分同一口径：本地 CE 是原始 logit、
    # 智谱是 relevance_score 0–1、LLM 兜底是自报 0–10 分——三种口径不可互换，
    # 必须按**实际启用的 reranker** 用 backend/evals 金标集校准后再开启。
    # None = 不启用闸门。NaN/Inf 在配置加载期即拒绝（否则 NaN 恒「通过」、
    # Inf 恒「拒答」，且都没有可读信号）。
    RAG_RELEVANCE_GATE_THRESHOLD: float | None = Field(default=None, allow_inf_nan=False)

    # Local cross-encoder reranker (free, no API cost)
    RERANK_USE_LOCAL: bool = True
    RERANK_LOCAL_MODEL: str = "BAAI/bge-reranker-base"

    # Reranker API fallback — None = disabled, only use local BGE
    RERANK_API_KEY: str | None = None
    RERANK_API_URL: str | None = None
    # issue #190：默认值移到 reranker 的解析器（RERANK_MODEL → legacy
    # RAG_RERANK_MODEL → 协议默认 "rerank"）。此处保留 None 才能让
    # legacy 别名在未显式设置 RERANK_MODEL 时真正生效。
    RERANK_MODEL: str | None = None

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

    # Code execution sandbox — issue #206：闭集校验 + fail-closed 语义。
    #   "auto"   — Docker 可用时走容器，不可用时**拒绝执行**（不再静默回落进程内）；
    #   "docker" — 仅容器，Docker 不可用即拒绝；
    #   "ast"    — 显式选择进程内 AST 沙箱（子进程执行，仅 AST 白名单约束）。
    # 未知值在配置加载期即 ValidationError；auto/docker 的拒绝发生在执行侧
    # （见 docker_sandbox.resolve_sandbox_mode）。
    SANDBOX_MODE: Literal["auto", "docker", "ast"] = "auto"
    SANDBOX_DOCKER_IMAGE: str = "python:3.11-slim"
    SANDBOX_DOCKER_MEMORY_MB: int = 256
    SANDBOX_DOCKER_NETWORK: str = "none"
    SANDBOX_DOCKER_TIMEOUT_SECONDS: int = 30

    # Persistent terminal（issue #477）：容器持久 PTY + WebSocket。
    # 默认关闭——开启后 backend 进程需要 Docker socket（= 宿主 root 等价权限），
    # 只应通过显式 overlay（backend/docker-compose.terminal.yml）启用。
    # 语义：TERMINAL_ENABLED=false 或 SANDBOX_MODE=ast 或 Docker 不可达时，
    # 所有终端 API / 工具一律 503 / terminal_disabled，绝不创建半成品资源。
    TERMINAL_ENABLED: bool = False
    # 空串 = 回落到 SANDBOX_DOCKER_IMAGE；显式覆盖时也必须固定 tag/digest
    # （禁止 latest），保证容器基线与审计可复现。
    TERMINAL_CONTAINER_IMAGE: str = ""
    TERMINAL_IDLE_TIMEOUT_SECONDS: int = Field(default=900, gt=0)
    TERMINAL_MAX_LIFETIME_SECONDS: int = Field(default=3600, gt=0)
    TERMINAL_MAX_PER_USER: int = Field(default=3, ge=1)
    TERMINAL_MAX_PER_ORG: int = Field(default=10, ge=1)
    TERMINAL_RING_BUFFER_BYTES: int = Field(default=262144, ge=1024)
    TERMINAL_MAX_READ_BYTES: int = Field(default=65536, ge=1)
    TERMINAL_MAX_INPUT_BYTES: int = Field(default=65536, ge=1)
    TERMINAL_TICKET_TTL_SECONDS: int = Field(default=60, gt=0)
    # true 时 executor 对 terminal_* 强制走人工审批（即使 approval_mode="never"）；
    # 无审批通道时 fail-closed（结构化 approval_denied），绝不静默执行。
    TERMINAL_REQUIRE_APPROVAL: bool = True

    # 最终综合阶段 2 的内部灰度门。默认 False；只用于 fake-provider 验证单一
    # 最终综合与质量链，不代表 final-only resume、done.final_answer 或会话迁移
    # 已经实现，也不得据此在真实环境默认启用。
    AGENT_UNIFIED_FINAL_ENABLED: bool = False

    # Observability — Langfuse
    # Deprecated candidate（issue #510 第三步待仓库所有者批准删除；未获批准前
    # 文件/依赖/埋点全部保留）。显式总开关：默认 True 维持既有语义
    # （配了 key 才真正启用）；置 false 可在保留代码的同时彻底关闭。
    LANGFUSE_ENABLED: bool = True
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_HOST: str = "https://cloud.langfuse.com"
