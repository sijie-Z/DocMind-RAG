# backend/app/services/embedding_service.py

import logging
import asyncio
from typing import List
from openai import AsyncOpenAI
from app.core.config import settings

logger = logging.getLogger(__name__)

class EmbeddingService:
    def __init__(self):
        # 优先使用本地 Embedding 模型
        if settings.ENABLE_LOCAL_EMBEDDING:
            self.client = AsyncOpenAI(
                api_key="ollama",
                base_url=settings.LOCAL_LLM_URL, # 共享 Ollama 基础 URL
                timeout=60.0,
            )
            self.model = settings.LOCAL_EMBEDDING_MODEL
            logger.info(f"EmbeddingService 已初始化，使用本地模型: {self.model}")
        else:
            api_key = settings.EMBEDDING_API_KEY or settings.DEEPSEEK_API_KEY
            if not api_key:
                raise RuntimeError("Embedding API key is missing. Set EMBEDDING_API_KEY or DEEPSEEK_API_KEY")

            raw_base = settings.EMBEDDING_API_URL or "https://api.openai.com/v1"
            # 兼容误配: 用户可能把完整 /embeddings endpoint 填进 base_url
            base_url = raw_base.replace("/embeddings", "")

            self.client = AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                timeout=60.0,
            )
            self.model = settings.EMBEDDING_MODEL
            logger.info(f"EmbeddingService 已初始化，使用在线模型: {self.model}")
            
        # 保守批次，降低 RPM/TPM 风险
        self.batch_size = 8
        self.max_retries = 5

    async def _embed_batch_with_retry(self, batch: List[str]) -> List[List[float]]:
        last_err: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = await self.client.embeddings.create(
                    input=batch,
                    model=self.model,
                )
                return [data.embedding for data in response.data]
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

    async def get_embeddings(self, texts: List[str]) -> List[List[float]]:
        """获取文本向量，带限流重试与退避。"""
        try:
            valid_texts = [t for t in texts if t and t.strip()]
            if not valid_texts:
                return []

            all_embeddings: List[List[float]] = []
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

# 单例模式
embedding_service = EmbeddingService()
