import logging
import re

import numpy as np

from app.services.embedding_service import embedding_service

logger = logging.getLogger(__name__)

class SemanticChunker:
    """语义分块器：使用 Embedding 相似度在语义断点处切分文本。"""

    def __init__(self, buffer_size: int = 1, threshold_percentile: float = 0.9):
        self.buffer_size = buffer_size
        self.threshold_percentile = threshold_percentile

    async def split_text(self, text: str) -> list[str]:
        if not text or len(text) < 500:
            return [text]

        # 1. 按句子切分 (简单正则表达式)
        # 匹配 中文句号、英文句号、感叹号、问号、换行符
        sentences = re.split(r'(?<=[。！？；\?！\n])', text)
        sentences = [s.strip() for s in sentences if s.strip()]

        if len(sentences) < 5:
            return [text]

        # 2. 组合句子形成“窗口”以便计算上下文语义
        combined_sentences = []
        for i in range(len(sentences)):
            combined = ""
            start = max(0, i - self.buffer_size)
            end = min(len(sentences), i + self.buffer_size + 1)
            combined = " ".join(sentences[start:end])
            combined_sentences.append(combined)

        # 3. 获取所有窗口的 Embedding
        try:
            embeddings = await embedding_service.get_embeddings(combined_sentences)
        except Exception as e:
            logger.error(f"Semantic chunking failed to get embeddings: {e}")
            return [text] # 兜底返回原文本

        # 4. 计算相邻窗口之间的余弦距离 (1 - 相似度)
        distances = []
        for i in range(len(embeddings) - 1):
            emb1 = embeddings[i]
            emb2 = embeddings[i+1]

            # 余弦相似度
            sim = np.dot(emb1, emb2) / (np.linalg.norm(emb1) * np.linalg.norm(emb2))
            distances.append(1 - sim)

        # 5. 寻找切分点 (距离突变的地方)
        if not distances:
            return [text]

        threshold = np.percentile(distances, self.threshold_percentile * 100)
        indices_above_thresh = [i for i, x in enumerate(distances) if x > threshold]

        # 6. 执行切分
        chunks = []
        start_index = 0
        for index in indices_above_thresh:
            chunk = "".join(sentences[start_index : index + 1])
            if len(chunk) > 100: # 避免太小的块
                chunks.append(chunk)
                start_index = index + 1

        # 添加最后一块
        last_chunk = "".join(sentences[start_index:])
        if last_chunk:
            chunks.append(last_chunk)

        # 7. 兜底策略：如果分块太少或太多，进行二次合并或切分
        final_chunks = []
        current_chunk = ""
        for c in chunks:
            if len(current_chunk) + len(c) < 1200: # 合并过小的块
                current_chunk += c
            else:
                if current_chunk:
                    final_chunks.append(current_chunk)
                current_chunk = c
        if current_chunk:
            final_chunks.append(current_chunk)

        return final_chunks

semantic_chunker = SemanticChunker()
