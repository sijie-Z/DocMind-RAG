# -*- coding: utf-8 -*-
import hashlib
import logging
import math
import re
import time
import asyncio
import json
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple, cast, Literal
from app.core.elasticsearch import ElasticsearchTools
from app.core.config import settings
from app.services.masking_service import masking_service
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam

logger = logging.getLogger(__name__)

ENABLE_GRAPH_RAG = getattr(settings, 'ENABLE_GRAPH_RAG', True)


QueryIntent = Literal["factual", "procedural", "list", "definition", "comparison", "causal", "summary", "other"]


class QueryIntentClassifier:
    """
    查询意图分类器 - 识别用户查询类型以优化检索策略
    支持的意图类型：
    - factual: 事实性问题（谁、什么、何时、何地）
    - procedural: 流程/步骤问题（如何、怎么做、步骤）
    - list: 列表请求（有哪些、列出）
    - definition: 定义问题（什么是、定义）
    - comparison: 比较问题（区别、比较）
    - causal: 原因结果问题（为什么、如何导致）
    - summary: 摘要总结问题（总结、概括）
    - other: 其他
    """

    INTENT_PATTERNS: Dict[QueryIntent, List[str]] = {
        "factual": ["是谁", "是什么", "在哪", "什么时候", "第几", "多少时间", "长度", "面积", "人口"],
        "procedural": ["如何", "怎么", "怎样", "步骤", "流程", "操作", "方法", "过程", "操作步骤"],
        "list": ["有哪些", "有什么", "列出", "哪些", "名单", "列表", "罗列"],
        "definition": ["什么是", "定义", "含义", "意思", "概念", "解释"],
        "comparison": ["区别", "不同", "比较", "差异", "对比", "哪一个好", "优缺点"],
        "causal": ["为什么", "原因", "结果", "导致", "因此", "所以", "由于"],
        "summary": ["总结", "概括", "摘要", "概述", "要点", "核心内容"],
    }

    @classmethod
    def classify(cls, query: str) -> QueryIntent:
        query_lower = query.lower().strip()
        scores: Dict[QueryIntent, float] = {intent: 0.0 for intent in cls.INTENT_PATTERNS}

        for intent, patterns in cls.INTENT_PATTERNS.items():
            for pattern in patterns:
                if pattern in query_lower:
                    scores[intent] += 1.0

        if max(scores.values()) == 0:
            return "other"

        return max(scores, key=scores.get)

    @classmethod
    def get_intent_boost_fields(cls, intent: QueryIntent) -> List[str]:
        field_boosts = {
            "factual": ["content^2", "title^1.5"],
            "procedural": ["content^2", "section_title^1.8"],
            "list": ["content^1.5", "list_content^2"],
            "definition": ["content^2", "title^1.5", "definition^2.5"],
            "comparison": ["content^2", "comparison^2", "pros_cons^2"],
            "causal": ["content^2", "reason^2", "result^2"],
            "summary": ["content^1.5", "summary^2", "conclusion^2"],
            "other": ["content^2", "filename^1.2"],
        }
        return field_boosts.get(intent, ["content^2", "filename^1.2"])


class ContextCompressor:
    """
    上下文压缩器 - 对检索到的长文本进行压缩
    保留与查询最相关的部分，去除冗余信息
    """

    @staticmethod
    def compress(text: str, query: str, max_chars: int = 800) -> str:
        if not text or len(text) <= max_chars:
            return text

        query_terms = ContextCompressor._extract_query_terms(query)
        if not query_terms:
            return text[:max_chars]

        sentences = re.split(r'[。！？\n]', text)
        compressed_parts = []
        current_len = 0

        for sentence in sentences:
            sentence = sentence.strip()
            if not sentence:
                continue

            sentence_lower = sentence.lower()
            relevance = sum(1 for term in query_terms if term in sentence_lower)

            if relevance > 0 or current_len < max_chars * 0.3:
                if current_len + len(sentence) + 1 <= max_chars:
                    compressed_parts.append(sentence)
                    current_len += len(sentence) + 1
                elif relevance >= len(query_terms) * 0.5:
                    truncated = sentence[:max_chars - current_len - 3]
                    if truncated:
                        compressed_parts.append(truncated + "...")
                        break

        if not compressed_parts:
            return text[:max_chars]

        result = "。".join(compressed_parts)
        if len(result) > max_chars:
            result = result[:max_chars - 3] + "..."

        return result

    @staticmethod
    def _extract_query_terms(query: str) -> List[str]:
        raw_terms = re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9]{3,}", query.lower())
        stop_words = {"的", "了", "是", "在", "和", "与", "或", "等", "这", "那", "请", "帮我", "一下"}
        return [t for t in raw_terms if t not in stop_words and len(t) >= 2]

    @classmethod
    def compress_context_list(
        cls,
        contexts: List[Dict[str, Any]],
        query: str,
        max_context_chars: int = 2000
    ) -> List[Dict[str, Any]]:
        compressed = []
        total_chars = 0

        for ctx in contexts:
            text = ctx.get("text", "") or ctx.get("content", "")
            original_len = len(text)

            compressed_text = cls.compress(text, query, max_chars=int(max_context_chars / max(1, len(contexts))))

            ctx_copy = dict(ctx)
            ctx_copy["original_length"] = original_len
            ctx_copy["text"] = compressed_text
            ctx_copy["is_compressed"] = len(compressed_text) < original_len
            compressed.append(ctx_copy)
            total_chars += len(compressed_text)

            if total_chars > max_context_chars:
                break

        return compressed


class RagService:
    def __init__(self):
        self.openai_client = None
        self.embedding_client = None
        self.rerank_client = None
        self._retrieval_cache: Dict[str, Dict[str, Any]] = {}
        self._semantic_cache: Dict[str, Dict[str, Any]] = {}
        self._metrics: Dict[str, Any] = {
            "retrieval_total": 0,
            "retrieval_hit": 0,
            "grounded_total": 0,
            "grounded_hit": 0,
            "latency_sum_ms": 0.0,
            "latency_count": 0,
            "cache_hit": 0,
            "retry_total": 0,
            "semantic_cache_hit": 0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_embedding_tokens": 0,
            "llm_request_count": 0,
        }
        self._metric_events: Dict[str, List[Any]] = {
            "retrieval": [],
            "retrieval_hit": [],
            "grounded": [],
            "grounded_hit": [],
            "latency": [],
            "cache_hit": [],
            "retry": [],
            "input_tokens": [],
            "output_tokens": [],
        }

        if settings.ENABLE_LOCAL_LLM:
            logger.info(f"🚀 使用本地模型: {settings.LOCAL_LLM_MODEL} @ {settings.LOCAL_LLM_URL}")
            self.openai_client = AsyncOpenAI(
                api_key="ollama", # Ollama 不需要真实的 key
                base_url=settings.LOCAL_LLM_URL
            )
        elif settings.DEEPSEEK_API_KEY:
            base_url = settings.DEEPSEEK_API_URL.split("/chat/completions")[0]
            self.openai_client = AsyncOpenAI(api_key=settings.DEEPSEEK_API_KEY, base_url=base_url)

        embed_key = settings.EMBEDDING_API_KEY or settings.DEEPSEEK_API_KEY
        if embed_key:
            raw_embed_base = settings.EMBEDDING_API_URL or "https://api.openai.com/v1"
            embed_base = raw_embed_base.replace("/embeddings", "")
            self.embedding_client = AsyncOpenAI(api_key=embed_key, base_url=embed_base)

        # Rerank 初始化 (使用 ZhipuAI 专用 API 或 OpenAI 兼容 API)
        rerank_key = settings.RERANK_API_KEY or settings.RAG_RERANK_API_KEY or settings.DEEPSEEK_API_KEY
        rerank_url = settings.RERANK_API_URL or settings.RAG_RERANK_API_URL or settings.DEEPSEEK_API_URL
        if rerank_key and rerank_url:
            self.rerank_api_key = rerank_key
            # 这里的 base_url 在 _cross_encoder_rerank 中会根据模型名判断用途
            self.rerank_api_url = rerank_url.split("/chat/completions")[0]
            if not self.rerank_api_url.endswith("/v4"):
                 self.rerank_api_url = self.rerank_api_url.rstrip("/")
            
            # 同时也初始化一个 OpenAI 客户端作为备用（针对 LLM-based rerank）
            self.rerank_client = AsyncOpenAI(api_key=rerank_key, base_url=self.rerank_api_url)

    async def get_embedding(self, text: str) -> List[float]:
        if not self.embedding_client:
            logger.warning("Embedding client not configured, skip vector retrieval")
            return []
        try:
            resp = await self.embedding_client.embeddings.create(
                input=text.replace("\n", " "),
                model=settings.EMBEDDING_MODEL
            )
            return resp.data[0].embedding
        except Exception as e:
            logger.error(f"❌ Embedding Error: {e}")
            return []

    def _build_snippet(self, content: str, query: str, max_len: int = 260) -> str:
        if not content:
            return ""
        text = content.replace("\n", " ").strip()
        if len(text) <= max_len:
            return text

        q = query.strip().lower()
        idx = text.lower().find(q) if q else -1
        if idx < 0:
            return text[:max_len]

        start = max(0, idx - max_len // 3)
        end = min(len(text), start + max_len)
        return text[start:end]

    def _extract_query_terms(self, query: str) -> List[str]:
        q = (query or "").strip().lower()
        if not q:
            return []
        # Use bigram extraction for Chinese, word-level for English
        cjk_segments = re.findall(r"[一-鿿]+", q)
        raw_terms = []
        for seg in cjk_segments:
            for i in range(len(seg) - 1):
                gram = seg[i:i+2]
                if gram not in raw_terms:
                    raw_terms.append(gram)
        en_terms = re.findall(r"[a-z0-9]{2,}", q)
        for t in en_terms:
            if t not in raw_terms:
                raw_terms.append(t)
        stop_words = {
            "请", "帮我", "一下", "关于", "如何", "怎么", "什么", "是什么", "可以", "这个", "那个",
            "因为", "所以", "如果", "但是", "而且", "或者", "也是", "还是", "并且",
            "一个", "一些", "他们", "我们", "你们", "自己", "没有", "还有", "就是",
            "the", "and", "for", "with", "from", "that", "this", "is", "are", "was", "were",
            "be", "been", "being", "have", "has", "had", "do", "does", "did", "will", "would",
            "could", "should", "may", "might", "must", "shall", "can", "need", "dare", "ought",
            "used", "to", "of", "in", "on", "at", "by", "as", "into",
            "through", "during", "before", "after", "above", "below", "between", "under",
            "again", "further", "then", "once", "here", "there", "when", "where", "why", "how",
            "all", "each", "few", "more", "most", "other", "some", "such", "no", "nor", "not",
            "only", "own", "same", "so", "than", "too", "very", "just", "but", "if", "or",
            "work", "works", "working", "it", "its", "they", "them", "their",
        }
        terms: List[str] = []
        for term in raw_terms:
            t = term.strip()
            if not t or len(t) < 2 or t in stop_words:
                continue
            if t not in terms:
                terms.append(t)
        return terms[:12]

    def _compute_query_overlap(self, text: str, terms: List[str]) -> float:
        if not terms:
            return 0.0
        lower_text = (text or "").lower()
        hit_count = sum(1 for term in terms if term in lower_text)
        base_overlap = float(hit_count) / float(len(terms))
        if hit_count == 0:
            return 0.0
        elif hit_count == 1:
            base_overlap = base_overlap * 0.5
        elif hit_count == 2:
            base_overlap = base_overlap * 0.7
        elif hit_count >= 3:
            base_overlap = min(1.0, base_overlap * 1.15)
        return base_overlap

    def _compute_keyword_match_count(self, text: str, terms: List[str]) -> int:
        if not terms:
            return 0
        lower_text = (text or "").lower()
        return sum(1 for term in terms if term in lower_text)

    def _parse_upload_timestamp(self, upload_time: Any) -> Optional[float]:
        if isinstance(upload_time, (int, float)):
            ts = float(upload_time)
            return ts if ts > 0 else None
        if isinstance(upload_time, str):
            try:
                cleaned = upload_time.replace("Z", "+00:00")
                return datetime.fromisoformat(cleaned).timestamp()
            except Exception:
                return None
        return None

    def _optimize_long_query(self, query: str) -> str:
        """
        优化长查询，防止查询过长导致的检索质量问题。
        - 截断超长查询（超过200字符）
        - 提取核心关键词
        - 保留问句结构
        """
        if len(query) <= 200:
            return query

        logger.info(f"📝 检测到长查询，长度: {len(query)}，开始优化...")

        q = query.strip()

        question_markers = ["如何", "怎么", "什么", "为什么", "是不是", "能不能", "是不是", "有没有"]
        has_question_structure = any(q.startswith(marker) for marker in question_markers)

        core_terms = re.findall(r"[\u4e00-\u9fff]{2,}", q)

        important_markers = ["流程", "步骤", "规范", "要求", "政策", "制度", "标准", "方法", "原因", "结果", "定义", "概念"]
        prioritized_terms = []
        for term in core_terms:
            if any(marker in term for marker in important_markers):
                prioritized_terms.insert(0, term)
            else:
                prioritized_terms.append(term)

        optimized = " ".join(prioritized_terms[:15])

        if has_question_structure and optimized != q:
            optimized = q[:100] + " " + optimized[:100]

        if len(optimized) > 200:
            optimized = optimized[:197] + "..."

        logger.info(f"✅ 查询优化完成，长度: {len(optimized)}")
        return optimized

    def _rewrite_query_candidates(self, query: str) -> List[str]:
        """多查询扩展：在不依赖外部模型的前提下做轻量 query rewrite。"""
        candidates: List[str] = []
        q = (query or "").strip()
        if not q:
            return candidates

        optimized_query = self._optimize_long_query(q)

        # 原始 query
        candidates.append(optimized_query)

        # 去标点版本
        no_punct = re.sub(r"[，。！？；：、,.!?;:]", " ", optimized_query)
        no_punct = re.sub(r"\s+", " ", no_punct).strip()
        if no_punct and no_punct != optimized_query:
            candidates.append(no_punct)

        # 去停用词版本（非常轻量）
        stop_words = {"请", "帮我", "一下", "关于", "如何", "怎么", "是什么", "什么是", "一下子", "给我", "是否"}
        tokens = [t for t in re.split(r"\s+", no_punct or optimized_query) if t]
        filtered = " ".join([t for t in tokens if t not in stop_words])
        if filtered and filtered not in candidates:
            candidates.append(filtered)

        # 去重并限制数量
        dedup: List[str] = []
        for c in candidates:
            c = c.strip()
            if c and c not in dedup:
                dedup.append(c)
        rewrite_count = int(getattr(settings, "RAG_QUERY_REWRITE_COUNT", 4) or 4)
        return dedup[: max(1, rewrite_count)]

    def _rrf_fuse(self, keyword_hits: List[Dict[str, Any]], vector_hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Reciprocal Rank Fusion: 稳定融合关键词与向量召回。"""
        k = 60.0
        fused: Dict[str, Dict[str, Any]] = {}

        for rank, hit in enumerate(keyword_hits, start=1):
            doc_id = hit.get("_id")
            if not doc_id:
                continue
            source = hit.get("_source", {})
            row = fused.setdefault(doc_id, {
                "_id": doc_id,
                "_source": source,
                "_score": 0.0,
                "keyword_rank": None,
                "vector_rank": None,
                "keyword_score": 0.0,
                "vector_score": 0.0,
                "rewrite_hits": 0,
                "has_keyword": False,
                "has_vector": False,
            })
            row["_score"] += 1.0 / (k + rank)
            row["keyword_rank"] = rank
            row["keyword_score"] = float(hit.get("_score") or 0.0)
            row["has_keyword"] = True
            row["rewrite_hits"] = max(int(row.get("rewrite_hits") or 0), int(hit.get("_rewrite_hits") or 1))

        for rank, hit in enumerate(vector_hits, start=1):
            doc_id = hit.get("_id")
            if not doc_id:
                continue
            source = hit.get("_source", {})
            row = fused.setdefault(doc_id, {
                "_id": doc_id,
                "_source": source,
                "_score": 0.0,
                "keyword_rank": None,
                "vector_rank": None,
                "keyword_score": 0.0,
                "vector_score": 0.0,
                "rewrite_hits": 0,
                "has_keyword": False,
                "has_vector": False,
            })
            row["_score"] += 1.0 / (k + rank)
            row["vector_rank"] = rank
            row["vector_score"] = float(hit.get("_score") or 0.0)
            row["has_vector"] = True

        return sorted(fused.values(), key=lambda x: x.get("_score", 0.0), reverse=True)

    def _cosine_similarity(self, a: List[float], b: List[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        if na == 0.0 or nb == 0.0:
            return 0.0
        return dot / (na * nb)

    def _mmr_select(
        self,
        candidates: List[Dict[str, Any]],
        query_vector: List[float],
        top_k: int,
        lambda_mult: float,
    ) -> List[Dict[str, Any]]:
        """MMR 去冗余重排：在相关性与多样性之间折中。"""
        if not candidates:
            return []

        selected: List[Dict[str, Any]] = []
        remaining = candidates[:]

        while remaining and len(selected) < top_k:
            best_idx = 0
            best_score = -1e9

            for i, cand in enumerate(remaining):
                cand_vec = cand.get("_embedding") or []
                rel = self._cosine_similarity(query_vector, cand_vec)

                if not selected:
                    mmr = rel
                else:
                    max_sim_to_selected = max(
                        self._cosine_similarity(cand_vec, s.get("_embedding") or [])
                        for s in selected
                    )
                    mmr = lambda_mult * rel - (1.0 - lambda_mult) * max_sim_to_selected

                if mmr > best_score:
                    best_score = mmr
                    best_idx = i

            chosen = remaining.pop(best_idx)
            chosen["_mmr_score"] = best_score
            selected.append(chosen)

        return selected

    async def _cross_encoder_rerank(self, query: str, hits: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """使用 Reranker 模型或 LLM 作为 Cross-Encoder 近似重排器。"""
        if not hits:
            return hits

        if not settings.RAG_ENABLE_RERANKER:
            return hits

        top_n = max(1, int(settings.RAG_RERANK_TOP_N or 20))
        candidates = hits[:top_n]
        model_name = settings.RERANK_MODEL or settings.RAG_RERANK_MODEL or "rerank"

        try:
            # 1. 尝试使用智谱专用的 Rerank API (如果模型名是 'rerank')
            if model_name == "rerank" and hasattr(self, 'rerank_api_key'):
                import httpx
                async with httpx.AsyncClient() as client:
                    documents = [hit.get("_source", {}).get("content", "")[:1000] for hit in candidates]
                    url = f"{self.rerank_api_url.rstrip('/')}/rerank"
                    
                    response = await client.post(
                        url,
                        headers={"Authorization": f"Bearer {self.rerank_api_key}"},
                        json={
                            "model": "rerank",
                            "query": query,
                            "documents": documents,
                            "top_n": top_n
                        },
                        timeout=settings.RAG_RERANK_TIMEOUT_SECONDS
                    )
                    
                    if response.status_code == 200:
                        data = response.json()
                        # 智谱返回格式: {"results": [{"index": 0, "relevance_score": 0.9}, ...]}
                        results = data.get("results", [])
                        ordered = []
                        for res in results:
                            idx = res.get("index")
                            if 0 <= idx < len(candidates):
                                ordered.append(candidates[idx])
                        
                        # 追加未被选中的候选（如果有）
                        used_indices = {res.get("index") for res in results}
                        for i, h in enumerate(candidates):
                            if i not in used_indices:
                                ordered.append(h)
                        
                        return ordered + hits[top_n:]
                    else:
                        logger.warning(f"Zhipu Rerank API failed ({response.status_code}): {response.text}")

            # 2. 回退到通用的 LLM-based Rerank (针对兼容 OpenAI 接口的模型)
            if not self.rerank_client:
                return hits

            brief_docs = []
            for idx, hit in enumerate(candidates, start=1):
                src = hit.get("_source", {})
                text = (src.get("content") or "")[:500]
                fname = src.get("filename", "未知")
                brief_docs.append(f"[{idx}] 文件:{fname}\n内容:{text}")

            prompt = (
                "你是检索重排器。请根据用户问题判断候选片段相关性，"
                "只返回 JSON 数组，元素是候选编号，按相关性从高到低排序。\n\n"
                f"用户问题: {query}\n\n"
                "候选片段:\n" + "\n\n".join(brief_docs) + "\n\n"
                "输出示例: [3,1,2]"
            )

            rsp = await self.rerank_client.chat.completions.create(
                model=model_name,
                messages=[
                    {"role": "system", "content": "你是严格输出JSON数组的重排器。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0,
                timeout=settings.RAG_RERANK_TIMEOUT_SECONDS,
            )

            content = (rsp.choices[0].message.content or "").strip()
            nums = [int(x) for x in re.findall(r"\d+", content)]
            ordered = []
            used = set()
            for n in nums:
                if 1 <= n <= len(candidates) and n not in used:
                    ordered.append(candidates[n - 1])
                    used.add(n)

            for i, h in enumerate(candidates, start=1):
                if i not in used:
                    ordered.append(h)

            return ordered + hits[top_n:]

        except Exception as e:
            logger.warning(f"Reranker failed, fallback to original rank: {e}")
            return hits
    def _cache_key(self, query: str, organization_id: int, top_k: int) -> str:
        return f"org={organization_id}|topk={top_k}|q={query.strip().lower()}"

    def _cache_get(self, key: str) -> Optional[List[Dict[str, Any]]]:
        if not settings.RAG_ENABLE_CACHE:
            return None
        row = self._retrieval_cache.get(key)
        if not row:
            return None
        ttl = max(1, int(settings.RAG_CACHE_TTL_SECONDS or 120))
        if time.time() - row.get("ts", 0) > ttl:
            self._retrieval_cache.pop(key, None)
            return None
        return row.get("value")

    def _cache_set(self, key: str, value: List[Dict[str, Any]]) -> None:
        if not settings.RAG_ENABLE_CACHE:
            return
        max_size = max(100, int(settings.RAG_CACHE_MAX_SIZE or 1000))
        if len(self._retrieval_cache) >= max_size:
            oldest_key = min(self._retrieval_cache, key=lambda k: self._retrieval_cache[k].get("ts", 0))
            self._retrieval_cache.pop(oldest_key, None)
        self._retrieval_cache[key] = {"ts": time.time(), "value": value}

    def _semantic_cache_key(self, query_vector: List[float]) -> Optional[str]:
        if not getattr(settings, "RAG_ENABLE_SEMANTIC_CACHE", False):
            return None
        # Convert list of floats to a deterministic string or hash
        vec_str = ",".join([f"{v:.4f}" for v in query_vector])
        return f"semantic:{hashlib.md5(vec_str.encode()).hexdigest()}"

    async def _get_semantic_cache(self, query_vector: List[float]) -> Optional[List[Dict[str, Any]]]:
        if not getattr(settings, "RAG_ENABLE_SEMANTIC_CACHE", False) or not query_vector:
            return None

        semantic_cache_key = self._semantic_cache_key(query_vector)
        if not semantic_cache_key:
            return None

        row = self._semantic_cache.get(semantic_cache_key)
        if not row:
            return None

        ttl = max(1, int(settings.RAG_SEMANTIC_CACHE_TTL_SECONDS or 300))
        if time.time() - row.get("ts", 0) > ttl:
            self._semantic_cache.pop(semantic_cache_key, None)
            return None

        cached_vector = row.get("query_vector", [])
        if not cached_vector:
            return None

        similarity = self._cosine_similarity(query_vector, cached_vector)
        threshold = float(settings.RAG_SEMANTIC_CACHE_SIMILARITY_THRESHOLD or 0.92)

        if similarity >= threshold:
            logger.info(f"♻️ 语义缓存命中，相似度: {similarity:.3f}")
            return row.get("value")

        return None

    def _set_semantic_cache(self, query_vector: List[float], value: List[Dict[str, Any]]) -> None:
        if not getattr(settings, "RAG_ENABLE_SEMANTIC_CACHE", False) or not query_vector:
            return

        semantic_key = self._semantic_cache_key(query_vector)
        if not semantic_key:
            return

        max_size = max(50, int(settings.RAG_SEMANTIC_CACHE_MAX_SIZE or 200))
        if len(self._semantic_cache) >= max_size:
            oldest_key = min(self._semantic_cache, key=lambda k: self._semantic_cache[k].get("ts", 0))
            self._semantic_cache.pop(oldest_key, None)

        self._semantic_cache[semantic_key] = {
            "ts": time.time(),
            "query_vector": query_vector,
            "value": value
        }
        logger.info(f"💾 语义缓存已存储，key: {semantic_key[:16]}...")

    async def _rewrite_query_llm(self, query: str) -> List[str]:
        """使用 LLM 进行查询扩展，生成 3-5 个相关的搜索变体。"""
        if not self.openai_client or not getattr(settings, "RAG_ENABLE_QUERY_REWRITE", True):
            return [query]
        
        try:
            prompt = (
                "作为搜索专家，请为用户的原始问题生成 3 个相关的搜索查询变体（中文）。"
                "这些变体应该从不同角度描述同一个意图，或者包含可能的关键词。"
                "只返回 JSON 字符串数组，不要有任何解释。\n\n"
                f"原始问题: {query}\n\n"
                "输出示例: [\"查询1\", \"查询2\", \"查询3\"]"
            )
            
            resp = await self.openai_client.chat.completions.create(
                model=settings.DEEPSEEK_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=200
            )
            
            content = resp.choices[0].message.content or ""
            # 尝试提取 JSON 数组
            match = re.search(r"\[.*\]", content, re.DOTALL)
            if match:
                rewritten = json.loads(match.group(0))
                if isinstance(rewritten, list):
                    # 确保包含原问题并去重
                    results = [query]
                    for r in rewritten:
                        if r.strip() and r.strip() not in results:
                            results.append(r.strip())
                    return results[:4]
            return [query]
        except Exception as e:
            logger.warning(f"LLM Query Rewrite failed: {e}")
            return [query]

    async def _generate_hyde_doc(self, query: str, intent: QueryIntent = "other") -> str:
        """
        HyDE v2 (Hypothetical Document Embeddings): 生成多个虚拟文档以增强检索。
        根据意图类型生成不同风格的假设文档。
        """
        if not self.openai_client:
            return ""

        hyde_templates = {
            "factual": "请提供一个准确的事实性回答，包含具体的数值、日期或名称。50-100字。",
            "procedural": "请按步骤说明操作流程，使用数字序号。50-100字。",
            "list": "请列出相关要点，每个要点简洁明了。50-100字。",
            "definition": "请给出清晰的定义，包含关键特征和例子。50-100字。",
            "comparison": "请从多个维度对比分析，列出异同点。50-100字。",
            "causal": "请说明原因和可能的结果/影响。50-100字。",
            "summary": "请给出简明扼要的总结，包含核心要点。50-100字。",
            "other": "请提供一段事实性的简短回答。50-100字。",
        }

        template = hyde_templates.get(intent, hyde_templates["other"])

        try:
            prompt = (
                f"请为以下问题写一段理想的回答。\n"
                f"要求：{template}\n"
                f"这段回答将用于向量检索，请尽可能使用专业术语和核心关键词。\n\n"
                f"问题: {query}"
            )

            resp = await self.openai_client.chat.completions.create(
                model=settings.DEEPSEEK_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=300
            )
            return resp.choices[0].message.content or ""
        except Exception as e:
            logger.warning(f"HyDE generation failed: {e}")
            return ""

    async def _generate_multi_hyde_docs(self, query: str, intent: QueryIntent = "other", num_docs: int = 2) -> List[str]:
        """
        生成多个不同的HyDE文档，通过多视角检索提高召回率。
        """
        if not self.openai_client:
            return []

        hyde_perspectives = {
            "factual": ["官方定义视角", "数据统计视角", "实际案例视角"],
            "procedural": ["步骤概览视角", "细节说明视角", "注意事项视角"],
            "list": ["完整清单视角", "优先级视角", "分类整理视角"],
            "definition": ["概念定义视角", "特征描述视角", "应用场景视角"],
            "comparison": ["共同点视角", "差异点视角", "适用场景视角"],
            "causal": ["直接原因视角", "深层原因视角", "影响结果视角"],
            "summary": ["核心结论视角", "关键要点视角", "行动建议视角"],
            "other": ["概述视角", "细节视角"],
        }

        perspectives = hyde_perspectives.get(intent, hyde_perspectives["other"])[:num_docs]

        try:
            docs = []
            for perspective in perspectives:
                prompt = (
                    f"从【{perspective}】的角度，为以下问题提供一个理想的回答。\n"
                    f"要求：50-80字，事实性表述，用于向量检索。\n\n"
                    f"问题: {query}"
                )

                resp = await self.openai_client.chat.completions.create(
                    model=settings.DEEPSEEK_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.4,
                    max_tokens=200
                )
                content = resp.choices[0].message.content or ""
                if content:
                    docs.append(content)

            return docs
        except Exception as e:
            logger.warning(f"Multi-HyDE generation failed: {e}")
            return []

    async def _get_keyword_hits(self, query: str, filters: List[Dict[str, Any]], candidate_size: int) -> List[Dict[str, Any]]:
        """执行多查询扩展后的并行关键词搜索。"""
        enable_qr = bool(getattr(settings, "RAG_ENABLE_QUERY_REWRITE", True))
        rewrite_queries = self._rewrite_query_candidates(query)
        if enable_qr:
            timeout_sec = float(getattr(settings, "RAG_QUERY_REWRITE_TIMEOUT_SECONDS", 1.2) or 1.2)
            try:
                llm_rewrites = await asyncio.wait_for(self._rewrite_query_llm(query), timeout=max(0.2, timeout_sec))
                for item in llm_rewrites:
                    cleaned = (item or "").strip()
                    if cleaned and cleaned not in rewrite_queries:
                        rewrite_queries.append(cleaned)
            except Exception as e:
                logger.warning(f"LLM query rewrite timeout/fail, fallback to local rewrite: {e}")
        if not rewrite_queries:
            rewrite_queries = [query]
        rewrite_limit = max(1, int(getattr(settings, "RAG_QUERY_REWRITE_COUNT", 4) or 4))
        rewrite_queries = rewrite_queries[:rewrite_limit]
        
        async def _es_keyword_search(q_text: str, boost_fields: List[str]):
            keyword_query = {
                "size": candidate_size,
                "min_score": 3.0,  # 关键词搜索的最低相关性得分，严格过滤不匹配的结果
                "query": {
                    "bool": {
                        "must": [{
                            "multi_match": {
                                "query": q_text,
                                "fields": boost_fields,
                                "type": "best_fields",
                                "fuzziness": "AUTO",
                                "minimum_should_match": "30%"  # 至少匹配 30% 的查询词
                            }
                        }],
                        "filter": filters
                    }
                },
                "highlight": {
                    "fields": {"content": {"fragment_size": 120, "number_of_fragments": 2}}
                },
                "_source": ["content", "filename", "organization_id", "document_id", "embedding", "upload_time"]
            }
            res = await ElasticsearchTools.search_documents(keyword_query)
            return res.get("hits", {}).get("hits", [])

        query_intent = QueryIntentClassifier.classify(query)
        boost_fields = QueryIntentClassifier.get_intent_boost_fields(query_intent)
        logger.info(f"🔍 查询意图分类: {query_intent}, 使用字段权重: {boost_fields}")

        kw_tasks = [_es_keyword_search(rq, boost_fields) for rq in rewrite_queries]
        kw_results = await asyncio.gather(*kw_tasks)
        
        merged_hits: Dict[str, Dict[str, Any]] = {}
        for res in kw_results:
            for hit in res:
                doc_id = str(hit.get("_id") or "")
                if not doc_id:
                    continue
                prev = merged_hits.get(doc_id)
                score = float(hit.get("_score") or 0.0)
                if prev is None:
                    merged = dict(hit)
                    merged["_score"] = score
                    merged["_rewrite_hits"] = 1
                    merged_hits[doc_id] = merged
                    continue
                prev["_rewrite_hits"] = int(prev.get("_rewrite_hits", 1)) + 1
                prev_score = float(prev.get("_score") or 0.0)
                # 不再给 rewrite_hits 额外加分，避免不相关文档被提升
                prev["_score"] = max(prev_score, score)
                if score > prev_score:
                    prev["_source"] = hit.get("_source", prev.get("_source", {}))
                    prev["highlight"] = hit.get("highlight", prev.get("highlight", {}))

        return sorted(merged_hits.values(), key=lambda x: float(x.get("_score") or 0.0), reverse=True)[:candidate_size]

    async def _get_vector_hits(self, query: str, filters: List[Dict[str, Any]], candidate_size: int) -> Tuple[List[Dict[str, Any]], Optional[List[float]]]:
        """执行带 HyDE v2 增强的向量搜索。"""
        query_intent = QueryIntentClassifier.classify(query)

        hyde_doc_task = self._generate_hyde_doc(query, query_intent)
        multi_hyde_task = self._generate_multi_hyde_docs(query, query_intent, num_docs=2)
        query_vector_task = self.get_embedding(query)

        hyde_doc, multi_hyde_docs, query_vector = await asyncio.gather(
            hyde_doc_task, multi_hyde_task, query_vector_task
        )

        embedding_to_search = query_vector

        all_hyde_docs = [hyde_doc] + multi_hyde_docs if hyde_doc else multi_hyde_docs
        hyde_vectors: List[List[float]] = []

        if all_hyde_docs:
            logger.info(f"🚀 使用 Multi-HyDE 增强检索，生成 {len(all_hyde_docs)} 个虚拟文档")
            hyde_vector_tasks = [self.get_embedding(doc) for doc in all_hyde_docs if doc]
            hyde_vectors = await asyncio.gather(*hyde_vector_tasks)

        if hyde_vectors and query_vector:
            avg_hyde_vector = [sum(v[i] for v in hyde_vectors) / len(hyde_vectors) for i in range(len(query_vector))]
            embedding_to_search = [(v1 * 0.6 + v2 * 0.4) for v1, v2 in zip(query_vector, avg_hyde_vector)]
            logger.info(f"✅ 融合向量构建完成，原问权重60%，HyDE权重40%")

        if not embedding_to_search:
            return [], query_vector

        # cosineSimilarity + 1.0 将范围映射到 [0, 2]
        # min_score=1.15 表示原始余弦相似度 >= 0.15（中等相关性）
        # 过滤掉向量空间中距离较远的文档
        vector_query = {
            "size": candidate_size,
            "min_score": 1.15,
            "query": {
                "script_score": {
                    "query": {
                        "bool": {
                            "filter": [
                                *filters,
                                {"exists": {"field": "embedding"}}
                            ]
                        }
                    },
                    "script": {
                        "source": "cosineSimilarity(params.query_vector, 'embedding') + 1.0",
                        "params": {"query_vector": embedding_to_search}
                    }
                }
            },
            "_source": ["content", "filename", "organization_id", "document_id", "embedding", "upload_time"]
        }
        vector_res = await ElasticsearchTools.search_documents(vector_query)
        return vector_res.get("hits", {}).get("hits", []), query_vector

    async def _retrieve_once(
        self,
        query: str,
        organization_id: int,
        top_k: int,
        document_ids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        org_filter = {"term": {"organization_id": str(organization_id)}}
        filters: List[Dict[str, Any]] = [org_filter]
        if document_ids:
            doc_ids = [str(d) for d in document_ids if d]
            if doc_ids:
                filters.append({"terms": {"document_id": doc_ids}})
        
        candidate_size = max(top_k * max(1, settings.RAG_MMR_CANDIDATE_MULTIPLIER), 12)
        logger.info(f"🔍 检索 ES Index={settings.ELASTICSEARCH_INDEX_NAME} org={organization_id} docs={len(document_ids or [])}")

        # 1) 关键词 + 向量 并行召回
        kw_hits_task = self._get_keyword_hits(query, filters, candidate_size)
        vec_hits_task = self._get_vector_hits(query, filters, candidate_size)
        keyword_hits, (vector_hits, query_vector) = await asyncio.gather(kw_hits_task, vec_hits_task)

        # 2) RRF 融合
        fused_hits = self._rrf_fuse(keyword_hits, vector_hits)
        if not fused_hits:
            logger.warning("⚠️ RAG混合检索未命中，开始Fallback策略...")
            fallback_results = await self._fallback_retrieval(query, filters, top_k)
            if fallback_results:
                logger.info(f"✅ Fallback检索返回 {len(fallback_results)} 条结果")
                return self._post_process_results(query, fallback_results, top_k)
            logger.info("⚠️ RAG未命中任何片段")
            return []

        # 3) MMR 去冗余
        if settings.RAG_ENABLE_MMR and query_vector:
            mmr_candidates: List[Dict[str, Any]] = []
            for hit in fused_hits[:candidate_size]:
                src = hit.get("_source", {})
                emb = src.get("embedding")
                if not emb:
                    continue
                mmr_candidates.append({
                    "_id": hit.get("_id"),
                    "_source": src,
                    "_score": hit.get("_score", 0.0),
                    "keyword_rank": hit.get("keyword_rank"),
                    "vector_rank": hit.get("vector_rank"),
                    "keyword_score": hit.get("keyword_score", 0.0),
                    "vector_score": hit.get("vector_score", 0.0),
                    "rewrite_hits": hit.get("rewrite_hits", 0),
                    "has_keyword": hit.get("has_keyword", False),
                    "has_vector": hit.get("has_vector", False),
                    "_embedding": emb,
                })

            if mmr_candidates:
                fused_hits = self._mmr_select(
                    candidates=mmr_candidates,
                    query_vector=query_vector,
                    top_k=top_k,
                    lambda_mult=max(0.0, min(1.0, settings.RAG_MMR_LAMBDA)),
                )

        # 4) Cross-Encoder Rerank
        fused_hits = await self._cross_encoder_rerank(query, fused_hits)

        # 5) 后处理：过滤重复与相关性极低的结果
        return self._post_process_results(query, fused_hits, top_k)

    async def _fallback_retrieval(
        self,
        query: str,
        filters: List[Dict[str, Any]],
        top_k: int
    ) -> List[Dict[str, Any]]:
        """
        Fallback检索策略 - 当主检索失败时尝试的备选方案
        1. 放宽搜索条件（去除fuzziness限制）
        2. 仅用关键词搜索
        3. 仅用向量搜索
        4. 返回相关性较低但仍可用的结果
        """
        logger.info(f"🔄 执行Fallback检索: {query[:50]}...")

        try:
            # Fallback关键词检索：添加最低匹配度要求，避免返回完全不相关的结果
            broad_query = {
                "size": top_k * 2,
                "min_score": 2.5,  # Fallback时降低要求但仍需相关性
                "query": {
                    "bool": {
                        "must": [{
                            "match": {
                                "content": {
                                    "query": query,
                                    "operator": "or",
                                    "minimum_should_match": "30%"  # 至少匹配 30% 的查询词
                                }
                            }
                        }],
                        "filter": filters
                    }
                },
                "_source": ["content", "filename", "organization_id", "document_id", "embedding", "upload_time"]
            }
            res = await ElasticsearchTools.search_documents(broad_query)
            hits = res.get("hits", {}).get("hits", [])

            if hits:
                logger.info(f"✅ Fallback关键词检索返回 {len(hits)} 条")
                return hits

            if not self.embedding_client:
                return []

            query_vector = await self.get_embedding(query)
            if not query_vector:
                return []

            vector_query = {
                "size": top_k,
                "min_score": 1.15,  # 与主检索保持一致
                "query": {
                    "script_score": {
                        "query": {"bool": {"filter": filters}},
                        "script": {
                            "source": "cosineSimilarity(params.query_vector, 'embedding') + 1.0",
                            "params": {"query_vector": query_vector}
                        }
                    }
                },
                "_source": ["content", "filename", "organization_id", "document_id", "embedding", "upload_time"]
            }
            vector_res = await ElasticsearchTools.search_documents(vector_query)
            hits = vector_res.get("hits", {}).get("hits", [])

            logger.info(f"✅ Fallback向量检索返回 {len(hits)} 条")
            return hits

        except Exception as e:
            logger.error(f"❌ Fallback检索失败: {e}")
            return []

    def _post_process_results(self, query: str, fused_hits: List[Dict[str, Any]], top_k: int) -> List[Dict[str, Any]]:
        """后处理：规范化结果 + 文档去重 + 文本片段生成。"""
        documents: List[Dict[str, Any]] = []
        seen_docs: Dict[str, int] = {}
        query_terms = self._extract_query_terms(query)
        raw_scores = [float(hit.get("_score") or 0.0) for hit in fused_hits]
        score_min = min(raw_scores) if raw_scores else 0.0
        score_max = max(raw_scores) if raw_scores else 0.0

        now_ts = time.time()
        freshness_values: List[float] = []
        for hit in fused_hits:
            source = hit.get("_source", {})
            ts = self._parse_upload_timestamp(source.get("upload_time"))
            if ts is not None and ts <= now_ts:
                freshness_values.append(ts)

        fresh_min = min(freshness_values) if freshness_values else None
        fresh_max = max(freshness_values) if freshness_values else None
        
        for hit in fused_hits:
            source = hit.get("_source", {})
            text = (source.get("content", "") or "").strip()
            if not text:
                continue

            doc_id = str(source.get("document_id") or "")
            seen_docs[doc_id] = seen_docs.get(doc_id, 0) + 1
            if doc_id and seen_docs[doc_id] > 2:
                continue

            snippet = self._build_snippet(text, query)
            overlap_score = self._compute_query_overlap(f"{source.get('filename', '')} {text[:1600]}", query_terms)
            has_keyword = bool(hit.get("has_keyword")) or hit.get("keyword_rank") is not None
            has_vector = bool(hit.get("has_vector")) or hit.get("vector_rank") is not None
            rewrite_hits = max(1, int(hit.get("rewrite_hits") or 1))

            score_range = max(1e-9, score_max - score_min)
            score_percentile = (float(hit.get("_score") or 0.0) - score_min) / score_range if score_range > 1e-9 else 0.0

            # 计算关键词匹配数量（用于更精确的过滤）
            keyword_match_count = self._compute_keyword_match_count(f"{source.get('filename', '')} {text[:1600]}", query_terms)
            
            # ========== 核心过滤逻辑 ==========
            # 目标：只保留真正相关的文档，过滤掉向量相似但实际无关的结果
            
            # 条件1: 完全不匹配 - 既没有关键词也没有向量匹配，且没有任何查询词重叠
            if not has_keyword and not has_vector and overlap_score < 0.01:
                logger.info(f"🚫 跳过无关文档: {source.get('filename')} overlap={overlap_score:.3f} 无任何匹配")
                continue
            
            # 条件2: 只有向量匹配但没有关键词匹配，需要更严格的重叠度要求
            if not has_keyword and has_vector:
                # 纯向量匹配容易产生幻觉相关，必须有足够的文本重叠
                if overlap_score < 0.1 and keyword_match_count < 2:
                    logger.info(f"🚫 跳过弱相关向量文档: {source.get('filename')} overlap={overlap_score:.3f} kw_match={keyword_match_count}")
                    continue
            
            # 条件3: 关键词匹配但重叠度极低，说明只是边缘词匹配
            if has_keyword and overlap_score < 0.03:
                logger.info(f"🚫 跳过边缘词匹配文档: {source.get('filename')} overlap={overlap_score:.3f}")
                continue
            
            # 条件4: 排名很低且重叠度不足
            if score_percentile < 0.3 and overlap_score < 0.05:
                logger.info(f"🚫 跳过低排名低重叠文档: {source.get('filename')} overlap={overlap_score:.3f} percentile={score_percentile:.2f}")
                continue
                
            fused_score = float(hit.get("_score") or 0.0)
            if score_max - score_min > 1e-9:
                normalized = (fused_score - score_min) / (score_max - score_min)
                normalized = max(0.0, min(1.0, 0.1 + 0.9 * normalized))
            else:
                normalized = 0.6 if fused_score > 0 else 0.0

            fresh_factor = 1.0
            if fresh_min is not None and fresh_max is not None and fresh_max - fresh_min > 1e-6:
                ts = self._parse_upload_timestamp(source.get("upload_time"))
                if ts is not None and fresh_min <= ts <= fresh_max:
                    rel = (ts - fresh_min) / (fresh_max - fresh_min)
                    fresh_factor = 0.75 + 0.25 * rel

            modality_factor = 1.0
            if has_keyword and has_vector:
                modality_factor = 1.1
            elif has_keyword or has_vector:
                modality_factor = 1.03
            overlap_factor = 0.9 + 0.2 * min(1.0, overlap_score)
            rewrite_factor = 1.0  # 不再给 rewrite_hits 加分，避免不相关文档被提升
            final_score = min(1.0, normalized * fresh_factor * modality_factor * overlap_factor * rewrite_factor)

            documents.append({
                "text": text,
                "filename": source.get("filename", "未知"),
                "score": round(final_score, 4),
                "raw_score": fused_score,
                "snippet": snippet,
                "document_id": source.get("document_id"),
                "mmr_score": float(hit.get("_mmr_score") or 0.0),
                "overlap_score": overlap_score,
                "has_keyword": has_keyword,
                "has_vector": has_vector,
                "rewrite_hits": rewrite_hits,
                "fresh_factor": round(fresh_factor, 2)
            })

            if len(documents) >= top_k:
                break

        # ========== 最终质量检查 ==========
        # 即使通过了前面的过滤，也要确保结果质量
        if documents:
            filtered = []
            for doc in documents:
                overlap = doc.get("overlap_score", 0)
                score = doc.get("score", 0)
                has_kw = doc.get("has_keyword", False)
                has_vec = doc.get("has_vector", False)
                
                # 保留条件（必须满足其一）:
                # 1. 有实质性的文本重叠 (>= 0.15) - 说明文档内容与查询高度相关
                # 2. 有关键词匹配且重叠度 >= 0.10 - 关键词匹配需要更高重叠度
                # 3. 双模态匹配(关键词+向量) 且重叠度 >= 0.07 - 双重保障
                should_keep = (
                    overlap >= 0.15 or
                    (has_kw and overlap >= 0.10) or
                    (has_kw and has_vec and overlap >= 0.07)
                )
                
                if should_keep:
                    filtered.append(doc)
                else:
                    logger.info(f"🚫 最终过滤移除: {doc.get('filename')} overlap={overlap:.3f} score={score:.3f} kw={has_kw} vec={has_vec}")
            
            logger.info(f"✅ RAG最终过滤: {len(filtered)} 条 (过滤前 {len(documents)} 条)")
            documents = filtered
        else:
            logger.info("⚠️ RAG未找到任何匹配文档")

        return documents

    def _append_metric_event(self, key: str, value: float) -> None:
        now = time.time()
        arr = self._metric_events.setdefault(key, [])
        arr.append((now, value))
        # 保留最近 24h 事件，防止内存增长
        cutoff = now - 24 * 3600
        while arr and arr[0][0] < cutoff:
            arr.pop(0)

    def _window_sum(self, key: str, window_seconds: int) -> float:
        now = time.time()
        arr = self._metric_events.get(key, [])
        if window_seconds <= 0:
            return float(sum(v for _, v in arr))
        cutoff = now - window_seconds
        return float(sum(v for ts, v in arr if ts >= cutoff))

    def _window_count(self, key: str, window_seconds: int) -> int:
        now = time.time()
        arr = self._metric_events.get(key, [])
        if window_seconds <= 0:
            return len(arr)
        cutoff = now - window_seconds
        return sum(1 for ts, _ in arr if ts >= cutoff)

    async def search_knowledge_base(
        self,
        query: str,
        organization_id: int,
        top_k: int = 5,
        document_ids: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """检索知识库：带缓存 + 失败重试。支持限定 document_ids。"""
        start = time.perf_counter()
        self._metrics["retrieval_total"] += 1
        self._append_metric_event("retrieval", 1)
        query_vector: Optional[List[float]] = None

        # 指定文档检索不走缓存，避免跨上下文污染
        cache_key = self._cache_key(query, organization_id, top_k) if not document_ids else ""
        cached = self._cache_get(cache_key) if cache_key else None
        if cached is not None:
            self._metrics["cache_hit"] += 1
            self._append_metric_event("cache_hit", 1)
            if len(cached) > 0:
                self._metrics["retrieval_hit"] += 1
                self._append_metric_event("retrieval_hit", 1)
            elapsed_ms = (time.perf_counter() - start) * 1000
            self._metrics["latency_sum_ms"] += elapsed_ms
            self._metrics["latency_count"] += 1
            self._append_metric_event("latency", elapsed_ms)
            logger.info("♻️ RAG cache hit")
            return cached

        if not document_ids:
            query_vector = await self.get_embedding(query)
            if query_vector:
                semantic_cached = await self._get_semantic_cache(query_vector)
                if semantic_cached is not None:
                    self._metrics["semantic_cache_hit"] += 1
                    self._metrics["cache_hit"] += 1
                    self._append_metric_event("cache_hit", 1)
                    if len(semantic_cached) > 0:
                        self._metrics["retrieval_hit"] += 1
                        self._append_metric_event("retrieval_hit", 1)
                    elapsed_ms = (time.perf_counter() - start) * 1000
                    self._metrics["latency_sum_ms"] += elapsed_ms
                    self._metrics["latency_count"] += 1
                    self._append_metric_event("latency", elapsed_ms)
                    logger.info("♻️ RAG semantic cache hit")
                    if cache_key:
                        self._cache_set(cache_key, semantic_cached)
                    return semantic_cached

        retries = max(0, int(settings.RAG_RETRIEVAL_MAX_RETRIES or 2))
        last_err: Optional[Exception] = None

        for attempt in range(retries + 1):
            try:
                if attempt > 0:
                    self._metrics["retry_total"] += 1
                    self._append_metric_event("retry", 1)
                result = await self._retrieve_once(query, organization_id, top_k, document_ids=document_ids)
                if cache_key:
                    self._cache_set(cache_key, result)
                if query_vector and result:
                    self._set_semantic_cache(query_vector, result)
                if len(result) > 0:
                    self._metrics["retrieval_hit"] += 1
                    self._append_metric_event("retrieval_hit", 1)
                elapsed_ms = (time.perf_counter() - start) * 1000
                self._metrics["latency_sum_ms"] += elapsed_ms
                self._metrics["latency_count"] += 1
                self._append_metric_event("latency", elapsed_ms)
                return result
            except Exception as e:
                last_err = e
                logger.warning(f"RAG retrieval attempt {attempt + 1}/{retries + 1} failed: {e}")
                if attempt < retries:
                    await asyncio.sleep(min(1.5, 0.3 * (2 ** attempt)))

        elapsed_ms = (time.perf_counter() - start) * 1000
        self._metrics["latency_sum_ms"] += elapsed_ms
        self._metrics["latency_count"] += 1
        self._append_metric_event("latency", elapsed_ms)
        logger.error(f"RAG检索失败: {last_err}", exc_info=True)
        return []

    def report_grounded(self, has_sources: bool) -> None:
        self._metrics["grounded_total"] += 1
        self._append_metric_event("grounded", 1)
        if has_sources:
            self._metrics["grounded_hit"] += 1
            self._append_metric_event("grounded_hit", 1)

    def report_tokens(self, input_tokens: int, output_tokens: int) -> None:
        self._metrics["total_input_tokens"] += input_tokens
        self._metrics["total_output_tokens"] += output_tokens
        self._metrics["llm_request_count"] += 1
        self._append_metric_event("input_tokens", input_tokens)
        self._append_metric_event("output_tokens", output_tokens)

    def report_embedding_tokens(self, tokens: int) -> None:
        self._metrics["total_embedding_tokens"] += tokens

    def get_metrics(self, window_seconds: int = 0) -> Dict[str, Any]:
        if window_seconds and window_seconds > 0:
            retrieval_total = max(1, self._window_sum("retrieval", window_seconds))
            retrieval_hit = self._window_sum("retrieval_hit", window_seconds)
            grounded_total = max(1, self._window_sum("grounded", window_seconds))
            grounded_hit = self._window_sum("grounded_hit", window_seconds)
            latency_count = max(1, self._window_count("latency", window_seconds))
            latency_sum = self._window_sum("latency", window_seconds)
            cache_hit = self._window_sum("cache_hit", window_seconds)
            retry_total = self._window_sum("retry", window_seconds)
            semantic_cache_hit = self._window_sum("semantic_cache_hit", window_seconds)

            latency_p50 = self._calculate_percentile("latency", 0.5, window_seconds)
            latency_p95 = self._calculate_percentile("latency", 0.95, window_seconds)
            latency_p99 = self._calculate_percentile("latency", 0.99, window_seconds)

            return {
                "window_seconds": window_seconds,
                "retrieval_total": int(retrieval_total),
                "retrieval_hit": int(retrieval_hit),
                "hit_rate": round(float(retrieval_hit) / float(retrieval_total), 4),
                "grounded_total": int(grounded_total),
                "grounded_hit": int(grounded_hit),
                "groundedness": round(float(grounded_hit) / float(grounded_total), 4),
                "avg_latency_ms": round(float(latency_sum) / float(latency_count), 2),
                "latency_p50_ms": round(latency_p50, 2) if latency_p50 else 0,
                "latency_p95_ms": round(latency_p95, 2) if latency_p95 else 0,
                "latency_p99_ms": round(latency_p99, 2) if latency_p99 else 0,
                "cache_hit": int(cache_hit),
                "semantic_cache_hit": int(semantic_cache_hit),
                "retry_total": int(retry_total),
                "cache_hit_rate": round(float(cache_hit) / float(retrieval_total), 4) if retrieval_total > 0 else 0,
            }

        retrieval_total = max(1, int(self._metrics["retrieval_total"]))
        grounded_total = max(1, int(self._metrics["grounded_total"]))
        latency_count = max(1, int(self._metrics["latency_count"]))

        return {
            "window_seconds": 0,
            "retrieval_total": self._metrics["retrieval_total"],
            "retrieval_hit": self._metrics["retrieval_hit"],
            "hit_rate": round(float(self._metrics["retrieval_hit"]) / retrieval_total, 4),
            "grounded_total": self._metrics["grounded_total"],
            "grounded_hit": self._metrics["grounded_hit"],
            "groundedness": round(float(self._metrics["grounded_hit"]) / grounded_total, 4),
            "avg_latency_ms": round(float(self._metrics["latency_sum_ms"]) / latency_count, 2),
            "cache_hit": self._metrics["cache_hit"],
            "semantic_cache_hit": self._metrics.get("semantic_cache_hit", 0),
            "retry_total": self._metrics["retry_total"],
            "cache_hit_rate": round(float(self._metrics["cache_hit"]) / retrieval_total, 4) if retrieval_total > 0 else 0,
        }

    def _calculate_percentile(self, key: str, percentile: float, window_seconds: int) -> Optional[float]:
        now = time.time()
        cutoff = now - window_seconds if window_seconds > 0 else 0
        arr = [v for ts, v in self._metric_events.get(key, []) if ts >= cutoff]
        if not arr:
            return None
        arr.sort()
        idx = int(len(arr) * percentile)
        return arr[min(idx, len(arr) - 1)]

    def reset_metrics(self) -> None:
        self._metrics = {
            "retrieval_total": 0,
            "retrieval_hit": 0,
            "grounded_total": 0,
            "grounded_hit": 0,
            "latency_sum_ms": 0.0,
            "latency_count": 0,
            "cache_hit": 0,
            "retry_total": 0,
            "semantic_cache_hit": 0,
        }
        self._metric_events = {k: [] for k in self._metric_events}
        self._retrieval_cache.clear()
        self._semantic_cache.clear()
        logger.info("📊 RAG metrics reset")

    async def chat_stream(
        self,
        query: str,
        context: List[Dict[str, Any]],
        history: List[Dict[str, str]] = [],
        system_prompt_override: Optional[str] = None,
        enable_compression: bool = True,
        enable_masking: bool = True
    ):
        if not self.openai_client:
            yield "LLM未配置"; return

        # ⚡ 敏感信息脱敏 (PII Masking)
        original_query = query
        masking_mapping = {}
        if enable_masking and getattr(settings, "ENABLE_PII_MASKING", False):
            query, masking_mapping = masking_service.mask_text(query)

        if enable_compression and context:
            compressed_context = ContextCompressor.compress_context_list(context, query, max_context_chars=8000)
        else:
            compressed_context = context

        context_str = "\n\n".join([
            f"资料[{i+1}] (文件名: {item.get('filename', '未知文档')}, 压缩: {'是' if item.get('is_compressed') else '否'}):\n{(item.get('snippet') or item.get('text', ''))[:3000]}"
            for i, item in enumerate(compressed_context)
        ]) if compressed_context else "（未找到相关文档）"

        # ⚡ GraphRAG 增强：提取并注入知识图谱上下文
        graph_context = ""
        if ENABLE_GRAPH_RAG and context:
            try:
                from app.services.graph_rag_service import graph_rag_service
                all_text = " ".join([item.get('snippet') or item.get('text', '')[:500] for item in context[:5]])
                entities = graph_rag_service.extract_entities_with_llm(all_text, self.openai_client)
                if entities:
                    graph_rag_service.build_graph_from_entities(entities)
                    graph_results = graph_rag_service.search_graph(query)
                    if graph_results:
                        graph_context = "\n\n【知识图谱关联信息】：\n" + "\n".join([
                            f"- {g['entity']}（{g['type']}）：{g.get('description', '')[:100]}"
                            for g in graph_results[:5]
                        ])
                        logger.info(f"🕸️ GraphRAG: 找到 {len(graph_results)} 个相关实体")
            except Exception as e:
                logger.warning(f"GraphRAG enhancement failed: {e}")

        query_intent = QueryIntentClassifier.classify(query)
        intent_guidance = {
            "factual": "请以事实陈述的方式回答，准确引用来源。",
            "procedural": "请按步骤清晰说明操作流程。",
            "list": "请列出所有相关项并简要说明。",
            "definition": "请给出清晰的定义和解释。",
            "comparison": "请从多个维度对比分析。",
            "causal": "请说明原因和结果。",
            "summary": "请给出简明扼要的总结。",
            "other": "请基于文档内容回答。",
        }.get(query_intent, "请基于文档内容回答。")

        base_system_prompt = (
            "你是企业知识库问答助手。你的任务是基于提供的【参考文档】提供准确、客观的回答。\n"
            f"📌 回答指导: {intent_guidance}\n"
            "⚠️ 核心约束（违反这些规则将导致回答失败）：\n"
            "1. **严格忠于原文**：只能根据提供的【参考文档】回答问题。如果文档中没有提到相关信息，必须明确告知用户，不得编造任何内容。\n"
            "2. **精准引用**：在回答的每一句话或每一个核心观点结束时，必须使用 [n] 格式标注引用来源（如 [1]、[2]）。严禁在没有依据的情况下使用引用标注。\n"
            "3. **处理噪声**：提供的参考文档可能包含无关的干扰信息（如比赛列表、广告、元数据等）。你需要具备辨别能力，忽略 those 与用户问题无关的内容，重点捕捉核心语义。\n"
            "4. **结构化输出**：回答应逻辑清晰，多使用分点列表（Markdown 格式）。\n"
            "5. **语言要求**：无论用户使用何种语言提问，你必须始终使用【简体中文】回答。\n"
            "6. **拒绝臆测**：严禁引用你自身训练数据中的外部知识来补充文档中缺失的部分。你的知识边界仅限于本次提供的【参考文档】。\n"
            "7. **聚焦提问目标**：如果文档包含多个主题，仅回答与当前问题直接相关的部分，不要输出与问题无关的大段内容。\n"
            "8. **计划提取优先**：当问题涉及“计划/安排/课程/出行”时，优先提取时间、事项、课程、地点和注意事项，按结构化小节输出。"
        )
        system_prompt = system_prompt_override or base_system_prompt
        messages: List[ChatCompletionMessageParam] = [
            {"role": "system", "content": system_prompt},
            *cast(List[ChatCompletionMessageParam], history[-8:]),
            {"role": "user", "content": f"【参考文档】：\n{context_str}{graph_context}\n\n【问题】：{query}"}
        ]

        try:
            model = settings.LOCAL_LLM_MODEL if settings.ENABLE_LOCAL_LLM else settings.DEEPSEEK_MODEL

            input_text = ""
            for m in messages:
                if m.get("content"):
                    input_text += str(m.get("content", ""))

            input_tokens_est = max(1, int(len(input_text) / 1.5))

            stream = await self.openai_client.chat.completions.create(
                model=model, messages=messages, stream=True, temperature=0.1,
                max_tokens=settings.AI_MAX_TOKENS,
                timeout=settings.AI_STREAM_TIMEOUT
            )

            full_response = ""
            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content:
                    content = chunk.choices[0].delta.content
                    full_response += content
                    yield content

            output_tokens_est = max(1, int(len(full_response) / 1.5))
            self.report_tokens(input_tokens_est, output_tokens_est)

            if masking_mapping:
                final_answer = masking_service.unmask_text(full_response, masking_mapping)
                logger.info(f"🛡️ PII Masking: 回答已还原")
                
        except Exception as e:
            yield f"LLM Error: {e}"


rag_service = RagService()
