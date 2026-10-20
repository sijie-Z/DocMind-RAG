"""Hybrid retriever — keyword + vector search with RRF fusion."""
import asyncio
import hashlib
import logging
import math
import re
import time
import unicodedata
from datetime import datetime

from app.core.config import settings
from app.core.elasticsearch import ElasticsearchTools
from app.core.prometheus import RAG_ADAPTIVE_STRATEGY, RAG_QUERY_REWRITE_TOTAL
from app.rag.context_compressor import compress
from app.rag.errors import EmbeddingUnavailableError
from app.rag.query_processor import (
    SEARCH_MODE_TO_STRATEGY,
    QueryIntentClassifier,
    extract_query_terms,
    generate_hyde_doc,
    resolve_retrieval_strategy,
    rewrite_query_candidates,
    rewrite_query_llm,
)

logger = logging.getLogger(__name__)

# ---- #208 Phase 2 决策 b：minimum_should_match 口径（剔除《…》文档全称 token） ----

# 《…》文档全称跨度（查询里引用文档标题的写法）。
_TITLE_SPAN_RE = re.compile(r"《[^《》]+》")
# cjk 分析器的 CJK 段：Han / 假名 / 谚文（approximation，见 _count_tokens_cjk）。
_CJK_RUN_RE = re.compile("[぀-ヿ㐀-䶿一-鿿豈-﫿가-힯]+")
# 标准分词器的拉丁/数字词（下划线、小数点、千分位逗号粘连为单 token）。
_ASCII_TOKEN_RE = re.compile(r"[a-z0-9_]+(?:[.,][a-z0-9_]+)*")


def _count_tokens_cjk(text: str) -> int:
    """近似 ES cjk 分析器（standard tokenizer + CJK bigram + lowercase）的 token 数。

    仅用于 #208 Phase 2 决策 b 的 minimum_should_match 基数（不改变查询文本）。
    已与 ES `_analyze`（评估索引，analyzer=cjk）逐条校准（证据见 PR）。
    已知近似边界：ASCII 停用词不剔除（偏差 ≤1 个 token 量级）、未建模 per-field
    分析器差异（基数按 content 形态的 cjk 计数）。
    """
    t = unicodedata.normalize("NFKC", text or "").lower()
    n = 0
    for run in _CJK_RUN_RE.findall(t):
        # 单字 CJK 段保留为单 token；≥2 字段产出相邻 bigram（n-1 个）。
        n += 1 if len(run) == 1 else len(run) - 1
    n += len(_ASCII_TOKEN_RE.findall(t))
    return n


def _minimum_should_match_for(query_text: str) -> int | str:
    """multi_match 的 minimum_should_match 取值（#208 Phase 2 决策 b：口径变、常量不变）。

    30% 常量不变，基数剔除《…》文档全称 token——标题 bigram 此前把查询 token 基数
    抬高（实验实测：标题贡献 8 个 bigram，基数 7 → 15，30% 阈值随之从 2 抬到 4），
    只命中内容词的 gold 因此被整条剪掉。无《…》全称时返回 "30%"（与剔除口径等价，
    不改动非全称查询）。返回值：整数（剔除标题后按 30% 向下取整、下限 1）或 "30%"。
    """
    if not query_text or not _TITLE_SPAN_RE.search(query_text):
        return "30%"
    stripped = _TITLE_SPAN_RE.sub(" ", query_text)
    base = _count_tokens_cjk(stripped)
    return max(1, base * 30 // 100)


# ---- #208 Phase 2 环 C：keyword 臂语料术语表（corpus-derived glossary） ----

# 由 `backend/evals/corpus/` 的 bigram 统计按冻结规则 FR-1 推得的前 16 名
# （2026-10-09 预注册，脚本 `_orchestrate/round2/_ringc_glossary_stats.py`，
# 完整排序表 `_ringc_glossary_table.tsv`）：
#   score(bigram) = body_count + 0.3 × heading_count（标题位折扣 0.3，
#   heading = 行首 `#` 标题行；计数按出现次数、重叠窗口；排序 score↓→body↓→heading↓→码点↑；
#   取前 16）。规则只用语料数据推表——不引用评测集（golden set）、不针对任何具体查询。
# 机制 3 实测（PR #325 前）：keyword 臂按查询序截前 12 项，带《…》全称的查询里
# 标题 bigram 占 8 位，内容 bigram 被截断出列表、overlap 分子结构性归零；
# 环 C：改为「语料表成员优先 + 上限 16」（见 _keyword_arm_terms）。
_CORPUS_GLOSSARY: tuple[str, ...] = (
    "文档", "数据", "知识", "组织", "故障", "识库", "告警", "恢复",
    "服务", "的文", "负责", "响应", "成员", "演练", "上传", "保留",
)

# keyword 臂术语表上限（机制 3 旧值 12；环 C 起 16）。
_KEYWORD_TERM_CAP = 16


def _keyword_arm_terms(query: str) -> list[str]:
    """keyword 臂术语选择（#208 Phase 2 环 C）：语料术语表优先 + 上限 16。

    把查询的全部术语（不先行截断）按「是否属于语料术语表 `_CORPUS_GLOSSARY`」分两档
    ——表内成员（按查询序）在前、其余（按查询序）在后——再截到 ≤ `_KEYWORD_TERM_CAP`。
    表由语料统计（FR-1）推得，与评测集无关；选择规则不引用任何具体查询。
    查询术语 ≤12 项时与旧口径的集合相同（仅排序不同，overlap 判定与序无关）；
    仅用于 keyword 臂 post-process 的 overlap/术语匹配口径——`extract_query_terms`
    对其它调用方（context_compressor / agent core_tools）的默认行为保持不变。
    """
    raw = extract_query_terms(query, limit=None)
    in_glossary = [t for t in raw if t in _CORPUS_GLOSSARY]
    rest = [t for t in raw if t not in _CORPUS_GLOSSARY]
    return (in_glossary + rest)[:_KEYWORD_TERM_CAP]


# ---- issue #192：wildcard 子句术语（只保留拉丁/数字词；CJK 不生成通配项） ----

# AND 连接的通配项上限：控制 wildcard 查询代价与该子句的满足难度
# （它是可选 should，项数不影响整体召回下限）。
_WILDCARD_TERM_CAP = 8

# 术语白名单（= extract_query_terms 拉丁支路的产出域 [a-z0-9]），
# 同时保证引号/冒号/通配符等 query_string 语法不会进入查询体。
_WILDCARD_SAFE_TERM_RE = re.compile(r"[a-z0-9]+")


def _wildcard_terms(query: str) -> list[str]:
    """keyword 臂 wildcard should 子句的术语列表（issue #192）。

    旧实现 ``[f"*{t}*" for t in q_text.split() if len(t) < 20]``：中文没有空格，
    ``split()`` 对中文查询只产出**一个** token（整句），``*《…》第七条是什么*``
    永不命中任何索引词项——wildcard 子句对中文恒空转，还让排查者误以为
    「查询做了额外匹配」。现复用既有 ``extract_query_terms``（与应用在 keyword
    臂 overlap 的口径同源，不新增解析器），但只保留拉丁/数字词：

    - 中文召回由 multi_match 的 bigram + minimum_should_match 负责
      （#208 Phase 2 决策 b）。默认 cjk 分析器把中文切成 unigram+bigram，
      ``*bigram*`` 与词项匹配等价，不增加召回；且自然语言查询里的跨词
      bigram（如「条是」「是什」）会让 AND 通配子句整体落空。（该等价结论
      在 IK 分析器下只是近似——IK 词项可能更长；但 AND 里的跨词 bigram
      在 IK 下同样不是词项，净召回增量约为 0。IK 部署待栈恢复后复测。）
    - 拉丁词通配（``*config*``）提供词项匹配之外的子串召回
      （configuration 等），这是该分支唯一有增益的场景。
    - 术语仅含 [a-z0-9]（``extract_query_terms`` 产出域），再用
      ``_WILDCARD_SAFE_TERM_RE`` 兜底；沿用旧实现的 len<20 边界，并以
      ``_WILDCARD_TERM_CAP`` 限制 AND 项数。
    """
    terms: list[str] = []
    for term in extract_query_terms(query, limit=None):
        if len(term) >= 20 or not _WILDCARD_SAFE_TERM_RE.fullmatch(term):
            continue
        terms.append(f"*{term}*")
        if len(terms) >= _WILDCARD_TERM_CAP:
            break
    return terms


# ---- #208 Phase 2 决策 c：`第X条` 数形互为扩展（查询侧同义扩展；不动索引/种子） ----

# 「第X条」跨度（X = 阿拉伯数字或中文数词；允许「第 9 条」空格写法）。
# 中文数词集与 evals/clauses.py 的条号口径一致（零〇两 + 一-九 + 十百千）；
# 未纳入「廿」（语料与查询均未出现，扩集需同步 evals 口径并显式更新本注释）。
_CLAUSE_NUM_SPAN_RE = re.compile(r"第[ \t]*([0-9０-９〇零一二三四五六七八九十百千两]+)[ \t]*条")
_CN_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3,
    "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000}


def _parse_clause_number(raw: str) -> int | None:
    """「第X条」的 X 串 → int；无法解析返回 None（全角数字经 NFKC 归一）。

    中文数词口径与 evals/clauses.parse_cn_number 对齐（七→7、十二→12、
    二十二→22、一百零五→105、两千→2000）。app 层不依赖 evals 包，
    此处为对齐实现——两处口径需同步修改（校准锚点在判别测试里）。
    """
    s = unicodedata.normalize("NFKC", raw or "").strip()
    if not s:
        return None
    if s.isdigit():
        try:
            return int(s)
        except ValueError:
            return None
    total = 0
    current = 0
    for ch in s:
        if ch in _CN_DIGITS:
            current = _CN_DIGITS[ch]
        elif ch in _CN_UNITS:
            total += (current or 1) * _CN_UNITS[ch]
            current = 0
        else:
            return None
    return total + current


def _int_to_cn_clause_number(n: int) -> str | None:
    """1..99 → 中文数词形（七/十二/二十二）；范围外返回 None（该跨度不改写）。

    条号量级（≤99）覆盖语料实况；100+ 只保留阿拉伯向扩展（生成「第105条」），
    中文形按 None 跳过——避免引入百千构词法与 evals 口径产生第二套映射。
    """
    if not 1 <= n <= 99:
        return None
    digits = "一二三四五六七八九"
    if n < 10:
        return digits[n - 1]
    tens, ones = divmod(n, 10)
    head = "" if tens == 1 else digits[tens - 1]
    return head + "十" + (digits[ones - 1] if ones else "")


def _numeral_clause_variants(query: str) -> list[str]:
    """#208 Phase 2 决策 c：`第X条` 中文形⇄阿拉伯形互为扩展项（查询侧）。

    实验事实：cjk bigram 分词下 `第七` ≠ `第7`，中文数词查询的 gold 连候选
    都进不去（clause_chinese_7 / clause_chinese_12）；语料条号以阿拉伯写作
    （第7条/第12条）。本函数对查询里每个「第X条」跨度生成阿拉伯形与中文形
    两个改写变体（X 可解析且 ≥1 才参与；解析失败/越界的跨度原样保留），
    随本地改写变体一同进入 keyword 臂的「每变体一次检索 → 合并取最高分」，
    **不改索引、不重 seed**（同物理索引 before/after 可对照）。

    边界：不触及「第一章」（须以 条 收尾）、「第3.5条」（数字段不含小数点）、
    「第0条」（0 不转换）；空格写法（第 9 条）按规范形（第9条/第九条）生成；
    返回最多 2 条（阿拉伯向 + 中文向），与查询相同或重复的不返回。
    """
    if not query:
        return []
    spans = list(_CLAUSE_NUM_SPAN_RE.finditer(query))
    if not spans:
        return []
    variants: list[str] = []
    for to_arabic in (True, False):
        pieces: list[str] = []
        last = 0
        changed = False
        for m in spans:
            pieces.append(query[last:m.start()])
            replacement = None
            n = _parse_clause_number(m.group(1))
            if n is not None and n >= 1:
                if to_arabic:
                    replacement = f"第{n}条"
                else:
                    cn = _int_to_cn_clause_number(n)
                    if cn is not None:
                        replacement = f"第{cn}条"
            if replacement is None:
                pieces.append(m.group(0))
            else:
                pieces.append(replacement)
                changed = changed or replacement != m.group(0)
            last = m.end()
        pieces.append(query[last:])
        candidate = "".join(pieces)
        if changed and candidate != query and candidate not in variants:
            variants.append(candidate)
    return variants


# ---- #208 Phase 2 决策 e：条号直查类判据（此类查询跳过 MMR） ----

# 章节引用：§X.Y（允许 § 后空格；至少一段小数点，与 evals/clauses 的章节口径一致）。
# 捕获组（#188）：查询侧结构化精确腿需要 X.Y 原文。
_SECTION_REF_RE = re.compile(r"§[ \t]*(\d+(?:\.\d+)+)")


def _is_clause_lookup_query(query: str) -> bool:
    """条号直查类查询的确定性判据（#208 Phase 2 决策 e）。

    判据：查询含「第X条」式条号引用（中文/阿拉伯数词、允许空格，同 `_CLAUSE_NUM_SPAN_RE`）
    或「§X.Y」式章节引用。此类查询的目标是**精确定位**某一条/节，与 MMR 的多样性
    惩罚相斥——实验实测（hybrid 反事实）：clause_arabic_3 的 gold 分数在场却被
    `_mmr_select` 从 top5 剔出，post-process 根本没收到它。

    边界（负判）：`第一章`（须以 条 收尾）、`第3.5条`、裸 `2.3`（章节需带 §，
    避免与版本号/数值混淆）、空串。判据按查询文本模式判定，与路由/语料无关；
    负难例 `第99条…`（语料中不存在的条号）同样命中此判据（它是「直查」语义）。
    """
    if not query:
        return False
    return bool(_CLAUSE_NUM_SPAN_RE.search(query) or _SECTION_REF_RE.search(query))


# ---- #187：文档级多样性预算（帽从 rerank 之前移到 rerank 之后） ----

# 普通语义查询：2/文档（保持 #208 决策 a 的口径；放开到无帽会 NearMiss 结构性 +8.3pt，
# 实验数据见 backend/docs/eval-gate-phase2-experiment.md §4）。
DOC_CAP_DEFAULT = 2
# 条号直查类查询：4/文档。目标是精确定位某一条；同文档的前言/相邻条与 gold 同属
# 一份文件，2/文档会在 rerank 之后仍结构性压掉 gold（#208 实验的剩余扼杀环）。
DOC_CAP_CLAUSE_LOOKUP = 4


def clause_lookup_doc_cap(query: str) -> int:
    """rerank 之后的每文档块数预算（#187）：直查类 4，普通查询 2。"""
    return DOC_CAP_CLAUSE_LOOKUP if _is_clause_lookup_query(query) else DOC_CAP_DEFAULT


def apply_doc_cap(hits: list[dict], doc_cap: int | None) -> list[dict]:
    """按文档截断命中（保持输入顺序）；``doc_cap=None`` 表示不截断。

    在 pipeline 的 **rerank 之后**调用（#187）：先让 rerank 看到全部候选、按相关度
    重排，再按文档做多样性截断——帽不再发生在「相关度尚未判定」的候选序上。
    缺 ``document_id`` 的命中原样保留（无法归组，不因缺字段被静默丢弃）。
    """
    if doc_cap is None:
        return hits
    counts: dict[str, int] = {}
    kept: list[dict] = []
    for hit in hits:
        doc_id = str(hit.get("document_id") or (hit.get("_source") or {}).get("document_id") or "")
        if not doc_id:
            kept.append(hit)
            continue
        counts[doc_id] = counts.get(doc_id, 0) + 1
        if counts[doc_id] <= doc_cap:
            kept.append(hit)
    return kept


# ---- #188：结构化条款字段（索引侧 doc_processor 写入顶层） ----

# 检索命中需要携带的结构化字段白名单：section_title / clause_no / section_no 是
# #188 打通的顶层字段；chunk_id / chunk_index 供稳定标识与调试展示。
_HIT_SOURCE_FIELDS = [
    "content",
    "filename",
    "organization_id",
    "document_id",
    "embedding",
    "upload_time",
    "chunk_id",
    "chunk_index",
    "section_title",
    "clause_no",
    "section_no",
    "page_number",
]


# 查询侧条号目标的上界：clause_no 是 ES integer 字段（int32），且真实制度文档
# 不可能有百万条——超界值（如「第99999999999条」）直接跳过结构化精确腿，
# 避免把非法值打进 terms 查询导致整次检索 400。
_MAX_CLAUSE_NO = 1_000_000
# 结构化精确腿的返回条数上限：filter-only 命中的 _score 为 0，若不设界且组织内
# 定义同一条号的块远超候选窗口，会把带内容词的 BM25 候选整体挤出 `[:candidate_size]`。
_EXACT_ARM_MAX_SIZE = 20


def _query_target_clauses(query: str) -> list[int]:
    """查询中「第X条」的目标条号（中文/阿拉伯形归一化为整数，去重保序）。"""
    targets: list[int] = []
    for m in _CLAUSE_NUM_SPAN_RE.finditer(query or ""):
        n = _parse_clause_number(m.group(1))
        if n is not None and 1 <= n <= _MAX_CLAUSE_NO and n not in targets:
            targets.append(n)
    return targets


def _query_target_sections(query: str) -> list[str]:
    """查询中「§X.Y」的目标章节编号（如 ``["2.3"]``，去重保序）。"""
    targets: list[str] = []
    for m in _SECTION_REF_RE.finditer(query or ""):
        value = m.group(1)
        if value and value not in targets:
            targets.append(value)
    return targets


def _hit_defines_target(
    hit: dict, target_clauses: set[int], target_sections: set[str]
) -> bool:
    """该命中是否**定义了**查询目标的条号/章节（结构化字段来自索引侧定义位）。

    只读顶层 ``clause_no`` / ``section_no``；旧索引没有这些字段时恒 False，
    排序行为与修前逐字一致（平滑迁移）。
    """
    source = hit.get("_source") or {}
    if target_clauses:
        raw = source.get("clause_no")
        values = raw if isinstance(raw, list) else ([] if raw is None else [raw])
        numbers: set[int] = set()
        for value in values:
            try:
                numbers.add(int(value))
            except (TypeError, ValueError):
                continue
        if numbers & target_clauses:
            return True
    if target_sections:
        raw = source.get("section_no")
        values = raw if isinstance(raw, list) else ([] if raw is None else [raw])
        if {str(value) for value in values} & target_sections:
            return True
    return False


class HybridRetriever:
    """Retrieves documents using keyword search, vector search, and RRF fusion."""

    def __init__(self, openai_client=None, embedding_provider=None):
        self.openai_client = openai_client
        # issue #196：检索期不再自建 embedding 客户端，统一接收
        # ``app.services.embedding_service.EmbeddingService``（索引期同一实例）。
        # None = 按设计未配置向量臂（返回 []，不产生降级信号）。
        self.embedding_provider = embedding_provider

    @property
    def embedding_client(self):
        """向后兼容只读视图：provider 底层的 OpenAI 客户端（可能为 None）。"""
        return getattr(self.embedding_provider, "client", None)

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

        issue #196：模型名/维度校验不在检索器里做——provider（embedding_service）
        与索引期是同一实例，保证「索引用的模型 == 查询用的模型」。
        """
        if not self.embedding_provider:
            return []
        try:
            return await self.embedding_provider.embed_query(text)
        except Exception as e:
            raise EmbeddingUnavailableError(f"embedding service call failed: {e}") from e

    # ---- Keyword search ----

    async def _keyword_hits(self, query: str, filters: list[dict], candidate_size: int) -> list[dict]:
        enable_qr = bool(getattr(settings, "RAG_ENABLE_QUERY_REWRITE", True))
        rewrite_queries = rewrite_query_candidates(query)
        # #208 Phase 2 决策 c：`第X条` 数形互为扩展（查询侧同义扩展）——与本地
        # 改写变体一同走「每变体一次检索 → 合并取最高分」的既有机制，不改索引。
        for variant in _numeral_clause_variants(query):
            if variant not in rewrite_queries:
                rewrite_queries.append(variant)
        # #188：条号直查类查询的结构化目标（定义位字段 clause_no / section_no）。
        target_clauses = _query_target_clauses(query)
        target_sections = _query_target_sections(query)
        target_clause_set = set(target_clauses)
        target_section_set = set(target_sections)
        # 本地站产出条数（含原查询与数形扩展），用于折算存活新增
        base_count = len(rewrite_queries)
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
            # #192：通配项来自 extract_query_terms 的拉丁词（中文为 []，不再产出
            # 「*整句*」死项）；见 _wildcard_terms。
            wildcard_terms = _wildcard_terms(q_text)
            should_clauses = [
                {"multi_match": {
                    "query": q_text, "fields": boost_fields,
                    "type": "best_fields", "fuzziness": "AUTO",
                    # #208 Phase 2 决策 b：带《…》全称时按「剔除标题 token 后的 30%」计算。
                    "minimum_should_match": _minimum_should_match_for(q_text),
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
                "_source": _HIT_SOURCE_FIELDS,
            }
            res = await ElasticsearchTools.search_documents(es_query)
            return res.get("hits", {}).get("hits", [])

        async def _es_exact_structured_search():
            """#188：条号/章节的结构化精确检索腿（与 BM25 平行、并集去重）。

            只用**定义位**字段（clause_no / section_no）做 terms 过滤，不参与 BM25
            打分竞争：gold 定义块即使被标题/前言洪泛压到低分，也保证进入合并集，
            并由下方排序键提到 keyword 臂前部。旧索引没有这些字段时本查询返回空、
            行为与修前一致——精确腿是**并集增强**，不是硬过滤（不会把非目标条号的
            关键词候选整条剪掉）。
            """
            should: list[dict] = []
            if target_clauses:
                should.append({"terms": {"clause_no": target_clauses}})
            if target_sections:
                should.append({"terms": {"section_no": target_sections}})
            es_query = {
                "size": min(candidate_size, _EXACT_ARM_MAX_SIZE),
                "query": {
                    "bool": {
                        "filter": [
                            *filters,
                            {"bool": {"should": should, "minimum_should_match": 1}},
                        ]
                    }
                },
                "_source": _HIT_SOURCE_FIELDS,
            }
            res = await ElasticsearchTools.search_documents(es_query)
            return res.get("hits", {}).get("hits", [])

        tasks = [_es_search(rq) for rq in rewrite_queries]
        has_exact_arm = bool(target_clauses or target_sections)
        if has_exact_arm:
            tasks.append(_es_exact_structured_search())
        results = await asyncio.gather(*tasks)
        exact_hits: list[dict] = []
        if has_exact_arm:
            results, exact_hits = results[:-1], results[-1]

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

        # #188：结构化精确腿的命中并入候选集（并集去重，不硬过滤）。
        # 它们不是「改写变体命中」——不增 _rewrite_hits（该计数只反映变体覆盖数）。
        for hit in exact_hits:
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
            elif score > float(prev.get("_score", 0)):
                prev["_score"] = score
                prev["_source"] = hit.get("_source", {})

        # #188：定义目标条号/章节的命中排到 keyword 臂前部（结构化 boost）。
        # 依赖索引侧顶层 clause_no/section_no；旧索引无字段 → 全部 False，排序与修前一致。
        return sorted(
            merged.values(),
            key=lambda h: (
                0 if _hit_defines_target(h, target_clause_set, target_section_set) else 1,
                -float(h.get("_score") or 0),
            ),
        )[:candidate_size]

    # ---- Vector search ----

    async def _vector_hits(
        self, query: str, filters: list[dict], candidate_size: int, enable_hyde: bool = True
    ) -> tuple[list[dict], list[float], str | None]:
        """向量臂检索。返回 (hits, query_vector, degraded_reason)。

        degraded_reason 非 None 表示向量臂因服务故障未能执行（issue #292）——
        上层据此区分「确实没有向量命中」与「根本没查成」，不得静默关臂。

        issue #192：关键词臂的多改写变体（本地改写 + LLM 改写 + 条号数形扩展）
        不进入本臂；向量臂仍按既有策略 embed **原查询**（``hybrid_hyde`` 下
        另含 HyDE 伪文档融合，与本边界无关）——这是当前的**有意边界**（改写
        收益只在关键词臂）。把变体并行 embed 并按最高分合并属于检索质量变更，
        需在栈恢复后用 backend/evals 校准延迟/质量后再接线；hybrid 检索的
        ``debug_info["query_rewrite"]`` 会如实标出这一点，避免再被误读为
        「改写对向量臂也生效」。
        """
        intent = QueryIntentClassifier.classify(query)
        try:
            query_vector = await self._embed(query)
        except EmbeddingUnavailableError as e:
            # 修复（#292）：向量臂因 embedding 服务故障关闭时如实标记降级，
            # 不再与「没有命中」同形（此前是 #2 假无结果的静默放大器）。
            # issue #196：维度/模型漂移是**配置错误**而非瞬态故障——用专用 reason
            # 标记，pipeline 据此 fail-closed（即使关键词臂有命中也不返回）。
            # 用类名判定避免在检索路径上 import embedding_service（无 key 的
            # 只读工具不应因构造单例而失败）。
            cause = e.__cause__
            if cause is not None and type(cause).__name__ == "EmbeddingDimensionMismatchError":
                logger.error(
                    "Vector retrieval arm refused by embedding dimension drift: %s",
                    cause,
                )
                return [], [], f"vector_arm_config_error: {cause}"
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
            "_source": _HIT_SOURCE_FIELDS,
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
    def _build_snippet(content: str, query: str, max_len: int | None = None) -> str:
        """构建展示用 snippet（issue #186）。

        - 查询字面量命中：取以命中位置为中心的窗口（保留原有语义）；
        - 未命中：**不再硬切 chunk 头部**，复用 context_compressor 的规则式
          句子抽取——保留开头少量上下文 + 含查询术语的句子，关键句在 chunk
          后半段时仍可能展示出来；
        - 上限可经 `RAG_SNIPPET_MAX_CHARS` 配置（默认 260，与原行为一致）。
        """
        if not content:
            return ""
        limit = max_len if max_len is not None else int(settings.RAG_SNIPPET_MAX_CHARS)
        limit = max(1, limit)
        text = content.replace("\n", " ").strip()
        if len(text) <= limit:
            return text
        idx = text.lower().find(query.strip().lower()) if query else -1
        if idx >= 0:
            start = max(0, idx - limit // 3)
            return text[start: min(len(text), start + limit)]
        return compress(text, query, max_chars=limit)

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

    def _post_process(
        self, query: str, fused_hits: list[dict], top_k: int, doc_cap: int | None = DOC_CAP_DEFAULT
    ) -> list[dict]:
        """Normalize, deduplicate, and filter retrieval results.

        #208 Phase 2 决策 a（doc 帽保守修法）：帽保持 2/文档，但计数移到质量过滤
        之后——被任一质量闸门（含 0.15/0.10/0.07 终滤档）剔除的块不占帽位、
        也不占 top_k 结果预算（此前计数在质量过滤前，被剔的块也占槽）。

        #187：``doc_cap=None`` 关闭本函数内的文档截断——pipeline 以它拿到
        rerank 前的完整候选集（帽移到 rerank 之后，见 ``apply_doc_cap``）。
        默认值 2 保持未接线 rerank 的旧调用方（eval runner / 单测）行为不变。

        issue #186 输出契约：每条结果同时携带
        - `prompt_text` / `text` / `content`：完整 chunk 正文（LLM 输入的唯一来源，
          由 `context_compressor.get_prompt_text` 读取，压缩器在全文上工作）；
        - `snippet`：展示用短片段（UI/debug），不得回流进提示词。
        #188 输出契约：透传索引侧结构化字段 ``section_title`` / ``clause_no`` /
        ``section_no`` / ``chunk_index``（旧索引缺字段时为 None/[]）。
        #197 输出契约：透传顶层 ``page_number``（PDF 页码；非 PDF / 旧索引为 None）。

        issue #185 分数契约：`rank_score`（与旧 `score` 同值）只是**融合阶段的批内
        min-max 相对分**，用于排序/序位展示——榜首恒 ~1.0 是构造性的，不是相关度。
        绝对分 `relevance_score` 由 pipeline 在 rerank 之后写入（无重排分则为 None），
        post-process 绝不伪造。
        """

        documents = []
        if top_k <= 0:
            return documents
        seen_docs: dict[str, int] = {}
        # #188：定义目标条号/章节的块是本类查询的**目标本身**——词面质量闸门
        # （overlap 档）不得把它误杀。裸「第5条」这类查询的提取术语为空
        # （CJK 单字不成 bigram），词面 overlap 恒 0；若不放行，结构化精确腿
        # 召回的 gold 会在 post-process 被整条丢弃（精确腿形同虚设）。
        target_clause_set = set(_query_target_clauses(query))
        target_section_set = set(_query_target_sections(query))
        has_structured_target = bool(target_clause_set or target_section_set)
        # #208 Phase 2 环 C：overlap 口径改用「语料术语表优先 + 上限 16」的术语选择
        # （机制 3 修复点；见 _keyword_arm_terms 与 _CORPUS_GLOSSARY）。
        query_terms = _keyword_arm_terms(query)
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

            # #188：定义目标条号/章节的命中跳过词面质量闸门（见函数开头说明）；
            # 其余命中逐字保持原判定式。
            defines_target = has_structured_target and _hit_defines_target(
                hit, target_clause_set, target_section_set
            )
            if not defines_target:
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

            # doc 帽（#187：doc_cap=None 时不截断，交由 pipeline 在 rerank 后施加）：
            # 只对通过质量过滤的块计数（被剔的块不占槽）。
            doc_id = str(source.get("document_id") or "")
            if doc_id and doc_cap is not None:
                seen_docs[doc_id] = seen_docs.get(doc_id, 0) + 1
                if seen_docs[doc_id] > doc_cap:
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
                "text": text, "content": text,
                # issue #186：提示词正文单一来源。snippet 只是展示片段，
                # 不再参与任何提示词组装（见 context_compressor.get_prompt_text）。
                "prompt_text": text,
                "filename": source.get("filename", "未知"),
                "chunk_id": chunk_id,
                # issue #185：rank_score 是批内 min-max 相对分（排序/序位用）；
                # score 保留为同值别名（workflow 阈值/agent 载荷沿用既有语义）。
                # 绝对相关度 relevance_score 由 pipeline rerank 后写入。
                "score": round(final_score, 4),
                "rank_score": round(final_score, 4),
                "raw_score": fused_score,
                "snippet": snippet, "document_id": source.get("document_id"),
                # #188：结构化条款字段透传（旧索引缺字段时为 None/[]）
                "section_title": source.get("section_title"),
                "clause_no": source.get("clause_no") or [],
                "section_no": source.get("section_no") or [],
                "chunk_index": source.get("chunk_index"),
                # #197：PDF 页码透传（非 PDF / 旧索引为 None）
                "page_number": source.get("page_number"),
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
                "_source": _HIT_SOURCE_FIELDS,
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
                "_source": _HIT_SOURCE_FIELDS,
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
        search_mode: str | None = None,
        doc_cap: int | None = DOC_CAP_DEFAULT,
    ) -> tuple[list[dict], list[float], dict | None, list[str]]:
        """Execute adaptive retrieval and return (results, query_vector, debug_info, degraded).

        degraded：臂级降级原因列表（空 = 全部预期检索臂都正常执行）。issue #292：
        向量臂 / 兜底臂因服务故障未能执行时不得与「确实没有命中」同形；
        keyword 臂失败与兜底臂异常会直接上抛（由 pipeline 归类为故障态）。
        策略为 keyword_only / vector_only 时无另一臂是**按设计**行为，不产生降级信号。

        doc_cap（#187）：本函数内的每文档块数上限。默认 2 = 旧行为逐字不变；
        pipeline 传入 ``None`` 让 rerank 看到全部候选，再在 rerank 之后用
        ``clause_lookup_doc_cap(query)`` + ``apply_doc_cap`` 施加多样性截断。

        Strategy：显式 `search_mode`（hybrid/vector/keyword）**强制本次请求的检索臂组合**
        （issue #225 决策 7A）；为 None 时按查询复杂度自动路由：
        - keyword_only: fast keyword search, no embedding calls
        - vector_only:  pure vector arm（仅显式 search_mode="vector" 可达，自动路由不产生）
        - hybrid: keyword + vector (default)
        - hybrid_hyde: keyword + vector + HyDE + multi-rewrite (full pipeline)

        覆盖仅作用于本次调用：不修改任何进程级状态，其它请求/路径（chat、agent、
        workflow）不传 search_mode 时行为逐条不变。

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

        # Strategy selection：显式模式覆盖自动路由（issue #225 决策 7A）。
        # 覆盖只在本次调用内生效；显式值与未知值都经 resolve_retrieval_strategy
        # 单点解析（未知值回落自动路由，API 边界由 SearchMode 枚举 422 兜住）。
        forced_mode = search_mode if search_mode in SEARCH_MODE_TO_STRATEGY else None
        strategy = resolve_retrieval_strategy(query, search_mode)
        # 指标口径：自动路由保持 keyword_only/hybrid/hybrid_hyde 三值（回归不变）；
        # 显式覆盖用 forced_* 单列，运维可直接看到「模式字段真的开始被使用」。
        strategy_label = f"forced_{forced_mode}" if forced_mode else strategy
        RAG_ADAPTIVE_STRATEGY.labels(strategy=strategy_label).inc()
        logger.info(
            "Adaptive RAG: query='%s...' → strategy=%s%s",
            query[:50], strategy,
            f" (forced by search_mode={forced_mode})" if forced_mode else "",
        )

        debug_info = (
            {"strategy": strategy, "query": query, "search_mode": search_mode}
            if debug else None
        )

        if strategy == "keyword_only":
            kw_hits = await self._keyword_hits(query, filters, candidate_size)
            # #208 Phase 2 决策 d：keyword-only 序的命中来自 keyword 臂本体，补上与
            # hybrid 序（`_rrf_fuse` 写 `has_keyword`）一致的臂标记——终滤走同一
            # 质量档（has_kw → ov>=0.10），不再按「无臂命中」的最严 0.15 档论处
            # （实测 gold overlap=0.1167 恰死两档之间）。命中为拷贝，不改进参对象。
            kw_hits = [{**h, "has_keyword": True} for h in kw_hits]
            if debug and debug_info is not None:
                debug_info["keyword_top"] = self._debug_format_hits(kw_hits[:10])
                debug_info["vector_top"] = []
                debug_info["rrf_result"] = debug_info["keyword_top"][:top_k]
                debug_info["rerank_result"] = []
            documents = self._post_process(query, kw_hits, top_k, doc_cap=doc_cap)
            return documents, [], debug_info, degraded

        if strategy == "vector_only":
            # issue #225 决策 7A：显式 search_mode="vector" → 仅向量臂。
            # 与 keyword_only 分支对称：单臂序直接进 post-process。
            # 刻意不做 keyword 兜底、不跑 MMR——显式模式必须**纯**：
            # 静默混入另一臂正是本 issue 要消灭的「模式说了不算」语义。
            # （MMR 是融合序的多样性环节，单臂序无融合可多样化。）
            # HyDE 对齐显式 hybrid：显式模式一概不跑 HyDE（enable_hyde=False）——
            # HyDE 是自动路由给复杂查询的增强（LLM 调用 + 不确定性），显式模式的
            # 「纯」语义不含它；这是 m1 的定夺（评审要求二选一时选「对齐」）。
            vec_hits, query_vector, vec_degraded = await self._vector_hits(
                query, filters, candidate_size, enable_hyde=False
            )
            if vec_degraded:
                degraded.append(vec_degraded)
            # 臂标记与 hybrid 序（`_rrf_fuse` 写 `has_vector`）对齐（同决策 d 口径）。
            vec_hits = [{**h, "has_vector": True} for h in vec_hits]
            if debug and debug_info is not None:
                debug_info["keyword_top"] = []
                debug_info["vector_top"] = self._debug_format_hits(vec_hits[:10])
                debug_info["rrf_result"] = debug_info["vector_top"][:top_k]
                debug_info["rerank_result"] = []
            documents = self._post_process(query, vec_hits, top_k, doc_cap=doc_cap)
            return documents, query_vector, debug_info, degraded

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
            # issue #192：把「改写变体只进关键词臂」的现状边界写进 debug——
            # rewrite_hits 只出现在关键词臂命中上，向量臂用的是原查询向量。
            # llm_rewrite_enabled 只反映 LLM 改写开关；本地改写与条号数形扩展
            # 无条件执行。vector_hyde_enabled 是**策略级**标记（hybrid_hyde 路由
            # 会尝试 HyDE），不代表本次真的发生了融合——无 LLM client、生成
            # 失败/超时、embedding 不可用或返回空向量时均不会融合；避免
            # 「只 embed 原查询」被绝对化误读。
            debug_info["query_rewrite"] = {
                "llm_rewrite_enabled": bool(
                    getattr(settings, "RAG_ENABLE_QUERY_REWRITE", True)
                ),
                "keyword_arm": "rewritten_variants",
                "vector_arm": "original_query_only",
                "vector_hyde_enabled": strategy == "hybrid_hyde",
            }
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
            results = (
                self._post_process(query, fallback, top_k, doc_cap=doc_cap) if fallback else []
            )
            if debug and debug_info is not None:
                debug_info["note"] = "RRF fusion empty, used fallback"
            return results, query_vector, debug_info, degraded

        # MMR
        # #208 Phase 2 决策 e：条号直查类查询（第X条 / §X.Y）跳过 MMR——多样性惩罚
        # 与「精确定位」目标相斥（实测 clause_arabic_3 的 gold 分数在场却被
        # _mmr_select 从 top5 剔出，post-process 根本没收到它）；此类查询的 RRF 序
        # 直接进入 post-process。非直查查询的 MMR 行为不变。
        #
        # issue #193：修前候选列表把「缺 embedding」的命中直接过滤掉，MMR 结果又
        # 整表替换 fused——关键词臂召回到、但索引期未写向量的 chunk 会静默消失，
        # 且 debug 无从判断。现改为：带向量候选参与 MMR 打分；无向量候选按原
        # fused 序确定性追加在选中项之后（仍保留 keyword 臂信息，由 post-process
        # 的质量档决定去留），计数写入 debug_info["mmr"]。
        mmr_debug: dict[str, object]
        clause_lookup = _is_clause_lookup_query(query)
        if settings.RAG_ENABLE_MMR and query_vector and not clause_lookup:
            window = fused[:candidate_size]
            embedded_cands = [
                {**h, "_embedding": h.get("_source", {}).get("embedding", [])}
                for h in window if h.get("_source", {}).get("embedding")
            ]
            no_embedding = [
                h for h in window if not h.get("_source", {}).get("embedding")
            ]
            lambda_mult = max(0, min(1, settings.RAG_MMR_LAMBDA))
            if embedded_cands and top_k > 0:
                selected = self._mmr_select(
                    embedded_cands, query_vector, top_k, lambda_mult
                )
                # 追加的无向量候选与选中项一起进入 _post_process 的 min-max
                # 归一化（有意口径：展示分覆盖本次考虑过的全部候选；去留仍由
                # 质量档与 doc 帽决定）。debug 的 no_embedding_appended 是
                # 「追加条数」，不是最终存活数。
                fused = selected + no_embedding
                mmr_debug = {
                    "applied": True,
                    "lambda": lambda_mult,
                    "candidates": len(window),
                    "with_embedding": len(embedded_cands),
                    "selected": len(selected),
                    "no_embedding_appended": len(no_embedding),
                    "not_selected": len(embedded_cands) - len(selected),
                }
            else:
                # 无带向量候选（或 top_k<=0）：MMR 无输入，保持原 fused 序进入
                # post-process——与修复前同语义，候选同样不丢。
                mmr_debug = {
                    "applied": False,
                    "reason": (
                        "no_embedded_candidates" if not embedded_cands else "zero_top_k"
                    ),
                    "no_embedding_appended": len(no_embedding),
                }
        elif settings.RAG_ENABLE_MMR:
            mmr_debug = {
                "applied": False,
                "reason": "clause_lookup" if clause_lookup else "no_query_vector",
            }
        else:
            mmr_debug = {"applied": False, "reason": "disabled"}
        if debug and debug_info is not None:
            debug_info["mmr"] = mmr_debug

        return self._post_process(query, fused, top_k, doc_cap=doc_cap), query_vector, debug_info, degraded

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
                # #188：结构化条款字段（旧索引为 None/[]）
                "section_title": src.get("section_title"),
                "clause_no": src.get("clause_no") or [],
                "section_no": src.get("section_no") or [],
                "snippet": (src.get("content", "") or "")[:200],
            })
        return formatted
