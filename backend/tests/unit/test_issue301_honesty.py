"""issue #301 批 B：反思/审查/工具诚实化（假成功家族 #7/#8/#9/#12/#13）。

- #7 反思服务挂 → reflection_result="unavailable"（不得判 pass、不得编造分数）
- #8 反思非 JSON / 未知 decision → "unparsed"（不得静默 pass、不得以 success 记经验）
- #9 对抗审查崩溃/无结论 → recommendation="error"（调用侧如实记录，非 pass）
- #12 错误 JSON 不得被当"分析结果"写进报告（生产者结构化失败 + 消费端先校验；
      合法 JSON 含 "Error..." 字样不得被误伤 —— #297 教训的对照用例）
- #13 经验库加载失败不得置 _loaded（下次访问可重试，不必重启）

断言的对象是外在可观察面：事件字段、ctx 决策、run report 文本、经验库写入、
工具返回值形态、store 的加载状态。
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agent.config import AgentConfig
from app.agent.events import AgentEvent
from app.agent.loop import PERAgentLoop
from app.agent.planner import Plan, PlanStep
from app.agent.registry import ToolResult

# ── 工具函数 ────────────────────────────────────────────────────────────────


def _plan_with_failed_step() -> Plan:
    """非 quick-pass 的计划（有失败步）→ reflect() 必然走 LLM 路径。"""
    return Plan(id="p1", goal="分析营收", steps=[
        PlanStep(id="s1", description="s1", status="failed"),
    ])


def _client_returning(content: str) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=None,
        model="mock-model",
    ))
    return client


def _failing_client(exc: Exception) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create = AsyncMock(side_effect=exc)
    return client


def _mem_stub() -> MagicMock:
    return MagicMock(record_experience=AsyncMock(), record_lesson=AsyncMock())


async def _reflect_events(reflector, plan=None, ctx=None):
    plan = plan or _plan_with_failed_step()
    return [e async for e in reflector.reflect(plan, {"s1": {"result": "r"}}, ctx=ctx)]


# ── #7：反思服务挂 → unavailable（三态之"无判定"） ──────────────────────────


class TestReflectionUnavailable:
    @pytest.mark.asyncio
    async def test_llm_failure_yields_unavailable_not_pass(self):
        from app.agent.reflector import Reflector

        ctx = MagicMock()
        reflector = Reflector(_failing_client(RuntimeError("reflection down")), AgentConfig())
        events = await _reflect_events(reflector, ctx=ctx)

        assert events, "reflect() produced no events"
        assert all(e.type == "reflection" for e in events)
        # 旧行为：结构化面判 "pass" + 捏造 achievement=80 的摘要事件。
        assert all(e.reflection_result == "unavailable" for e in events)
        assert not any(e.reflection_result == "pass" for e in events)
        assert len(events) == 1, "不得再发出携带捏造分数的摘要事件"
        # 不编造分数：不得渲染任何"完成度 N"分数（文案提到"未获得完成度判定"是允许的）。
        assert all("完成度 " not in e.content for e in events)
        # 人读暴露文本钉死（评审 m1：N1 空串变异曾全绿存活）——降级文案必须如实
        # 说明为何没有判定，不得被静默清空。
        assert events[0].content and "评估服务异常" in events[0].content
        assert "未获得完成度判定" in events[0].content
        # 结构化决策记录（run report 的 reflect_decisions 读它）。
        ctx.record_decision.assert_called_once()
        args = ctx.record_decision.call_args.args
        assert args[0] == "reflect" and args[1] == "unavailable"

    @pytest.mark.asyncio
    async def test_unavailable_does_not_record_experience(self):
        from app.agent.reflector import Reflector

        mem = _mem_stub()
        reflector = Reflector(_failing_client(RuntimeError("down")), AgentConfig(), memory_bridge=mem)
        await _reflect_events(reflector)

        # 无判定不写经验库：既不冒充成功（旧行为把非 JSON 判 pass 记 success），
        # 也不把"评估缺席"当 run 的教训写进 planner 注入面。
        mem.record_experience.assert_not_awaited()
        mem.record_lesson.assert_not_awaited()


# ── #8：非 JSON / 未知 decision → unparsed ──────────────────────────────────


class TestReflectionUnparsed:
    @pytest.mark.asyncio
    async def test_non_json_output_is_unparsed(self):
        from app.agent.reflector import Reflector

        ctx = MagicMock()
        mem = _mem_stub()
        reflector = Reflector(_client_returning("我觉得还不错，应该没问题"), AgentConfig(), memory_bridge=mem)
        events = await _reflect_events(reflector, ctx=ctx)

        assert all(e.reflection_result == "unparsed" for e in events)
        assert not any(e.reflection_result == "pass" for e in events)
        args = ctx.record_decision.call_args.args
        assert args[0] == "reflect" and args[1] == "unparsed"
        # 旧行为：这条路径最终 _record_reflection("pass") → success=True 进经验库。
        mem.record_experience.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unknown_decision_is_unparsed(self):
        from app.agent.reflector import Reflector

        reflector = Reflector(
            _client_returning('{"decision": "totally_unknown", "achievement": 77}'),
            AgentConfig(),
        )
        events = await _reflect_events(reflector)

        # 旧行为：第一条事件原样透出 "totally_unknown"，第二条被 _summary_event
        # 静默改写成 "pass" —— 面与面互相矛盾且以假绿收尾。
        assert all(e.reflection_result == "unparsed" for e in events)

    @pytest.mark.asyncio
    async def test_missing_decision_field_is_unparsed(self):
        from app.agent.reflector import Reflector

        reflector = Reflector(_client_returning('{"achievement": 90, "gaps": []}'), AgentConfig())
        events = await _reflect_events(reflector)

        # 模型给了 JSON 但没给 decision：无判定 ≠ 通过（旧行为默认 pass）。
        assert all(e.reflection_result == "unparsed" for e in events)

    @pytest.mark.asyncio
    async def test_valid_pass_still_passes_with_model_achievement(self):
        """对照（防误伤）：合法判定照常通过，分数取模型给的值。"""
        from app.agent.reflector import Reflector

        mem = _mem_stub()
        reflector = Reflector(
            _client_returning('{"decision": "pass", "achievement": 90, "reasoning": "ok", "gaps": ["缺 X"]}'),
            AgentConfig(),
            memory_bridge=mem,
        )
        events = await _reflect_events(reflector)

        assert any(e.reflection_result == "pass" for e in events)
        summary = events[-1]
        assert summary.reflection_result == "pass"
        assert "完成度 90" in summary.content
        assert "缺 X" in summary.content
        # 真判定照常回写经验（成功）。
        mem.record_experience.assert_awaited_once()
        assert mem.record_experience.call_args.kwargs["success"] is True

    @pytest.mark.asyncio
    async def test_valid_pass_without_achievement_does_not_fabricate_score(self):
        from app.agent.reflector import Reflector

        reflector = Reflector(
            _client_returning('{"decision": "pass", "reasoning": "ok", "gaps": ["缺 X"]}'),
            AgentConfig(),
        )
        events = await _reflect_events(reflector)

        summary = events[-1]
        assert summary.reflection_result == "pass"
        # 模型没给分数 → 不渲染成任何数字（旧行为回退到编造的 85）。
        assert "完成度" not in summary.content
        assert "缺 X" in summary.content

    def test_summary_event_unknown_decision_is_unparsed_not_pass(self):
        """兜底护栏：未知 decision 不得再被改写为 pass。"""
        from app.agent.reflector import ReflectionResult, Reflector

        reflector = Reflector(MagicMock(), AgentConfig())
        event = reflector._summary_event(_plan_with_failed_step(), ReflectionResult(decision="banana"))
        assert event.reflection_result == "unparsed"

    def test_extract_json_none_on_unparseable_dict_on_valid(self):
        from app.agent.reflector import Reflector

        reflector = Reflector(MagicMock(), AgentConfig())
        assert reflector._extract_json("我觉得还不错") is None
        assert reflector._extract_json("") is None
        parsed = reflector._extract_json('```json\n{"decision": "pass", "achievement": 90}\n```')
        assert parsed == {"decision": "pass", "achievement": 90}


# ── #7/#8：loop 侧对降级的处置（不重试/不重规划；报告如实显现） ─────────────


class TestReflectionDegradationLoopSide:
    @pytest.mark.asyncio
    async def test_degraded_reflection_is_visible_and_does_not_actuate(self, monkeypatch):
        from app.agent.reflector import Reflector

        # 让真实 Reflector 走 LLM 路径（假 executor 的产出会命中 quick-pass）。
        monkeypatch.setattr(Reflector, "_quick_pass_check", lambda self, plan, results: False)

        real_reflector = Reflector(_failing_client(RuntimeError("reflection down")), AgentConfig())
        loop = make_loop(real_reflector)
        events = await drive(loop)

        # 不重试、不重规划（降级 ≠ 失败；好 run 不因评估缺席被打坏）。
        assert len(loop._planner.calls) == 1, "unavailable 不应触发额外规划"
        assert loop._executor.retry_calls == 0, "unavailable 不应触发步骤重试"
        assert any(e.type == "done" for e in events), "降级后 run 必须正常收尾"

        # 结构化面：事件是 unavailable，绝无 pass（旧行为即假绿）。
        refl = [e for e in events if e.type == "reflection"]
        assert refl, "必须发出 reflection 事件"
        assert all(e.reflection_result == "unavailable" for e in refl)

        # 决策记录进入 run report 的 reflect 块（此前该路径零记录）。
        decisions = decisions_of(events)
        assert any(d["phase"] == "reflect" and d["action"] == "unavailable" for d in decisions)
        assert "[unavailable]" in run_report_text(events)

        # 降级 ≠ 失败：clean_success 不因评估缺席被翻转（不然好 run 变坏）。
        assert "clean_success: True" in run_report_text(events)


# ── #9：对抗审查崩溃 → error（调用侧如实处置） ──────────────────────────────


class TestReviewerDegradation:
    @pytest.mark.asyncio
    async def test_crash_returns_error_not_pass(self):
        from app.agent.reviewer import Reviewer

        reviewer = Reviewer(_failing_client(RuntimeError("review model down")), AgentConfig())
        review = await reviewer.review(_plan_with_failed_step(), {})

        assert review["recommendation"] == "error"
        assert review["recommendation"] != "pass", "崩溃不得与真 pass 不可区分"
        assert review["issues_found"] == []
        assert "review model down" in str(review.get("error", ""))

    @pytest.mark.asyncio
    async def test_non_json_content_is_error_not_pass(self):
        from app.agent.reviewer import Reviewer

        reviewer = Reviewer(_client_returning("请自行判断"), AgentConfig())
        review = await reviewer.review(_plan_with_failed_step(), {})

        assert review["recommendation"] == "error"

    @pytest.mark.asyncio
    async def test_missing_recommendation_field_is_error_not_pass(self):
        from app.agent.reviewer import Reviewer

        reviewer = Reviewer(
            _client_returning('{"overall_assessment": "看起来还行", "issues_found": []}'),
            AgentConfig(),
        )
        review = await reviewer.review(_plan_with_failed_step(), {})

        # 模型给了 JSON 却没给判定：无结论 ≠ 通过。
        assert review["recommendation"] == "error"

    @pytest.mark.asyncio
    async def test_valid_recommendations_pass_through(self):
        """对照（防误伤）：真判定原样返回。"""
        from app.agent.reviewer import Reviewer

        for rec in ("pass", "revise", "reject"):
            reviewer = Reviewer(
                _client_returning(json.dumps({"issues_found": [], "recommendation": rec})),
                AgentConfig(),
            )
            review = await reviewer.review(_plan_with_failed_step(), {})
            assert review["recommendation"] == rec

    @pytest.mark.asyncio
    async def test_loop_records_review_error_without_flipping_clean_success(self):
        reviewer = StubReviewer(
            {"issues_found": [], "recommendation": "error", "error": "review model down"}
        )
        loop = make_loop(ScriptedReflector(["pass"]), reviewer=reviewer)
        events = await drive(loop)

        # error 不得被收敛进 pass（旧行为：未识别值一律 pass，静默消失）。
        decisions = decisions_of(events)
        assert any(d["phase"] == "review" and d["action"] == "error" for d in decisions)

        text = run_report_text(events)
        assert "review_recommendation: error" in text
        # 无结论 ≠ 失败：没有证据说 run 坏，clean_success 不翻转。
        assert "clean_success: True" in text
        # 用户可见面如实暴露。
        assert any(e.type == "thinking" and "对抗审查未完成" in e.content for e in events)


# ── #12：错误 JSON 不得被当"分析结果"写进报告 ───────────────────────────────


def _llm_pipeline(content: str) -> SimpleNamespace:
    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=None,
        model="mock-model",
    ))
    return SimpleNamespace(openai_client=client)


_ES_HIT = {"hits": {"hits": [{"_source": {"content": "全文内容", "filename": "f.md"}}]}}


class TestExtractInsightsHonesty:
    @pytest.mark.asyncio
    async def test_document_not_found_returns_structured_failure(self, monkeypatch):
        from app.agent.core_tools import extract_insights
        from app.core.elasticsearch import ElasticsearchTools

        monkeypatch.setattr(
            ElasticsearchTools, "search_documents",
            AsyncMock(return_value={"hits": {"hits": []}}),
        )
        res = await extract_insights("nope")

        assert isinstance(res, ToolResult)
        assert res.success is False
        assert res.error.code == "document_not_found"
        assert str(res).startswith("Error: [document_not_found]")

    @pytest.mark.asyncio
    async def test_llm_unavailable_returns_structured_failure(self, monkeypatch):
        from app.agent.core_tools import extract_insights
        from app.core.elasticsearch import ElasticsearchTools

        monkeypatch.setattr(ElasticsearchTools, "search_documents", AsyncMock(return_value=_ES_HIT))
        monkeypatch.setattr(
            "app.dependencies.get_rag_pipeline",
            lambda: SimpleNamespace(openai_client=None),
        )
        res = await extract_insights("doc1")

        assert isinstance(res, ToolResult)
        assert res.success is False
        assert res.error.code == "llm_unavailable"
        # 旧行为返回 insights={"error": "LLM unavailable"} 的"正常"JSON —— 被消费端当结果。
        assert "insights" not in str(res)

    @pytest.mark.asyncio
    async def test_analysis_exception_returns_structured_failure(self, monkeypatch):
        from app.agent.core_tools import extract_insights
        from app.core.elasticsearch import ElasticsearchTools

        monkeypatch.setattr(ElasticsearchTools, "search_documents", AsyncMock(return_value=_ES_HIT))
        client = MagicMock()
        client.chat.completions.create = AsyncMock(side_effect=RuntimeError("LLM boom"))
        monkeypatch.setattr(
            "app.dependencies.get_rag_pipeline",
            lambda: SimpleNamespace(openai_client=client),
        )
        res = await extract_insights("doc1")

        assert isinstance(res, ToolResult)
        assert res.success is False
        assert res.error.code == "analysis_failed"
        assert res.error.raw["document_id"] == "doc1"

    @pytest.mark.asyncio
    async def test_success_payload_unchanged_even_with_error_words(self, monkeypatch):
        """对照（防误伤）：合法 JSON 分析结果照常返回，哪怕正文含 "Error" 字样。"""
        from app.agent.core_tools import extract_insights
        from app.core.elasticsearch import ElasticsearchTools

        monkeypatch.setattr(ElasticsearchTools, "search_documents", AsyncMock(return_value=_ES_HIT))
        claims = [{"claim": "Error rate declined sharply", "confidence": "high"}]
        monkeypatch.setattr(
            "app.dependencies.get_rag_pipeline",
            lambda: _llm_pipeline(json.dumps({"claims": claims}, ensure_ascii=False)),
        )
        res = await extract_insights("doc1")

        assert isinstance(res, str), "成功形态不变：仍是普通字符串"
        payload = json.loads(res)
        assert payload["insights"]["claims"] == claims
        assert "Error rate declined" in res


class _FakeExecuteRecorder:
    """按工具名返回预置结果，并记录调用（给复合技能当 tool_registry.execute 替身）。"""

    def __init__(self, mapping):
        self.mapping = mapping
        self.calls: list[str] = []

    async def __call__(self, name, arguments, **context):
        self.calls.append(name)
        value = self.mapping.get(name, "")
        return value(arguments) if callable(value) else value


class TestSkillConsumersHonesty:
    @pytest.mark.asyncio
    async def test_knowledge_analysis_skips_error_payload(self, monkeypatch):
        from app.agent.registry import tool_registry
        from app.agent.tools import skills

        error_json = json.dumps({
            "filename": "f.md", "document_id": "doc1",
            "error": "Analysis failed: ES connection refused",
        }, ensure_ascii=False)
        fake = _FakeExecuteRecorder({
            "search_knowledge_base": "- ID: doc1 | 文件名: f.md\n- ID: doc2 | 文件名: g.md",
            "extract_insights": error_json,
            "generate_report": "报告已生成",
        })
        monkeypatch.setattr(tool_registry, "execute", fake)

        out = await skills.knowledge_analysis("分析f.md的财务情况")

        # 旧行为：小写 "error" 键躲过 `"Error" not in insight` → 原样写进报告。
        assert "Analysis failed" not in out
        assert "洞察提取失败" in out
        # 全部提取失败时不再"生成报告"（旧行为把错误载荷拼进报告 sections）。
        assert "generate_report" not in fake.calls

    @pytest.mark.asyncio
    async def test_knowledge_analysis_rejects_payload_without_insights_anchor(self, monkeypatch):
        """结构锚缺位（无 insights 键）的未知载荷 → 拒收，不得当分析结果消费。

        评审 m2：N2（删 `"insights" not in parsed` 结构锚）此前 27 全绿存活——
        该锚是防未来形态漂移的护栏，本用例把它钉住。
        """
        from app.agent.registry import tool_registry
        from app.agent.tools import skills

        no_anchor = json.dumps({"filename": "x.md", "document_id": "doc1"})
        fake = _FakeExecuteRecorder({
            "search_knowledge_base": "- ID: doc1 | 文件名: x.md",
            "extract_insights": no_anchor,
            "generate_report": "报告已生成",
        })
        monkeypatch.setattr(tool_registry, "execute", fake)

        out = await skills.knowledge_analysis("分析")

        assert "洞察提取失败" in out, "缺结构锚的载荷必须被拒收（不得当分析结果消费）"
        assert "generate_report" not in fake.calls

    @pytest.mark.asyncio
    async def test_knowledge_analysis_skips_toolresult_fail_string(self, monkeypatch):
        from app.agent.registry import tool_registry
        from app.agent.tools import skills

        fake = _FakeExecuteRecorder({
            "search_knowledge_base": "- ID: doc1 | 文件名: f.md",
            "extract_insights": "Error: [document_not_found] Document doc1 not found in index.",
        })
        monkeypatch.setattr(tool_registry, "execute", fake)

        out = await skills.knowledge_analysis("分析")
        assert "not found in index" not in out
        assert "洞察提取失败" in out

    @pytest.mark.asyncio
    async def test_knowledge_analysis_consumes_legit_json_despite_error_words(self, monkeypatch):
        """对照（#297 教训：误伤反例必测）：合法载荷含 "Error..." 文本仍被消费。"""
        from app.agent.registry import tool_registry
        from app.agent.tools import skills

        ok_payload = json.dumps({
            "filename": "a.md", "document_id": "doc1", "chunk_count": 3, "content_length": 100,
            "insights": {"claims": [{"claim": "Error rate declined", "confidence": "high"}]},
        }, ensure_ascii=False)
        error_json = json.dumps({"filename": "b.md", "error": "Analysis failed: timeout"})
        fake = _FakeExecuteRecorder({
            "search_knowledge_base": "- ID: doc1 | 文件名: a.md\n- ID: doc2 | 文件名: b.md",
            "extract_insights": lambda args: ok_payload if args["document_id"] == "doc1" else error_json,
            "generate_report": "报告已生成",
        })
        monkeypatch.setattr(tool_registry, "execute", fake)

        out = await skills.knowledge_analysis("对比", generate_report=False)

        assert "Error rate declined" in out, "合法分析正文不得被误伤丢弃"
        assert "Analysis failed" not in out
        assert "另有 1 份文档洞察提取失败" in out, "部分失败必须如实标注"

    @pytest.mark.asyncio
    async def test_document_comparison_skips_error_payload(self, monkeypatch):
        from app.agent.registry import tool_registry
        from app.agent.tools import skills

        error_json = json.dumps({"filename": "f.md", "error": "Analysis failed: ES down"})
        fake = _FakeExecuteRecorder({
            "get_document_info": "文档信息: f.md",
            "extract_insights": error_json,
            "cross_document_analysis": "跨文档分析正文",
        })
        monkeypatch.setattr(tool_registry, "execute", fake)

        out = await skills.document_comparison(["doc1", "doc2"])

        assert "Analysis failed" not in out
        assert "洞察提取失败" in out


# ── #13：经验库加载失败不置 _loaded（下次可重试） ───────────────────────────


class _FakeRedis:
    def __init__(self, behavior):
        self.behavior = behavior

    async def get(self, key):
        if isinstance(self.behavior, Exception):
            raise self.behavior
        return self.behavior


_EXP_JSON = json.dumps([{
    "id": "e1", "scenario": "cross_document", "symptom": "only_one_doc",
    "lesson": "对比要逐文档取证", "confidence": 0.8,
}])


class TestExperienceStoreLoadRetry:
    def _new_store(self, monkeypatch, tmp_path):
        import app.agent.experience.store as store_mod

        store = store_mod.ExperienceStore()
        monkeypatch.setattr(store_mod, "LOCAL_PATH", tmp_path / "experiences.json")
        return store

    @pytest.mark.asyncio
    async def test_redis_outage_not_loaded_then_recovers_without_restart(self, monkeypatch, tmp_path):
        store = self._new_store(monkeypatch, tmp_path)
        monkeypatch.setattr("app.core.redis.redis_client", _FakeRedis(ConnectionError("transient")))

        assert await store.count() == 0
        # 关键：加载失败不得置位（旧行为 _loaded=True → 本进程恒空、重启才恢复）。
        assert store._loaded is False

        # Redis 恢复（同进程，不重启）→ 下一次访问必须真的加载出来。
        monkeypatch.setattr("app.core.redis.redis_client", _FakeRedis(_EXP_JSON))
        assert await store.count() == 1
        assert store._loaded is True

    @pytest.mark.asyncio
    async def test_authoritative_empty_marks_loaded(self, monkeypatch, tmp_path):
        store = self._new_store(monkeypatch, tmp_path)
        # Redis 明确答复"没有数据"（键为空）+ 本地文件不存在 → 空库是确定性事实。
        monkeypatch.setattr("app.core.redis.redis_client", _FakeRedis(None))

        assert await store.count() == 0
        assert store._loaded is True

    @pytest.mark.asyncio
    async def test_local_fallback_still_works_when_redis_down(self, monkeypatch, tmp_path):
        import app.agent.experience.store as store_mod

        store = self._new_store(monkeypatch, tmp_path)
        (tmp_path / "experiences.json").write_text(_EXP_JSON, encoding="utf-8")
        monkeypatch.setattr(store_mod, "LOCAL_PATH", tmp_path / "experiences.json")
        monkeypatch.setattr("app.core.redis.redis_client", _FakeRedis(ConnectionError("down")))

        assert await store.count() == 1, "Redis 挂时本地回退语义不变"
        assert store._loaded is True

    @pytest.mark.asyncio
    async def test_local_read_error_is_not_authoritative(self, monkeypatch, tmp_path):
        import app.agent.experience.store as store_mod

        store = self._new_store(monkeypatch, tmp_path)
        bad = tmp_path / "experiences.json"
        bad.write_text("{not json", encoding="utf-8")
        monkeypatch.setattr(store_mod, "LOCAL_PATH", bad)
        monkeypatch.setattr("app.core.redis.redis_client", _FakeRedis(ConnectionError("down")))

        assert await store.count() == 0
        # 文件坏了 + Redis 挂 → "答不出来"，不得冒充确定性空库。
        assert store._loaded is False


# ── 精简 loop 驱动（自包含，模式同 test_issue106_evaluation_actuation） ─────


class _FakeMemory:
    def __init__(self):
        self.results: dict = {}
        self.interactions: list[tuple[str, str]] = []

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
        self.calls = getattr(self, "calls", [])
        self.calls.append({"query": query, "replan_feedback": replan_feedback})
        yield AgentEvent(type="plan_start", plan_id="plan-1", content="制定计划...")
        yield AgentEvent(
            type="plan_step", plan_id="plan-1", plan_step_id="s1",
            content="步骤", tool_hint="search_knowledge_base",
        )
        yield AgentEvent(type="plan_complete", plan_id="plan-1", plan_goal="目标", content="计划完成")


class _RecordingExecutor:
    def __init__(self, memory, result_text="这是执行产出的完整结果内容。"):
        self.memory = memory
        self.result_text = result_text
        self.retry_calls = 0

    def reset_for_request(self) -> None:
        pass

    async def execute(
        self, plan, history=None, organization_id=1, user_id=0, enable_thinking=True,
        ctx=None, on_step_done=None, human_guidance="",
    ):
        for s in plan.steps:
            s.status = "completed"
            plan.completed_steps += 1
            self.memory.store_step_result(s.id, {
                "description": s.description, "status": "completed",
                "result": self.result_text, "tool_hint": s.tool_hint,
            })
        yield AgentEvent(type="chunk", content=f"答案-{plan.id}")

    async def _execute_step_with_retry(
        self, step, plan, history, organization_id, user_id, enable_thinking, ctx=None
    ):
        self.retry_calls += 1
        yield AgentEvent(type="chunk", content=f"重试产出-{step.id}")


class ScriptedReflector:
    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.calls = 0

    async def reflect(self, plan, results, ctx=None):
        decision = self.decisions[min(self.calls, len(self.decisions) - 1)]
        self.calls += 1
        yield AgentEvent(type="reflection", reflection_result=decision, content="评估理由", plan_id=plan.id)


class StubReviewer:
    def __init__(self, result=None):
        self.result = result if result is not None else {"issues_found": [], "recommendation": "pass"}

    async def review(self, plan, results, ctx=None):
        return self.result


def make_loop(reflector, reviewer=None):
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
    loop._reflector = reflector
    loop._reviewer = reviewer or StubReviewer()
    loop._write_experiences = AsyncMock()
    return loop


async def drive(loop, query="对比两份财报", timeout=15.0):
    import asyncio

    async def _collect():
        return [e async for e in loop.run(query)]

    return await asyncio.wait_for(_collect(), timeout=timeout)


def decisions_of(events) -> list[dict]:
    ctx_events = [e for e in events if e.type == "execution_context"]
    assert ctx_events, "run() 必须发出 execution_context 事件"
    return ctx_events[-1].content["decisions"]


def run_report_text(events) -> str:
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
