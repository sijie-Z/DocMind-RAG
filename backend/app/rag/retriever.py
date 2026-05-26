"""Hybrid retriever — keyword + vector search with RRF fusion."""
import asyncio
import logging
import math
import time
from datetime import datetime

from app.core.config import settings
from app.core.elasticsearch import ElasticsearchTools
from app.core.prometheus import RAG_ADAPTIVE_STRATEGY
from app.rag.query_processor import (
    QueryComplexityClassifier,
    QueryIntentClassifier,
    extract_query_terms,
    generate_hyde_doc,
    generate_multi_hyde_docs,
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
        if not self.embedding_client:
            return []
        try:
            resp = await self.embedding_client.embeddings.create(
                input=text.replace("\n", " "),
                model=settings.EMBEDDING_MODEL,
            )
            return resp.data[0].embedding
        except Exception as e:
            logger.error(f"Embedding error: {e}")
            return []

    # ---- Keyword search ----

    async def _keyword_hits(self, query: str, filters: list[dict], candidate_size: int) -> list[dict]:
        enable_qr = bool(getattr(settings, "RAG_ENABLE_QUERY_REWRITE", True))
        rewrite_queries = rewrite_query_candidates(query)
        if enable_qr:
            timeout_sec = float(getattr(settings, "RAG_QUERY_REWRITE_TIMEOUT_SECONDS", 1.2) or 1.2)
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

        intent = QueryIntentClassifier.classify(query)
        boost_fields = QueryIntentClassifier.get_boost_fields(intent)
        logger.info(f"Query intent: {intent}, fields: {boost_fields}")

        async def _es_search(q_text: str):
            es_query = {
                "size": candidate_size,
                "min_score": 0.5,
                "query": {
                    "bool": {
                        "must": [{"multi_match": {
                            "query": q_text, "fields": boost_fields,
                            "type": "best_fields", "fuzziness": "AUTO",
                            "minimum_should_match": "30%",
                        }}],
                        "filter": filters,
                    }
                },
                "highlight": {"fields": {"chunk_text": {"fragment_size": 120, "number_of_fragments": 2}}},
                "_source": ["chunk_text", "filename", "organization_id", "document_id", "embedding", "upload_time", "section_title"],
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
    ) -> tuple[list[dict], list[float]]:
        intent = QueryIntentClassifier.classify(query)
        query_vector = await self.get_embedding(query)

        embedding_to_search = query_vector

        if enable_hyde and query_vector:
            hyde_task = generate_hyde_doc(query, intent, self.openai_client, settings.DEEPSEEK_MODEL)
            multi_hyde_task = generate_multi_hyde_docs(query, intent, self.openai_client, settings.DEEPSEEK_MODEL, 2)
            hyde_doc, multi_hyde_docs = await asyncio.gather(hyde_task, multi_hyde_task)
            all_hyde = [hyde_doc] + multi_hyde_docs if hyde_doc else multi_hyde_docs

            if all_hyde:
                hyde_vectors = await asyncio.gather(*[self.get_embedding(d) for d in all_hyde if d])
                if hyde_vectors:
                    avg_hyde = [sum(v[i] for v in hyde_vectors) / len(hyde_vectors) for i in range(len(query_vector))]
                    embedding_to_search = [v1 * 0.6 + v2 * 0.4 for v1, v2 in zip(query_vector, avg_hyde, strict=False)]
                    logger.info(f"Multi-HyDE fusion: {len(hyde_vectors)} docs, 60/40 weight")

        if not embedding_to_search:
            return [], query_vector

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
            "_source": ["chunk_text", "filename", "organization_id", "document_id", "embedding", "upload_time", "section_title"],
        }
        res = await ElasticsearchTools.search_documents(es_query)
        return res.get("hits", {}).get("hits", []), query_vector

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
        """Normalize, deduplicate, and filter retrieval results."""

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
            text = (source.get("chunk_text") or source.get("content", "") or "").strip()
            if not text:
                continue

            doc_id = str(source.get("document_id") or "")
            seen_docs[doc_id] = seen_docs.get(doc_id, 0) + 1
            if doc_id and seen_docs[doc_id] > 2:
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

            documents.append({
                "text": text, "filename": source.get("filename", "未知"),
                "score": round(final_score, 4), "raw_score": fused_score,
                "snippet": snippet, "document_id": source.get("document_id"),
                "mmr_score": float(hit.get("_mmr_score", 0)),
                "overlap_score": overlap, "has_keyword": has_kw, "has_vector": has_vec,
                "rewrite_hits": rewrite_hits, "fresh_factor": round(fresh_factor, 2),
            })
            if len(documents) >= top_k:
                break

        # Final quality check
        filtered = []
        for doc in documents:
            ov = doc.get("overlap_score", 0)
            should_keep = (
                ov >= 0.15
                or (doc.get("has_keyword") and ov >= 0.10)
                or (doc.get("has_keyword") and doc.get("has_vector") and ov >= 0.07)
            )
            if should_keep:
                filtered.append(doc)

        return filtered

    # ---- Fallback ----

    async def _fallback(self, query: str, filters: list[dict], top_k: int) -> list[dict]:
        try:
            broad = {
                "size": top_k * 2, "min_score": 0.5,
                "query": {"bool": {"must": [{"match": {"chunk_text": {"query": query, "operator": "or", "minimum_should_match": "30%"}}}], "filter": filters}},
                "_source": ["chunk_text", "filename", "organization_id", "document_id", "embedding", "upload_time", "section_title"],
            }
            res = await ElasticsearchTools.search_documents(broad)
            hits = res.get("hits", {}).get("hits", [])
            if hits:
                return hits

            qv = await self.get_embedding(query)
            if not qv:
                return []
            vec_q = {
                "size": top_k, "min_score": 1.15,
                "query": {"script_score": {"query": {"bool": {"filter": filters}}, "script": {"source": "cosineSimilarity(params.query_vector, 'embedding') + 1.0", "params": {"query_vector": qv}}}},
                "_source": ["chunk_text", "filename", "organization_id", "document_id", "embedding", "upload_time", "section_title"],
            }
            res = await ElasticsearchTools.search_documents(vec_q)
            return res.get("hits", {}).get("hits", [])
        except Exception as e:
            logger.error(f"Fallback retrieval failed: {e}")
            return []

    # ---- Public API ----

    async def retrieve(
        self,
        query: str,
        organization_id: int,
        top_k: int = 5,
        document_ids: list[str] | None = None,
    ) -> tuple[list[dict], list[float]]:
        """Execute adaptive retrieval and return (results, query_vector).

        Strategy is selected automatically based on query complexity:
        - keyword_only: fast keyword search, no embedding calls
        - hybrid: keyword + vector (default)
        - hybrid_hyde: keyword + vector + HyDE + multi-rewrite (full pipeline)
        """
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

        if strategy == "keyword_only":
            # Fast path: keyword search only, skip embedding
            kw_hits = await self._keyword_hits(query, filters, candidate_size)
            documents = self._post_process(query, kw_hits, top_k)
            return documents, []

        # Hybrid paths: keyword + vector (parallel)
        if strategy == "hybrid_hyde":
            # Full pipeline: enable HyDE and multi-rewrite
            kw_hits, (vec_hits, query_vector) = await asyncio.gather(
                self._keyword_hits(query, filters, candidate_size),
                self._vector_hits(query, filters, candidate_size),
            )
        else:
            # Standard hybrid: keyword + vector without HyDE
            kw_hits, (vec_hits, query_vector) = await asyncio.gather(
                self._keyword_hits(query, filters, candidate_size),
                self._vector_hits(query, filters, candidate_size, enable_hyde=False),
            )

        fused = self._rrf_fuse(kw_hits, vec_hits)
        if not fused:
            logger.warning("RRF fusion empty, trying fallback")
            fallback = await self._fallback(query, filters, top_k)
            return (self._post_process(query, fallback, top_k) if fallback else []), query_vector

        # MMR
        if settings.RAG_ENABLE_MMR and query_vector:
            mmr_cands = [
                {**h, "_embedding": h.get("_source", {}).get("embedding", [])}
                for h in fused[:candidate_size] if h.get("_source", {}).get("embedding")
            ]
            if mmr_cands:
                fused = self._mmr_select(mmr_cands, query_vector, top_k, max(0, min(1, settings.RAG_MMR_LAMBDA)))

        return self._post_process(query, fused, top_k), query_vector
