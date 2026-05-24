# -*- coding: utf-8 -*-
"""RAG quality evaluation — Faithfulness, Relevancy, Context Precision.

Uses LLM-as-judge for scoring. Each metric returns a float in [0, 1].
"""
import logging
import json
import re
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


@dataclass
class EvalResult:
    """Single evaluation result."""
    faithfulness: float = 0.0
    relevancy: float = 0.0
    context_precision: float = 0.0
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BatchEvalResult:
    """Aggregated evaluation over multiple queries."""
    count: int = 0
    avg_faithfulness: float = 0.0
    avg_relevancy: float = 0.0
    avg_context_precision: float = 0.0
    results: List[EvalResult] = field(default_factory=list)


def _extract_score(text: str) -> float:
    """Extract a 0-1 float score from LLM response text."""
    # Try JSON first
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            for key in ("score", "faithfulness", "relevancy", "precision"):
                if key in data:
                    return max(0.0, min(1.0, float(data[key])))
    except (json.JSONDecodeError, ValueError):
        pass

    # Try regex — order matters: specific patterns first, generic last
    # "8/10" style
    m = re.search(r'(\d*\.?\d+)\s*/\s*10\b', text)
    if m:
        return max(0.0, min(1.0, float(m.group(1)) / 10.0))
    # "Score: 0.8" style
    m = re.search(r'[Ss]core[:\s]*(\d*\.?\d+)', text)
    if m:
        val = float(m.group(1))
        if val > 1:
            val = val / 100.0 if val <= 100 else val / 10.0
        return max(0.0, min(1.0, val))
    # "85%" style
    m = re.search(r'(\d+)%', text)
    if m:
        return max(0.0, min(1.0, float(m.group(1)) / 100.0))
    # Bare float "0.85"
    m = re.search(r'(\d\.\d+)', text)
    if m:
        return max(0.0, min(1.0, float(m.group(1))))
    return 0.0


FAITHFULNESS_PROMPT = """你是一个 RAG 系统质量评估专家。请评估以下回答是否忠实于提供的参考文档。

**评分标准**：
- 1.0：回答完全基于参考文档，没有添加外部信息
- 0.7-0.9：回答主要基于文档，有少量合理推断
- 0.4-0.6：回答部分基于文档，但包含较多外部信息
- 0.1-0.3：回答大部分是臆测，与文档关联很小
- 0.0：回答完全与文档无关或编造信息

**参考文档**：
{context}

**用户问题**：
{question}

**系统回答**：
{answer}

请只返回一个 JSON 对象：{{"score": <0.0-1.0>, "reason": "<简要说明>"}}"""

RELEVANCY_PROMPT = """你是一个 RAG 系统质量评估专家。请评估以下回答与用户问题的相关性。

**评分标准**：
- 1.0：回答完全切题，精准解答了用户的问题
- 0.7-0.9：回答基本切题，涵盖了问题的主要方面
- 0.4-0.6：回答部分相关，但遗漏了关键信息
- 0.1-0.3：回答偏离了问题的核心
- 0.0：回答完全不相关

**用户问题**：
{question}

**系统回答**：
{answer}

请只返回一个 JSON 对象：{{"score": <0.0-1.0>, "reason": "<简要说明>"}}"""

CONTEXT_PRECISION_PROMPT = """你是一个 RAG 系统质量评估专家。请评估以下检索到的文档与用户问题的相关程度。

**评分标准**：
- 1.0：所有检索文档都与问题高度相关
- 0.7-0.9：大部分文档相关，少量无关
- 0.4-0.6：约一半文档相关
- 0.1-0.3：大部分文档不相关
- 0.0：所有文档都不相关

**用户问题**：
{question}

**检索到的文档**：
{context}

请只返回一个 JSON 对象：{{"score": <0.0-1.0>, "reason": "<简要说明>"}}"""


class RAGEvaluator:
    """Evaluates RAG quality using LLM-as-judge."""

    def __init__(self, client: Optional[AsyncOpenAI] = None, model: Optional[str] = None):
        self.client = client
        self.model = model

    async def _score(self, prompt: str) -> tuple[float, str]:
        """Call LLM to score, return (score, raw_response)."""
        if not self.client:
            return 0.0, "LLM client not configured"

        try:
            response = await self.client.chat.completions.create(
                model=self.model or "deepseek-v4-flash",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=200,
            )
            text = response.choices[0].message.content or ""
            return _extract_score(text), text
        except Exception as e:
            logger.warning(f"RAG eval LLM call failed: {e}")
            return 0.0, str(e)

    async def evaluate_faithfulness(
        self, question: str, answer: str, context: List[Dict[str, Any]]
    ) -> tuple[float, str]:
        """Score whether the answer is faithful to the retrieved context."""
        context_str = "\n\n".join(
            f"[{i+1}] {item.get('snippet') or item.get('text', '')}"
            for i, item in enumerate(context)
        )
        prompt = FAITHFULNESS_PROMPT.format(
            context=context_str, question=question, answer=answer
        )
        return await self._score(prompt)

    async def evaluate_relevancy(self, question: str, answer: str) -> tuple[float, str]:
        """Score whether the answer is relevant to the question."""
        prompt = RELEVANCY_PROMPT.format(question=question, answer=answer)
        return await self._score(prompt)

    async def evaluate_context_precision(
        self, question: str, context: List[Dict[str, Any]]
    ) -> tuple[float, str]:
        """Score whether retrieved documents are relevant to the question."""
        context_str = "\n\n".join(
            f"[{i+1}] {item.get('snippet') or item.get('text', '')}"
            for i, item in enumerate(context)
        )
        prompt = CONTEXT_PRECISION_PROMPT.format(
            question=question, context=context_str
        )
        return await self._score(prompt)

    async def evaluate(
        self,
        question: str,
        answer: str,
        context: List[Dict[str, Any]],
    ) -> EvalResult:
        """Run all three evaluations for a single query."""
        faith, faith_detail = await self.evaluate_faithfulness(question, answer, context)
        rel, rel_detail = await self.evaluate_relevancy(question, answer)
        cp, cp_detail = await self.evaluate_context_precision(question, context)

        return EvalResult(
            faithfulness=round(faith, 4),
            relevancy=round(rel, 4),
            context_precision=round(cp, 4),
            details={
                "faithfulness_reason": faith_detail,
                "relevancy_reason": rel_detail,
                "context_precision_reason": cp_detail,
            },
        )

    async def evaluate_batch(
        self,
        items: List[Dict[str, Any]],
    ) -> BatchEvalResult:
        """Evaluate multiple queries and return aggregated results.

        Each item: {"question": str, "answer": str, "context": list}
        """
        results = []
        for item in items:
            r = await self.evaluate(
                question=item["question"],
                answer=item["answer"],
                context=item.get("context", []),
            )
            results.append(r)

        n = max(1, len(results))
        return BatchEvalResult(
            count=len(results),
            avg_faithfulness=round(sum(r.faithfulness for r in results) / n, 4),
            avg_relevancy=round(sum(r.relevancy for r in results) / n, 4),
            avg_context_precision=round(sum(r.context_precision for r in results) / n, 4),
            results=results,
        )
