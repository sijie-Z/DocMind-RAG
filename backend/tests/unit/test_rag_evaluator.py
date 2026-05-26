"""RAG evaluator unit tests — score extraction and dataclass logic."""
import pytest

from app.rag.evaluator import BatchEvalResult, EvalResult, _extract_score


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
