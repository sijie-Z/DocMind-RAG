"""Hybrid retriever — keyword + vector search with RRF fusion."""
import asyncio
import hashlib
import logging
import math
import time
from datetime import datetime

from app.core.config import settings
from app.core.elasticsearch import ElasticsearchTools
from app.core.prometheus import RAG_ADAPTIVE_STRATEGY, RAG_QUERY_REWRITE_TOTAL
from app.rag.errors import EmbeddingUnavailableError
from app.rag.query_processor import (
    QueryComplexityClassifier,
    QueryIntentClassifier,
    extract_query_terms,
    generate_hyde_doc,
    rewrite_query_candidates,
    rewrite_query_llm,
)

logger = logging.getLogger(__name__)


class HybridRetriever:
    """Retrieves documents using keyword search, vector search, and RRF fusion."""

    def __init__(self, openai_client=None, embedding_client=None):
        self.openai_client = openai_client
        self.embedding_client = embedding_client

    async def get_embedding(self, text: str) -> list[float]:
        try:
            return await self._embed(text)
        except EmbeddingUnavailableError as e:
            logger.error(f"Embedding error: {e}")
            return []

    async def _embed(self, text: str) -> list[float]:
        """embedding 调用（失败抛 EmbeddingUnavailableError）。

        issue #292：向量层故障此前被 get_embedding 静默吞成 []，向量臂无声关闭，
        最终与「知识库确实没有内容」同形。_embed 让向量臂能区分
        「按设计未配置向量臂」（返回 []）与「服务故障」（抛异常），后者由
        retrieve 以结构化降级信号上抛。get_embedding 保持原吞异常语义不变
        （问答链、语义缓存等调用方依赖该契约）。
        """
        if not self.embedding_client:
            return []
        try:
            resp = await self.embedding_client.embeddings.create(
                input=text.replace("\n", " "),
                model=settings.EMBEDDING_MODEL,
            )
            return resp.data[0].embedding
        except Exception as e:
            raise EmbeddingUnavailableError(f"embedding service call failed: {e}") from e

    # ---- Keyword search ----

    async def _keyword_hits(self, query: str, filters: list[dict], candidate_size: int) -> list[dict]:
        enable_qr = bool(getattr(settings, "RAG_ENABLE_QUERY_REWRITE", True))
        rewrite_queries = rewrite_query_candidates(query)
        base_count = len(rewrite_queries)  # 本地改写站产出条数（含原查询），用于折算存活新增
        if enable_qr:
            # issue #259：字段已补进 Settings（此前 getattr 兜底因字段缺失而 .env 无效）
            timeout_sec = settings.RAG_QUERY_REWRITE_TIMEOUT_SECONDS
            try:
                llm_rewrites = await asyncio.wait_for(
                    rewrite_query_llm(query, self.openai_client, settings.DEEPSEEK_MODEL),
                    timeout=max(0.2, timeout_sec),
                )
                for item in llm_rewrites:
                    cleaned = (item or "").strip()
                    if cleaned and cleaned not in rewrite_queries:
                        rewrite_queries.append(cleaned)
            except Exception as e:
                logger.warning(f"LLM query rewrite timeout/fail: {e}")

        rewrite_limit = max(1, int(getattr(settings, "RAG_QUERY_REWRITE_COUNT", 4) or 4))
        rewrite_queries = rewrite_queries[:rewrite_limit]

        # 指标补埋（issue #242 第 10 条；过计修复 issue #250 第 1 条）：
        # LLM 改写变体按**最终采用**的一份计数 —— 计数移到截断之后，只记截断后
        # 仍存活、且由 LLM 追加的变体数。此前在截断前计 `added`，被
        # `rewrite_limit` 截掉、从未执行的变体也入了账（单次最多过计 2 条），
        # 与本地站（query_processor 截断后计数）口径不自洽。
        surviving_llm_added = max(0, len(rewrite_queries) - base_count)
        if surviving_llm_added:
            RAG_QUERY_REWRITE_TOTAL.inc(surviving_llm_added)

        intent = QueryIntentClassifier.classify(query)
        boost_fields = QueryIntentClassifier.get_boost_fields(intent)
        logger.info(f"Query intent: {intent}, fields: {boost_fields}")

        async def _es_search(q_text: str):
            # Build query - try exact match first, fall back to wildcard for substrings
            wildcard_terms = [f"*{t}*" for t in q_text.split() if len(t) < 20]
            should_clauses = [
                {"multi_match": {
                    "query": q_text, "fields": boost_fields,
                    "type": "best_fields", "fuzziness": "AUTO",
                    "minimum_should_match": "30%",
                }},
            ]
            if wildcard_terms:
                should_clauses.append({
                    "query_string": {
                        "query": " AND ".join(wildcard_terms),
                        "fields": ["content^2", "filename^1.2"],
                        "analyze_wildcard": True,
                        "default_operator": "AND",
                    }
                })
            es_query = {
                "size": candidate_size,
                "query": {
                    "bool": {
                        "must": [{"bool": {"should": should_clauses, "minimum_should_match": 1}}],
                        "filter": filters,
                    }
                },
                "highlight": {"fields": {"content": {"fragment_size": 120, "number_of_fragments": 2}}},
                "_source": ["content", "filename", "organization_id", "document_id", "embedding", "upload_time"],
            }
            res = await ElasticsearchTools.search_documents(es_query)
            return res.get("hits", {}).get("hits", [])

        tasks = [_es_search(rq) for rq in rewrite_queries]
        results = await asyncio.gather(*tasks)

        merged: dict[str, dict] = {}
        for res in results:
            for hit in res:
                doc_id = str(hit.get("_id") or "")
                if not doc_id:
                    continue
                score = float(hit.get("_score") or 0)
                prev = merged.get(doc_id)
                if prev is None:
                    m = dict(hit)
                    m["_score"] = score
                    m["_rewrite_hits"] = 1
                    merged[doc_id] = m
                else:
                    prev["_rewrite_hits"] = int(prev.get("_rewrite_hits", 1)) + 1
                    prev["_score"] = max(float(prev.get("_score", 0)), score)
                    if score > float(prev.get("_score", 0)):
                        prev["_source"] = hit.get("_source", {})
                        prev["highlight"] = hit.get("highlight", {})

        return sorted(merged.values(), key=lambda x: x.get("_score", 0), reverse=True)[:candidate_size]

    # ---- Vector search ----

    async def _vector_hits(
        self, query: str, filters: list[dict], candidate_size: int, enable_hyde: bool = True
    ) -> tuple[list[dict], list[float], str | None]:
        """向量臂检索。返回 (hits, query_vector, degraded_reason)。

        degraded_reason 非 None 表示向量臂因服务故障未能执行（issue #292）——
        上层据此区分「确实没有向量命中」与「根本没查成」，不得静默关臂。
        """
        intent = QueryIntentClassifier.classify(query)
        try:
            query_vector = await self._embed(query)
        except EmbeddingUnavailableError as e:
            # 修复（#292）：向量臂因 embedding 服务故障关闭时如实标记降级，
            # 不再与「没有命中」同形（此前是 #2 假无结果的静默放大器）。
            logger.error(f"Vector retrieval arm unavailable, query embedding failed: {e}")
            return [], [], f"vector_arm_unavailable: {e}"

        embedding_to_search = query_vector

        if enable_hyde and query_vector:
            # HyDE with timeout — if LLM is slow, skip gracefully
            hyde_doc = ""
            try:
                hyde_doc = await asyncio.wait_for(
                    generate_hyde_doc(query, intent, self.openai_client, settings.DEEPSEEK_MODEL),
                    timeout=1.5,
                )
            except TimeoutError:
                logger.warning("HyDE generation timed out, skipping")
            except Exception as e:
                logger.warning(f"HyDE generation failed: {e}")

            if hyde_doc:
                hyde_vector = await self.get_embedding(hyde_doc)
                if hyde_vector:
                    weight = getattr(settings, "RAG_HYDE_WEIGHT", 0.3)
                    embedding_to_search = [
                        v1 * (1 - weight) + v2 * weight
                        for v1, v2 in zip(query_vector, hyde_vector, strict=False)
                    ]
                    logger.info(f"HyDE fusion with weight={weight}")

        if not embedding_to_search:
            return [], query_vector, None

        es_query = {
            "size": candidate_size,
            "min_score": 1.15,
            "query": {
                "script_score": {
                    "query": {"bool": {"filter": [*filters, {"exists": {"field": "embedding"}}]}},
                    "script": {
                        "source": "cosineSimilarity(params.query_vector, 'embedding') + 1.0",
                        "params": {"query_vector": embedding_to_search},
                    },
                }
            },
            "_source": ["content", "filename", "organization_id", "document_id", "embedding", "upload_time"],
        }
        res = await ElasticsearchTools.search_documents(es_query)
        return res.get("hits", {}).get("hits", []), query_vector, None

    # ---- RRF Fusion ----

    @staticmethod
    def _rrf_fuse(keyword_hits: list[dict], vector_hits: list[dict]) -> list[dict]:
        k = 60.0
        fused: dict[str, dict] = {}

        for rank, hit in enumerate(keyword_hits, 1):
            doc_id = hit.get("_id")
            if not doc_id:
                continue
            row = fused.setdefault(doc_id, {
                "_id": doc_id, "_source": hit.get("_source", {}), "_score": 0.0,
                "keyword_rank": None, "vector_rank": None,
                "keyword_score": 0.0, "vector_score": 0.0,
                "rewrite_hits": 0, "has_keyword": False, "has_vector": False,
            })
            row["_score"] += 1.0 / (k + rank)
            row["keyword_rank"] = rank
            row["keyword_score"] = float(hit.get("_score", 0))
            row["has_keyword"] = True
            row["rewrite_hits"] = max(int(row.get("rewrite_hits", 0)), int(hit.get("_rewrite_hits", 1)))

        for rank, hit in enumerate(vector_hits, 1):
            doc_id = hit.get("_id")
            if not doc_id:
                continue
            row = fused.setdefault(doc_id, {
                "_id": doc_id, "_source": hit.get("_source", {}), "_score": 0.0,
                "keyword_rank": None, "vector_rank": None,
                "keyword_score": 0.0, "vector_score": 0.0,
                "rewrite_hits": 0, "has_keyword": False, "has_vector": False,
            })
            row["_score"] += 1.0 / (k + rank)
            row["vector_rank"] = rank
            row["vector_score"] = float(hit.get("_score", 0))
            row["has_vector"] = True

        return sorted(fused.values(), key=lambda x: x.get("_score", 0), reverse=True)

    # ---- Post-processing ----

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b, strict=False))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)

    @staticmethod
    def _mmr_select(candidates: list[dict], query_vector: list[float], top_k: int, lambda_mult: float) -> list[dict]:
        if not candidates:
            return []
        selected, remaining = [], candidates[:]
        while remaining and len(selected) < top_k:
            best_idx, best_score = 0, -1e9
            for i, cand in enumerate(remaining):
                rel = HybridRetriever._cosine_similarity(query_vector, cand.get("_embedding", []))
                if not selected:
                    mmr = rel
                else:
                    max_sim = max(HybridRetriever._cosine_similarity(cand.get("_embedding", []), s.get("_embedding", [])) for s in selected)
                    mmr = lambda_mult * rel - (1 - lambda_mult) * max_sim
                if mmr > best_score:
                    best_score = mmr
                    best_idx = i
            chosen = remaining.pop(best_idx)
            chosen["_mmr_score"] = best_score
            selected.append(chosen)
        return selected

    @staticmethod
    def _build_snippet(content: str, query: str, max_len: int = 260) -> str:
        if not content:
            return ""
        text = content.replace("\n", " ").strip()
        if len(text) <= max_len:
            return text
        idx = text.lower().find(query.strip().lower()) if query else -1
        if idx < 0:
            return text[:max_len]
        start = max(0, idx - max_len // 3)
        return text[start: min(len(text), start + max_len)]

    @staticmethod
    def _compute_overlap(text: str, terms: list[str]) -> float:
        if not terms:
            return 0.0
        lower = text.lower()
        hits = sum(1 for t in terms if t in lower)
        base = hits / len(terms)
        if hits == 0:
            return 0.0
        if hits == 1:
            base *= 0.5
        elif hits == 2:
            base *= 0.7
        else:
            base = min(1.0, base * 1.15)
        return base

    @staticmethod
    def _parse_timestamp(val) -> float | None:
        if isinstance(val, (int, float)):
            return float(val) if val > 0 else None
        if isinstance(val, str):
            try:
                return datetime.fromisoformat(val.replace("Z", "+00:00")).timestamp()
            except Exception:
                return None
        return None

    def _post_process(self, query: str, fused_hits: list[dict], top_k: int) -> list[dict]:
        """Normalize, deduplicate, and filter retrieval results.

        #208 Phase 2 决策 a（doc 帽保守修法）：帽保持 2/文档，但计数移到质量过滤
        之后——被任一质量闸门（含 0.15/0.10/0.07 终滤档）剔除的块不占帽位、
        也不占 top_k 结果预算（此前计数在质量过滤前，被剔的块也占槽）。
        """

        documents = []
        seen_docs: dict[str, int] = {}
        query_terms = extract_query_terms(query)
        raw_scores = [float(h.get("_score", 0)) for h in fused_hits]
        score_min, score_max = (min(raw_scores), max(raw_scores)) if raw_scores else (0, 0)
        now_ts = time.time()

        freshness_values = []
        for hit in fused_hits:
            ts = self._parse_timestamp(hit.get("_source", {}).get("upload_time"))
            if ts and ts <= now_ts:
                freshness_values.append(ts)
        fresh_min = min(freshness_values) if freshness_values else None
        fresh_max = max(freshness_values) if freshness_values else None

        for hit in fused_hits:
            source = hit.get("_source", {})
            text = (source.get("content") or "").strip()
            if not text:
                continue

            snippet = self._build_snippet(text, query)
            overlap = self._compute_overlap(f"{source.get('filename', '')} {text[:1600]}", query_terms)
            has_kw = bool(hit.get("has_keyword")) or hit.get("keyword_rank") is not None
            has_vec = bool(hit.get("has_vector")) or hit.get("vector_rank") is not None
            rewrite_hits = max(1, int(hit.get("rewrite_hits", 1)))
            kw_match = sum(1 for t in query_terms if t in (text[:1600]).lower())

            # Filter irrelevant results - relaxed when no vector search is available
            if not has_kw and not has_vec and overlap < 0.005:
                continue
            if not has_kw and has_vec and overlap < 0.1 and kw_match < 1:
                continue
            if has_kw and overlap < 0.02:
                continue

            score_range = max(1e-9, score_max - score_min)
            score_pct = (float(hit.get("_score", 0)) - score_min) / score_range if score_range > 1e-9 else 0
            if score_pct < 0.3 and overlap < 0.05:
                continue

            # 终滤质量档（#208 Phase 2 决策 a）：判定式与原「Final quality check」一致，
            # 仅前移为「先过滤、后计数」——被本档剔除的块同样不占 doc 帽槽位。
            should_keep = (
                overlap >= 0.15
                or (has_kw and overlap >= 0.10)
                or (has_kw and has_vec and overlap >= 0.07)
            )
            if not should_keep:
                continue

            # doc 帽 2/文档：只对通过质量过滤的块计数（被剔的块不占槽）。
            doc_id = str(source.get("document_id") or "")
            if doc_id:
                seen_docs[doc_id] = seen_docs.get(doc_id, 0) + 1
                if seen_docs[doc_id] > 2:
                    continue

            # Normalize score
            fused_score = float(hit.get("_score", 0))
            if score_max - score_min > 1e-9:
                normalized = max(0, min(1, 0.1 + 0.9 * (fused_score - score_min) / (score_max - score_min)))
            else:
                normalized = 0.6 if fused_score > 0 else 0

            fresh_factor = 1.0
            if fresh_min and fresh_max and fresh_max - fresh_min > 1e-6:
                ts = self._parse_timestamp(source.get("upload_time"))
                if ts and fresh_min <= ts <= fresh_max:
                    fresh_factor = 0.75 + 0.25 * ((ts - fresh_min) / (fresh_max - fresh_min))

            modality = 1.1 if (has_kw and has_vec) else (1.03 if (has_kw or has_vec) else 1.0)
            overlap_factor = 0.9 + 0.2 * min(1.0, overlap)
            final_score = min(1.0, normalized * fresh_factor * modality * overlap_factor)

            # 修复（R1/R2）：保留稳定 chunk id（优先 ES _id，缺失时用文档 id + 文本哈希合成），
            # 供子查询合并去重与调试载荷使用；同时以 content 键携带正文，
            # 保证重排序器（及下游 workflow 检索节点）拿到非空文档文本。
            chunk_id = str(hit.get("_id") or "")
            if not chunk_id:
                # 仅用于去重的稳定标识，不是密码学用途 —— 显式标注以免被静态扫描
                # 当作弱哈希（与本仓库其它 hashlib 调用一致）。
                text_hash = hashlib.sha1(
                    text.encode("utf-8"), usedforsecurity=False
                ).hexdigest()[:16]
                chunk_id = f"{doc_id or 'unknown'}:{text_hash}"

            documents.append({
                "text": text, "content": text, "filename": source.get("filename", "未知"),
                "chunk_id": chunk_id,
                "score": round(final_score, 4), "raw_score": fused_score,
                "snippet": snippet, "document_id": source.get("document_id"),
                "mmr_score": float(hit.get("_mmr_score", 0)),
                "overlap_score": overlap, "has_keyword": has_kw, "has_vector": has_vec,
                "rewrite_hits": rewrite_hits, "fresh_factor": round(fresh_factor, 2),
            })
            if len(documents) >= top_k:
                break

        return documents

    # ---- Fallback ----

    async def _fallback(self, query: str, filters: list[dict], top_k: int) -> tuple[list[dict], str | None]:
        """兜底检索。返回 (hits, degraded_reason)。

        兜底臂自身失败同样是「没查成」而不是「没有命中」（issue #292）：
        此前 except 吞掉异常返回 []，与正常的空结果在调用方面前同形。
        """
        try:
            broad = {
                "size": top_k * 2, "min_score": 0.5,
                "query": {"bool": {"must": [{"match": {"content": {"query": query, "operator": "or", "minimum_should_match": "30%"}}}], "filter": filters}},
                "_source": ["content", "filename", "organization_id", "document_id", "embedding", "upload_time"],
            }
            res = await ElasticsearchTools.search_documents(broad)
            hits = res.get("hits", {}).get("hits", [])
            if hits:
                return hits, None

            try:
                qv = await self._embed(query)
            except EmbeddingUnavailableError as e:
                return [], f"fallback_vector_unavailable: {e}"
            if not qv:
                return [], None
            vec_q = {
                "size": top_k, "min_score": 1.15,
                "query": {"script_score": {"query": {"bool": {"filter": filters}}, "script": {"source": "cosineSimilarity(params.query_vector, 'embedding') + 1.0", "params": {"query_vector": qv}}}},
                "_source": ["content", "filename", "organization_id", "document_id", "embedding", "upload_time"],
            }
            res = await ElasticsearchTools.search_documents(vec_q)
            return res.get("hits", {}).get("hits", []), None
        except Exception as e:
            logger.error(f"Fallback retrieval failed: {e}")
            return [], f"fallback_failed: {e}"

    # ---- Public API ----

    async def retrieve(
        self,
        query: str,
        organization_id: int,
        top_k: int = 5,
        document_ids: list[str] | None = None,
        debug: bool = False,
    ) -> tuple[list[dict], list[float], dict | None, list[str]]:
        """Execute adaptive retrieval and return (results, query_vector, debug_info, degraded).

        degraded：臂级降级原因列表（空 = 全部预期检索臂都正常执行）。issue #292：
        向量臂 / 兜底臂因服务故障未能执行时不得与「确实没有命中」同形；
        keyword 臂失败与兜底臂异常会直接上抛（由 pipeline 归类为故障态）。
        策略为 keyword_only 时无向量臂是**按设计**行为，不产生降级信号。

        Strategy is selected automatically based on query complexity:
        - keyword_only: fast keyword search, no embedding calls
        - hybrid: keyword + vector (default)
        - hybrid_hyde: keyword + vector + HyDE + multi-rewrite (full pipeline)

        When debug=True, returns intermediate retrieval results for visualization.
        """
        degraded: list[str] = []
        org_filter = {"term": {"organization_id": str(organization_id)}}
        filters = [org_filter]
        if document_ids:
            doc_ids = [str(d) for d in document_ids if d]
            if doc_ids:
                filters.append({"terms": {"document_id": doc_ids}})

        candidate_size = max(top_k * max(1, settings.RAG_MMR_CANDIDATE_MULTIPLIER), 12)

        # Adaptive strategy selection
        strategy = QueryComplexityClassifier.get_strategy(query)
        RAG_ADAPTIVE_STRATEGY.labels(strategy=strategy).inc()
        logger.info(f"Adaptive RAG: query='{query[:50]}...' → strategy={strategy}")

        debug_info = {"strategy": strategy, "query": query} if debug else None

        if strategy == "keyword_only":
            kw_hits = await self._keyword_hits(query, filters, candidate_size)
            if debug and debug_info is not None:
                debug_info["keyword_top"] = self._debug_format_hits(kw_hits[:10])
                debug_info["vector_top"] = []
                debug_info["rrf_result"] = debug_info["keyword_top"][:top_k]
                debug_info["rerank_result"] = []
            documents = self._post_process(query, kw_hits, top_k)
            return documents, [], debug_info, degraded

        # Hybrid paths: keyword + vector (parallel)
        if strategy == "hybrid_hyde":
            kw_hits, (vec_hits, query_vector, vec_degraded) = await asyncio.gather(
                self._keyword_hits(query, filters, candidate_size),
                self._vector_hits(query, filters, candidate_size),
            )
        else:
            kw_hits, (vec_hits, query_vector, vec_degraded) = await asyncio.gather(
                self._keyword_hits(query, filters, candidate_size),
                self._vector_hits(query, filters, candidate_size, enable_hyde=False),
            )
        if vec_degraded:
            degraded.append(vec_degraded)

        if debug and debug_info is not None:
            debug_info["keyword_top"] = self._debug_format_hits(kw_hits[:10])
            debug_info["vector_top"] = self._debug_format_hits(vec_hits[:10])

        fused = self._rrf_fuse(kw_hits, vec_hits)

        if debug and debug_info is not None:
            debug_info["rrf_result"] = self._debug_format_hits(fused[:15])

        if not fused:
            logger.warning("RRF fusion empty, trying fallback")
            fallback, fb_degraded = await self._fallback(query, filters, top_k)
            if fb_degraded:
                degraded.append(fb_degraded)
            results = self._post_process(query, fallback, top_k) if fallback else []
            if debug and debug_info is not None:
                debug_info["note"] = "RRF fusion empty, used fallback"
            return results, query_vector, debug_info, degraded

        # MMR
        if settings.RAG_ENABLE_MMR and query_vector:
            mmr_cands = [
                {**h, "_embedding": h.get("_source", {}).get("embedding", [])}
                for h in fused[:candidate_size] if h.get("_source", {}).get("embedding")
            ]
            if mmr_cands:
                fused = self._mmr_select(mmr_cands, query_vector, top_k, max(0, min(1, settings.RAG_MMR_LAMBDA)))

        return self._post_process(query, fused, top_k), query_vector, debug_info, degraded

    @staticmethod
    def _debug_format_hits(hits: list[dict]) -> list[dict]:
        """Format raw ES hits for debug display (compact, readable)."""
        formatted = []
        for h in hits[:15]:
            src = h.get("_source", {}) or {}
            formatted.append({
                "id": h.get("_id", ""),
                "filename": src.get("filename", "未知"),
                "score": round(float(h.get("_score", 0)), 4),
                "keyword_rank": h.get("keyword_rank"),
                "vector_rank": h.get("vector_rank"),
                "keyword_score": round(float(h.get("keyword_score", 0)), 4) if h.get("keyword_score") else None,
                "vector_score": round(float(h.get("vector_score", 0)), 4) if h.get("vector_score") else None,
                "rewrite_hits": h.get("_rewrite_hits", 1),
                "has_keyword": h.get("has_keyword", False),
                "has_vector": h.get("has_vector", False),
                "snippet": (src.get("content", "") or "")[:200],
            })
        return formatted
