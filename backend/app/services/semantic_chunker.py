"""语义分块器：使用 Embedding 相似度在语义断点处切分文本，融合条款感知切分策略。"""
import logging
import re
from typing import Optional

import numpy as np

from app.services.embedding_service import embedding_service

logger = logging.getLogger(__name__)


# ── 中文政策/法规/制度文档的常见条款标记 ──────────────────────────
CLAUSE_PATTERNS: list[re.Pattern] = [
    re.compile(r'^第[一二三四五六七八九十百\d]+[条章节款]'),   # 第一条 / 第二章 / 第三节 / 第X款
    re.compile(r'^[一二三四五六七八九十百]+[、．\.]'),          # 一、 / 二． / 三、
    re.compile(r'^\（[一二三四五六七八九十百]+\）'),            # （一）（二）
    re.compile(r'^\([一二三四五六七八九十百]+\)'),              # (一)(二)
    re.compile(r'^\d+[、．\.]'),                                # 1. 2、 3．
    re.compile(r'^\(\d+\)'),                                    # (1) (2)
    re.compile(r'^【.*?】'),                                    # 【通知】【办法】
    re.compile(r'^[A-Z]\.'),                                    # A. B.
]

# 段落分隔符（按优先级）
PARAGRAPH_SEPARATORS = ['\n\n\n', '\n\n', '\r\n\r\n']
SENTENCE_SEPARATORS = ['。', '！', '？', '；', '\n']


def detect_section_title(line: str) -> Optional[str]:
    """检测一行文本是否为章节标题，返回标题文本（去除标记符号），否则返回 None."""
    stripped = line.strip()
    for pat in CLAUSE_PATTERNS:
        m = pat.match(stripped)
        if m:
            # 标题字数不超过 50（过长就不是标题了）
            title = stripped[:80].strip()
            if len(title) <= 80:
                return title
    return None


def smart_truncate(text: str, max_length: int) -> str:
    """在段落/句子边界处智能截断，保持语义完整。"""
    if len(text) <= max_length:
        return text

    candidates: list[tuple[int, int]] = []  # (position, priority)  priority: 低=优先
    truncated = text[:max_length]

    # 优先级 1: 段落分隔符
    for sep in PARAGRAPH_SEPARATORS:
        pos = truncated.rfind(sep)
        if pos > max_length * 0.4:
            candidates.append((pos + len(sep), 1))

    # 优先级 2: 句子分隔符
    for sep in SENTENCE_SEPARATORS:
        pos = truncated.rfind(sep)
        if pos > max_length * 0.5:
            candidates.append((pos + len(sep), 2))

    # 优先级 3: 空格
    pos = truncated.rfind(' ')
    if pos > max_length * 0.7:
        candidates.append((pos + 1, 3))

    if candidates:
        candidates.sort(key=lambda x: (x[1], -x[0]))  # 低优先级优先，同优先级取最靠后的
        best_pos = candidates[0][0]
        return text[:best_pos].strip()

    return truncated.strip()


def find_clause_boundaries(text: str) -> list[int]:
    """扫描全文，返回所有条款起始位置的字符偏移列表（含 0）。"""
    boundaries = [0]
    for m in re.finditer(r'^', text, re.MULTILINE):
        line_start = m.start()
        line = text[line_start:line_start + 80]
        if detect_section_title(line):
            if line_start not in boundaries:
                boundaries.append(line_start)
    boundaries.sort()
    return boundaries


def assign_section_titles(chunks: list[dict]) -> list[dict]:
    """为每个 chunk 分配最近的 section_title（条款标题）。"""
    sections: list[tuple[int, str]] = []  # (chunk_index, title)
    for i, ch in enumerate(chunks):
        raw = ch.get("original_text") or ch.get("text", "")
        first_line = raw.strip().split("\n")[0][:80]
        title = detect_section_title(first_line)
        if title:
            sections.append((i, title))

    if not sections:
        return chunks

    for i, ch in enumerate(chunks):
        # 找最近的前一个 section
        candidate_title = None
        for idx, title in reversed(sections):
            if idx <= i:
                candidate_title = title
                break
        if candidate_title and "metadata" in ch:
            ch["metadata"]["section_title"] = candidate_title

    return chunks


class SemanticChunker:
    """语义分块器：使用 Embedding 相似度在语义断点处切分文本。

    融合两种切分策略：
    - 条款感知切分：优先在条款标记（第一条、一、、（一）等）处作为切分候选
    - 语义切分：在条款内使用 Embedding 余弦距离检测语义断点
    """

    def __init__(self, buffer_size: int = 1, threshold_percentile: float = 0.9, chunk_max_chars: int = 1200):
        self.buffer_size = buffer_size
        self.threshold_percentile = threshold_percentile
        self.chunk_max_chars = chunk_max_chars

    async def split_text(self, text: str) -> list[dict]:
        """返回带元数据的 chunks 列表，每个元素为 {"text": str, "metadata": dict}.

        metadata 包含:
        - section_title: 所属章节/条款标题（如有）
        - index: 块序号
        """
        if not text or len(text) < 500:
            # 短文本不做语义切分，但仍尝试提取条款标题
            title = detect_section_title(text.strip()[:80]) if text else None
            return [{"text": text, "metadata": {"index": 0, "section_title": title}}]

        # 1. 检测条款边界
        clause_boundaries = find_clause_boundaries(text)
        # 2. 先按条款分区，再对每个长分区做语义切分
        partitions: list[tuple[int, int, Optional[str]]] = []  # (start, end, section_title)
        sorted_bounds = clause_boundaries
        if 0 not in sorted_bounds:
            sorted_bounds.insert(0, 0)
        for idx, start in enumerate(sorted_bounds):
            end = sorted_bounds[idx + 1] if idx + 1 < len(sorted_bounds) else len(text)
            line = text[start:start + 80]
            title = detect_section_title(line)
            partitions.append((start, end, title))

        if not partitions:
            partitions = [(0, len(text), None)]

        # 3. 每个分区独立做语义切分
        all_chunks: list[dict] = []
        global_idx = 0
        for start, end, section_title in partitions:
            partition_text = text[start:end].strip()
            if not partition_text:
                continue
            sub_chunks = await self._semantic_split(partition_text)
            for ch in sub_chunks:
                ch["metadata"]["section_title"] = section_title
                ch["metadata"]["index"] = global_idx
                all_chunks.append(ch)
                global_idx += 1

        # 4. 合并过小的尾块
        merged = self._merge_small_tails(all_chunks)

        return merged

    async def _semantic_split(self, text: str) -> list[dict]:
        """对一段文本做基于 Embedding 余弦距离的语义切分。"""
        # 1. 按句子初步切分
        sentences = re.split(r'(?<=[。！？；\?！\n])', text)
        sentences = [s.strip() for s in sentences if s.strip()]

        if len(sentences) < 5:
            return [{"text": text, "metadata": {}}]

        # 2. 构造滑动窗口
        combined_sentences = []
        for i in range(len(sentences)):
            start = max(0, i - self.buffer_size)
            end = min(len(sentences), i + self.buffer_size + 1)
            combined_sentences.append(" ".join(sentences[start:end]))

        # 3. 获取 Embedding
        try:
            embeddings = await embedding_service.get_embeddings(combined_sentences)
        except Exception as e:
            logger.error(f"Semantic chunking embedding 获取失败: {e}")
            return [{"text": text, "metadata": {}}]

        # 4. 计算相邻窗口余弦距离
        distances = []
        for i in range(len(embeddings) - 1):
            sim = np.dot(embeddings[i], embeddings[i + 1]) / (
                np.linalg.norm(embeddings[i]) * np.linalg.norm(embeddings[i + 1]) + 1e-12
            )
            distances.append(1 - sim)

        if not distances:
            return [{"text": text, "metadata": {}}]

        # 5. 找语义断点
        threshold = np.percentile(distances, self.threshold_percentile * 100)
        split_indices = [i for i, d in enumerate(distances) if d > threshold]

        # 6. 执行切分
        raw_chunks: list[dict] = []
        start_idx = 0
        for idx in split_indices:
            chunk_text = "".join(sentences[start_idx: idx + 1])
            if len(chunk_text) > 100:
                raw_chunks.append({"text": chunk_text, "metadata": {}})
                start_idx = idx + 1

        remaining = "".join(sentences[start_idx:])
        if remaining:
            raw_chunks.append({"text": remaining, "metadata": {}})

        if not raw_chunks:
            return [{"text": text, "metadata": {}}]

        # 7. 二次合并过小的块
        merged_chunks: list[dict] = []
        current_text = ""
        for ch in raw_chunks:
            if len(current_text) + len(ch["text"]) < self.chunk_max_chars:
                current_text += ch["text"]
            else:
                if current_text:
                    merged_chunks.append({"text": current_text, "metadata": {}})
                current_text = ch["text"]
        if current_text:
            merged_chunks.append({"text": current_text, "metadata": {}})

        # 8. 智能截断超长块
        for ch in merged_chunks:
            if len(ch["text"]) > self.chunk_max_chars:
                ch["text"] = smart_truncate(ch["text"], self.chunk_max_chars)

        return merged_chunks

    def _merge_small_tails(self, chunks: list[dict]) -> list[dict]:
        """把过小的尾块（< 200 字符）合并到前一块。"""
        if len(chunks) <= 1:
            return chunks
        result = []
        for ch in chunks:
            if result and len(ch["text"]) < 200:
                result[-1]["text"] += ch["text"]
                if ch["metadata"].get("section_title") and not result[-1]["metadata"].get("section_title"):
                    result[-1]["metadata"]["section_title"] = ch["metadata"]["section_title"]
            else:
                result.append(ch)
        return result


semantic_chunker = SemanticChunker()
