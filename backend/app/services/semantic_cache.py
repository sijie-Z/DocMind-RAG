import json
import logging
import hashlib
import numpy as np
from typing import Optional, Dict, Any, List

from app.core.redis import get_redis
from app.core.config import settings

logger = logging.getLogger(__name__)

class SemanticCache:
    """
    语义缓存 (Semantic Cache)
    原理：将用户的问题进行 Embedding，然后在 Redis 中寻找最相似的历史问题。
    如果相似度高于阈值，直接返回缓存的答案，节省大模型 Token 和时间。
    """
    def __init__(self):
        self.similarity_threshold = getattr(settings, "SEMANTIC_CACHE_THRESHOLD", 0.92)  # 严格阈值
        self.ttl = getattr(settings, "SEMANTIC_CACHE_TTL_SECONDS", 3600)  # 缓存过期时间 (默认1小时)
        self.cache_prefix = "rag:semantic_cache:"
        self.index_key = "rag:semantic_cache_index"

    def _cosine_similarity(self, v1: List[float], v2: List[float]) -> float:
        """计算两个向量的余弦相似度"""
        vec1 = np.array(v1)
        vec2 = np.array(v2)
        if np.linalg.norm(vec1) == 0 or np.linalg.norm(vec2) == 0:
            return 0.0
        return np.dot(vec1, vec2) / (np.linalg.norm(vec1) * np.linalg.norm(vec2))

    async def get_similar_answer(self, current_embedding: List[float]) -> Optional[Dict[str, Any]]:
        """
        在缓存中寻找最相似的问答对
        注意：此处为基础实现，遍历 Redis 中的缓存索引。
        在超大规模下，应使用 Redisearch 的 VSS (Vector Similarity Search) 或专属缓存库如 GPTCache。
        """
        try:
            redis = await get_redis()
            # 获取所有缓存的键
            cache_keys = await redis.smembers(self.index_key)
            if not cache_keys:
                return None

            best_match = None
            highest_score = -1.0

            for key in cache_keys:
                # 获取缓存内容
                raw_data = await redis.get(key)
                if not raw_data:
                    # 如果数据已过期，清理索引
                    await redis.srem(self.index_key, key)
                    continue
                
                data = json.loads(raw_data)
                cached_embedding = data.get("embedding")
                
                if not cached_embedding:
                    continue

                # 计算相似度
                score = self._cosine_similarity(current_embedding, cached_embedding)
                
                if score >= self.similarity_threshold and score > highest_score:
                    highest_score = score
                    best_match = data

            if best_match:
                logger.info(f"Semantic Cache Hit! Similarity: {highest_score:.4f}, Original Query: '{best_match.get('query', '')[:20]}...'")
                return best_match
                
            return None
        except Exception as e:
            logger.error(f"Semantic cache retrieval failed: {e}")
            return None

    async def set_cache(self, query: str, embedding: List[float], answer: str, sources: List[Dict[str, Any]] = None):
        """
        将新的问答对存入语义缓存
        """
        try:
            redis = await get_redis()
            
            # 生成唯一键
            query_hash = hashlib.md5(query.encode("utf-8")).hexdigest()
            cache_key = f"{self.cache_prefix}{query_hash}"
            
            data = {
                "query": query,
                "embedding": embedding,
                "answer": answer,
                "sources": sources or []
            }
            
            # 存入数据并设置过期时间
            await redis.setex(cache_key, self.ttl, json.dumps(data, ensure_ascii=False))
            # 将键加入索引集合
            await redis.sadd(self.index_key, cache_key)
            
            logger.info(f"Saved to Semantic Cache: '{query[:20]}...'")
        except Exception as e:
            logger.error(f"Semantic cache saving failed: {e}")

semantic_cache = SemanticCache()
