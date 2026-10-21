"""RAG evaluator unit tests — score extraction and dataclass logic."""

import asyncio
import logging
from types import SimpleNamespace

import pytest

from app.rag.evaluator import BatchEvalResult, EvalResult, RAGEvaluator, _extract_score


class TestExtractScore:
    def test_json_score(self):
        assert _extract_score('{"score": 0.85, "reason": "good"}') == pytest.approx(0.85)

    def test_json_faithfulness_key(self):
        assert _extract_score('{"faithfulness": 0.7}') == pytest.approx(0.7)

    def test_json_relevancy_key(self):
        assert _extract_score('{"relevancy": 0.9}') == pytest.approx(0.9)

    def test_json_precision_key(self):
        assert _extract_score('{"precision": 0.6}') == pytest.approx(0.6)

    def test_score_text_format(self):
        assert _extract_score("Score: 0.75") == pytest.approx(0.75)

    def test_score_out_of_10(self):
        assert _extract_score("8/10") == pytest.approx(0.8)

    def test_score_percentage(self):
        assert _extract_score("85%") == pytest.approx(0.85)

    def test_score_float_in_text(self):
        assert _extract_score("The score is 0.65 based on the criteria.") == pytest.approx(0.65)

    def test_score_clamped_above_1(self):
        assert _extract_score('{"score": 1.5}') == pytest.approx(1.0)

    def test_score_clamped_below_0(self):
        assert _extract_score('{"score": -0.5}') == pytest.approx(0.0)

    def test_no_score_returns_zero(self):
        assert _extract_score("I cannot determine a score.") == 0.0

    def test_empty_string(self):
        assert _extract_score("") == 0.0

    def test_score_100_percent(self):
        assert _extract_score("100%") == pytest.approx(1.0)

    def test_score_zero_percent(self):
        assert _extract_score("0%") == pytest.approx(0.0)


class TestEvalResult:
    def test_defaults(self):
        r = EvalResult()
        assert r.faithfulness == 0.0
        assert r.relevancy == 0.0
        assert r.context_precision == 0.0
        assert r.details == {}

    def test_custom_values(self):
        r = EvalResult(faithfulness=0.9, relevancy=0.8, context_precision=0.7)
        assert r.faithfulness == 0.9
        assert r.relevancy == 0.8
        assert r.context_precision == 0.7


class TestBatchEvalResult:
    def test_aggregation(self):
        r1 = EvalResult(faithfulness=0.8, relevancy=0.9, context_precision=0.7)
        r2 = EvalResult(faithfulness=0.6, relevancy=0.7, context_precision=0.5)
        batch = BatchEvalResult(count=2, results=[r1, r2])
        batch.avg_faithfulness = round((0.8 + 0.6) / 2, 4)
        batch.avg_relevancy = round((0.9 + 0.7) / 2, 4)
        batch.avg_context_precision = round((0.7 + 0.5) / 2, 4)
        assert batch.avg_faithfulness == pytest.approx(0.7)
        assert batch.avg_relevancy == pytest.approx(0.8)
        assert batch.avg_context_precision == pytest.approx(0.6)


class _CapturingJudge:
    """Fake AsyncOpenAI client that records the prompt sent to the judge."""

    def __init__(self, reply: str = '{"score": 0.8}'):
        self.prompts: list[str] = []
        self._reply = reply
        self.chat = SimpleNamespace(completions=self)

    async def create(self, *args, **kwargs):
        self.prompts.append(kwargs["messages"][0]["content"])
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self._reply))]
        )


def _call_context_method(evaluator: RAGEvaluator, method: str, context: list[dict]) -> None:
    if method == "faithfulness":
        asyncio.run(evaluator.evaluate_faithfulness(question="q", answer="a", context=context))
    else:
        asyncio.run(evaluator.evaluate_context_precision(question="q", context=context))


class TestContextLengthCap:
    """超长检索上下文在进入评委 prompt 前必须被长度帽截断，且截断比例可观测。"""

    @pytest.mark.parametrize("method", ["faithfulness", "context_precision"])
    def test_long_context_truncated_with_observable_ratio(self, method, caplog):
        snippet = "x" * 3000 + "TAIL_MARKER"
        judge = _CapturingJudge()
        evaluator = RAGEvaluator(client=judge, model="m")
        with caplog.at_level(logging.INFO, logger="app.rag.evaluator"):
            _call_context_method(evaluator, method, [{"snippet": snippet}])

        prompt = judge.prompts[0]
        assert "TAIL_MARKER" not in prompt, "超出长度帽的上下文尾部不得进入评委 prompt"
        assert len(prompt) <= 2500, f"评委 prompt 未被长度帽约束: {len(prompt)} chars"

        truncation_logs = [
            record.getMessage()
            for record in caplog.records
            if "truncated_ratio=" in record.getMessage()
        ]
        assert truncation_logs, "截断发生后必须以日志记录截断比例"
        ratio = float(truncation_logs[0].split("truncated_ratio=")[1].split()[0])
        expected = 1.0 - 1200 / len("[1] " + snippet)
        assert ratio == pytest.approx(expected, abs=1e-4)

    @pytest.mark.parametrize("method", ["faithfulness", "context_precision"])
    def test_short_context_passes_through_unharmed(self, method, caplog):
        judge = _CapturingJudge()
        evaluator = RAGEvaluator(client=judge, model="m")
        with caplog.at_level(logging.INFO, logger="app.rag.evaluator"):
            _call_context_method(evaluator, method, [{"snippet": "短上下文内容"}])

        prompt = judge.prompts[0]
        assert "短上下文内容" in prompt, "未超帽的上下文必须原样进入评委 prompt"
        assert not [
            record for record in caplog.records if "truncated_ratio=" in record.getMessage()
        ], "未截断时不得出现截断日志"
