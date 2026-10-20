"""issue #195：语义缓存跨问题命中（P1）——条号/实体等价判定 + 默认关闭。

背景
----
`SemanticCache` 的命中判据此前只有「query embedding 余弦 ≥
SEMANTIC_CACHE_THRESHOLD（默认 0.92）」，不比对查询文本。相邻条款
（第七条 / 第十三条）或同句式换实体（北京 / 上海、劳动法 / 劳动合同法）的
查询向量余弦可以 ≥ 0.92，于是：

- 检索链（`app/rag/pipeline.py`，for_retrieval 命名空间）直接返回**另一个
  问题**的 sources；
- 问答链（`app/services/chat_service.py`，默认命名空间）更严重——直接返回
  **另一个问题**的完整答案。

两条链共用 `app/rag/cache.py::SemanticCache`，本次修复口径（本文件锁定的契约）：

1. 两个语义缓存命名空间**默认关闭**：
   `SEMANTIC_CACHE_RETRIEVAL_ENABLED=False`（检索 sources）、
   `SEMANTIC_CACHE_ANSWER_ENABLED=False`（问答 answer）。
   精确缓存 `RetrievalCache`（query+org+top_k 精确键）不受影响、默认仍开启。
2. 显式开启后，阈值内的候选还必须通过 `queries_equivalent()` 文本等价判定：
   - 「第X条」/「§X.Y」/「第X款（项）」/「第X条之一」/「第X条（二）」引用集合
     必须完全一致（第七十七 ⇄ 第77 数形先归一；多引用/少引用一律不命中）；
   - 数字 token（NFKC 后、排除条款跨度内数字）一致（2024 年 ≠ 2025 年）；
   - 否定词计数一致（含税 ≠ 不含税）；
   - 疑问意图一致（两侧都有意图词时）：是什么 ≠ 为什么、怎么 ≠ 是什么；
   - CJK 内容（去问句功能字/问句短语、去掉条款跨度后）**完全一致**——
     北京 ≠ 上海、劳动法 ≠ 劳动合同法、劳动合同法 ≠ 劳动合同法实施条例、
     高温补贴标准 ≠ 北京高温补贴标准；「第七条的工资怎么算」与
     「第七条工资怎么算」可命中；
   - 拉丁术语（含单字母选项 A/B）集合完全一致（k8s deployment ≠ k8s service、
     方案 A ≠ 方案 B）。
3. 无查询文本（query=None）→ fail-closed：无法做等价判定 = 不命中。
   （所有消费方都必须显式传入查询原文。）
4. `get(..., hit_info=...)` 输出命中/未命中元数据：cache_type / namespace /
   hit / similarity / cached_query / key_digest / rejected_reason /
   candidates，供 debug_info 排查「是不是缓存导致的」。

变异验证（修复后逐一还原，对应断言应变红）
------------------------------------------
- M1 去掉 `queries_equivalent` 调用（只看 sim ≥ threshold）→
  `TestCrossQuestionRejection` 全部红（跨问题命中复活）；
- M2 去掉条号集合比较（只留内容比较）→
  `test_multi_clause_query_does_not_hit_single_clause` 红（真实命中）；
  `test_cross_clause_number_does_not_hit` 也红，但红在 rejected_reason
  （reasons 变为 no_content_signal，不再命中——记录此差异，避免过度解读）；
- M3 把默认值改回 True → `test_namespaces_default_disabled` 红；
- M4 等价判定改成「查询原文完全相等」→ `test_same_clause_paraphrase_hits` 与
  纯函数正例中的非原文相等项红；
- M5 去掉 query=None 的 fail-closed 分支 → `test_missing_query_text_fails_closed`
  红；但红在 rejected_reason 字符串（no_content_signal ≠ missing_query_text），
  命中仍为 None——等价判定自身也 fail-closed，这是纵深防御而非单点守卫；
- M6 去掉数字 token / 否定词比较 → 对应数字/否定用例红；
- M7 pipeline 不写 debug["cache"] → `TestPipelineDebug` 红；
- M8 把内容比较从「完全一致」退回「单侧包含」→
  `TestCrossQuestionRejection::test_document_title_prefix_does_not_hit` /
  `test_one_sided_qualifier_does_not_hit` 红（跨文件/限定语命中复活）。
"""
import asyncio
import json
import math
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import Settings, settings
from app.core.config.ai import AISettings
from app.rag.cache import RetrievalCache, SemanticCache, queries_equivalent

# ── 测试替身 ─────────────────────────────────────────────────────────

class _FakeRedis:
    """最小内存 Redis 替身（覆盖 SemanticCache / RetrievalCache 用到的接口）。"""

    def __init__(self):
        self.values: dict[str, str] = {}
        self.members: dict[str, dict[str, int]] = {}

    def pipeline(self):
        return self

    def setex(self, key: str, _ttl: int, value: str) -> None:
        self.values[key] = value

    def zadd(self, key: str, mapping: dict[str, int]) -> None:
        self.members.setdefault(key, {}).update(mapping)

    async def execute(self) -> list:
        return []

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def zrangebyscore(self, key: str, lo: int, hi: int) -> list[str]:
        return [m for m, s in self.members.get(key, {}).items() if lo <= s <= hi]

    async def zrem(self, key: str, member: str) -> None:
        self.members.get(key, {}).pop(member, None)

    async def scan_iter(self, match: str = "*", count: int = 100):
        import fnmatch

        for key in list(self.values):
            if fnmatch.fnmatch(key, match):
                yield key

    async def delete(self, key: str) -> None:
        self.values.pop(key, None)
        self.members.pop(key, None)


_VEC_A = [1.0, 0.0, 0.0, 0.0]
# cos([1,0,0,0], [0.98,0.19,0,0]) ≈ 0.9817 ≥ 0.92 —— 阈值内但语义不同。
_VEC_NEAR = [0.98, 0.19, 0.0, 0.0]


def _enable(monkeypatch, *, answer: bool | None = None, retrieval: bool | None = None):
    if answer is not None:
        monkeypatch.setattr(settings, "SEMANTIC_CACHE_ANSWER_ENABLED", answer)
    if retrieval is not None:
        monkeypatch.setattr(settings, "SEMANTIC_CACHE_RETRIEVAL_ENABLED", retrieval)


async def _seed(cache: SemanticCache, query: str, embedding=None, org: int = 1, answer: str = "A"):
    await cache.set(
        query, list(embedding or _VEC_A), answer,
        [{"chunk_id": "cached_chunk"}], organization_id=org,
    )


def _fake_redis(monkeypatch) -> _FakeRedis:
    fake = _FakeRedis()
    monkeypatch.setattr("app.rag.cache._get_redis", lambda: fake)
    return fake


# ── 跨问题命中必须被拒绝 ─────────────────────────────────────────────

class TestCrossQuestionRejection:
    @pytest.mark.asyncio
    async def test_cross_clause_number_does_not_hit(self, monkeypatch):
        """第七条 ≠ 第十三条：余弦 0.9817（阈值内）也不得命中。"""
        _enable(monkeypatch, answer=True)
        _fake_redis(monkeypatch)
        cache = SemanticCache()
        await _seed(cache, "《劳动合同法》第七条是什么")

        meta: dict = {}
        hit = await cache.get(
            _VEC_NEAR, 1,
            query="《劳动合同法》第十三条是什么", hit_info=meta,
        )

        assert hit is None, "条号不同（第七 vs 第十三）仍命中——跨问题命中复活"
        assert meta["hit"] is False
        assert meta["rejected_reason"] == "clause_mismatch"
        assert meta["similarity"] >= 0.92, "前提：候选余弦在阈值内（不是靠低相似度拒绝）"
        assert meta["cached_query"] == "《劳动合同法》第七条是什么", "debug 必须指出命中了哪条缓存查询"

    @pytest.mark.asyncio
    async def test_multi_clause_query_does_not_hit_single_clause(self, monkeypatch):
        """多条引用 vs 单条引用：内容字符全被覆盖，只有条号集合比较能拦住。

        变异 M2（删条号集合比较）→ 本用例精确红。
        """
        _enable(monkeypatch, answer=True)
        _fake_redis(monkeypatch)
        cache = SemanticCache()
        await _seed(cache, "《X法》第七条是什么")

        hit = await cache.get(_VEC_NEAR, 1, query="《X法》第七条和第二十条是什么")
        assert hit is None, "查询引用了额外条号（第二十条），不得命中单条缓存"

    @pytest.mark.asyncio
    async def test_different_entity_does_not_hit(self, monkeypatch):
        """同句式换实体：北京 ≠ 上海。"""
        _enable(monkeypatch, answer=True)
        _fake_redis(monkeypatch)
        cache = SemanticCache()
        await _seed(cache, "北京地区高温补贴标准是什么")

        meta: dict = {}
        hit = await cache.get(_VEC_NEAR, 1, query="上海地区高温补贴标准是什么", hit_info=meta)

        assert hit is None, "实体不同（北京 vs 上海）仍命中——归属错误"
        assert meta["rejected_reason"] == "entity_mismatch"

    @pytest.mark.asyncio
    async def test_entity_substring_does_not_hit(self, monkeypatch):
        """劳动法 ≠ 劳动合同法：单侧内容字被包含也不够（必须按 run 连续覆盖）。"""
        _enable(monkeypatch, answer=True)
        _fake_redis(monkeypatch)
        cache = SemanticCache()
        await _seed(cache, "《劳动法》第七条是什么")

        hit = await cache.get(_VEC_NEAR, 1, query="《劳动合同法》第七条是什么")
        assert hit is None, "不同法律（劳动法/劳动合同法）不得互相命中"

    @pytest.mark.asyncio
    async def test_number_token_does_not_hit(self, monkeypatch):
        """数字实体不同：2024 年 ≠ 2025 年。"""
        _enable(monkeypatch, answer=True)
        _fake_redis(monkeypatch)
        cache = SemanticCache()
        await _seed(cache, "2024年高温补贴标准是什么")

        meta: dict = {}
        hit = await cache.get(_VEC_NEAR, 1, query="2025年高温补贴标准是什么", hit_info=meta)

        assert hit is None, "数字 token 不同仍命中"
        assert meta["rejected_reason"] == "number_mismatch"

    @pytest.mark.asyncio
    async def test_negation_does_not_hit(self, monkeypatch):
        """否定词不同：含税价格 ≠ 不含税价格。"""
        _enable(monkeypatch, answer=True)
        _fake_redis(monkeypatch)
        cache = SemanticCache()
        await _seed(cache, "含税价格怎么计算")

        meta: dict = {}
        hit = await cache.get(_VEC_NEAR, 1, query="不含税价格怎么计算", hit_info=meta)

        assert hit is None, "否定词不同仍命中（含税/不含税是相反问题）"
        assert meta["rejected_reason"] == "negation_mismatch"

    @pytest.mark.asyncio
    async def test_sub_clause_ref_does_not_hit(self, monkeypatch):
        """第七条 ≠ 第七条第三款：款/项引用也必须一致。"""
        _enable(monkeypatch, answer=True)
        _fake_redis(monkeypatch)
        cache = SemanticCache()
        await _seed(cache, "第七条是什么")

        hit = await cache.get(_VEC_NEAR, 1, query="第七条第三款是什么")
        assert hit is None, "款引用不同仍命中"

    @pytest.mark.asyncio
    async def test_subarticle_ref_does_not_hit(self, monkeypatch):
        """第七条 ≠ 第七条之一：正式立法子条引用也必须一致（对抗评审 F1）。"""
        _enable(monkeypatch, answer=True)
        _fake_redis(monkeypatch)
        cache = SemanticCache()
        await _seed(cache, "第七条是什么")

        meta: dict = {}
        hit = await cache.get(_VEC_NEAR, 1, query="第七条之一是什么", hit_info=meta)
        assert hit is None, "子条「之一」与正文互相命中"
        assert meta["rejected_reason"] == "sub_clause_mismatch"

    @pytest.mark.asyncio
    async def test_item_paren_ref_does_not_hit(self, monkeypatch):
        """第七条 ≠ 第七条（二）：括号项引用也必须一致（对抗评审 F1）。"""
        _enable(monkeypatch, answer=True)
        _fake_redis(monkeypatch)
        cache = SemanticCache()
        await _seed(cache, "第七条是什么")

        hit = await cache.get(_VEC_NEAR, 1, query="第七条（二）是什么")
        assert hit is None, "括号项引用与正文互相命中"

    @pytest.mark.asyncio
    async def test_document_title_prefix_does_not_hit(self, monkeypatch):
        """不同文件：劳动合同法 ≠ 劳动合同法实施条例（对抗评审 F2）。

        变异 M8（内容比较退回单侧包含）→ 本用例精确红。
        """
        _enable(monkeypatch, answer=True)
        _fake_redis(monkeypatch)
        cache = SemanticCache()
        await _seed(cache, "《劳动合同法》第七条是什么", answer="劳动合同法第七条答案")

        meta: dict = {}
        hit = await cache.get(
            _VEC_NEAR, 1, query="《劳动合同法实施条例》第七条是什么", hit_info=meta
        )
        assert hit is None, "不同法律文件（法律 vs 行政法规）互相命中"
        assert meta["rejected_reason"] == "entity_mismatch"

    @pytest.mark.asyncio
    async def test_one_sided_qualifier_does_not_hit(self, monkeypatch):
        """单侧限定语/范围词：5天 ≠ 5天以上、泛问 ≠ 带条件问法（对抗评审 F4）。

        变异 M8（内容比较退回单侧包含）→ 本用例精确红。
        """
        _enable(monkeypatch, answer=True)
        _fake_redis(monkeypatch)
        cache = SemanticCache()
        await _seed(cache, "年假5天怎么计算")

        hit = await cache.get(_VEC_NEAR, 1, query="年假5天以上怎么计算")
        assert hit is None, "单侧新增范围词仍命中"

    @pytest.mark.asyncio
    async def test_single_letter_latin_entity_does_not_hit(self, monkeypatch):
        """单字母选项标签：方案A ≠ 方案B（对抗评审 F3）。"""
        _enable(monkeypatch, answer=True)
        _fake_redis(monkeypatch)
        cache = SemanticCache()
        await _seed(cache, "方案A的具体内容")

        meta: dict = {}
        hit = await cache.get(_VEC_NEAR, 1, query="方案B的具体内容", hit_info=meta)
        assert hit is None, "单字母拉丁实体被丢弃后互相命中"
        assert meta["rejected_reason"] == "term_mismatch"

    @pytest.mark.asyncio
    async def test_question_intent_does_not_hit(self, monkeypatch):
        """是什么 ≠ 为什么（对抗评审 F5）。"""
        _enable(monkeypatch, answer=True)
        _fake_redis(monkeypatch)
        cache = SemanticCache()
        await _seed(cache, "第七条是什么")

        meta: dict = {}
        hit = await cache.get(_VEC_NEAR, 1, query="第七条为什么", hit_info=meta)
        assert hit is None, "what/why 意图被停用字抹平后互相命中"
        assert meta["rejected_reason"] == "intent_mismatch"


class TestQueriesEquivalentUnit:
    """等价判定纯函数直测（不经缓存管道，固定语义口径）。"""

    @pytest.mark.parametrize(
        "q1,q2",
        [
            # 问句功能字/短语差异：去掉功能字后内容逐字一致。
            ("第七条是什么", "第七条是什么？"),
            ("第七条的工资怎么算", "第七条工资怎么算"),
            ("《劳动合同法》第七条是什么", "劳动合同法第七条是什么"),
            ("第七十七条是什么", "第77条是什么"),
            ("§2.3 怎么规定", "§2.3 的规定"),
            ("怎么办理", "如何办理"),
            ("公司注册资本最低限额", "公司的注册资本最低限额是多少"),
            # 规范化后原文相同：即使全是功能字（无内容信号）也按同一问题处理。
            ("怎么办", "怎么办"),
        ],
    )
    def test_equivalent_pairs(self, q1, q2):
        ok, reason = queries_equivalent(q1, q2)
        assert ok, f"{q1!r} ≈ {q2!r} 应等价，实际拒绝原因 {reason}"

    @pytest.mark.parametrize(
        "q1,q2,reason",
        [
            ("第七条是什么", "第十三条是什么", "clause_mismatch"),
            ("第七条规定", "第七条和第二十条规定", "clause_mismatch"),
            ("北京天气怎么样", "上海天气怎么样", "entity_mismatch"),
            ("含税价格", "不含税价格", "negation_mismatch"),
            ("2024年标准", "2025年标准", "number_mismatch"),
            ("k8s deployment 怎么配置", "k8s service 怎么配置", "term_mismatch"),
            # 对抗评审 F1/F2/F3/F4/F5 的绕过类固定为负例。
            ("第七条是什么", "第七条之一是什么", "sub_clause_mismatch"),
            ("第七条是什么", "第七条（二）是什么", "sub_clause_mismatch"),
            ("第七条第一项", "第七条第二项", "sub_clause_mismatch"),
            ("劳动合同法", "劳动合同法实施条例", "entity_mismatch"),
            (
                "《劳动合同法》第七条是什么",
                "《劳动合同法实施条例》第七条是什么",
                "entity_mismatch",
            ),
            ("年假5天", "年假5天以上", "entity_mismatch"),
            ("方案A", "方案B", "term_mismatch"),
            ("plan A", "plan B", "term_mismatch"),
            ("第七条是什么", "第七条为什么", "intent_mismatch"),
            ("§2.3 怎么规定", "§2.4 怎么规定", "section_mismatch"),
        ],
    )
    def test_non_equivalent_pairs(self, q1, q2, reason):
        ok, got = queries_equivalent(q1, q2)
        assert not ok, f"{q1!r} ≉ {q2!r} 却被判等价"
        assert got == reason


# ── 精确/同义命中不得被误伤 ───────────────────────────────────────────

class TestPositiveHits:
    @pytest.mark.asyncio
    async def test_identical_query_hits(self, monkeypatch):
        _enable(monkeypatch, answer=True)
        _fake_redis(monkeypatch)
        cache = SemanticCache()
        await _seed(cache, "《劳动合同法》第七条是什么", answer="缓存答案")

        meta: dict = {}
        hit = await cache.get(_VEC_A, 1, query="《劳动合同法》第七条是什么", hit_info=meta)

        assert hit is not None, "完全相同的查询必须命中（缓存的正当收益）"
        assert hit["answer"] == "缓存答案"
        assert hit["sources"] == [{"chunk_id": "cached_chunk"}]
        assert meta["hit"] is True
        assert meta["similarity"] == pytest.approx(1.0)
        assert meta["cache_type"] == "semantic"
        assert meta["cached_query"] == "《劳动合同法》第七条是什么"
        assert meta["key_digest"], "debug 必须给出缓存键摘要"

    @pytest.mark.asyncio
    async def test_same_clause_paraphrase_hits(self, monkeypatch):
        """同条号、同实体、仅功能字/词序噪声差异（去掉「的」）仍应命中。

        变异 M4（改成原文完全相等）→ 本用例红。注意实体口径是「完全一致」，
        额外内容词（如「如何计算」替换「怎么算」）属于接受的误拒，不在此列。
        """
        _enable(monkeypatch, answer=True)
        _fake_redis(monkeypatch)
        cache = SemanticCache()
        await _seed(cache, "第七条的工资怎么算")

        hit = await cache.get(_VEC_NEAR, 1, query="第七条工资怎么算")
        assert hit is not None, "同条号同实体的近义问法被误伤"

    @pytest.mark.asyncio
    async def test_below_threshold_still_rejected(self, monkeypatch):
        """阈值仍是第一道门：文本等价但余弦 0.5 < 0.92，不得命中。"""
        _enable(monkeypatch, answer=True)
        _fake_redis(monkeypatch)
        cache = SemanticCache()
        await _seed(cache, "第七条是什么")

        meta: dict = {}
        # 同一量化窗口（符号位一致）但余弦 ≈0.707 < 0.92：候选能被取到，靠阈值拒绝。
        hit = await cache.get([0.7, 0.7, 0.0, 0.0], 1, query="第七条是什么", hit_info=meta)
        assert hit is None
        assert meta["rejected_reason"] == "below_threshold"


# ── 默认关闭 / fail-closed / 配置独立性 ──────────────────────────────

class TestDefaultsAndSafety:
    def test_namespaces_default_disabled(self):
        assert AISettings.model_fields["SEMANTIC_CACHE_RETRIEVAL_ENABLED"].default is False, (
            "检索 sources 语义缓存必须默认关闭（精确缓存已足够）"
        )
        assert AISettings.model_fields["SEMANTIC_CACHE_ANSWER_ENABLED"].default is False, (
            "问答答案语义缓存必须默认关闭（跨问题返回他人答案不可接受）"
        )
        assert SemanticCache().enabled is False
        assert SemanticCache.for_retrieval().enabled is False

    @pytest.mark.asyncio
    async def test_disabled_cache_never_hits_or_writes(self, monkeypatch):
        """默认关闭：即使存在 100% 相似的条目也不命中；set 不落任何键。"""
        fake = _fake_redis(monkeypatch)
        # 开启 → 写入一条完美候选 → 再关闭（模拟历史条目 / 配置回退）。
        _enable(monkeypatch, answer=True)
        cache = SemanticCache()
        await _seed(cache, "第七条是什么")
        assert fake.values, "前提：开启时确实写入了缓存"

        _enable(monkeypatch, answer=False)
        meta: dict = {}
        hit = await cache.get(_VEC_A, 1, query="第七条是什么", hit_info=meta)
        assert hit is None, "命名空间关闭时仍命中了缓存"
        assert meta["rejected_reason"] == "namespace_disabled"

        before = dict(fake.values)
        await cache.set("新查询", list(_VEC_A), "答案", organization_id=1)
        assert fake.values == before, "命名空间关闭时 set 不得写入任何键"

    @pytest.mark.asyncio
    async def test_missing_query_text_fails_closed(self, monkeypatch):
        """无查询文本（旧调用签名）→ 无法等价判定 → 不命中。

        变异 M5（去掉该分支）→ 本用例红（阈值内直接命中）。
        """
        _enable(monkeypatch, answer=True)
        _fake_redis(monkeypatch)
        cache = SemanticCache()
        await _seed(cache, "第七条是什么")

        meta: dict = {}
        hit = await cache.get(_VEC_A, 1, hit_info=meta)  # 不传 query
        assert hit is None, "缺 query 文本时不得仅凭向量相似度命中（fail-closed）"
        assert meta["rejected_reason"] == "missing_query_text"

    def test_retrieval_and_answer_flags_are_independent(self, monkeypatch):
        _enable(monkeypatch, answer=True, retrieval=False)
        assert SemanticCache().enabled is True
        assert SemanticCache.for_retrieval().enabled is False

        _enable(monkeypatch, answer=False, retrieval=True)
        assert SemanticCache().enabled is False
        assert SemanticCache.for_retrieval().enabled is True

    @pytest.mark.asyncio
    async def test_exact_retrieval_cache_unaffected(self, monkeypatch):
        """检索精确缓存（RetrievalCache）默认仍工作——语义缓存关闭不误伤它。"""
        monkeypatch.setattr("app.rag.cache._get_redis", lambda: None)
        cache = RetrievalCache()
        q = f"精确缓存探针-{uuid.uuid4().hex}"
        await cache.set(q, 3, 5, [{"chunk_id": "exact"}])
        assert await cache.get(q, 3, 5) == [{"chunk_id": "exact"}]

    def test_env_overrides_reach_rebuilt_settings(self, monkeypatch):
        """两个开关可经环境变量覆盖（与 #259 的 .env 接线同一口径）。"""
        monkeypatch.setenv("SEMANTIC_CACHE_RETRIEVAL_ENABLED", "true")
        monkeypatch.setenv("SEMANTIC_CACHE_ANSWER_ENABLED", "true")
        rebuilt = Settings(_env_file=None)
        assert rebuilt.SEMANTIC_CACHE_RETRIEVAL_ENABLED is True
        assert rebuilt.SEMANTIC_CACHE_ANSWER_ENABLED is True


# ── pipeline debug_info：命中与否都可解释 ────────────────────────────

def _make_pipeline(monkeypatch, embedding):
    from app.rag.pipeline import RAGPipeline

    monkeypatch.setattr(
        "app.rag.pipeline.rerank",
        AsyncMock(side_effect=lambda query, docs, client=None: docs),
    )
    p = RAGPipeline()
    p.retriever = MagicMock()
    p.retriever.get_embedding = AsyncMock(return_value=list(embedding))
    p.retriever.retrieve = AsyncMock(return_value=([], [], None, []))
    p.cache = RetrievalCache()
    p.semantic_cache = SemanticCache.for_retrieval()
    return p


class TestPipelineDebug:
    @pytest.mark.asyncio
    async def test_exact_hit_debug_has_key_digest(self, monkeypatch):
        """精确缓存命中也要写 debug["cache"]（键摘要，不含明文 query）。"""
        _fake_redis(monkeypatch)
        q = f"pipeline精确命中探针-{uuid.uuid4().hex}"
        p = _make_pipeline(monkeypatch, _VEC_A)
        await p.cache.set(q, 1, 5, [{"chunk_id": "exact_cached"}])

        results, debug_info = await p.search_knowledge_base(q, 1, top_k=5, debug=True)

        assert [h["chunk_id"] for h in results] == ["exact_cached"]
        assert debug_info["cache_hit"] is True
        assert debug_info["cache_type"] == "exact"
        dbg = debug_info["cache"]
        assert dbg["cache_type"] == "exact"
        assert dbg["key_digest"], "精确缓存命中缺键摘要"
        assert dbg["organization_id"] == 1 and dbg["top_k"] == 5
        assert q not in json.dumps(dbg, ensure_ascii=False), "debug 不得回显明文 query"

    @pytest.mark.asyncio
    async def test_semantic_hit_debug_has_similarity_and_cached_query(self, monkeypatch):
        _enable(monkeypatch, retrieval=True)
        _fake_redis(monkeypatch)
        q = f"pipeline语义命中探针-{uuid.uuid4().hex}"
        p = _make_pipeline(monkeypatch, _VEC_A)
        # 直接写入检索命名空间条目（answer 为空，同 pipeline 写侧形态）
        await p.semantic_cache.set(
            q, list(_VEC_A), "", [{"chunk_id": "cached_sources"}], organization_id=1
        )

        results, debug_info = await p.search_knowledge_base(q, 1, top_k=5, debug=True)

        assert [h["chunk_id"] for h in results] == ["cached_sources"]
        assert debug_info["cache_hit"] is True
        assert debug_info["cache_type"] == "semantic"
        cache_dbg = debug_info["cache"]
        assert cache_dbg["cache_type"] == "semantic"
        assert cache_dbg["similarity"] == pytest.approx(1.0)
        assert cache_dbg["cached_query"] == q
        assert cache_dbg["key_digest"], "debug 缺键摘要"
        assert p.retriever.retrieve.await_count == 0, "语义缓存命中不应再执行检索"

    @pytest.mark.asyncio
    async def test_semantic_miss_debug_reports_rejection_reason(self, monkeypatch):
        """阈值内但条号不同：debug 必须能说明「为什么没命中」，且继续真实检索。"""
        _enable(monkeypatch, retrieval=True)
        _fake_redis(monkeypatch)
        cached_q = f"pipeline跨条探针-{uuid.uuid4().hex}-第七条"
        p = _make_pipeline(monkeypatch, _VEC_NEAR)
        await p.semantic_cache.set(
            cached_q, list(_VEC_A), "", [{"chunk_id": "clause7"}], organization_id=1
        )

        results, debug_info = await p.search_knowledge_base(
            cached_q.replace("第七条", "第十三条"), 1, top_k=5, debug=True
        )

        assert results == [], "跨条款候选不得被当作命中返回"
        assert p.retriever.retrieve.await_count == 1, "语义缓存拒绝后必须继续真实检索"
        assert debug_info["cache"]["hit"] is False
        assert debug_info["cache"]["rejected_reason"] == "clause_mismatch"
        assert debug_info["cache"]["similarity"] >= 0.92


# ── 问答链消费者：跨条不得短路，同问仍可服务 ─────────────────────────

def _mock_chat_session() -> MagicMock:
    session = MagicMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    hist = MagicMock()
    hist.scalars.return_value.all.return_value = []
    hist.scalar_one_or_none = MagicMock(return_value=None)
    session.execute = AsyncMock(return_value=hist)
    return session


def _fake_rag_stack(embedding, search_side_effect, captured: dict) -> MagicMock:
    fake_rag = MagicMock()
    fake_rag.get_embedding = AsyncMock(return_value=list(embedding))
    fake_rag.search_knowledge_base = AsyncMock(side_effect=search_side_effect)
    fake_rag.report_grounded = MagicMock()
    fake_rag.get_last_token_usage = MagicMock(return_value=None)

    async def _chat_stream(query, context, history=None, system_prompt_override=None, **kw):
        captured["chat_stream_calls"] = captured.get("chat_stream_calls", 0) + 1
        yield "通用知识回答"

    fake_rag.chat_stream = _chat_stream
    return fake_rag


def _fake_memory_system() -> MagicMock:
    memory = MagicMock()
    memory._embedding_provider = "stub"
    memory.get_context = AsyncMock(return_value="")
    memory.store_interaction = AsyncMock()
    return memory


def _drain(queue: asyncio.Queue) -> list:
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


async def _run_chat(session, fake_rag, sem_cache, user_content: str, debug: bool = True) -> list:
    from app.services.chat_service import run_rag_pipeline

    queue: asyncio.Queue = asyncio.Queue()
    with (
        patch("app.services.chat_service.rag_service", new=fake_rag),
        patch("app.services.chat_service._semantic_cache", new=sem_cache),
        patch(
            "app.services.chat_service.get_memory_system",
            new=MagicMock(return_value=_fake_memory_system()),
        ),
    ):
        await run_rag_pipeline(
            session=session, user_content=user_content, user_id=1,
            search_org_id=1, organization_id=1, conversation_id=None, file_ids=[],
            strict_mode=False, privacy_mode=False, event_queue=queue, debug=debug,
        )
    return _drain(queue)


class TestAnswerCacheConsumer:
    @pytest.mark.asyncio
    async def test_answer_cache_does_not_answer_across_clauses(self, monkeypatch):
        """问答链：第七条的缓存答案不得回答第十三条的问题。

        修复前此处命中并直接返回 cached answer（search=0）；修复后必须继续
        真实检索链路。（消费者接线判别：get 必须携带查询原文。）
        """
        _enable(monkeypatch, answer=True)
        _fake_redis(monkeypatch)
        sem_cache = SemanticCache()
        await _seed(sem_cache, "《劳动合同法》第七条是什么", answer="第七条的答案")

        captured: dict = {}
        fake_rag = _fake_rag_stack(_VEC_NEAR, AsyncMock(return_value=([], None)), captured)
        events = await _run_chat(
            _mock_chat_session(), fake_rag, sem_cache, "《劳动合同法》第十三条是什么"
        )

        assert fake_rag.search_knowledge_base.await_count == 1, (
            "跨条款查询命中了问答缓存并短路（修复前行为）"
        )
        assert not any(e.type == "message" and e.is_cached for e in events), (
            "不得把第七条的缓存答案当作第十三条的回答返回"
        )

    @pytest.mark.asyncio
    async def test_answer_cache_serves_identical_query(self, monkeypatch):
        _enable(monkeypatch, answer=True)
        _fake_redis(monkeypatch)
        sem_cache = SemanticCache()
        await _seed(sem_cache, "《劳动合同法》第七条是什么", answer="第七条的答案")

        captured: dict = {}
        fake_rag = _fake_rag_stack(_VEC_A, AsyncMock(return_value=([], None)), captured)
        events = await _run_chat(
            _mock_chat_session(), fake_rag, sem_cache, "《劳动合同法》第七条是什么"
        )

        assert fake_rag.search_knowledge_base.await_count == 0, "同问必须由缓存直接服务"
        cached_msgs = [e for e in events if e.type == "message" and e.is_cached]
        assert cached_msgs and cached_msgs[0].content == "第七条的答案"
        debug_events = [e for e in events if e.type == "debug"]
        assert debug_events, "命中时 debug 事件必须带缓存元数据"
        dbg = debug_events[0].debug_data
        assert dbg["cache_type"] == "semantic"
        assert dbg["cache"]["similarity"] == pytest.approx(1.0)
        assert dbg["cache"]["cached_query"] == "《劳动合同法》第七条是什么"


# ── 纯函数锚点（防止等价判定被悄悄放宽）────────────────────────────

def test_cosine_anchor_matches_triage_repro():
    """三分类复现用的向量对余弦 ≈ 0.9817（阈值 0.92 内），作为判别前提锚点。"""
    cos = sum(a * b for a, b in zip(_VEC_A, _VEC_NEAR, strict=False)) / (
        math.sqrt(sum(a * a for a in _VEC_A)) * math.sqrt(sum(b * b for b in _VEC_NEAR))
    )
    assert cos == pytest.approx(0.9817, abs=1e-3)
    assert cos >= settings.SEMANTIC_CACHE_THRESHOLD
