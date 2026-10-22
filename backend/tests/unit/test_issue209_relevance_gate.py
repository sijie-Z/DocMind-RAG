"""issue #209 · 检索不合格 → 拒答闸门（P1，RAG 可信度）。

修前缺陷（origin/develop @ e8214bf0）：
- chat 生成链路只有 ``has_context = bool(compressed)`` 一个分支，而
  ``compress_context_list`` 对非空检索结果恒返回条目——只要检索有命中，
  哪怕与问题完全无关也会进入生成；
- reranker 算出的绝对分只用于排序/展示（#185/#190），不参与任何拒答决策；
- 生成提示词没有「资料无关则说明未找到」的条款。

修复后契约：
- ``RAG_RELEVANCE_GATE_THRESHOLD`` 默认 None（闸门不介入；生成提示词另加一条
  「资料无关则说明未找到」的纵深防御条款，见 pipeline.py）；
- 配置阈值后取检索结果里的 top ``relevance_score``（重排绝对分）比较：
  * 低于阈值 + strict → error 事件显式拒答，零 LLM 调用；
  * 低于阈值 + 非 strict → context_results 置空，走既有
    ``has_context=False`` 通用分支，sources 为空，``report_grounded(False)``；
  * 不低于阈值 → 正常生成；
  * 没有任何分数（重排降级/关闭/仅顺序）→ fail-open 放行并记
    ``fail_open_no_scores`` 指标（绝不静默拒答）。
- 闸门只在 chat 链路（services/chat_service.run_rag_pipeline），**不**进共享的
  ``search_knowledge_base``——REST search / agent / workflow 共用该入口。

变异点（对应测试转红）：
M1 删除闸门分支 → test_low_score_strict... / test_low_score_non_strict...；
M2 非 strict 分支不清空 context_results → test_low_score_non_strict... 的
   context/sources/groundedness 断言红；
M3 用 ``rank_score``（批内 min-max，榜首恒 ~1.0）代替 ``relevance_score`` →
   两个低分用例红（本文件命中故意让 rank_score=1.0）；
M4 阈值 None 仍执行闸门 → test_threshold_none... 红；
M5 无分数时拒答而非 fail-open → test_no_scores... 红；
M6 把闸门挪进 ``pipeline.search_knowledge_base`` → 本文件从 chat_service
   视角的拦截断言失效（且 REST search 会被误伤）；
M7 非 strict 拒答后未使用无文档通用提示词（置 None 或沿用组织模板）→
   test_gate_refusal_bypasses... 红（用例同时注入非空记忆：None 会被记忆
   文本覆盖，从而绕过 has_context=False 通用分支）；
M8 NaN 分数被当作有效分 → test_top_relevance... 的 NaN 断言红；
M9 严格拒答文案不在 RAG 错误白名单 → test_low_score_strict... 的
   public_rag_error_content 断言红（客户端会看到通用「知识库问答出错」）。
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from app.core.config import settings
from app.core.error_mapping import public_rag_error_content
from app.services import chat_service
from app.services.chat_service import run_rag_pipeline


def _hit(score: float | None, chunk_id: str = "d1_c1") -> dict:
    """pipeline 检索结果形状（已带 relevance_score 绝对分）。"""
    return {
        "chunk_id": chunk_id,
        "document_id": chunk_id.split("_")[0],
        "filename": "handbook.md",
        "snippet": "报销制度正文",
        "text": "报销制度正文",
        "prompt_text": "报销制度正文",
        # 融合批内 min-max 相对分：榜首恒 ~1.0，不能拿来当阈值判据。
        "score": 1.0,
        "rank_score": 1.0,
        "relevance_score": score,
    }


class _RecordingSemanticCache:
    def __init__(self):
        self.set_calls: list = []

    async def get(self, *args, **kwargs):
        return None

    async def set(self, *args, **kwargs):
        self.set_calls.append((args, kwargs))


class _GateMetricRecorder:
    """记录 RAG_RELEVANCE_GATE_TOTAL 的 outcome 标签。"""

    def __init__(self):
        self.outcomes: list[str | None] = []

    def labels(self, **kwargs):
        self.outcomes.append(kwargs.get("outcome"))

        class _Counter:
            def inc(self):
                return None

        return _Counter()


def _mock_chat_session() -> MagicMock:
    session = MagicMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    hist = MagicMock()
    hist.scalars.return_value.all.return_value = []
    hist.scalar_one_or_none = MagicMock(return_value=None)  # 无组织设置 / 无提示词模板
    session.execute = AsyncMock(return_value=hist)
    return session


def _mock_chat_session_with_template() -> MagicMock:
    """模拟组织配置了 RAG 系统提示词模板（get_system_prompt 会返回它）。"""
    session = _mock_chat_session()
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    result.scalar_one_or_none = MagicMock(
        return_value=SimpleNamespace(content="你必须严格依据提供的参考文档作答。")
    )
    session.execute = AsyncMock(return_value=result)
    return session


def _fake_rag_stack(hits: list[dict], captured: dict) -> MagicMock:
    fake_rag = MagicMock()
    fake_rag.openai_client = object()
    fake_rag.get_embedding = AsyncMock(return_value=None)
    fake_rag.search_knowledge_base = AsyncMock(return_value=(hits, None))
    fake_rag.report_grounded = MagicMock()
    fake_rag.get_last_token_usage = MagicMock(return_value=None)

    async def _chat_stream(query, context, history=None, system_prompt_override=None, **kw):
        captured["chat_stream_calls"] = captured.get("chat_stream_calls", 0) + 1
        captured["context"] = context
        captured["system_prompt_override"] = system_prompt_override
        yield "通用知识回答"

    fake_rag.chat_stream = _chat_stream
    return fake_rag


def _fake_memory_system(memory_context: str = "") -> MagicMock:
    memory = MagicMock()
    memory._embedding_provider = "stub"
    memory.get_context = AsyncMock(return_value=memory_context)
    memory.store_interaction = AsyncMock()
    return memory


async def _run(
    fake_rag,
    queue,
    *,
    strict_mode: bool,
    file_ids: list[str] | None = None,
    session=None,
    memory_context: str = "",
):
    session = session or _mock_chat_session()
    with (
        patch("app.services.chat_service.rag_service", new=fake_rag),
        patch("app.services.chat_service._semantic_cache", new=_RecordingSemanticCache()),
        patch(
            "app.services.chat_service.get_memory_system",
            new=MagicMock(return_value=_fake_memory_system(memory_context)),
        ),
    ):
        await run_rag_pipeline(
            session=session,
            user_content="今天天气怎么样",
            user_id=1,
            search_org_id=1,
            organization_id=1,
            conversation_id=None,
            file_ids=file_ids or [],
            strict_mode=strict_mode,
            privacy_mode=False,
            event_queue=queue,
        )
    return session


def _drain(queue) -> list:
    events = []
    while not queue.empty():
        events.append(queue.get_nowait())
    return events


# ── 低分 + strict：显式拒答、零 LLM 调用 ─────────────────────────


@pytest.mark.asyncio
async def test_low_score_strict_refuses_and_never_calls_llm(monkeypatch):
    monkeypatch.setattr(settings, "RAG_RELEVANCE_GATE_THRESHOLD", 0.5)
    recorder = _GateMetricRecorder()
    monkeypatch.setattr(chat_service, "RAG_RELEVANCE_GATE_TOTAL", recorder)
    queue: asyncio.Queue = asyncio.Queue()
    captured: dict = {}
    fake_rag = _fake_rag_stack([_hit(0.12)], captured)

    await _run(fake_rag, queue, strict_mode=True, file_ids=["doc-1"])

    events = _drain(queue)
    assert [e.type for e in events] == ["error"], (
        f"严格模式低分必须显式拒答: {[e.type for e in events]}"
    )
    assert "未找到与问题足够相关" in events[0].content
    assert public_rag_error_content(events[0].content) == events[0].content, (
        "拒答文案必须通过 RAG 错误白名单——否则客户端只会看到通用「知识库问答出错」，"
        "把「无相关内容」伪装成服务故障"
    )
    assert captured.get("chat_stream_calls", 0) == 0, "strict 拒答必须零 LLM 调用"
    assert recorder.outcomes == ["strict_refused"]
    assert fake_rag.report_grounded.call_count == 0, "拒答不是正常回答，不记 groundedness"


# ── 低分 + 非 strict：清空上下文走通用分支 ───────────────────────


@pytest.mark.asyncio
async def test_low_score_non_strict_uses_generic_branch_with_empty_sources(monkeypatch):
    monkeypatch.setattr(settings, "RAG_RELEVANCE_GATE_THRESHOLD", 0.5)
    recorder = _GateMetricRecorder()
    monkeypatch.setattr(chat_service, "RAG_RELEVANCE_GATE_TOTAL", recorder)
    queue: asyncio.Queue = asyncio.Queue()
    captured: dict = {}
    fake_rag = _fake_rag_stack([_hit(0.10), _hit(0.05, "d2_c2")], captured)

    await _run(fake_rag, queue, strict_mode=False)

    events = _drain(queue)
    assert "error" not in [e.type for e in events], "非 strict 不得走拒答/错误分支"
    assert captured["chat_stream_calls"] == 1
    assert captured["context"] == [], (
        "低于阈值必须清空检索上下文，走既有 has_context=False 通用分支"
    )
    assert "知识库中暂无相关文档" in (captured["system_prompt_override"] or "")
    chunk_events = [e for e in events if e.type == "chunk"]
    assert chunk_events and chunk_events[0].sources == [], "来源卡片必须为空"
    assert any(e.type == "message" and e.content == "通用知识回答" for e in events)
    assert fake_rag.report_grounded.call_args_list == [call(False)], (
        "闸门拒答后 groundedness 必须记「未命中来源」"
    )
    assert recorder.outcomes == ["refused"]


@pytest.mark.asyncio
async def test_gate_refusal_bypasses_org_rag_template(monkeypatch):
    """闸门清空上下文后必须使用「知识库暂无相关文档」提示词。

    组织模板预设「有参考文档」、记忆文本也会覆盖 None —— 两者都不得绕过
    通用分支的声明。变异验证：去掉 ``elif gate_refused`` 分支（或改回置 None）
    → 提示词变成模板文本 / 记忆文本 → 本用例红。
    """
    monkeypatch.setattr(settings, "RAG_RELEVANCE_GATE_THRESHOLD", 0.5)
    queue: asyncio.Queue = asyncio.Queue()
    captured: dict = {}
    fake_rag = _fake_rag_stack([_hit(0.1)], captured)
    session = _mock_chat_session_with_template()

    await _run(
        fake_rag,
        queue,
        strict_mode=False,
        session=session,
        memory_context="相关记忆：用户偏好简洁回答。",
    )

    assert captured["context"] == []
    prompt = captured["system_prompt_override"] or ""
    assert "知识库中暂无相关文档" in prompt, (
        "拒答后必须明确声明知识库无相关文档（组织模板/记忆都不得覆盖）"
    )
    assert "严格依据提供的参考文档" not in prompt, "不得沿用组织 RAG 模板"


# ── 高分：正常生成 ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_high_score_generates_normally(monkeypatch):
    monkeypatch.setattr(settings, "RAG_RELEVANCE_GATE_THRESHOLD", 0.5)
    recorder = _GateMetricRecorder()
    monkeypatch.setattr(chat_service, "RAG_RELEVANCE_GATE_TOTAL", recorder)
    queue: asyncio.Queue = asyncio.Queue()
    captured: dict = {}
    hits = [_hit(0.9), _hit(0.8, "d2_c2")]
    fake_rag = _fake_rag_stack(hits, captured)

    await _run(fake_rag, queue, strict_mode=False)

    assert captured["context"] == hits, "高于阈值必须原样把检索上下文交给生成"
    assert fake_rag.report_grounded.call_args_list == [call(True)]
    events = _drain(queue)
    chunk_events = [e for e in events if e.type == "chunk"]
    assert chunk_events and len(chunk_events[0].sources) == 2
    assert recorder.outcomes == ["pass"]


# ── 阈值 None：回归（行为与修前一致） ────────────────────────────


@pytest.mark.asyncio
async def test_threshold_none_keeps_legacy_behavior(monkeypatch):
    """默认关闭时，即使分数很低也完全按修前行为放行（回归）。"""
    monkeypatch.setattr(settings, "RAG_RELEVANCE_GATE_THRESHOLD", None)
    recorder = _GateMetricRecorder()
    monkeypatch.setattr(chat_service, "RAG_RELEVANCE_GATE_TOTAL", recorder)
    queue: asyncio.Queue = asyncio.Queue()
    captured: dict = {}
    hits = [_hit(0.01)]
    fake_rag = _fake_rag_stack(hits, captured)

    await _run(fake_rag, queue, strict_mode=False)

    assert captured["context"] == hits, "阈值未配置时不得拦截"
    assert fake_rag.report_grounded.call_args_list == [call(True)]
    assert recorder.outcomes == [], "未启用闸门时不产生闸门指标"
    assert "error" not in [e.type for e in _drain(queue)]


# ── 无分数：fail-open 放行并留观测 ───────────────────────────────


@pytest.mark.asyncio
async def test_no_scores_fail_open_with_metric(monkeypatch):
    """重排降级/关闭时没有绝对分——必须放行（fail-open），不得误拒。"""
    monkeypatch.setattr(settings, "RAG_RELEVANCE_GATE_THRESHOLD", 0.5)
    recorder = _GateMetricRecorder()
    monkeypatch.setattr(chat_service, "RAG_RELEVANCE_GATE_TOTAL", recorder)
    queue: asyncio.Queue = asyncio.Queue()
    captured: dict = {}
    hits = [_hit(None)]
    fake_rag = _fake_rag_stack(hits, captured)

    await _run(fake_rag, queue, strict_mode=False)

    assert captured["context"] == hits, "无分数必须 fail-open 放行"
    assert fake_rag.report_grounded.call_args_list == [call(True)]
    assert recorder.outcomes == ["fail_open_no_scores"], "无分数放行必须留观测，不得静默"


# ── 判据与默认值 ─────────────────────────────────────────────────


def test_top_relevance_score_never_falls_back_to_rank_score():
    from app.services.chat_service import _top_relevance_score

    assert _top_relevance_score([_hit(None)]) is None
    assert _top_relevance_score([{"score": 1.0, "rank_score": 1.0}]) is None, (
        "rank/score 是批内 min-max 相对分，绝不能当绝对分使用"
    )
    assert _top_relevance_score([_hit(0.2), _hit(0.7, "d2_c2")]) == pytest.approx(0.7)
    # NaN/Inf 不是有效分数：NaN 与阈值比较恒 False，必须按「无分数」fail-open。
    nan = float("nan")
    assert _top_relevance_score([{"relevance_score": nan}, _hit(0.4)]) == pytest.approx(0.4)
    assert _top_relevance_score([{"relevance_score": nan}]) is None
    assert _top_relevance_score([{"relevance_score": float("inf")}]) is None


def test_gate_threshold_defaults_to_disabled():
    from app.core.config import Settings

    field = Settings.model_fields["RAG_RELEVANCE_GATE_THRESHOLD"]
    assert field.default is None, "闸门默认必须关闭（阈值需金标集校准后显式开启）"


def test_gate_threshold_rejects_non_finite_values():
    """Inf 恒拒答、NaN 恒通过——两种都会让闸门失去可解释性，配置期直接拒绝。"""
    from pydantic import ValidationError

    from app.core.config import Settings

    for bad in (float("inf"), float("nan")):
        with pytest.raises(ValidationError):
            Settings(RAG_RELEVANCE_GATE_THRESHOLD=bad)


# ── LLM 兜底重排：同一次调用返回 [{"index","score"}]，不再只给序 ──


def _llm_client(content: str):
    class _Completions:
        async def create(self, **kwargs):
            class _Msg:
                pass

            class _Choice:
                pass

            class _Rsp:
                pass

            msg = _Msg()
            msg.content = content
            choice = _Choice()
            choice.message = msg
            rsp = _Rsp()
            rsp.choices = [choice]
            return rsp

    return MagicMock(chat=MagicMock(completions=_Completions()))


@pytest.mark.asyncio
async def test_llm_rerank_attaches_self_reported_scores(monkeypatch):
    """issue #209：LLM 兜底在同一次调用里自报 0–10 分，供闸门使用；不增调用成本。"""
    from app.rag.reranker import _rerank_llm

    monkeypatch.setattr(settings, "ENABLE_LOCAL_LLM", False)
    monkeypatch.setattr(settings, "DEEPSEEK_MODEL", "deepseek-chat-test")
    client = _llm_client('[{"index": 2, "score": 9}, {"index": 1, "score": 2}]')
    hits = [_hit(None, "d1_c1"), _hit(None, "d2_c2")]

    out, reason = await _rerank_llm("苹果", hits, top_n=2, llm_client=client)

    assert reason is None and out is not None
    assert [h["chunk_id"] for h in out] == ["d2_c2", "d1_c1"]
    assert out[0]["rerank_score"] == pytest.approx(9.0)
    assert out[1]["rerank_score"] == pytest.approx(2.0)


@pytest.mark.asyncio
async def test_llm_rerank_bare_index_list_still_orders_without_scores():
    """兼容只回编号的模型：只给序，不伪造 rerank_score。"""
    from app.rag.reranker import _rerank_llm

    client = _llm_client("[2, 1]")
    hits = [_hit(None, "d1_c1"), _hit(None, "d2_c2")]

    out, reason = await _rerank_llm("苹果", hits, top_n=2, llm_client=client)

    assert reason is None and out is not None
    assert [h["chunk_id"] for h in out] == ["d2_c2", "d1_c1"]
    assert all("rerank_score" not in h for h in out)


def test_llm_rerank_parser_robust_to_prose_and_malformed_json():
    """JSON 前后有正文、或 JSON 不合法（单引号/字符串编号）时不得把分数当编号。"""
    from app.rag.reranker import _coerce_llm_score, _parse_llm_rerank

    assert _parse_llm_rerank(
        '[{"index": 2, "score": 9}, {"index": 1, "score": 2}] 说明：[0-10 分制]'
    ) == [(2, 9.0), (1, 2.0)]
    assert _parse_llm_rerank("[{'index': '2', 'score': '9'}, {'index': 1, 'score': 2}]") == [
        (2, 9.0),
        (1, 2.0),
    ]
    assert _parse_llm_rerank("[3,1,2]") == [(3, None), (1, None), (2, None)]
    # "NaN"/"Infinity" 是合法 JSON 字符串，但会污染 sources.relevanceScore
    # （json.dumps 输出非法 JSON 的 NaN）——按无分数处理。
    assert _coerce_llm_score("NaN") is None
    assert _coerce_llm_score("Infinity") is None
    assert _coerce_llm_score("9") == pytest.approx(9.0)
