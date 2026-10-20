"""语义分块器：使用 Embedding 相似度在语义断点处切分文本，融合条款感知切分策略。"""
import logging
import re

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

# #191：标题硬上限与「正文开始」判据（句读/换行即视为正文开始）
MAX_SECTION_TITLE_CHARS = 50
_TITLE_TERMINATORS_RE = re.compile(r'[。！？；\n]')
# 句读后的条款边界只认「第X条/章/节/款」族（CLAUSE_PATTERNS[0]）——
# 句中的「一、」「1.」列表项不再误开分区。且必须呈**标题形态**（标记后为
# 空白/分隔符/行尾），引用句（如「第7条另有规定的除外」）不开分区。
_SENTENCE_END_RE = re.compile(r'[。！？；]')
_CLAUSE_HEADING_FOLLOW_CHARS = " \t、：:.．）)】。！？；\n"


def _is_clause_heading_at(text: str, pos: int) -> bool:
    """`pos` 起是否为「标题形态」的条款标记（#191 引用句负判）。

    句读后紧跟「第X条」既可能是下一条的开头（标题形态：``第二条 数据定义`` /
    ``第三条：…``），也可能是**正文引用句**（``……。第7条另有规定的除外。``）。
    后者若也开分区，该引用句会成为 chunk 首行，被索引侧「行首定义位」计成
    定义了第 7 条（精确腿误召回的来源）。只接受标记后有空白/分隔符/行尾的形态。
    """
    m = CLAUSE_PATTERNS[0].match(text[pos:pos + 80])
    if not m:
        return False
    rest = text[pos + m.end():pos + 80]
    return (not rest) or rest[0] in _CLAUSE_HEADING_FOLLOW_CHARS


def _match_clause_marker(line: str) -> bool:
    """行首（允许前导空白）是否为条款/章节标记——只判定「是不是」，不做标题截断。

    #191：边界识别与标题提取解耦。修前 `find_clause_boundaries` 依赖
    `detect_section_title` 的返回值；标题一旦真正限长（长正文行返回 None），
    条号与正文同行的行就会失去边界。标记判定独立后两者互不牵连。
    """
    stripped = line.lstrip(" \t")
    return any(pat.match(stripped) for pat in CLAUSE_PATTERNS)


def detect_section_title(line: str) -> str | None:
    """检测一行文本是否为章节标题，返回标题文本（去除标记符号），否则返回 None.

    #191：旧实现先 `stripped[:80]` 再判断 `len(title) <= 80`——护栏恒真，
    条号与正文同行时「标题」= 正文前 80 字，并随 chunk 元数据落库。现在：
    - 只取到首个句读（。！？；）或换行之前——标题不吞正文；
    - 硬上限 `MAX_SECTION_TITLE_CHARS`（50）字；无句读的长行在最近空格处回退截断。
    """
    stripped = line.strip()
    if not stripped or not _match_clause_marker(stripped):
        return None
    terminator = _TITLE_TERMINATORS_RE.search(stripped)
    title = (stripped[:terminator.start()] if terminator else stripped).strip()
    if len(title) > MAX_SECTION_TITLE_CHARS:
        window = title[:MAX_SECTION_TITLE_CHARS]
        space = window.rfind(' ')
        title = (window[:space] if space > 0 else window).strip()
    return title or None


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


def _find_split_point(text: str, max_length: int) -> int:
    """在 max_length 内寻找最近的分割位置（段落 > 句子 > 空格 > 硬切）。

    返回切分位置索引（>0 且 <= max_length）；调用方负责保证剩余文本逐段收缩。
    """
    window = text[:max_length]
    for sep in PARAGRAPH_SEPARATORS:
        pos = window.rfind(sep)
        if pos > max_length * 0.4:
            return pos + len(sep)
    for sep in SENTENCE_SEPARATORS:
        pos = window.rfind(sep)
        if pos > max_length * 0.5:
            return pos + len(sep)
    pos = window.rfind(' ')
    if pos > max_length * 0.7:
        return pos + 1
    return max_length


def _split_overlong_with_spans(text: str, max_length: int) -> list[tuple[str, int, int]]:
    """把超长文本切成连续片段，并返回每段在输入文本中的 ``(起, 止)``。

    #197：切分器必须能回答「这个块在原文的哪一段」，超长块二次切分不能把
    位置信息丢掉。分割点算法与 ``_split_overlong_text`` 完全相同（段落 >
    句子 > 空格 > 硬切），这里只是额外携带偏移。
    """
    pieces: list[tuple[str, int, int]] = []
    remaining = text
    pos = 0
    while len(remaining) > max_length:
        cut = _find_split_point(remaining, max_length)
        pieces.append((remaining[:cut], pos, pos + cut))
        pos += cut
        remaining = remaining[cut:]
    if remaining:
        pieces.append((remaining, pos, pos + len(remaining)))
    return pieces


def _split_overlong_text(text: str, max_length: int) -> list[str]:
    """把超长文本切成多个连续片段（每段 <= max_length，内容零丢失）。

    修复（R3）：此前超长块走 smart_truncate 直接截断，当语义切分未产生
    断点时（重复文本/表格/列表等），整个分区会被截掉一大半且无任何报错。
    """
    return [piece for piece, _start, _end in _split_overlong_with_spans(text, max_length)]


def find_clause_boundaries(text: str) -> list[int]:
    """扫描全文，返回所有条款起始位置的字符偏移列表（含 0）。

    #191：旧实现只认行首条号，且借助 `detect_section_title` 判定——条号不在
    行首时整篇单一分区（``boundaries == [0]``），整篇交给 p90 语义切分。现在：
    - 行首扫描允许前导空白，边界落在标记字符本身；
    - 句读（。！？；）后紧跟「第X条/章/节/款」且呈标题形态（标记后有空白/分隔符/
      行尾）也作为边界——覆盖中文文档「……。第二条 数据定义」的同段写法；
      引用句（「……。第7条另有规定的除外。」）不开分区，避免把正文引用
      变成 chunk 行首定义位；
    - 只依赖条款标记（`_match_clause_marker` / `_is_clause_heading_at`），
      不依赖标题截断结果。
    """
    boundaries: set[int] = {0}
    offset = 0
    for raw_line in text.splitlines(keepends=True):
        stripped = raw_line.lstrip(" \t")
        if stripped and _match_clause_marker(stripped):
            boundaries.add(offset + (len(raw_line) - len(stripped)))
        offset += len(raw_line)
    for m in _SENTENCE_END_RE.finditer(text):
        pos = m.end()
        while pos < len(text) and text[pos] in " \t":
            pos += 1
        if pos < len(text) and _is_clause_heading_at(text, pos):
            boundaries.add(pos)
    return sorted(boundaries)


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

    async def split_text(
        self,
        text: str,
        page_ranges: list[tuple[int, int, int]] | None = None,
    ) -> list[dict]:
        """返回带元数据的 chunks 列表，每个元素为 {"text": str, "metadata": dict}.

        metadata 包含:
        - section_title: 所属章节/条款标题（如有）
        - index: 块序号
        - start_pos / end_pos: 块在输入文本（未 strip 的原文）中的字符区间（#197）
        - page_number: 块起始位置所在页码；仅当调用方提供 ``page_ranges``
          （PDF 解析期记录的 ``(页码, 起偏移, 止偏移)``）时写入，其余格式
          保持 None——没有页概念就不伪造页码（#197）。
        """
        if not text or not text.strip():
            # issue #301 批 D（#28）：空/全空白文本返回**零个** chunk。修前返回
            # [{"text": ""}]——这个空 chunk 会绕过 doc_processor 的"无 chunks_data"
            # 空内容护栏（列表长度为 1），空内容一路向量化并写 INDEXED。
            return []
        if len(text) < 500:
            # 短文本不做语义切分，但仍尝试提取条款标题
            title = detect_section_title(text.strip()[:80]) if text else None
            lead = len(text) - len(text.lstrip())
            return self._assign_page_numbers(
                [{
                    "text": text,
                    "metadata": {
                        "index": 0,
                        "section_title": title,
                        "start_pos": lead,
                        "end_pos": len(text.rstrip()),
                    },
                }],
                page_ranges,
            )

        # 1. 检测条款边界
        clause_boundaries = find_clause_boundaries(text)
        # 2. 先按条款分区，再对每个长分区做语义切分
        partitions: list[tuple[int, int, str | None]] = []  # (start, end, section_title)
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
        for partition_idx, (start, end, section_title) in enumerate(partitions):
            raw_partition = text[start:end]
            partition_text = raw_partition.strip()
            if not partition_text:
                continue
            # #197：strip 会挪动起点，基偏移必须补上前导空白，否则 span 错位
            lead = len(raw_partition) - len(raw_partition.lstrip())
            sub_chunks = await self._semantic_split(
                partition_text, base_offset=start + lead
            )
            for ch in sub_chunks:
                ch["metadata"]["section_title"] = section_title
                ch["metadata"]["index"] = global_idx
                # #191：内部标记——合并只允许发生在同一分区（同一条款/章节）内
                ch["metadata"]["_partition"] = partition_idx
                all_chunks.append(ch)
                global_idx += 1

        # 4. 合并过小的尾块（只限同一分区内），再映射页码（#197）
        return self._assign_page_numbers(self._merge_small_tails(all_chunks), page_ranges)

    @staticmethod
    def _sentence_spans(text: str) -> list[tuple[str, int, int]]:
        """按既有断句口径返回 ``[(句子, 源起, 源止)]``（源偏移追踪，#197）。

        口径与历史实现逐字等价：``re.split(r'(?<=[。！？；\\?！\\n])', text)``
        后逐句 strip、丢空句。这里是同一规则的**定位版本**——每个终止符之后
        断开、句首尾空白剔除——不改变任何切分行为，只多记录每句在原文中的
        区间，供 chunk 的 start_pos/end_pos 与页码映射使用。
        """
        spans: list[tuple[str, int, int]] = []
        cursor = 0
        for match in re.finditer(r'[。！？；\?！\n]', text):
            raw = text[cursor:match.end()]
            sentence = raw.strip()
            if sentence:
                lead = len(raw) - len(raw.lstrip())
                start = cursor + lead
                spans.append((sentence, start, start + len(sentence)))
            cursor = match.end()
        raw = text[cursor:]
        sentence = raw.strip()
        if sentence:
            lead = len(raw) - len(raw.lstrip())
            start = cursor + lead
            spans.append((sentence, start, start + len(sentence)))
        return spans

    @staticmethod
    def _chunk_from_sentences(
        sentences: list[tuple[str, int, int]], first: int, last: int
    ) -> dict | None:
        """由句子区间构造 chunk，并记录「拼接文本 → 源文」的段表（#197）。

        ``_segments`` 为 ``(拼接起, 拼接止, 源起, 源止)`` 列表：块文本是各句
        strip 后的直接拼接（句间空白被丢弃），线性偏移不能直接当源偏移用；
        段表让超长块二次切分后的每一片仍能精确投影回原文。

        区间为空（provider 返回的向量数多于窗口数时，split index 可能越界）
        返回 None——与旧实现「切片为空串 → 跳过」等价，不得抛 IndexError。
        """
        parts = sentences[first:last + 1]
        if not parts:
            return None
        segments: list[tuple[int, int, int, int]] = []
        cursor = 0
        for sentence, s_start, s_end in parts:
            segments.append((cursor, cursor + len(sentence), s_start, s_end))
            cursor += len(sentence)
        return {
            "text": "".join(sentence for sentence, _s, _e in parts),
            "metadata": {
                "start_pos": segments[0][2],
                "end_pos": segments[-1][3],
                "_segments": segments,
            },
        }

    @staticmethod
    def _map_concat_offset(segments: list[tuple[int, int, int, int]], offset: int) -> int:
        """拼接文本偏移 → 源文偏移；恰在段边界时归入后一段。"""
        for c_start, c_end, s_start, _s_end in segments:
            if c_start <= offset < c_end:
                return s_start + (offset - c_start)
        return segments[-1][3] if segments else offset

    async def _semantic_split(self, text: str, base_offset: int = 0) -> list[dict]:
        """对一段文本做基于 Embedding 余弦距离的语义切分。

        ``base_offset``：该段文本首字符在整篇原文中的偏移（#197）。返回的
        每个 chunk 都带 ``start_pos``/``end_pos``（整篇坐标）。
        """
        # 1. 按句子初步切分（带源偏移）
        sentences = self._sentence_spans(text)

        if len(sentences) < 5:
            return [{
                "text": text,
                "metadata": {
                    "start_pos": base_offset,
                    "end_pos": base_offset + len(text),
                },
            }]

        # 2. 构造滑动窗口
        combined_sentences = []
        for i in range(len(sentences)):
            start = max(0, i - self.buffer_size)
            end = min(len(sentences), i + self.buffer_size + 1)
            combined_sentences.append(" ".join(sentences[j][0] for j in range(start, end)))

        # 性能加固：限制单段 embedding 预算，防止万句级文档产生上千次
        # embedding 请求拖垮解析链路（超预算时按长度切分回退）。
        max_windows = 120
        if len(combined_sentences) > max_windows:
            logger.warning(
                f"Semantic split window count {len(combined_sentences)} exceeds "
                f"budget {max_windows}, falling back to size-based split"
            )
            # 按近似字符数均分
            mid = len(text) // 2
            left = text[:mid].rstrip()
            right_raw = text[mid:]
            right = right_raw.lstrip()
            out = []
            if left:
                out.append({
                    "text": left,
                    "metadata": {
                        "start_pos": base_offset,
                        "end_pos": base_offset + len(left),
                    },
                })
            if right:
                lead = len(right_raw) - len(right)
                right_start = mid + lead
                out.append({
                    "text": right,
                    "metadata": {
                        "start_pos": base_offset + right_start,
                        "end_pos": base_offset + right_start + len(right),
                    },
                })
            return out or [{
                "text": text,
                "metadata": {
                    "start_pos": base_offset,
                    "end_pos": base_offset + len(text),
                },
            }]

        # 3. 获取 Embedding
        try:
            embeddings = await embedding_service.get_embeddings(combined_sentences)
        except Exception as e:
            logger.error(f"Semantic chunking embedding 获取失败: {e}")
            return [{
                "text": text,
                "metadata": {
                    "start_pos": base_offset,
                    "end_pos": base_offset + len(text),
                },
            }]

        # 4. 计算相邻窗口余弦距离
        distances = []
        for i in range(len(embeddings) - 1):
            sim = np.dot(embeddings[i], embeddings[i + 1]) / (
                np.linalg.norm(embeddings[i]) * np.linalg.norm(embeddings[i + 1]) + 1e-12
            )
            distances.append(1 - sim)

        if not distances:
            return [{
                "text": text,
                "metadata": {
                    "start_pos": base_offset,
                    "end_pos": base_offset + len(text),
                },
            }]

        # 5. 找语义断点
        threshold = np.percentile(distances, self.threshold_percentile * 100)
        split_indices = [i for i, d in enumerate(distances) if d > threshold]

        # 6. 执行切分（记录句子区间与源偏移）
        raw_chunks: list[dict] = []
        start_idx = 0
        for idx in split_indices:
            candidate = self._chunk_from_sentences(sentences, start_idx, idx)
            # None：split index 越界（provider 向量数多于窗口数）——跳过，
            # 与旧实现的空切片行为一致。
            if candidate is not None and len(candidate["text"]) > 100:
                raw_chunks.append(candidate)
                start_idx = idx + 1

        if start_idx < len(sentences):
            raw_chunks.append(
                self._chunk_from_sentences(sentences, start_idx, len(sentences) - 1)
            )

        if not raw_chunks:
            return [{
                "text": text,
                "metadata": {
                    "start_pos": base_offset,
                    "end_pos": base_offset + len(text),
                },
            }]

        # 7. 二次合并过小的块（段表随合并平移，保全偏移信息）
        merged_chunks: list[dict] = []
        current: dict | None = None
        for ch in raw_chunks:
            if (
                current is not None
                and len(current["text"]) + len(ch["text"]) < self.chunk_max_chars
            ):
                offset = len(current["text"])
                current["text"] += ch["text"]
                current["metadata"]["end_pos"] = ch["metadata"]["end_pos"]
                current["metadata"]["_segments"].extend(
                    (c_start + offset, c_end + offset, s_start, s_end)
                    for c_start, c_end, s_start, s_end
                    in ch["metadata"]["_segments"]
                )
            else:
                if current is not None:
                    merged_chunks.append(current)
                current = ch
        if current is not None:
            merged_chunks.append(current)

        # 8. 超长块连续切分（修复 R3：不再截断；保证内容不丢失）
        #    #197：每一片按段表投影回源文偏移，start_pos/end_pos 不得丢失。
        split_chunks: list[dict] = []
        for ch in merged_chunks:
            if len(ch["text"]) <= self.chunk_max_chars:
                split_chunks.append({
                    "text": ch["text"],
                    "metadata": {
                        "start_pos": base_offset + ch["metadata"]["start_pos"],
                        "end_pos": base_offset + ch["metadata"]["end_pos"],
                    },
                })
                continue
            segments = ch["metadata"]["_segments"]
            for piece, piece_start, piece_end in _split_overlong_with_spans(
                ch["text"], self.chunk_max_chars
            ):
                src_start = self._map_concat_offset(segments, piece_start)
                src_end = self._map_concat_offset(segments, piece_end - 1) + 1
                split_chunks.append({
                    "text": piece,
                    "metadata": {
                        "start_pos": base_offset + src_start,
                        "end_pos": base_offset + src_end,
                    },
                })

        return split_chunks

    @staticmethod
    def _assign_page_numbers(
        chunks: list[dict], page_ranges: list[tuple[int, int, int]] | None
    ) -> list[dict]:
        """按 chunk 起始偏移映射页码（#197）；未提供页信息时保持 None。

        ``page_ranges`` 为 ``(页码, 起偏移, 止偏移)``（解析期按页拼接顺序记录，
        区间连续）；页码取起始位置所在页（跨页块归起始页）。偏移超出所有区间
        （调用方传入不完整/非连续页信息）时回退到最近的前一页，否则为 None。
        """
        if not page_ranges or not chunks:
            return chunks
        ranges = sorted(page_ranges, key=lambda item: item[1])
        for ch in chunks:
            metadata = ch.setdefault("metadata", {})
            start = metadata.get("start_pos")
            if start is None:
                continue
            page: int | None = None
            for page_no, p_start, p_end in ranges:
                if p_start > start:
                    break
                page = page_no
                if start < p_end:
                    break
            metadata["page_number"] = page
        return chunks

    def _merge_small_tails(self, chunks: list[dict]) -> list[dict]:
        """把过小的尾块（< 200 字符）合并到**同一分区**的前一块。

        #191：修前对扁平化后的全部 chunk 无差别合并——短「第二条」会被并进上一条
        的分区；且只有前块**无标题**时才继承标题，前块有标题时被并正文永久留在
        前一块的 section_title 下（跨条合并 + 标题错配）。现在只在同一分区
        （同一条款/章节，即同一次 `split_text` 分区）内合并；跨分区小尾块保留
        独立——宁可短块，不可错标题。`_partition` 是内部标记，合并后从产物中剔除。

        #197：合并后前块的 ``end_pos`` 必须延伸到尾块末尾——否则该块的源文
        区间会少掉被并进来的正文。
        """
        if len(chunks) <= 1:
            for ch in chunks:
                ch.get("metadata", {}).pop("_partition", None)
            return chunks
        result: list[dict] = []
        for ch in chunks:
            metadata = ch.get("metadata", {}) or {}
            prev_meta = result[-1].get("metadata", {}) if result else {}
            same_partition = (
                bool(result) and prev_meta.get("_partition") == metadata.get("_partition")
            )
            if result and len(ch["text"]) < 200 and same_partition:
                result[-1]["text"] += ch["text"]
                if metadata.get("end_pos") is not None:
                    prev_meta["end_pos"] = metadata["end_pos"]
                if metadata.get("section_title") and not prev_meta.get("section_title"):
                    prev_meta["section_title"] = metadata["section_title"]
            else:
                result.append(ch)
        for ch in result:
            ch.get("metadata", {}).pop("_partition", None)
        return result


semantic_chunker = SemanticChunker()
