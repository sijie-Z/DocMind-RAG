"""RAG pipeline — orchestrates retrieval, reranking, compression, and LLM generation."""
import asyncio
import logging
import time
from collections.abc import AsyncGenerator
from typing import Any, cast

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

from app.core.config import settings
from app.core.prometheus import (
    LLM_LATENCY,
    LLM_REQUEST_ERRORS,
    LLM_REQUEST_TOTAL,
    LLM_TOKENS,
    RAG_CACHE_HITS,
    RAG_CACHE_MISSES,
    RAG_GROUNDED_HITS,
    RAG_GROUNDED_TOTAL,
    RAG_PIPELINE_IN_FLIGHT,
    RAG_QUERY_INTENT,
    RAG_RETRIEVAL_ERRORS,
    RAG_RETRIEVAL_HITS,
    RAG_RETRIEVAL_LATENCY,
    RAG_RETRIEVAL_TOTAL,
)
from app.rag.cache import RetrievalCache, SemanticCache
from app.rag.context_compressor import compress_context_list
from app.rag.metrics import RAGMetrics
from app.rag.query_processor import QueryComplexityClassifier, QueryIntentClassifier, decompose_query
from app.rag.reranker import rerank
from app.rag.retriever import HybridRetriever

logger = logging.getLogger(__name__)


class RAGPipeline:
    """Composes retrieval, reranking, caching, and generation into a single pipeline."""

    def __init__(
        self,
        openai_client: AsyncOpenAI | None = None,
        embedding_client: AsyncOpenAI | None = None,
        rerank_client: AsyncOpenAI | None = None,
    ):
        self.openai_client = openai_client
        self.rerank_client = rerank_client
        self.retriever = HybridRetriever(openai_client=openai_client, embedding_client=embedding_client)
        self.cache = RetrievalCache()
        self.semantic_cache = SemanticCache()
        self.metrics = RAGMetrics()

    async def get_embedding(self, text: str) -> list[float]:
        return await self.retriever.get_embedding(text)

    # ---- Retrieval with caching ----

    async def search_knowledge_base(
        self,
        query: str,
        organization_id: int,
        top_k: int = 5,
        document_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve relevant documents with caching and retry."""
        start = time.perf_counter()
        RAG_RETRIEVAL_TOTAL.inc()
        RAG_PIPELINE_IN_FLIGHT.inc()
        self.metrics.inc("retrieval_total")
        self.metrics.record_event("retrieval", 1)

        try:
            # Exact cache (skip for document-specific queries)
            if not document_ids:
                cached = await self.cache.get(query, organization_id, top_k)
                if cached is not None:
                    RAG_CACHE_HITS.labels(cache_type="exact").inc()
                    self.metrics.inc("cache_hit")
                    self.metrics.record_event("cache_hit", 1)
                    if cached:
                        RAG_RETRIEVAL_HITS.inc()
                        self.metrics.inc("retrieval_hit")
                        self.metrics.record_event("retrieval_hit", 1)
                    else:
                        RAG_CACHE_MISSES.inc()
                    elapsed = (time.perf_counter() - start) * 1000
                    RAG_RETRIEVAL_LATENCY.observe(time.perf_counter() - start)
                    self.metrics.inc("latency_count")
                    self.metrics.record_event("latency", elapsed)
                    return cached
                RAG_CACHE_MISSES.inc()

            # Semantic cache
            query_vector = None
            if not document_ids:
                query_vector = await self.get_embedding(query)
                if query_vector:
                    sem_cached = await self.semantic_cache.get(query_vector)
                    if sem_cached:
                        RAG_CACHE_HITS.labels(cache_type="semantic").inc()
                        self.metrics.inc("semantic_cache_hit")
                        self.metrics.inc("cache_hit")
                        if sem_cached:
                            RAG_RETRIEVAL_HITS.inc()
                            self.metrics.inc("retrieval_hit")
                        elapsed = (time.perf_counter() - start) * 1000
                        RAG_RETRIEVAL_LATENCY.observe(time.perf_counter() - start)
                        self.metrics.inc("latency_count")
                        self.metrics.record_event("latency", elapsed)
                        if not document_ids:
                            await self.cache.set(query, organization_id, top_k, sem_cached)
                        return sem_cached

                # Query decomposition for complex queries
            sub_queries = [query]
            complexity = QueryComplexityClassifier.classify(query)
            if (
                not document_ids
                and self.openai_client
                and getattr(settings, "RAG_ENABLE_QUERY_DECOMPOSITION", True)
                and complexity == "complex"
            ):
                max_sq = max(1, int(getattr(settings, "RAG_DECOMPOSITION_MAX_SUBQUERIES", 4) or 4))
                model = settings.LOCAL_LLM_MODEL if settings.ENABLE_LOCAL_LLM else settings.DEEPSEEK_MODEL
                decomposed = await decompose_query(query, self.openai_client, model, max_sq)
                if len(decomposed) > 1:
                    sub_queries = decomposed
                    logger.info(
                        "Query decomposition: %s → %s",
                        query, sub_queries,
                    )

            # Retrieve (single or parallel per sub-query)
            retries = max(0, int(settings.RAG_RETRIEVAL_MAX_RETRIES or 2))
            all_results: list[list[dict]] = []
            for sq in sub_queries:
                sq_result = []
                for attempt in range(retries + 1):
                    try:
                        if attempt > 0:
                            self.metrics.inc("retry_total")
                        sq_result, qv = await self.retriever.retrieve(
                            sq, organization_id,
                            max(top_k, int(settings.RAG_RERANK_TOP_N or 20)),
                            document_ids,
                        )
                        if not query_vector and qv:
                            query_vector = qv
                        break
                    except Exception as e:
                        RAG_RETRIEVAL_ERRORS.inc()
                        logger.warning(
                            "Retrieval attempt %d/%d for sub-query %r failed: %s",
                            attempt + 1, retries + 1, sq, e,
                        )
                        if attempt < retries:
                            await asyncio.sleep(min(1.5, 0.3 * (2 ** attempt)))
                if sq_result:
                    all_results.append(sq_result)

            # Merge results from multiple sub-queries (dedup by chunk_id / doc_id)
            result = []
            seen_ids: set[str] = set()
            for sq_results in all_results:
                for hit in sq_results:
                    chunk_id = hit.get("_id") or hit.get("chunk_id", "")
                    doc_id = (hit.get("_source") or {}).get("doc_id", "")
                    dedup_key = chunk_id or doc_id
                    if dedup_key and dedup_key in seen_ids:
                        continue
                    if dedup_key:
                        seen_ids.add(dedup_key)
                    result.append(hit)

            # Rerank: cross-encoder / LLM reorder, then trim to top_k
            if result:
                result = await rerank(query, result, self.rerank_client)
                result = result[:top_k]

            if not document_ids:
                await self.cache.set(query, organization_id, top_k, result)
            if query_vector and result:
                await self.semantic_cache.set(query, query_vector, "", result)

            if result:
                RAG_RETRIEVAL_HITS.inc()
                self.metrics.inc("retrieval_hit")
                self.metrics.record_event("retrieval_hit", 1)
            elapsed = (time.perf_counter() - start) * 1000
            RAG_RETRIEVAL_LATENCY.observe(time.perf_counter() - start)
            self.metrics.inc("latency_count")
            self.metrics.record_event("latency", elapsed)
            return result
        finally:
            RAG_PIPELINE_IN_FLIGHT.dec()

    # ---- Groundedness reporting ----

    def report_grounded(self, has_sources: bool) -> None:
        RAG_GROUNDED_TOTAL.inc()
        self.metrics.inc("grounded_total")
        self.metrics.record_event("grounded", 1)
        if has_sources:
            RAG_GROUNDED_HITS.inc()
            self.metrics.inc("grounded_hit")
            self.metrics.record_event("grounded_hit", 1)

    def report_tokens(self, input_tokens: int, output_tokens: int) -> None:
        LLM_TOKENS.labels(direction="input").inc(input_tokens)
        LLM_TOKENS.labels(direction="output").inc(output_tokens)
        LLM_REQUEST_TOTAL.inc()
        self.metrics.inc("total_input_tokens", input_tokens)
        self.metrics.inc("total_output_tokens", output_tokens)
        self.metrics.inc("llm_request_count")

    def get_metrics(self, window_seconds: int = 0) -> dict[str, Any]:
        return self.metrics.get_snapshot(window_seconds)

    # ---- LLM streaming ----

    async def chat_stream(
        self,
        query: str,
        context: list[dict[str, Any]],
        history: list[dict[str, str]] = None,
        system_prompt_override: str | None = None,
        enable_compression: bool = True,
        enable_masking: bool = True,
    ) -> AsyncGenerator[str, None]:
        """Stream LLM response with context compression and optional PII masking."""
        if not self.openai_client:
            yield "LLM未配置"
            return

        # PII masking
        masking_mapping = {}
        if enable_masking and getattr(settings, "ENABLE_PII_MASKING", False):
            from app.services.masking_service import masking_service
            query, masking_mapping = masking_service.mask_text(query)

        # Compress context
        if enable_compression and context:
            compressed = compress_context_list(context, query, max_context_chars=8000)
        else:
            compressed = context

        context_str = "\n\n".join([
            f"资料[{i + 1}] (文件名: {item.get('filename', '未知文档')}):\n{(item.get('snippet') or item.get('text', ''))[:3000]}"
            for i, item in enumerate(compressed)
        ]) if compressed else "（未找到相关文档）"

        # Intent-based guidance
        intent = QueryIntentClassifier.classify(query)
        RAG_QUERY_INTENT.labels(intent=intent).inc()
        intent_guidance = {
            "factual": "请以事实陈述的方式回答，准确引用来源。",
            "procedural": "请按步骤清晰说明操作流程。",
            "list": "请列出所有相关项并简要说明。",
            "definition": "请给出清晰的定义和解释。",
            "comparison": "请从多个维度对比分析。",
            "causal": "请说明原因和结果。",
            "summary": "请给出简明扼要的总结。",
            "other": "请基于文档内容回答。",
        }.get(intent, "请基于文档内容回答。")

        has_context = bool(compressed)
        if system_prompt_override:
            system_prompt = system_prompt_override
        elif has_context:
            system_prompt = (
                "你是企业知识库问答助手。你的任务是基于提供的【参考文档】提供准确、客观的回答。\n"
                f"📌 回答指导: {intent_guidance}\n"
                "⚠️ 核心约束：\n"
                "1. **严格忠于原文**：优先根据提供的【参考文档】回答。\n"
                "2. **精准引用**：使用 [n] 格式标注引用来源。\n"
                "3. **结构化输出**：多使用分点列表（Markdown 格式）。\n"
                "4. **语言要求**：始终使用【简体中文】回答。\n"
                "5. **拒绝臆测**：严禁引用训练数据中的外部知识补充文档缺失部分。"
            )
        else:
            system_prompt = (
                "你是 DocMind 智能助手。\n"
                "知识库中没有找到与该问题相关的文档。\n"
                "请用你自身的知识回答用户的问题，并在回答开头说明：「知识库中暂无相关文档，以下回答基于通用知识：」\n"
                f"📌 回答指导: {intent_guidance}\n"
                "⚠️ 约束：\n"
                "1. 使用【简体中文】回答。\n"
                "2. 结构化输出，多使用分点列表。\n"
                "3. 如果问题涉及专业领域，注明建议用户上传相关文档以获得更精准的回答。"
            )

        # Build token-budget-aware message list
        from app.rag.context_window import build_rag_messages
        raw_messages = build_rag_messages(
            system_prompt=system_prompt,
            context_docs=context_str,
            history=history or [],
            user_query=query,
            max_tokens=settings.AI_MAX_TOKENS,
        )
        messages: list[ChatCompletionMessageParam] = cast(
            list[ChatCompletionMessageParam], raw_messages
        )

        llm_start = time.perf_counter()
        try:
            model = settings.LOCAL_LLM_MODEL if settings.ENABLE_LOCAL_LLM else settings.DEEPSEEK_MODEL
            stream = await self.openai_client.chat.completions.create(
                model=model, messages=messages, stream=True,
                temperature=0.1, max_tokens=settings.AI_MAX_TOKENS,
                timeout=settings.AI_STREAM_TIMEOUT,
            )
            full_response = ""
            first_token = True
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    full_response += content
                    if first_token:
                        LLM_LATENCY.observe(time.perf_counter() - llm_start)
                        first_token = False
                    yield content

            # Token estimation
            input_text = "".join(str(m.get("content", "")) for m in messages if m.get("content"))
            self.report_tokens(max(1, int(len(input_text) / 1.5)), max(1, int(len(full_response) / 1.5)))

            # Unmask
            if masking_mapping:
                from app.services.masking_service import masking_service
                full_response = masking_service.unmask_text(full_response, masking_mapping)
                logger.info("PII masking: response unmasked")

        except Exception as e:
            LLM_REQUEST_ERRORS.inc()
            logger.error(f"LLM streaming failed: {e}", exc_info=True)
            yield "系统处理异常，请重试"
