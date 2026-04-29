from typing import List, Tuple, Dict, Any, Optional
from dataclasses import dataclass

from .vectorizer import HashedVectorizer, cosine_similarity
from .bm25 import BM25, BM25Index


def hybrid_retrieve(query: str,
                    chunks: List[dict],
                    vectorizer: HashedVectorizer,
                    top_k: int = 5,
                    alpha: float = 0.6) -> List[Tuple[float, dict]]:
    """
    功能：关键词（BM25）+语义（余弦相似度）的混合检索。
    小白解释：
    - 语义搜索更“理解意思”，关键词搜索更“精准匹配”；
    - 用加权融合（alpha）把二者合成综合分数，取最相关的前 K 条。

    参数：
    - alpha：语义分数权重（0~1），越大越看重语义；1-alpha 就是关键词权重。
    返回：[(综合分数, 块对象), ...]
    """
    texts = [c["text"] for c in chunks]
    bm25 = BM25(texts)

    # BM25 得分（归一化）
    bm_scores = bm25.score(query)
    max_bm = bm_scores[0][0] if bm_scores else 1.0
    bm_map: Dict[int, float] = {idx: (s / max_bm if max_bm > 0 else 0.0) for s, idx in bm_scores}

    # 语义相似度
    q_vec = vectorizer.transform(query)
    sem_scores: List[Tuple[float, int]] = []
    for i, item in enumerate(chunks):
        sem_scores.append((cosine_similarity(q_vec, item["vector"]), i))

    # 融合分数
    fused: List[Tuple[float, dict]] = []
    for sem, i in sem_scores:
        bm = bm_map.get(i, 0.0)
        score = alpha * sem + (1 - alpha) * bm
        fused.append((score, chunks[i]))

    fused.sort(key=lambda x: x[0], reverse=True)
    return fused[:top_k]


@dataclass
class SearchResult:
    """搜索结果"""
    chunk: Any  # DocumentChunk对象
    score: float
    relevance: str
    highlight: List[str]


class HybridRetriever:
    """
    混合检索器 - 结合BM25和向量相似度
    小白解释：这是一个超级聪明的"图书管理员"，既能理解你的问题意思，又能精准匹配关键词
    """
    
    def __init__(self, vector_store=None, bm25_index=None, alpha: float = 0.6):
        self.vector_store = vector_store
        self.bm25_index = bm25_index or BM25Index()
        self.alpha = alpha  # 向量相似度权重
        self.vectorizer = HashedVectorizer(dim=768)
        
    def search(self, query: str, top_k: int = 10, search_type: str = "hybrid") -> List[SearchResult]:
        """
        混合搜索
        小白解释：同时用两种方式找答案，然后综合评分，选出最好的
        """
        if search_type == "semantic":
            return self._semantic_search(query, top_k)
        elif search_type == "keyword":
            return self._keyword_search(query, top_k)
        else:  # hybrid
            return self._hybrid_search(query, top_k)
    
    def _semantic_search(self, query: str, top_k: int) -> List[SearchResult]:
        """语义搜索"""
        if not self.vector_store:
            return []
        
        # 生成查询向量
        query_vector = self.vectorizer.transform(query)
        
        # 向量搜索
        results = self.vector_store.search([query_vector], k=top_k)
        
        # 格式化结果
        search_results = []
        for result in results[0]:
            # 这里需要创建DocumentChunk对象，简化处理
            search_results.append(SearchResult(
                chunk=type('DocumentChunk', (), {
                    'id': result['id'],
                    'content': result['metadata'].get('content', ''),
                    'source': result['metadata'].get('source', ''),
                    'page': result['metadata'].get('page', 1),
                    'metadata': result['metadata']
                })(),
                score=result['score'],
                relevance="high" if result['score'] > 0.7 else "medium" if result['score'] > 0.3 else "low",
                highlight=self._extract_highlights(result['metadata'].get('content', ''), query)
            ))
        
        return search_results
    
    def _keyword_search(self, query: str, top_k: int) -> List[SearchResult]:
        """关键词搜索"""
        if not self.bm25_index:
            return []
        
        # BM25搜索
        results = self.bm25_index.search(query, top_k)
        
        # 格式化结果
        search_results = []
        for result in results:
            search_results.append(SearchResult(
                chunk=type('DocumentChunk', (), {
                    'id': result['doc_id'],
                    'content': result['content'],
                    'source': 'unknown',
                    'page': 1,
                    'metadata': {'score': result['score']}
                })(),
                score=result['score'],
                relevance="high" if result['score'] > 0.5 else "medium" if result['score'] > 0.2 else "low",
                highlight=self._extract_highlights(result['content'], query)
            ))
        
        return search_results
    
    def _hybrid_search(self, query: str, top_k: int) -> List[SearchResult]:
        """混合搜索"""
        # 获取语义搜索结果
        semantic_results = self._semantic_search(query, top_k * 2)
        
        # 获取关键词搜索结果
        keyword_results = self._keyword_search(query, top_k * 2)
        
        # 合并结果并重新排序
        combined_results = {}
        
        # 添加语义搜索结果
        for result in semantic_results:
            chunk_id = result.chunk.id
            if chunk_id not in combined_results:
                combined_results[chunk_id] = {
                    'chunk': result.chunk,
                    'semantic_score': result.score,
                    'keyword_score': 0.0,
                    'relevance': result.relevance,
                    'highlight': result.highlight
                }
            else:
                combined_results[chunk_id]['semantic_score'] = max(combined_results[chunk_id]['semantic_score'], result.score)
        
        # 添加关键词搜索结果
        for result in keyword_results:
            chunk_id = result.chunk.id
            if chunk_id not in combined_results:
                combined_results[chunk_id] = {
                    'chunk': result.chunk,
                    'semantic_score': 0.0,
                    'keyword_score': result.score,
                    'relevance': result.relevance,
                    'highlight': result.highlight
                }
            else:
                combined_results[chunk_id]['keyword_score'] = max(combined_results[chunk_id]['keyword_score'], result.score)
        
        # 计算综合分数并排序
        final_results = []
        for chunk_id, data in combined_results.items():
            # 综合分数 = α * 语义分数 + (1-α) * 关键词分数
            combined_score = self.alpha * data['semantic_score'] + (1 - self.alpha) * data['keyword_score']
            
            final_results.append(SearchResult(
                chunk=data['chunk'],
                score=combined_score,
                relevance=data['relevance'],
                highlight=data['highlight']
            ))
        
        # 按分数排序并返回top_k
        final_results.sort(key=lambda x: x.score, reverse=True)
        return final_results[:top_k]
    
    def _extract_highlights(self, content: str, query: str) -> List[str]:
        """提取高亮片段"""
        # 简单的关键词高亮
        import re
        
        # 提取查询关键词
        keywords = re.findall(r'\w+', query.lower())
        
        highlights = []
        content_lower = content.lower()
        
        for keyword in keywords:
            if len(keyword) > 2:  # 只考虑长度大于2的关键词
                start = content_lower.find(keyword)
                if start != -1:
                    # 提取关键词周围的上下文
                    context_start = max(0, start - 30)
                    context_end = min(len(content), start + len(keyword) + 30)
                    highlight = content[context_start:context_end]
                    
                    # 高亮关键词
                    highlighted = highlight.replace(keyword, f"**{keyword}**")
                    highlights.append(highlighted)
        
        return highlights[:3]  # 最多返回3个高亮片段