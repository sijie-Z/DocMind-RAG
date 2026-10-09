"""Experience Store — persistent storage for lessons the Agent learns.

Dual-backend storage:
    - Redis: fast retrieval during normal agent operation
    - Local JSON file: reproducibility for benchmark validation

Usage:
    store = get_experience_store()
    await store.add(experience)
    lessons = await store.search("cross document comparison")
    prompt_section = await store.format_for_planner(query)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.agent.experience.models import Experience

logger = logging.getLogger(__name__)

REDIS_KEY = "agent:experience:store"
"""Redis key for the full experience list."""

LOCAL_PATH = Path("benchmark/experiences.json")
"""Local JSON fallback for benchmark reproducibility (repo-committable)."""


class ExperienceStore:
    """Persistent experience store. Thread-safe for single-process usage.

    Loads lazily on first access. Always prefers Redis; falls back to local JSON.
    A load only counts as "loaded" when a backend actually answered (data, or
    an authoritative "no data"); an unreachable backend leaves the store
    unloaded so the next access retries (issue #301 #13) instead of freezing
    an empty view for the process lifetime.
    """

    def __init__(self) -> None:
        self._experiences: dict[str, Experience] = {}
        self._loaded = False

    # ── CRUD ──

    async def add(self, exp: Experience) -> None:
        """Insert or update an experience."""
        await self._ensure_loaded()
        self._experiences[exp.id] = exp
        await self._save()
        logger.debug("Experience stored: %s", exp.short_summary())

    async def get(self, exp_id: str) -> Experience | None:
        """Retrieve a single experience by ID."""
        await self._ensure_loaded()
        return self._experiences.get(exp_id)

    async def delete(self, exp_id: str) -> bool:
        """Delete an experience by ID. Returns True if it existed."""
        await self._ensure_loaded()
        removed = self._experiences.pop(exp_id, None)
        if removed:
            await self._save()
        return removed is not None

    async def list_all(self) -> list[Experience]:
        """Return all experiences, sorted by confidence descending."""
        await self._ensure_loaded()
        return sorted(
            self._experiences.values(),
            key=lambda e: (e.confidence, e.verified_count),
            reverse=True,
        )

    async def count(self) -> int:
        """Return the total number of stored experiences."""
        await self._ensure_loaded()
        return len(self._experiences)

    # ── Semantic Search (keyword-based, Stage 1) ──

    # Query scenario detection — what kind of task is the user asking about?
    # Used to filter out experiences that cause Negative Transfer.
    _QUERY_SCENARIOS: dict[str, list[str]] = {
        "multi_step_analysis": [
            "分析", "趋势", "总结", "近三年", "逐年",
            "深入", "拆分", "拆解", "综合", "全流程",
        ],
        "framework_analysis": [
            "swot", "pest", "杜邦", "框架", "五力",
            "pestel", "波士顿", "矩阵",
        ],
        "cross_document": [
            "比较", "对比", "两家", "多家", "差异",
            "高于", "低于", "优劣", "区别",
        ],
        "single_document": [
            "找一份", "提取", "列出", "找出",
        ],
        "web_search": [
            "最新", "新闻", "融资", "搜索", "查询",
            "最近", "今天", "实时",
        ],
        "tool_recovery": [
            "不存在", "找不到", "没找到", "错误",
            "fake", "404", "失败",
        ],
        "edge_case_simple": [
            "知识库", "有什么", "有哪些", "多少文档",
        ],
        "ambiguity": [],  # detected by length < 8 chars
    }

    @staticmethod
    def _detect_query_scenario(query: str) -> list[str]:
        """Detect what scenario(s) a user query falls into.

        Returns a list of scenario tags (e.g. ['multi_step_analysis', 'cross_document']).
        Empty list means 'unknown/general'.
        """
        q = query.lower()
        detected: list[str] = []

        for scenario, triggers in ExperienceStore._QUERY_SCENARIOS.items():
            if scenario == "ambiguity":
                if len(q.strip()) < 8:
                    detected.append(scenario)
            else:
                for trigger in triggers:
                    if trigger in q:
                        detected.append(scenario)
                        break  # one match per scenario is enough

        return detected

    async def search(
        self,
        query: str,
        top_k: int = 5,
        min_confidence: float = 0.3,
    ) -> list[Experience]:
        """Retrieve top-k experiences relevant to the query.

        Stage 1 implementation: keyword overlap scoring over
        scenario/symptom/lesson fields, with Negative Transfer protection.

        Experiences whose `avoid_for` matches a detected query scenario
        are PENALISED (score × 0.3) rather than hard-filtered — this
        prevents over-filtering when a question spans multiple scenarios.
        """
        await self._ensure_loaded()
        query_lower = query.lower()
        query_scenarios = self._detect_query_scenario(query)

        scored: list[tuple[float, Experience]] = []
        for exp in self._experiences.values():
            if exp.confidence < min_confidence:
                continue

            score = self._score_relevance(query_lower, exp)
            if score <= 0:
                continue

            # ── Negative Transfer protection (soft penalty, not hard filter) ──
            # If this experience's avoid_for matches any detected scenario → ×0.3
            # If this experience's applicable_to doesn't match any → ×0.5
            if query_scenarios:
                if exp.avoid_for and any(s in exp.avoid_for for s in query_scenarios):
                    score *= 0.3
                if exp.applicable_to and not any(s in exp.applicable_to for s in query_scenarios):
                    score *= 0.5

            scored.append((score, exp))

        scored.sort(key=lambda x: -x[0])
        return [exp for _, exp in scored[:top_k]]

    @staticmethod
    def _score_relevance(query: str, exp: Experience) -> float:
        """Score how relevant an experience is to the query.

        Scoring signals:
            - Exact scenario match (+3.0)
            - English keyword overlap (×2.0)
            - Chinese character n-gram overlap (×1.5) — critical for CJK
        """
        score = 0.0
        haystack = f"{exp.scenario} {exp.symptom} {exp.lesson}".lower()
        query_lower = query.lower()

        # Exact scenario match — high signal
        scenario_normalized = exp.scenario.replace("_", " ")
        if scenario_normalized in query_lower:
            score += 3.0

        # English keyword overlap
        query_words = {w for w in query_lower.split() if len(w) > 1}
        if query_words:
            found = sum(1 for w in query_words if w in haystack)
            score += 2.0 * found / len(query_words)

        # Chinese character bigram overlap (enables "比较" ↔ "对比" matching)
        if len(query_lower) > 1:
            query_bigrams = {query_lower[i:i+2] for i in range(len(query_lower) - 1)}
            # Filter to only CJK bigrams
            query_bigrams = {bg for bg in query_bigrams if all('一' <= c <= '鿿' or '　' <= c <= '〿' for c in bg)}
            if query_bigrams:
                haystack_bigrams: set[str] = set()
                for i in range(len(haystack) - 1):
                    bg = haystack[i:i+2]
                    if all('一' <= c <= '鿿' or '　' <= c <= '〿' for c in bg):
                        haystack_bigrams.add(bg)
                if haystack_bigrams:
                    overlap = len(query_bigrams & haystack_bigrams)
                    score += 1.5 * overlap / len(query_bigrams)

        return score

    # ── Planner Injection ──

    async def format_for_planner(
        self,
        query: str,
        top_k: int = 3,
        min_confidence: float = 0.5,
    ) -> str:
        """Format the most relevant experiences as a prompt section.

        Returns an empty string if no relevant experiences found.
        The returned string is designed to be appended to the Planner's
        system prompt.
        """
        experiences = await self.search(query, top_k=top_k, min_confidence=min_confidence)
        if not experiences:
            return ""

        parts = [
            "## 经验教训",
            "以下是从历史失败中总结的经验教训，请在你的规划中参考：",
        ]
        for i, exp in enumerate(experiences, 1):
            conf = f"{exp.confidence:.0%}"
            parts.append(f"{i}. [{exp.scenario}] {exp.lesson}（置信度: {conf}）")

        return "\n".join(parts)

    # ── Import / Export ──

    async def import_experiences(self, experiences: list[Experience]) -> int:
        """Batch import. Returns the count of new experiences added."""
        await self._ensure_loaded()
        existing_ids = set(self._experiences.keys())
        count = 0
        for exp in experiences:
            if exp.id not in existing_ids:
                self._experiences[exp.id] = exp
                count += 1
        if count:
            await self._save()
        return count

    def to_serializable(self) -> list[dict[str, Any]]:
        return [exp.to_dict() for exp in self._experiences.values()]

    # ── Persistence ──

    async def _ensure_loaded(self) -> None:
        """Load once — but only after a backend actually answered (#301 #13).

        旧行为无条件置 ``_loaded=True``：Redis 瞬断 + 本地无文件时，两个后端
        都没答上来却宣布"加载完成（空）"，本进程经验库恒空、重启才恢复。
        新语义（对齐 agent/skills.py 的评审 m2 修法：失败不置位）：
          - 任一后端载入数据（True）→ 已加载；
          - 两个后端都明确答复"没有数据"（False/False）→ 空库是确定性事实，
            置位、不再重试；
          - 其余（至少一个后端"答不出来"：Redis 不可用 / 本地文件读不了）→
            不置位，下次访问重试（load 幂等，重复加载只是 merge）。
        """
        if self._loaded:
            return
        from_redis = await self._load_from_redis()
        from_local: bool | None = None
        if from_redis is not True:
            from_local = self._load_from_local()

        if from_redis is True or from_local is True:
            source = "redis" if from_redis else "local"
            self._loaded = True
            logger.info(
                "ExperienceStore loaded: %d experiences (source: %s)",
                len(self._experiences), source,
            )
        elif from_redis is False and from_local is False:
            self._loaded = True
            logger.info("ExperienceStore loaded: 0 experiences (source: empty)")
        else:
            logger.debug(
                "ExperienceStore: load inconclusive (redis=%s, local=%s); "
                "will retry on next access",
                from_redis, from_local,
            )

    async def _load_from_redis(self) -> bool | None:
        """Tri-state Redis load（issue #301 #13）。

        True  = 载入数据；
        False = Redis 明确答复"没有数据"（键为空）——确定性；
        None  = Redis 不可用（未配置 / 连接异常）——"查不了"，不得与
                "确实没有数据"同形，否则瞬断会被当成空库加载。
        """
        try:
            from app.core.redis import redis_client
            if not redis_client:
                logger.debug("ExperienceStore: Redis not configured; skipping Redis load")
                return None
            raw = await redis_client.get(REDIS_KEY)
            if not raw:
                return False
            data = json.loads(raw)
            for item in data:
                exp = Experience.from_dict(item)
                self._experiences[exp.id] = exp
            return True
        except Exception as e:
            logger.warning("ExperienceStore: Redis load failed: %s", e)
            return None

    def _load_from_local(self) -> bool | None:
        """Tri-state local load（issue #301 #13）。

        True  = 载入数据；False = 文件不存在（确定性"没有数据"）；
        None  = 文件存在但读取/解析失败——"答不出来"，下次重试。
        """
        try:
            if not LOCAL_PATH.exists():
                return False
            data = json.loads(LOCAL_PATH.read_text(encoding="utf-8"))
            for item in data:
                exp = Experience.from_dict(item)
                self._experiences[exp.id] = exp
            return True
        except Exception as e:
            logger.warning("ExperienceStore: local load failed: %s", e)
            return None

    async def _save(self) -> None:
        data = [exp.to_dict() for exp in self._experiences.values()]
        saved_redis = await self._save_to_redis(data)
        saved_local = self._save_to_local(data)
        if not saved_redis and not saved_local:
            logger.warning("ExperienceStore: failed to persist to any backend")

    async def _save_to_redis(self, data: list[dict]) -> bool:
        try:
            from app.core.redis import redis_client
            if redis_client:
                await redis_client.setex(
                    REDIS_KEY, 86400 * 30,
                    json.dumps(data, ensure_ascii=False),
                )
                return True
        except Exception as e:
            logger.debug("ExperienceStore: Redis save failed: %s", e)
        return False

    def _save_to_local(self, data: list[dict]) -> bool:
        try:
            LOCAL_PATH.parent.mkdir(parents=True, exist_ok=True)
            LOCAL_PATH.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return True
        except Exception as e:
            logger.debug("ExperienceStore: local save failed: %s", e)
        return False

    # ── Debug / Testing ──

    def clear(self) -> None:
        """Clear all experiences in memory (for testing)."""
        self._experiences.clear()
        self._loaded = True

    async def reload(self) -> None:
        """Force reload from storage."""
        self._loaded = False
        await self._ensure_loaded()


# ── Singleton ──────────────────────────────────────────────────────────

_store: ExperienceStore | None = None


def get_experience_store() -> ExperienceStore:
    """Return the singleton ExperienceStore instance."""
    global _store
    if _store is None:
        _store = ExperienceStore()
    return _store


def reset_experience_store() -> None:
    """Reset the singleton (for testing)."""
    global _store
    _store = None
