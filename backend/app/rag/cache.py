"""RAG retrieval cache — exact (key-based) + semantic (embedding-based).

Semantic cache uses Redis Sorted Sets for O(log n) similarity search.
Memory fallback uses bounded LRU eviction to prevent unbounded growth.
"""

import hashlib
import json
import logging
import math
import re
import time
import unicodedata
from collections import Counter, OrderedDict
from typing import Any

from app.core.config import settings
from app.rag.query_processor import extract_query_terms

logger = logging.getLogger(__name__)


def _get_redis():
    """Lazy import to avoid circular dependencies."""
    from app.core.redis import redis_client

    return redis_client


def _require_org_id(organization_id: int | None, where: str) -> int:
    """Validate an explicit tenant scope before touching a cache namespace.

    W0-6：``SemanticCache.get/set`` 此前以 ``organization_id: int = 0`` 作为
    默认值——漏传组织的调用会静默读写 ``org:0`` 这一跨租户共享命名空间。
    现在组织上下文是必填参数，且 0 / -1 / None 一律显式拒绝（fail-closed）。
    """
    if organization_id is None:
        raise ValueError(f"{where} 缺少 organization_id，拒绝在无组织上下文下访问缓存")
    if isinstance(organization_id, bool):
        raise ValueError(f"{where} 的 organization_id 非法: {organization_id!r}")
    try:
        org_id = int(organization_id)
    except (TypeError, ValueError):
        raise ValueError(f"{where} 的 organization_id 非法: {organization_id!r}") from None
    if org_id <= 0:
        raise ValueError(
            f"{where} 的 organization_id={organization_id!r} 非法："
            "必须是正整数组织 ID（0/-1 是历史 fail-open 哨兵，已禁用）"
        )
    return org_id


# ── issue #195：语义缓存的查询等价判定 ────────────────────────────────
#
# 背景：命中判据此前只有「embedding 余弦 ≥ SEMANTIC_CACHE_THRESHOLD(0.92)」。
# 相邻条款（第七条/第十三条）、同句式换实体（北京/上海）或不同文件
# （劳动合同法/劳动合同法实施条例）的查询向量余弦都可以 ≥0.92 —— 检索链会
# 返回另一问题的 sources，问答链会直接返回另一问题的完整答案。向量相似度
# 只能做粗筛，等价判定必须落到文本实体。
#
# 口径（保守优先；语义缓存是 opt-in 优化，误拒只损失一次缓存收益，误命中
# 会把他人答案当成本问题的答案）：
# - 「第X条」「§X.Y」「第X款/项」「第X条之一」「第X条（二）」引用集合必须
#   完全一致；「第七十七条」⇄「第77条」先归一（条款跨度不参与内容比较）；
# - 数字 token（NFKC 后、排除条款跨度内数字）一致；2024 年 ≠ 2025 年；
# - 否定词计数一致；含税 ≠ 不含税；
# - 疑问意图一致（两侧都表达意图时）：是什么 ≠ 为什么、怎么 ≠ 为什么；
# - CJK 内容（去问句功能字/问句短语、去除条款跨度后）必须**完全一致**，
#   不做单侧包含：北京 ≠ 上海、劳动法 ≠ 劳动合同法、
#   劳动合同法 ≠ 劳动合同法实施条例、高温补贴标准 ≠ 北京高温补贴标准；
# - 拉丁术语（含单字母选项 A/B）集合必须完全一致：
#   k8s deployment ≠ k8s service、方案 A ≠ 方案 B。
# 条号/数字等口径复用 retriever / query_processor 的既有实现，不造第三套映射。

# 问句功能字（不承载实体/条款信号）。刻意保持保守：不剥离
# 我/你/他/下/请/帮/过/有/对/从/到 等可能出现在法律术语里的字
# （我方/你方、有期徒刑、过错、对价、到期、申请……），避免把实体差异洗掉。
_QUERY_STOP_CHARS = frozenset("的了吗呢吧啊呀嘛哦是什么怎这那哪")
# 问句短语先整体剔除：字符级剔除不了「如何」的如/何、「多少」的多/少，
# 直接剥字符又会伤到「如下」「多数」等实体词。
_QUESTION_PHRASES = (
    "为什么",
    "怎么样",
    "怎样",
    "如何",
    "多少",
    "哪些",
    "哪个",
    "是什么",
    "是多少",
)
# 否定字：计数必须一致——"含税价格" 与 "不含税价格" 是相反问题。
_NEGATION_CHARS = frozenset("不无未非没否勿免")
# 疑问意图：两侧都表达意图时必须一致——「是什么 ≠ 为什么」「怎么 ≠ 是什么」。
# 「为什么」同时含「什么」，会同时命中 why 与 what；只会更严格，不会放行。
_QUESTION_INTENTS = (
    ("why", ("为什么", "为何")),
    ("how", ("如何", "怎么", "怎样")),
    ("which", ("哪些", "哪个")),
    ("how_much", ("多少",)),
    ("what", ("什么", "何为")),
)
# 「第X款」「第X项」子条款引用（条号正则只管「条」）。
_SUB_CLAUSE_SPAN_RE = re.compile(
    r"第[ \t]*([0-9０-９〇零一二三四五六七八九十百千两]+)[ \t]*(款|项)"
)
# 「第X条之一」子条（正式立法引用形式，如刑法修正案/司法解释）。
_SUBARTICLE_SPAN_RE = re.compile(
    r"第[ \t]*[0-9０-９〇零一二三四五六七八九十百千两]+[ \t]*条[ \t]*之[ \t]*"
    r"([0-9０-９〇零一二三四五六七八九十百千两]+)"
)
# 「第X条（二）」式项引用（括号数字，常见法条写法；NFKC 后括号为 ASCII）。
_ITEM_SPAN_RE = re.compile(
    r"第[ \t]*[0-9０-９〇零一二三四五六七八九十百千两]+[ \t]*条[ \t]*"
    r"[（(][ \t]*([0-9０-９〇零一二三四五六七八九十百千两]+)[ \t]*[）)]"
)

_ENTITY_MISMATCH = "entity_mismatch"


def _key_digest(key: str) -> str:
    """缓存键的短摘要（debug 展示用；不暴露明文查询）。"""
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def _normalize_query_text(query: str) -> str:
    return unicodedata.normalize("NFKC", query or "").lower().strip()


def _query_equivalence_signature(query: str) -> dict[str, Any]:
    """把查询拆成等价判定所需的信号（条款/数字/否定/意图/内容/拉丁术语）。"""
    q = _normalize_query_text(query)
    # 延迟导入：cache 是轻量模块，retriever 会带上 ES 依赖；只在真正命中
    # 判定时加载一次（模块级缓存），避免 cache 的导入方被迫加载检索栈。
    from app.rag.retriever import (
        _CLAUSE_NUM_SPAN_RE,
        _SECTION_REF_RE,
        _parse_clause_number,
    )

    masked = list(q)
    clause_numbers: list[int] = []
    for m in _CLAUSE_NUM_SPAN_RE.finditer(q):
        n = _parse_clause_number(m.group(1))
        if n is not None:
            clause_numbers.append(n)
        for i in range(m.start(), m.end()):
            masked[i] = " "
    sub_refs: list[tuple[str, int]] = []
    for m in _SUB_CLAUSE_SPAN_RE.finditer(q):
        n = _parse_clause_number(m.group(1))
        if n is not None:
            sub_refs.append((m.group(2), n))
        for i in range(m.start(), m.end()):
            masked[i] = " "
    for m in _SUBARTICLE_SPAN_RE.finditer(q):
        n = _parse_clause_number(m.group(1))
        if n is not None:
            sub_refs.append(("之", n))
        for i in range(m.start(), m.end()):
            masked[i] = " "
    for m in _ITEM_SPAN_RE.finditer(q):
        n = _parse_clause_number(m.group(1))
        if n is not None:
            sub_refs.append(("项", n))
        for i in range(m.start(), m.end()):
            masked[i] = " "
    section_refs = sorted({re.sub(r"\s+", "", m.group(0)) for m in _SECTION_REF_RE.finditer(q)})
    for m in _SECTION_REF_RE.finditer(q):
        for i in range(m.start(), m.end()):
            masked[i] = " "
    number_tokens = sorted(re.findall(r"\d+(?:\.\d+)?", "".join(masked)))

    # 内容比较只保留非条款文本：条款跨度已归为空格；问句短语/功能字剔除后，
    # 连续 CJK 片段必须逐字一致（不做单侧包含——「劳动合同法」是
    # 「劳动合同法实施条例」的前缀，包含式放行会跨法律文件命中）。
    content = "".join(masked)
    for phrase in _QUESTION_PHRASES:
        content = content.replace(phrase, " ")
    runs: list[str] = []
    current: list[str] = []
    for ch in content:
        if "㐀" <= ch <= "鿿" and ch not in _QUERY_STOP_CHARS:
            current.append(ch)
        else:
            if current:
                runs.append("".join(current))
                current = []
    if current:
        runs.append("".join(current))

    # extract_query_terms 的拉丁词要求 ≥2 字符（会丢「方案 A/B」这类单字母
    # 选项标签），这里补独立单字母；其余复用同一抽取口径。
    latin_terms = {
        t for t in extract_query_terms(q, limit=None) if re.fullmatch(r"[a-z][a-z0-9_]*", t)
    }
    latin_terms.update(re.findall(r"(?<![a-z0-9_])[a-z](?![a-z0-9_])", q))

    intents = frozenset(name for name, markers in _QUESTION_INTENTS if any(m in q for m in markers))

    return {
        "clause_numbers": sorted(clause_numbers),
        "sub_refs": sorted(sub_refs),
        "section_refs": section_refs,
        "number_tokens": number_tokens,
        "negations": Counter(ch for ch in q if ch in _NEGATION_CHARS),
        "intents": intents,
        "runs": runs,
        "latin_terms": sorted(latin_terms),
    }


def queries_equivalent(query: str, cached_query: str) -> tuple[bool, str]:
    """判断当前查询与缓存条目的查询在「条款/数字/否定/意图/实体」上是否等价。

    返回 ``(是否等价, 原因)``；原因用于 cache debug：
    ``ok`` / ``no_content_signal`` / ``clause_mismatch`` /
    ``sub_clause_mismatch`` / ``section_mismatch`` / ``number_mismatch`` /
    ``negation_mismatch`` / ``intent_mismatch`` / ``entity_mismatch`` /
    ``term_mismatch``。

    实体口径是**完全一致**（不是单侧包含）：CJK 内容与拉丁术语都必须逐字
    相同——宁可误拒（重算一次）不可误命中（返回他人答案）。规范化后原文
    完全相同者直接判等价（含「怎么办」这类纯功能字查询）。
    """
    if _normalize_query_text(query) == _normalize_query_text(cached_query):
        return True, "ok"
    a = _query_equivalence_signature(query)
    b = _query_equivalence_signature(cached_query)
    anchored = bool(
        a["clause_numbers"]
        or a["sub_refs"]
        or a["section_refs"]
        or b["clause_numbers"]
        or b["sub_refs"]
        or b["section_refs"]
    )
    # 没有任何内容/术语/条款信号 → 无法证明同一问题，fail-closed。
    if not a["runs"] and not a["latin_terms"] and not anchored:
        return False, "no_content_signal"
    if not b["runs"] and not b["latin_terms"] and not anchored:
        return False, "no_content_signal"
    if a["clause_numbers"] != b["clause_numbers"]:
        return False, "clause_mismatch"
    if a["sub_refs"] != b["sub_refs"]:
        return False, "sub_clause_mismatch"
    if a["section_refs"] != b["section_refs"]:
        return False, "section_mismatch"
    if a["number_tokens"] != b["number_tokens"]:
        return False, "number_mismatch"
    if a["negations"] != b["negations"]:
        return False, "negation_mismatch"
    if a["intents"] and b["intents"] and a["intents"] != b["intents"]:
        return False, "intent_mismatch"

    if "".join(a["runs"]) != "".join(b["runs"]):
        return False, _ENTITY_MISMATCH
    if set(a["latin_terms"]) != set(b["latin_terms"]):
        return False, "term_mismatch"
    return True, "ok"


# 修复（R5）：内存回退缓存必须是进程级共享注册表。
# 此前每次 clear_for_org 调用都 `RetrievalCache()` 新建实例，清空的是刚创建的空
# _memory，而真正服务读写的实例是 pipeline 单例持有的那个（dependencies.py），
# 导致 Redis 不可用时文档更新后旧内容仍命中缓存。
_SHARED_MEMORY: "BoundedLRUCache | None" = None


def _get_shared_memory() -> "BoundedLRUCache":
    """获取进程级共享的内存回退缓存（容量取 RAG_CACHE_MAX_SIZE）。"""
    global _SHARED_MEMORY
    if _SHARED_MEMORY is None:
        max_size = max(100, int(getattr(settings, "RAG_CACHE_MAX_SIZE", 1000) or 1000))
        _SHARED_MEMORY = BoundedLRUCache(max_size=max_size)
    return _SHARED_MEMORY


class BoundedLRUCache:
    """Size-bounded LRU cache using OrderedDict."""

    def __init__(self, max_size: int = 1000):
        self._cache: OrderedDict[str, Any] = OrderedDict()
        self._max_size = max(100, max_size)

    def get(self, key: str) -> Any | None:
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        return None

    def set(self, key: str, value: Any) -> None:
        if key in self._cache:
            self._cache.move_to_end(key)
        self._cache[key] = value
        if len(self._cache) > self._max_size:
            # 指标补埋（issue #242 第 10 条）：内存回退缓存的淘汰此前从未计数。
            from app.core.prometheus import RAG_CACHE_EVICTIONS

            while len(self._cache) > self._max_size:
                self._cache.popitem(last=False)
                RAG_CACHE_EVICTIONS.labels(cache_type="memory").inc()

    def pop(self, key: str) -> Any | None:
        return self._cache.pop(key, None)

    def __len__(self) -> int:
        return len(self._cache)

    def oldest_key(self) -> str | None:
        if self._cache:
            return next(iter(self._cache))
        return None

    def clear_matching(self, prefix: str) -> None:
        """Remove all entries whose key starts with prefix (org-scoped invalidation)."""
        keys = [k for k in list(self._cache.keys()) if k.startswith(prefix)]
        for k in keys:
            self._cache.pop(k, None)


class RetrievalCache:
    """Exact-match retrieval cache backed by Redis (with bounded LRU memory fallback)."""

    def __init__(self):
        # 修复（R5）：所有实例共享同一内存回退缓存，保证任意实例上的
        # clear_for_org 都能清理到实际服务读写的 pipeline 单例缓存数据。
        self._memory = _get_shared_memory()

    def _key(
        self, query: str, organization_id: int, top_k: int, search_mode: str | None = None
    ) -> str:
        # issue #225 决策 7A：显式 search_mode 的结果按模式分域——显式模式与自动
        # 路由互不可见（既不让覆盖请求命中/复用自动路由的结果，也防止显式模式的
        # 结果被后续自动路由请求当作自己的结果返回）。search_mode=None 时键与
        # 修前逐字节相同（org=…|topk=…|q=…），自动路由行为零变化。
        mode_seg = f"|mode={search_mode}" if search_mode else ""
        return f"org={organization_id}|topk={top_k}{mode_seg}|q={query.strip().lower()}"

    def describe_key(
        self, query: str, organization_id: int, top_k: int, search_mode: str | None = None
    ) -> dict[str, Any]:
        """issue #195：debug 用的键摘要（含分域信息，不含明文查询）。"""
        return {
            "key_digest": _key_digest(self._key(query, organization_id, top_k, search_mode)),
            "organization_id": organization_id,
            "top_k": top_k,
            "search_mode": search_mode,
        }

    async def get(
        self, query: str, organization_id: int, top_k: int, search_mode: str | None = None
    ) -> list[dict[str, Any]] | None:
        if not settings.RAG_ENABLE_CACHE:
            return None
        key = self._key(query, organization_id, top_k, search_mode)
        # Try Redis first
        try:
            rc = _get_redis()
            if rc is not None:
                raw = await rc.get(f"rag:cache:{key}")
                if raw:
                    return json.loads(raw)
        except Exception:
            pass
        # Fallback to memory
        row = self._memory.get(key)
        if not row:
            return None
        ttl = max(1, int(settings.RAG_CACHE_TTL_SECONDS or 120))
        if time.time() - row["ts"] > ttl:
            self._memory.pop(key)
            return None
        return row["value"]

    async def set(
        self,
        query: str,
        organization_id: int,
        top_k: int,
        value: list[dict[str, Any]],
        search_mode: str | None = None,
    ) -> None:
        if not settings.RAG_ENABLE_CACHE:
            return
        key = self._key(query, organization_id, top_k, search_mode)
        # Try Redis
        try:
            rc = _get_redis()
            if rc is not None:
                ttl = max(1, int(settings.RAG_CACHE_TTL_SECONDS or 120))
                await rc.setex(f"rag:cache:{key}", ttl, json.dumps(value, ensure_ascii=False))
                return
        except Exception:
            pass
        # Fallback to memory (bounded LRU)
        self._memory.set(key, {"ts": time.time(), "value": value})

    async def clear_for_org(self, organization_id: int) -> None:
        """Invalidate all exact-match cache entries for an organization."""
        # 内存 LRU 中清理该组织的匹配项
        self._memory.clear_matching(f"org={organization_id}|")
        # Redis 中按 SCAN 匹配删除（无 Redis 时静默跳过）
        try:
            rc = _get_redis()
            if rc is not None:
                pattern = f"rag:cache:org={organization_id}|*"
                async for key in rc.scan_iter(match=pattern, count=100):
                    await rc.delete(key)
        except Exception:
            pass


class SemanticCache:
    """Semantic cache using Redis Sorted Set for O(log n) similarity search.

    Stores query embeddings in a sorted set keyed by a quantized hash.
    At lookup time, finds candidates within a Hamming distance window
    and checks cosine similarity only on those candidates.

    修复（R4）：问答缓存（chat_service 缓存完整 answer）与检索缓存
    （RAGPipeline 只缓存 sources、answer 为空）使用两个独立命名空间，
    否则检索阶段写入的空 answer 条目会被问答链路按相似度命中，
    在用户中断流式回答后返回空消息。
    """

    # 检索专用命名空间（RAGPipeline 使用，见 for_retrieval）
    RETRIEVAL_PREFIX = "rag:sem:retrieval:"
    RETRIEVAL_INDEX_KEY = "rag:semantic_index:retrieval"

    def __init__(
        self,
        prefix: str = "rag:sem:",
        index_key: str = "rag:semantic_index",
        *,
        namespace: str = "answer",
    ):
        # issue #259：字段已补进 Settings（此前 getattr 兜底因字段缺失而 .env 无效）
        self.threshold = settings.SEMANTIC_CACHE_THRESHOLD
        self.ttl = settings.SEMANTIC_CACHE_TTL_SECONDS
        self.index_key = index_key
        self.prefix = prefix
        self._quantize_bits = 64
        # issue #195：两个命名空间分别受 SEMANTIC_CACHE_ANSWER_ENABLED /
        # SEMANTIC_CACHE_RETRIEVAL_ENABLED 控制，默认均为 False（见 enabled）。
        self._namespace = namespace

    @property
    def enabled(self) -> bool:
        """命名空间开关（动态读 settings；默认关闭，issue #195）。

        动态读取而非 __init__ 快照：chat_service 的模块级实例在 import 时
        构造，配置/测试 monkeypatch 必须能即时生效。
        """
        setting_name = (
            "SEMANTIC_CACHE_RETRIEVAL_ENABLED"
            if self._namespace == "retrieval"
            else "SEMANTIC_CACHE_ANSWER_ENABLED"
        )
        return bool(getattr(settings, setting_name, False))

    @classmethod
    def for_retrieval(cls) -> "SemanticCache":
        """构建检索专用实例（独立 key 空间，不与问答缓存互相命中）。"""
        return cls(
            prefix=cls.RETRIEVAL_PREFIX,
            index_key=cls.RETRIEVAL_INDEX_KEY,
            namespace="retrieval",
        )

    def _quantize(self, embedding: list[float]) -> int:
        """Quantize embedding to a compact integer for sorted set scoring."""
        if not embedding:
            return 0
        bits = 0
        step = max(1, len(embedding) // self._quantize_bits)
        for i in range(0, len(embedding), step):
            if i < len(embedding) and embedding[i] > 0:
                bits |= 1 << (i // step)
        return bits

    def _cosine_similarity(self, v1: list[float], v2: list[float]) -> float:
        if not v1 or not v2 or len(v1) != len(v2):
            return 0.0
        dot = sum(a * b for a, b in zip(v1, v2, strict=False))
        na = math.sqrt(sum(a * a for a in v1))
        nb = math.sqrt(sum(b * b for b in v2))
        if na == 0 or nb == 0:
            return 0.0
        return dot / (na * nb)

    def _hash_key(self, embedding: list[float]) -> str:
        vec_str = ",".join(f"{v:.4f}" for v in embedding[:20])
        return hashlib.md5(vec_str.encode(), usedforsecurity=False).hexdigest()[:16]

    async def get(
        self,
        query_embedding: list[float],
        organization_id: int,
        query: str | None = None,
        hit_info: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Find the most similar cached entry **whose query is equivalent**.

        issue #195 命中判据 = 向量余弦 ≥ 阈值 **且** ``queries_equivalent``
        （条号/数字/否定/实体一致）。``query`` 缺省时 fail-closed：没有查询
        原文就无法做等价判定，一律不命中——所有消费方都必须传入查询原文。

        ``hit_info`` 若提供，会被填入本次查找的可解释元数据（供 debug_info）：
        ``cache_type`` / ``namespace`` / ``hit`` / ``similarity`` /
        ``cached_query`` / ``key_digest`` / ``rejected_reason`` /
        ``candidates``。未命中时 ``similarity``/``cached_query`` 指向最相似
        的候选（可能是阈值内但被等价判定拒绝的那条）。

        W0-6：organization_id 必填；非法组织在进入缓存命名空间前显式拒绝。
        """
        org_id = _require_org_id(organization_id, "SemanticCache.get")
        meta = hit_info
        if meta is not None:
            meta.update(
                {
                    "cache_type": "semantic",
                    "namespace": self._namespace,
                    "hit": False,
                    "similarity": None,
                    "cached_query": None,
                    "key_digest": None,
                    "rejected_reason": None,
                    "candidates": 0,
                }
            )

        def _reject(reason: str) -> None:
            if meta is not None:
                meta["rejected_reason"] = reason

        if not self.enabled:
            _reject("namespace_disabled")
            return None
        if not query_embedding:
            _reject("empty_embedding")
            return None
        if not query:
            # issue #195 fail-closed：仅凭向量相似度不足以复用答案。
            _reject("missing_query_text")
            return None
        try:
            rc = _get_redis()
            if rc is None:
                _reject("redis_unavailable")
                return None

            index_key = f"{self.index_key}:{org_id}"
            org_prefix = f"org:{org_id}:"
            query_score = self._quantize(query_embedding)
            candidates = await rc.zrangebyscore(
                index_key,
                max(0, query_score - 2),
                query_score + 2,
            )
            if meta is not None:
                meta["candidates"] = len(candidates)
            if not candidates:
                _reject("no_candidates")
                return None

            best_match = None
            best_score = -1.0
            best_match_key = None
            best_seen_score = -1.0
            best_seen_data = None
            best_seen_key = None
            best_seen_reason = "no_candidates"

            for cache_key in candidates:
                if isinstance(cache_key, bytes):
                    cache_key = cache_key.decode()
                if org_prefix not in cache_key:
                    continue
                raw = await rc.get(cache_key)
                if not raw:
                    await rc.zrem(index_key, cache_key)
                    continue
                data = json.loads(raw)
                cached_emb = data.get("embedding", [])
                sim = self._cosine_similarity(query_embedding, cached_emb)

                if sim >= self.threshold:
                    equivalent, reason = queries_equivalent(query, data.get("query") or "")
                else:
                    equivalent, reason = False, "below_threshold"

                if sim > best_seen_score:
                    best_seen_score = sim
                    best_seen_data = data
                    best_seen_key = cache_key
                    best_seen_reason = reason if not equivalent else "ok"

                if equivalent and sim > best_score:
                    best_score = sim
                    best_match = data
                    best_match_key = cache_key

            if best_match:
                logger.info(f"Semantic cache hit (sim={best_score:.4f})")
                if meta is not None:
                    meta.update(
                        {
                            "hit": True,
                            "similarity": round(best_score, 4),
                            "cached_query": best_match.get("query"),
                            "key_digest": _key_digest(best_match_key or ""),
                            "rejected_reason": None,
                        }
                    )
                return best_match

            if meta is not None and best_seen_data is not None:
                meta.update(
                    {
                        "similarity": round(best_seen_score, 4),
                        "cached_query": best_seen_data.get("query"),
                        "key_digest": _key_digest(best_seen_key or ""),
                        "rejected_reason": best_seen_reason,
                    }
                )
            return None
        except Exception as e:
            logger.error(f"Semantic cache lookup failed: {e}")
            _reject(f"lookup_error:{type(e).__name__}")
            return None

    async def set(
        self,
        query: str,
        embedding: list[float],
        answer: str,
        sources: list[dict] | None = None,
        *,
        organization_id: int,
    ) -> None:
        """Store a Q&A pair in the semantic cache.

        W0-6：organization_id 改为必填的 keyword-only 参数（旧默认值 0 已删除），
        非法组织在写入任何键之前显式拒绝。
        issue #195：命名空间默认关闭（enabled=False）时不写入任何键。
        """
        org_id = _require_org_id(organization_id, "SemanticCache.set")
        if not self.enabled:
            return
        if not embedding:
            return
        try:
            rc = _get_redis()
            if rc is None:
                return

            index_key = f"{self.index_key}:{org_id}"
            org_prefix = f"org:{org_id}:"
            cache_key = f"{self.prefix}{org_prefix}{self._hash_key(embedding)}"
            data = {
                "query": query,
                "embedding": embedding,
                "answer": answer,
                "sources": sources or [],
            }
            score = self._quantize(embedding)

            pipe = rc.pipeline()
            pipe.setex(cache_key, self.ttl, json.dumps(data, ensure_ascii=False))
            pipe.zadd(index_key, {cache_key: score})
            await pipe.execute()
            logger.info(f"Semantic cache stored: '{query[:30]}...'")
        except Exception as e:
            logger.error(f"Semantic cache store failed: {e}")

    async def clear_for_org(self, organization_id: int) -> None:
        """Invalidate all semantic cache entries for an organization.

        修复（R4）：问答与检索两个命名空间都要清理（调用点仍以
        `SemanticCache()` 默认实例调用），否则文档更新后检索缓存仍会命中旧内容。
        """
        try:
            rc = _get_redis()
            if rc is None:
                return
            namespaces = {
                self.prefix: self.index_key,
                self.RETRIEVAL_PREFIX: self.RETRIEVAL_INDEX_KEY,
            }
            for prefix, index_key in namespaces.items():
                cache_prefix = f"{prefix}org:{organization_id}:"
                async for key in rc.scan_iter(match=f"{cache_prefix}*", count=100):
                    await rc.delete(key)
                await rc.delete(f"{index_key}:{organization_id}")
        except Exception as e:
            logger.error(f"Semantic cache clear failed: {e}")
