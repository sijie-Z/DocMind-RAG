"""评审残项尾批 · 两项微残收口（原始出处：仓库外评审报告，均不入 issue 单）。

项 1（#301 判定键收口的空值延伸）：
    `{"recommendation": null}` / `""` 等"键存在但值不可用"经 loop
    `review.get("recommendation") or "pass"` 宽容归一为 pass；既有收口只覆盖
    "缺键"。本批按同原则收口：**判定必须是可用的非空字符串** —— 缺键、null、
    空串/空白串、非字符串（数字/布尔/对象/数组）同语义 → no_verdict/error。
    修前实测（本地探针原文留存）：
    reviewer 原样透出 null/""/0/{}，loop 收敛 `review_recommendation: pass`
    且零 review 决策（探针原文）。

项 2（#225 同条件的先行存在缺口）：
    语义缓存**写侧**缺 `not document_ids` 守卫。读侧（精确 L107/语义 L131）
    对文档限定查询一律跳过，写侧此前没有对应守卫：被限定文档范围的检索结果
    经 qv 回填（pipeline `if not query_vector and qv`）后写入 **org 级**语义
    缓存（键只含 org，不含文档范围），同 query 的后续无范围请求命中后吃到被
    裁剪过的结果。修前实测（同一探针）：带 document_ids 查询写出
    `rag:sem:retrieval:org:1:...`，随后无范围同 query `retrieve=0`、返回
    限定臂结果（污染成立）。

变异验证（先提交后变异；每个用例类内注明对应点）：
- M1 还原宽容（reviewer 条件改回 `if "recommendation" not in review_data`）
  → TestRecommendationValueShape + 端到端用例精确红；
- M2 摘掉语义写条件 `and not document_ids` → TestSemanticCacheWriteScope 红；
- M3 条件改为真值判定 `if not recommendation_value`（漏掉 truthy 非字符串）
  → 值形态矩阵的 True/空白串参数位精确红。
"""

import asyncio
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent.config import AgentConfig
from app.agent.events import AgentEvent
from app.agent.loop import PERAgentLoop

# ══════════════════════════════════════════════════════════════════════════
# 项 1 · reviewer 判定值形态（#301 收口延伸）
# ══════════════════════════════════════════════════════════════════════════


def _client_returning(content: str) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
            usage=None,
            model="mock-model",
        )
    )
    return client


def _plan_with_failed_step():
    from app.agent.planner import Plan, PlanStep

    return Plan(
        id="p1",
        goal="分析营收",
        steps=[
            PlanStep(id="s1", description="s1", status="failed"),
        ],
    )


async def _review_with_recommendation(value) -> dict:
    from app.agent.reviewer import Reviewer

    reviewer = Reviewer(
        _client_returning(
            json.dumps(
                {
                    "overall_assessment": "看起来还行",
                    "issues_found": [],
                    "recommendation": value,
                }
            )
        ),
        AgentConfig(),
    )
    return await reviewer.review(_plan_with_failed_step(), {})


class TestRecommendationValueShape:
    """判定值形态矩阵：不可用值 → error + no_verdict；可用字符串原样保留。

    变异验证（M1）：条件改回 `if "recommendation" not in review_data` →
    本类"不可用值"参数位精确红（值原样透出，不再为 error）。
    变异验证（M3）：改真值判定 `if not recommendation_value` → True/空白串
    参数位红（truthy 非字符串漏网）。
    """

    @pytest.mark.parametrize(
        "value",
        [None, "", "   ", 0, {}, [], True, False],
        ids=[
            "null",
            "empty-str",
            "blank-str",
            "int-0",
            "empty-dict",
            "empty-list",
            "true",
            "false",
        ],
    )
    @pytest.mark.asyncio
    async def test_unusable_values_are_no_verdict_error(self, value):
        """规约点：null / "" / 0 / {} 及其同类（空白串/数组/布尔）同"缺键"语义。"""
        review = await _review_with_recommendation(value)

        assert review["recommendation"] == "error", (
            f"recommendation={value!r} 不是可用判定，必须与缺键同列 error（不得落 pass）"
        )
        assert review["error_kind"] == "no_verdict", (
            "三态归类：无判定 ⇒ no_verdict（非 service_error）"
        )
        assert "未给出可用判定" in str(review.get("error", "")), "错误说明必须如实（诊断用）"

    @pytest.mark.parametrize(
        "value",
        ["pass", "revise", "reject", "error", "通过", "  PASS  "],
        ids=["pass", "revise", "reject", "error", "zh-pass", "padded-pass"],
    )
    @pytest.mark.asyncio
    async def test_usable_string_verdicts_pass_through(self, value):
        """对照（防误伤/防过收紧）：非空字符串判定一律原样保留（含模型写"通过"）。

        未识别表述沿用 loop 既有口径（不制造噪音）；本层不做任何字符串改写，
        去除/大小写归一仍是 loop 的职责（`strip().lower()`）。
        """
        review = await _review_with_recommendation(value)

        assert review["recommendation"] == value, "可用字符串不得被本层改写"
        assert review.get("error_kind") is None, "真判定不得被标注 no_verdict"

    @pytest.mark.asyncio
    async def test_missing_key_same_semantics_as_empty_value(self):
        """缺键对照：与空值走同一分支（error + no_verdict + 同口径 error 文案）。

        变异验证（M1）：改回 `not in` 判定 → 本用例仍绿（缺键是 #9 原覆盖），
        但上面矩阵红 —— 两用例合起来钉住"缺键⇔空值同语义"。
        """
        from app.agent.reviewer import Reviewer

        reviewer = Reviewer(
            _client_returning('{"overall_assessment": "x", "issues_found": []}'),
            AgentConfig(),
        )
        review = await reviewer.review(_plan_with_failed_step(), {})

        assert review["recommendation"] == "error"
        assert review["error_kind"] == "no_verdict"


# ── loop 端到端：探针的因果重现（null 曾静默落 pass） ──────────────────────


class _FakeMemory:
    def __init__(self):
        self.results: dict = {}
        self.interactions: list = []

    def reset_working(self) -> None:
        self.results = {}

    def get_all_results(self) -> dict:
        return dict(self.results)

    def store_step_result(self, step_id, result) -> None:
        self.results[step_id] = result

    def get_step_result(self, step_id, run_id=None):
        return self.results.get(step_id)

    async def record_interaction(self, user_input, assistant_response) -> None:
        self.interactions.append((user_input, assistant_response))


class _RecordingPlanner:
    async def plan(self, query, history=None, memory_context="", ctx=None, replan_feedback=""):
        yield AgentEvent(type="plan_start", plan_id="plan-1", content="制定计划...")
        yield AgentEvent(
            type="plan_step",
            plan_id="plan-1",
            plan_step_id="s1",
            content="步骤",
            tool_hint="search_knowledge_base",
        )
        yield AgentEvent(
            type="plan_complete", plan_id="plan-1", plan_goal="目标", content="计划完成"
        )


class _RecordingExecutor:
    def __init__(self, memory, result_text="这是执行产出的完整结果内容。"):
        self.memory = memory
        self.result_text = result_text
        self.retry_calls = 0

    def reset_for_request(self) -> None:
        pass

    # human_guidance：前向兼容 HITL 合流——loop→executor 接缝在 HITL 后新增该
    # kwarg（本替身先于 HITL 落地，合入顺序下两态都须成立；缺省 None 不改本分支基线行为）。
    # document_context：同款前向兼容——第四波 @引用修复在 loop→executor 接缝
    # 新增该 kwarg（缺省 None 不改本分支基线行为）。
    async def execute(
        self,
        plan,
        history=None,
        organization_id=1,
        user_id=0,
        enable_thinking=True,
        ctx=None,
        on_step_done=None,
        human_guidance=None,
        document_context=None,
    ):
        for s in plan.steps:
            s.status = "completed"
            plan.completed_steps += 1
            self.memory.store_step_result(
                s.id,
                {
                    "description": s.description,
                    "status": "completed",
                    "result": self.result_text,
                    "tool_hint": s.tool_hint,
                },
            )
        yield AgentEvent(type="chunk", content=f"答案-{plan.id}")

    async def _execute_step_with_retry(
        self, step, plan, history, organization_id, user_id, enable_thinking, ctx=None
    ):
        self.retry_calls += 1
        yield None


class _ScriptedReflector:
    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.calls = 0

    async def reflect(self, plan, results, ctx=None):
        decision = self.decisions[min(self.calls, len(self.decisions) - 1)]
        self.calls += 1
        yield AgentEvent(
            type="reflection", reflection_result=decision, content="评估理由", plan_id=plan.id
        )


def _make_loop(reviewer):
    loop = PERAgentLoop(
        MagicMock(),
        AgentConfig(enable_memory=False, enable_thinking=False, enable_experience=False),
        organization_id=1,
        user_id=0,
    )
    memory = _FakeMemory()
    loop.memory = memory
    loop._executor = _RecordingExecutor(memory)
    loop._planner = _RecordingPlanner()
    loop._reflector = _ScriptedReflector(["pass"])
    loop._reviewer = reviewer
    loop._write_experiences = AsyncMock()
    return loop


async def _drive(loop):
    async def _collect():
        return [e async for e in loop.run("对比两份财报")]

    return await asyncio.wait_for(_collect(), timeout=20.0)


def _decisions_of(events) -> list:
    ctx_events = [e for e in events if e.type == "execution_context"]
    assert ctx_events, "run() 必须发出 execution_context 事件"
    return ctx_events[-1].content["decisions"]


def _run_report_text(events) -> str:
    reports = [e for e in events if e.type == "run_report"]
    assert reports, "run() 必须发出 run_report 事件"
    return reports[-1].content


@pytest.fixture(autouse=True)
def _no_ctx_persistence(monkeypatch):
    """ExecutionContext.save() 在测试里不应写 Redis / benchmark 目录。"""
    from app.agent.exec_context import ExecutionContext

    async def _save(self, ttl=86400 * 7):
        return False

    monkeypatch.setattr(ExecutionContext, "save", _save)


class TestLoopNullRecommendationCausal:
    """探针因果重现：模型给 null 判定 → 审查必须落 error 面，不得静默 pass。

    变异验证（M1）：还原宽容 → 本类两个断言先红（决策 `[]`、报告行回 pass）。
    """

    @pytest.mark.asyncio
    async def test_null_recommendation_surfaces_as_review_error(self):
        from app.agent.reviewer import Reviewer

        reviewer = Reviewer(
            _client_returning(
                '{"overall_assessment": "x", "issues_found": [], "recommendation": null}'
            ),
            AgentConfig(),
        )
        loop = _make_loop(reviewer)
        events = await _drive(loop)

        # 结构化面：review 决策如实记录 error（修前：null 经 `or "pass"` 静默消失）。
        decisions = _decisions_of(events)
        assert any(d["phase"] == "review" and d["action"] == "error" for d in decisions), (
            f"null 判定必须落 review/error 决策，实际 {decisions}"
        )
        text = _run_report_text(events)
        assert "review_recommendation: error" in text
        assert "review_recommendation: pass" not in text
        # 用户可见面如实暴露；无结论 ≠ 失败，clean_success 不翻转。
        assert any(e.type == "thinking" and "对抗审查未完成" in (e.content or "") for e in events)
        assert "clean_success: True" in text

    @pytest.mark.asyncio
    async def test_valid_pass_still_silent_pass(self):
        """对照（防过触发）：真 pass 判定照旧不产生 error 面。"""
        from app.agent.reviewer import Reviewer

        reviewer = Reviewer(
            _client_returning(
                '{"overall_assessment": "x", "issues_found": [], "recommendation": "pass"}'
            ),
            AgentConfig(),
        )
        loop = _make_loop(reviewer)
        events = await _drive(loop)

        decisions = _decisions_of(events)
        assert not any(d.get("phase") == "review" and d.get("action") == "error" for d in decisions)
        text = _run_report_text(events)
        assert "review_recommendation: pass" in text
        assert not any(
            e.type == "thinking" and "对抗审查未完成" in (e.content or "") for e in events
        )


# ══════════════════════════════════════════════════════════════════════════
# 项 2 · 语义缓存写侧 document_ids 守卫（#225 m4，先行存在）
# ══════════════════════════════════════════════════════════════════════════


class _RecordingCache:
    """记录写入的缓存替身（get 恒未命中）。"""

    def __init__(self):
        self.get_calls: list = []
        self.set_calls: list = []

    async def get(self, *args, **kwargs):
        self.get_calls.append((args, kwargs))
        return

    async def set(self, *args, **kwargs):
        self.set_calls.append((args, kwargs))


class _FakePipe:
    def __init__(self, r):
        self.r = r
        self.ops: list = []

    def setex(self, *a):
        self.ops.append(("setex", a))

    def zadd(self, *a):
        self.ops.append(("zadd", a))

    async def execute(self):
        for op, a in self.ops:
            if op == "setex":
                await self.r.setex(*a)
            else:
                await self.r.zadd(*a)


class _FakeRedis:
    """内存假 Redis：覆盖 SemanticCache / RetrievalCache 用到的接口。"""

    def __init__(self):
        self.kv: dict = {}
        self.z: dict = {}

    async def get(self, k):
        return self.kv.get(k)

    async def setex(self, k, ttl, v):
        self.kv[k] = v

    async def zadd(self, key, mapping):
        self.z.setdefault(key, {}).update(mapping)

    async def zrem(self, key, member):
        self.z.get(key, {}).pop(member, None)

    async def zrangebyscore(self, key, lo, hi):
        return [m for m, s in self.z.get(key, {}).items() if lo <= s <= hi]

    def pipeline(self):
        return _FakePipe(self)

    async def scan_iter(self, match=None, count=100):
        prefix = (match or "").rstrip("*")
        for k in list(self.kv):
            if k.startswith(prefix):
                yield k

    async def delete(self, *keys):
        for k in keys:
            self.kv.pop(k, None)
            self.z.pop(k, None)


# 同 query 恒定向量 → 余弦 1.0（> 阈值 0.92）；命中与否只取决于条目是否存在。
_SEM_VEC = [0.5, 0.5, 0.5, 0.5]


def _doc(chunk_id: str, filename: str) -> dict:
    return {
        "chunk_id": chunk_id,
        "content": f"{chunk_id} 正文",
        "filename": filename,
        "score": 0.9,
        "document_id": chunk_id,
    }


def _no_rerank(monkeypatch):
    """rerank 替身：保持输入顺序（避免真模型加载/网络）。

    issue #189：rerank 契约为 (hits, info)，替身必须回传结构化状态。
    """
    monkeypatch.setattr(
        "app.rag.pipeline.rerank",
        AsyncMock(
            side_effect=lambda query, docs, client=None: (
                docs,
                {
                    "status": "disabled",
                    "path": "noop",
                    "reason": "test_stub",
                    "attempts": [],
                    "scores_attached": False,
                    "model": None,
                },
            )
        ),
    )


def _make_pipeline(retrieve_mock, recording: bool):
    from app.rag.cache import RetrievalCache, SemanticCache
    from app.rag.pipeline import RAGPipeline

    p = RAGPipeline()
    p.retriever = MagicMock()
    p.retriever.get_embedding = AsyncMock(return_value=list(_SEM_VEC))
    p.retriever.retrieve = retrieve_mock
    if recording:
        p.cache = _RecordingCache()
        p.semantic_cache = _RecordingCache()
    else:
        p.cache = RetrievalCache()
        p.semantic_cache = SemanticCache.for_retrieval()
    return p


class TestSemanticCacheWriteScope:
    """写侧守卫（记录替身探针）：文档限定查询不得写任何 org 级缓存。

    变异验证（M2）：摘掉语义写条件 `and not document_ids` → 第一个用例
    "语义缓存零写入"断言精确红。
    """

    @pytest.mark.asyncio
    async def test_scoped_query_writes_no_org_level_cache(self, monkeypatch):
        """带 document_ids：精确缓存（既有守卫）与语义缓存（本批补守卫）零写入。"""
        _no_rerank(monkeypatch)
        retrieve = AsyncMock(
            return_value=([_doc("scoped_only", "scoped.md")], list(_SEM_VEC), None, [])
        )
        p = _make_pipeline(retrieve, recording=True)

        results, _ = await p.search_knowledge_base(
            f"resTail-范围探针-{uuid.uuid4().hex}", 1, top_k=5, document_ids=["d1"]
        )

        assert results and results[0]["chunk_id"] == "scoped_only", "限定检索本身照常返回结果"
        assert p.cache.set_calls == [], "（既有行为）精确缓存对文档限定查询零写入"
        assert p.semantic_cache.set_calls == [], (
            "语义缓存读侧跳过、写侧此前无守卫（#225 m4）：文档限定结果不得写 org 级语义缓存"
        )

    @pytest.mark.asyncio
    async def test_unscoped_query_still_writes_semantic_cache(self, monkeypatch):
        """对照（防过收紧）：无范围查询照常写语义缓存，读写路径不变。"""
        _no_rerank(monkeypatch)
        retrieve = AsyncMock(
            return_value=([_doc("unscoped_hit", "unscoped.md")], list(_SEM_VEC), None, [])
        )
        p = _make_pipeline(retrieve, recording=True)

        await p.search_knowledge_base(f"resTail-对照探针-{uuid.uuid4().hex}", 1, top_k=5)

        assert len(p.semantic_cache.set_calls) == 1, "无范围查询的语义写路径不得被守卫误伤"
        assert p.semantic_cache.set_calls[0][0][0].startswith("resTail-对照探针-")


class TestSemanticCacheScopedCausal:
    """因果链（真 SemanticCache + 假 Redis）：限定查询 → 无范围同 query 不受污染。

    变异验证（M2）：摘掉 `and not document_ids` → 第一段断言红（写侧探针：
    假 Redis 出现 rag:sem:retrieval 条目），第二段随之红（无范围同 query
    retrieve=0、吃到限定结果）。
    """

    @pytest.mark.asyncio
    async def test_scoped_write_does_not_leak_into_unscoped_causal_chain(self, monkeypatch):
        import app.rag.cache as cache_mod

        fake = _FakeRedis()
        monkeypatch.setattr(cache_mod, "_get_redis", lambda: fake)
        # issue #195：语义缓存默认关闭；本因果链以真实的语义读写为前提。
        monkeypatch.setattr(cache_mod.settings, "SEMANTIC_CACHE_RETRIEVAL_ENABLED", True)
        _no_rerank(monkeypatch)
        q = f"resTail-因果探针-{uuid.uuid4().hex}"

        # 第一跳：带 document_ids 的查询。修前：写出 rag:sem:retrieval:org:1:...
        p1 = _make_pipeline(
            AsyncMock(return_value=([_doc("scoped_only", "scoped.md")], list(_SEM_VEC), None, [])),
            recording=False,
        )
        await p1.search_knowledge_base(q, 1, top_k=5, document_ids=["d1"])
        sem_keys = [k for k in fake.kv if "rag:sem:retrieval:" in k]
        assert sem_keys == [], f"文档限定查询不得写 org 级语义缓存（m4 守卫），实际写了 {sem_keys}"

        # 第二跳：无范围同 query（同向量、相似度 1.0）。修前：命中第一跳的
        # 条目、retrieve=0、返回被裁剪过的限定结果（污染成立）。
        p2 = _make_pipeline(
            AsyncMock(
                return_value=([_doc("unscoped_hit", "unscoped.md")], list(_SEM_VEC), None, [])
            ),
            recording=False,
        )
        results, _ = await p2.search_knowledge_base(q, 1, top_k=5)

        assert p2.retriever.retrieve.await_count == 1, (
            "无范围查询必须执行自己的检索（不得借位限定查询的写入）"
        )
        assert [h["chunk_id"] for h in results] == ["unscoped_hit"], (
            f"无范围查询必须拿到自己的结果，实际 {[h['chunk_id'] for h in results]}"
        )

    @pytest.mark.asyncio
    async def test_unscoped_write_still_serves_semantic_hit(self, monkeypatch):
        """对照（回归）：无范围查询写 → 同 query 换 top_k 重查命中语义缓存（retrieve 零调用）。

        同一 key 空间、同一阈值下的既有良性路径（本批只是把限定查询从写侧
        排除，不动无范围路径）。
        """
        import app.rag.cache as cache_mod

        fake = _FakeRedis()
        monkeypatch.setattr(cache_mod, "_get_redis", lambda: fake)
        # issue #195：语义缓存默认关闭；本对照用例以真实的语义写读为前提。
        monkeypatch.setattr(cache_mod.settings, "SEMANTIC_CACHE_RETRIEVAL_ENABLED", True)
        _no_rerank(monkeypatch)
        q = f"resTail-对照因果-{uuid.uuid4().hex}"

        p1 = _make_pipeline(
            AsyncMock(
                return_value=([_doc("unscoped_hit", "unscoped.md")], list(_SEM_VEC), None, [])
            ),
            recording=False,
        )
        await p1.search_knowledge_base(q, 1, top_k=5)
        assert any("rag:sem:retrieval:" in k for k in fake.kv), "前提：无范围查询确实写了语义缓存"

        # 换 top_k（绕过精确缓存分键）→ 语义缓存命中 → 不执行检索，返回原 sources
        p2 = _make_pipeline(
            AsyncMock(return_value=([_doc("should_not_appear", "x.md")], list(_SEM_VEC), None, [])),
            recording=False,
        )
        results, _ = await p2.search_knowledge_base(q, 1, top_k=6)

        assert p2.retriever.retrieve.await_count == 0, "语义缓存命中路径应既无检索"
        assert [h["chunk_id"] for h in results] == ["unscoped_hit"], "命中的应是缓存里的 sources"
