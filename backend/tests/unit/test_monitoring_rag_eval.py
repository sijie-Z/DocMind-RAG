"""RAG 评估端点回归：曾读一个不存在的配置字段（`settings.DEEPSEEK_BASE_URL`）
—— AsyncOpenAI 构造即抛 AttributeError，被 except 吞掉 → 端点永远返回全 0 分
（"看起来能用、永远全 0"）。修复 = 改读真实字段 `DEEPSEEK_API_URL`。

变异点：把 monitoring.py 两处 base_url 改回 `settings.DEEPSEEK_BASE_URL` → 本测试红。
"""
import asyncio

from app.api.v1.endpoints import monitoring


class _FakeEvalResult:
    faithfulness = 0.9
    relevancy = 0.8
    context_precision = 0.7
    details = {}


class _FakeEvaluator:
    def __init__(self, client=None, model=None):
        self.client = client
        self.model = model

    async def evaluate(self, question, answer, context):
        return _FakeEvalResult()


def test_rag_eval_single_uses_defined_settings_field(monkeypatch):
    captured: dict = {}

    class _FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    import app.rag.evaluator as rag_evaluator

    monkeypatch.setattr("openai.AsyncOpenAI", _FakeOpenAI)
    monkeypatch.setattr(rag_evaluator, "RAGEvaluator", _FakeEvaluator)

    item = monitoring.EvalItem(question="q", answer="a", context=[{"text": "c"}])
    result = asyncio.run(monitoring.rag_eval_single(item))

    assert "error" not in (result.get("details") or {}), (
        f"端点不得因配置字段名错误抛异常：{result.get('details')}"
    )
    assert captured.get("base_url") == monitoring.settings.DEEPSEEK_API_URL, (
        "客户端必须用真实存在的配置字段构造 base_url"
    )
    assert result["faithfulness"] == 0.9
