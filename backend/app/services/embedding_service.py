# backend/app/services/embedding_service.py
"""Embedding 单一真源（issue #196）。

索引期（``doc_processor`` / ``semantic_chunker`` / demo / seed）与检索期
（``rag/retriever``）**必须**共用本模块解析出的客户端、模型与维度：

- **唯一构造点**：``resolve_embedding_config()`` / ``EmbeddingService``。
  别处不得再构造第二套 embedding 客户端（旧 ``dependencies._build_embedding_client``
  不读 ``ENABLE_LOCAL_EMBEDDING``，曾造成索引 ``nomic-embed-text`` / 查询 ``bge-m3``
  的模型漂移——不同模型即使维度相同，向量空间也不可混用）。
- **单一模型来源**：本地模式 = ``LOCAL_EMBEDDING_MODEL``，在线模式 =
  ``EMBEDDING_MODEL``；``ENABLE_LOCAL_EMBEDDING`` 只在这里判断。
- **维度契约**：返回向量长度必须等于 ``VECTOR_DIMENSION``（ES dense_vector
  mapping 的 dims）。不一致抛 ``EmbeddingDimensionMismatchError``，让漂移在
  写入路径上显式失败，而不是等 ES 抛 mapping 错误或静默混用两种向量。
"""

import asyncio
import logging
from dataclasses import dataclass

from openai import AsyncOpenAI

from app.core.config import resolve_embedding_model, settings

logger = logging.getLogger(__name__)


class EmbeddingDimensionMismatchError(RuntimeError):
    """embedding 服务返回的向量维度与 ``VECTOR_DIMENSION`` 不一致（模型/配置漂移）。"""


class EmbeddingProbeUnavailableError(RuntimeError):
    """启动探测无法完成（服务不可达/超时）。是否致命由调用方决定。"""


@dataclass(frozen=True)
class EmbeddingConfig:
    """一次解析得到的 embedding 契约（client + model + dimension）。"""

    client: AsyncOpenAI
    model: str
    dimension: int
    source: str  # "local" | "online"
    send_dimensions: bool = False


def resolve_embedding_config() -> EmbeddingConfig:
    """构造 embedding 客户端 —— **全仓唯一构造点**。"""
    dimension = int(settings.VECTOR_DIMENSION)
    send_dimensions = bool(settings.EMBEDDING_SEND_DIMENSIONS)
    # 模型名只经 resolve_embedding_model() 解析（config 单一真源）。
    model = resolve_embedding_model()

    if settings.ENABLE_LOCAL_EMBEDDING:
        # 优先使用本地 Embedding 模型（共享 Ollama 基础 URL）
        client = AsyncOpenAI(
            api_key="ollama",
            base_url=settings.LOCAL_LLM_URL,
            timeout=60.0,
        )
        return EmbeddingConfig(
            client=client,
            model=model,
            dimension=dimension,
            source="local",
            send_dimensions=send_dimensions,
        )

    api_key = settings.EMBEDDING_API_KEY or settings.DEEPSEEK_API_KEY
    if not api_key:
        raise RuntimeError(
            "Embedding API key is missing. Set EMBEDDING_API_KEY or DEEPSEEK_API_KEY"
        )

    raw_base = settings.EMBEDDING_API_URL or "https://api.openai.com/v1"
    # 兼容误配: 用户可能把完整 /embeddings endpoint 填进 base_url
    base_url = raw_base.replace("/embeddings", "").rstrip("/")
    client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=60.0)
    return EmbeddingConfig(
        client=client,
        model=model,
        dimension=dimension,
        source="online",
        send_dimensions=send_dimensions,
    )


class EmbeddingService:
    def __init__(self, config: EmbeddingConfig | None = None):
        cfg = config or resolve_embedding_config()
        self.client = cfg.client
        self.model = cfg.model
        self.dimension = cfg.dimension
        self.source = cfg.source
        self.send_dimensions = cfg.send_dimensions

        # 保守批次，降低 RPM/TPM 风险
        self.batch_size = 8
        self.max_retries = 5
        logger.info(
            "EmbeddingService 已初始化，使用%s模型: %s（维度=%s）",
            "本地" if cfg.source == "local" else "在线",
            self.model,
            self.dimension,
        )

    # ---- 维度契约 ----

    def _ensure_dimensions(self, vectors: list[list[float]]) -> None:
        """返回向量长度必须等于配置维度；否则显式失败（issue #196）。"""
        for vector in vectors:
            actual = len(vector)
            if actual != self.dimension:
                raise EmbeddingDimensionMismatchError(
                    f"Embedding 维度不一致：模型 {self.model!r} 返回 {actual} 维向量，"
                    f"但 VECTOR_DIMENSION={self.dimension}"
                    f"（ES 索引 dense_vector dims 依赖该值）。"
                    "这通常意味着 embedding 模型与配置漂移："
                    f"请把 VECTOR_DIMENSION 改为 {actual}，或切回原模型；"
                    "更换模型后必须重建索引并重新索引全部文档"
                    "（scripts/reconcile_knowledge_index.py --reindex）。"
                )

    # ---- 调用 ----

    async def _create_embeddings(self, batch: list[str]) -> list[list[float]]:
        params: dict = {"input": batch, "model": self.model}
        if self.send_dimensions:
            # 仅在确认服务端支持 dimensions 截断时开启（默认关闭：截断会掩盖
            # 「VECTOR_DIMENSION 已过期」的漂移，维度校验才是数据正确性防线）。
            params["dimensions"] = self.dimension
        response = await self.client.embeddings.create(**params)
        vectors = [data.embedding for data in response.data]
        self._ensure_dimensions(vectors)
        return vectors

    async def _embed_batch_with_retry(self, batch: list[str]) -> list[list[float]]:
        last_err: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                return await self._create_embeddings(batch)
            except EmbeddingDimensionMismatchError:
                # 配置/模型漂移不是瞬态错误，不重试、不降级。
                raise
            except Exception as e:
                last_err = e
                msg = str(e).lower()
                is_rate_limit = ("429" in msg) or ("rpm" in msg) or ("tpm" in msg) or ("rate" in msg)
                is_forbidden_quota = ("403" in msg) and (("rpm" in msg) or ("quota" in msg) or ("limit" in msg))
                is_timeout = ("timeout" in msg) or ("timed out" in msg)

                if is_rate_limit or is_forbidden_quota or is_timeout:
                    sleep_s = min(8.0, 0.8 * (2 ** (attempt - 1)))
                    logger.warning(
                        f"Embedding batch retry {attempt}/{self.max_retries} due to transient error: {e}; sleep={sleep_s}s"
                    )
                    await asyncio.sleep(sleep_s)
                    continue

                logger.error(f"Embedding batch failed (non-retryable): {e}")
                raise

        raise RuntimeError(f"Embedding batch failed after retries: {last_err}")

    async def get_embeddings(self, texts: list[str]) -> list[list[float]]:
        """批量获取文本向量（索引期），带限流重试与维度校验。"""
        try:
            valid_texts = [t for t in texts if t and t.strip()]
            if not valid_texts:
                return []

            all_embeddings: list[list[float]] = []
            total = len(valid_texts)

            for i in range(0, total, self.batch_size):
                batch = valid_texts[i : i + self.batch_size]
                if i > 0:
                    await asyncio.sleep(0.25)

                logger.info(
                    f"Embedding batch {i // self.batch_size + 1}/{(total + self.batch_size - 1) // self.batch_size}, size={len(batch)}"
                )
                batch_embeddings = await self._embed_batch_with_retry(batch)
                all_embeddings.extend(batch_embeddings)

            return all_embeddings

        except Exception as e:
            logger.error(f"Failed to generate embeddings: {str(e)}")
            raise

    async def embed_query(self, text: str) -> list[float]:
        """检索期单条 embedding —— 与索引期共用同一 client/model/维度校验。"""
        cleaned = (text or "").replace("\n", " ")
        if not cleaned.strip():
            return []
        vectors = await self._embed_batch_with_retry([cleaned])
        return vectors[0] if vectors else []

    async def probe(self) -> int:
        """真实调用一次 embedding 并返回维度（启动一致性校验用）。"""
        vectors = await self._embed_batch_with_retry(["embedding consistency probe"])
        if not vectors:
            raise EmbeddingProbeUnavailableError("embedding probe 未返回向量")
        return len(vectors[0])


async def verify_embedding_consistency(
    *, service: EmbeddingService | None = None
) -> dict:
    """启动时校验「模型可用 + 返回维度 == VECTOR_DIMENSION」（issue #196）。

    Returns:
        校验通过（或被配置禁用）的信息 dict。

    Raises:
        EmbeddingDimensionMismatchError: 服务可达但返回维度与配置不符
            —— 调用方应 fail fast（继续运行只会写坏数据）。
        EmbeddingProbeUnavailableError: 服务不可达/超时
            —— 调用方应显式告警后继续（离线开发属预期；检索向量臂已有降级信号）。
    """
    if not bool(settings.EMBEDDING_STARTUP_PROBE):
        return {"skipped": True}

    svc = service or embedding_service
    timeout = float(settings.EMBEDDING_PROBE_TIMEOUT_SECONDS)
    try:
        probed_dimension = await asyncio.wait_for(svc.probe(), timeout=timeout)
    except EmbeddingDimensionMismatchError:
        raise
    except Exception as e:
        raise EmbeddingProbeUnavailableError(
            f"embedding 启动探测失败（模型 {svc.model!r}，{svc.source}）：{e}"
        ) from e

    if probed_dimension != svc.dimension:
        # 防御未来替身未在 probe 内校验的路径；正常流程 probe 已抛错。
        raise EmbeddingDimensionMismatchError(
            f"Embedding 维度不一致：probe 返回 {probed_dimension} 维，"
            f"但 VECTOR_DIMENSION={svc.dimension}（模型 {svc.model!r}）。"
        )
    return {
        "skipped": False,
        "model": svc.model,
        "source": svc.source,
        "dimension": probed_dimension,
    }


# 单例模式 —— 索引与检索共用这一份客户端/模型/维度。
embedding_service = EmbeddingService()
