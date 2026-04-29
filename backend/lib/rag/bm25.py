import math
from collections import Counter, defaultdict
from typing import List, Dict, Tuple, Optional, Any


class BM25:
    """
    功能：对文本块做关键词评分（BM25），用来和语义相似度做“混合检索”。
    小白解释：关键词匹配越好，分数越高；长文档做长度归一，常见词做降权。
    """

    def __init__(self, docs: List[str], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.docs = docs

        self.doc_term_freqs: List[Counter] = [Counter(self._tokenize(d)) for d in docs]
        self.doc_lengths: List[int] = [sum(freq.values()) for freq in self.doc_term_freqs]
        self.avg_dl: float = (sum(self.doc_lengths) / len(self.docs)) if self.docs else 0.0

        # 文档频率（有多少文档出现过该词）
        self.df: Dict[str, int] = defaultdict(int)
        for freq in self.doc_term_freqs:
            for term in freq.keys():
                self.df[term] += 1

        self.N = len(self.docs)

    def _tokenize(self, text: str) -> List[str]:
        # 简单英文/数字分词；中文可以直接按字符或引入更好的分词器（后续升级）
        import re
        toks = re.split(r"[^a-zA-Z0-9]+", text.lower())
        return [t for t in toks if t]

    def _idf(self, term: str) -> float:
        df = self.df.get(term, 0)
        if df == 0:
            # 未出现过的词给一个低 idf
            return math.log((self.N + 0.5) / 0.5)
        return math.log((self.N - df + 0.5) / (df + 0.5) + 1)

    def score(self, query: str) -> List[Tuple[float, int]]:
        """
        对每个文档（文本块）计算 BM25 分数。
        返回：[(分数, 文档索引), ...]
        """
        q_terms = self._tokenize(query)
        result: List[Tuple[float, int]] = []
        for i, freq in enumerate(self.doc_term_freqs):
            dl = self.doc_lengths[i]
            score = 0.0
            for t in q_terms:
                tf = freq.get(t, 0)
                idf = self._idf(t)
                denom = tf + self.k1 * (1 - self.b + self.b * (dl / (self.avg_dl or 1.0)))
                score += idf * (tf * (self.k1 + 1)) / (denom or 1.0)
            result.append((score, i))
        result.sort(key=lambda x: x[0], reverse=True)
        return result


class BM25Index:
    """
    BM25索引管理器
    支持动态添加文档和搜索
    小白解释：这是一个智能的"关键词图书管理员"，能快速找到包含特定关键词的文档
    """
    
    def __init__(self, k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.documents: List[str] = []
        self.doc_ids: List[str] = []
        self.bm25: Optional[BM25] = None
        self.is_dirty = True  # 标记是否需要重建索引
        
    def add_document(self, doc_id: str, content: str):
        """添加文档到索引"""
        self.documents.append(content)
        self.doc_ids.append(doc_id)
        self.is_dirty = True
        
    def add_documents(self, doc_ids: List[str], contents: List[str]):
        """批量添加文档"""
        self.documents.extend(contents)
        self.doc_ids.extend(doc_ids)
        self.is_dirty = True
        
    def search(self, query: str, top_k: int = 10) -> List[Dict[str, Any]]:
        """搜索文档"""
        if not self.documents:
            return []
            
        # 如果需要重建索引
        if self.is_dirty:
            self.bm25 = BM25(self.documents, self.k1, self.b)
            self.is_dirty = False
        
        # 执行搜索
        scores = self.bm25.score(query)
        
        # 格式化结果
        results = []
        for score, doc_idx in scores[:top_k]:
            if score > 0:  # 只返回有分数的结果
                results.append({
                    "doc_id": self.doc_ids[doc_idx],
                    "score": float(score),
                    "content": self.documents[doc_idx][:200] + "..." if len(self.documents[doc_idx]) > 200 else self.documents[doc_idx]
                })
        
        return results
    
    def get_stats(self) -> Dict[str, Any]:
        """获取索引统计"""
        return {
            "total_documents": len(self.documents),
            "is_index_built": self.bm25 is not None,
            "is_dirty": self.is_dirty,
            "k1": self.k1,
            "b": self.b
        }