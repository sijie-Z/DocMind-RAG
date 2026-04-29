from typing import List, Tuple

from .vectorizer import HashedVectorizer, cosine_similarity


def retrieve_top_k(query: str,
                   chunks: List[dict],
                   vectorizer: HashedVectorizer,
                   top_k: int = 5) -> List[Tuple[float, dict]]:
    """
    功能：把用户的提问转成向量，与索引中的每个文本块计算相似度，返回前 K 条最相关内容。
    小白解释：就像“找相似的句子”，分数越高越相关，取最前面的几条。
    返回：[(相似度分数, 块对象), ...] 排序好的列表。
    """
    q_vec = vectorizer.transform(query)

    scored: List[Tuple[float, dict]] = []
    for item in chunks:
        score = cosine_similarity(q_vec, item["vector"])
        scored.append((score, item))

    scored.sort(key=lambda x: x[0], reverse=True)
    return scored[:top_k]