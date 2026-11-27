"""RAG quality evaluation — Faithfulness, Relevancy, Context Precision.

Uses LLM-as-judge for scoring. Each metric returns a float in [0, 1].
"""

import json
import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any

from openai import AsyncOpenAI

from app.rag.context_compressor import get_prompt_text
from app.rag.errors import EvalJudgeContractError, EvalJudgeUnavailableError

logger = logging.getLogger(__name__)

PROMPT_VERSION = "rag-eval-v1"
"""评委提示词/评分口径版本；修改下方任一 PROMPT 或解析规则时必须同步提升。

持久化的答案质量运行会存这个版本号，用于解释「两次分数为何不可直接比较」。
"""

# Judge prompts are capped so a single oversized retrieval batch cannot blow up
# prompt cost / latency. Characters, not tokens — no tokenizer dependency here.
MAX_CONTEXT_CHARS = 1200


def _build_context_str(context: list[dict[str, Any]]) -> str:
    """Join retrieved context into judge-prompt text, capped at MAX_CONTEXT_CHARS.

    When truncation happens, the dropped fraction is logged so prompt-cost
    incidents stay observable instead of silently degrading evaluation quality.
    """
    original = "\n\n".join(f"[{i + 1}] {get_prompt_text(item)}" for i, item in enumerate(context))
    if len(original) <= MAX_CONTEXT_CHARS:
        return original
    truncated_ratio = round(1.0 - MAX_CONTEXT_CHARS / len(original), 4)
    logger.info(
        "RAG eval context truncated: original=%d chars, kept=%d chars, truncated_ratio=%.4f",
        len(original),
        MAX_CONTEXT_CHARS,
        truncated_ratio,
    )
    return original[:MAX_CONTEXT_CHARS]


@dataclass
class EvalResult:
    """Single evaluation result."""

    faithfulness: float = 0.0
    relevancy: float = 0.0
    context_precision: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class BatchEvalResult:
    """Aggregated evaluation over multiple queries.

    issue #301：`count` 只计**真正评出分**的条目（不再是含故障条目的总数）；
    `failed_count` / `failures` 标注因评委不可用而被剔除的条目（含原始下标），
    均值仅对 `results` 计算 —— 故障条目不得以 0 分混入均值。
    """

    count: int = 0
    avg_faithfulness: float = 0.0
    avg_relevancy: float = 0.0
    avg_context_precision: float = 0.0
    results: list[EvalResult] = field(default_factory=list)
    failed_count: int = 0
    failures: list[dict[str, Any]] = field(default_factory=list)


def _coerce_score(value: object) -> float | None:
    """把 JSON 分数值归一为 0..1；bool/None/NaN/Infinity/非数字一律 None。

    布尔值在 Python 里是 int 的子类，``true`` 会被 ``float()`` 变成 1.0，
    这正是「伪造满分」的来源，必须显式排除。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return max(0.0, min(1.0, number))


def _extract_score(text: str) -> float | None:
    """Extract a 0-1 float score from LLM response text.

    无法解析出分数时返回 ``None``（而非 0.0）——调用方必须把「没有分数」
    与「评了 0 分」区分开，否则测量失败会被持久化成真实低质量。
    """
    # Try JSON first
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError, ValueError):
        data = None

    if isinstance(data, dict):
        for key in ("score", "faithfulness", "relevancy", "precision"):
            if key in data:
                return _coerce_score(data[key])
        # JSON 对象但没有可识别分数键：不回落正则，避免从 reason 里的数字
        # 臆造一个分数。
        return None
    if isinstance(data, list):
        # 数组不是评分契约；同样不回落正则。
        return None

    # Try regex — order matters: specific patterns first, generic last
    # "8/10" style
    m = re.search(r"(\d*\.?\d+)\s*/\s*10\b", text)
    if m:
        return max(0.0, min(1.0, float(m.group(1)) / 10.0))
    # "Score: 0.8" style
    m = re.search(r"[Ss]core[:\s]*(\d*\.?\d+)", text)
    if m:
        val = float(m.group(1))
        if val > 1:
            val = val / 100.0 if val <= 100 else val / 10.0
        return max(0.0, min(1.0, val))
    # "85%" style
    m = re.search(r"(\d+)%", text)
    if m:
        return max(0.0, min(1.0, float(m.group(1)) / 100.0))
    # Bare float "0.85"
    m = re.search(r"(\d\.\d+)", text)
    if m:
        return max(0.0, min(1.0, float(m.group(1))))
    return None


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

    def __init__(self, client: AsyncOpenAI | None = None, model: str | None = None):
        self.client = client
        self.model = model
        # 批次十四 B1：评委调用的真实 token 用量（model → (prompt, completion)）。
        # 端点评估后据此写 token_usage_records(source="rag_eval")；此前
        # `_score` 丢弃 `response.usage`，配额对 eval 入口空转。
        self.usage_by_model: dict[str, tuple[int, int]] = {}

    @staticmethod
    def _usage_counts(usage: Any) -> tuple[int, int]:
        """从 OpenAI usage（对象或 dict）提取 prompt/completion；坏值按 0。"""

        def _count(name: str) -> int:
            if isinstance(usage, dict):
                raw = usage.get(name)
            else:
                raw = getattr(usage, name, None)
            return raw if type(raw) is int and raw >= 0 else 0

        return _count("prompt_tokens"), _count("completion_tokens")

    def _accumulate_usage(self, response: Any) -> None:
        """累计本次评委调用用量；usage 缺失/形状漂移时静默跳过（不估算）。"""
        usage = getattr(response, "usage", None)
        if usage is None:
            return
        prompt_tokens, completion_tokens = self._usage_counts(usage)
        if prompt_tokens <= 0 and completion_tokens <= 0:
            return
        raw_model = getattr(response, "model", None)
        model = raw_model if isinstance(raw_model, str) and raw_model else (self.model or "unknown")
        previous_in, previous_out = self.usage_by_model.get(model, (0, 0))
        self.usage_by_model[model] = (
            previous_in + prompt_tokens,
            previous_out + completion_tokens,
        )

    async def _score(self, prompt: str) -> tuple[float, str]:
        """Call LLM to score, return (score, raw_response).

        issue #301：评委调用失败抛 `EvalJudgeUnavailableError`，不再吞成
        `(0.0, err)` —— 把「测量失败」伪装成「真实 0 分」正是本族要收的病。
        """
        if not self.client:
            raise EvalJudgeUnavailableError("LLM judge client not configured")

        try:
            response = await self.client.chat.completions.create(
                model=self.model or "deepseek-v4-flash",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=200,
                timeout=30,
            )
            # 先记账再解析：即使输出不可解析（契约违规）甚至 choices 形状异常，
            # 这次真实调用消耗的 token 也必须计入（失败条目同样计费）。
            self._accumulate_usage(response)
            text = response.choices[0].message.content or ""
        except Exception as e:
            logger.warning(f"RAG eval LLM call failed: {e}")
            raise EvalJudgeUnavailableError(
                f"RAG eval judge call failed: {type(e).__name__}"
            ) from e

        score = _extract_score(text)
        if score is None:
            # 调用成功但输出无法解析（拒答/空内容/超长截断）——这是契约违规，
            # 不是 0 分；上抛让调用方落成失败运行/5xx。
            raise EvalJudgeContractError("RAG eval judge output has no parseable score")
        return score, text

    async def evaluate_faithfulness(
        self, question: str, answer: str, context: list[dict[str, Any]]
    ) -> tuple[float, str]:
        """Score whether the answer is faithful to the retrieved context."""
        context_str = _build_context_str(context)
        prompt = FAITHFULNESS_PROMPT.format(context=context_str, question=question, answer=answer)
        return await self._score(prompt)

    async def evaluate_relevancy(self, question: str, answer: str) -> tuple[float, str]:
        """Score whether the answer is relevant to the question."""
        prompt = RELEVANCY_PROMPT.format(question=question, answer=answer)
        return await self._score(prompt)

    async def evaluate_context_precision(
        self, question: str, context: list[dict[str, Any]]
    ) -> tuple[float, str]:
        """Score whether retrieved documents are relevant to the question."""
        context_str = _build_context_str(context)
        prompt = CONTEXT_PRECISION_PROMPT.format(question=question, context=context_str)
        return await self._score(prompt)

    async def evaluate(
        self,
        question: str,
        answer: str,
        context: list[dict[str, Any]],
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
        items: list[dict[str, Any]],
    ) -> BatchEvalResult:
        """Evaluate multiple queries and return aggregated results.

        Each item: {"question": str, "answer": str, "context": list}

        issue #301：单条评委不可用 → 记入 `failures`（含原始下标）并从均值
        剔除，不再以 0 分混入；全部条目失败 → 抛 `EvalJudgeUnavailableError`，
        由端点映射为 5xx，绝不返回 count/avg 全零的「成功」。
        """
        results: list[EvalResult] = []
        failures: list[dict[str, Any]] = []
        contract_failures = 0
        for index, item in enumerate(items):
            try:
                r = await self.evaluate(
                    question=item["question"],
                    answer=item["answer"],
                    context=item.get("context", []),
                )
            except EvalJudgeContractError as e:
                contract_failures += 1
                logger.warning(f"RAG batch eval item {index} skipped (judge contract): {e}")
                failures.append({"index": index, "error": "judge_contract"})
                continue
            except EvalJudgeUnavailableError as e:
                logger.warning(f"RAG batch eval item {index} skipped (judge unavailable): {e}")
                failures.append({"index": index, "error": "judge_unavailable"})
                continue
            results.append(r)

        if not results:
            if contract_failures:
                raise EvalJudgeContractError(
                    "批量评估全部条目的评委输出均不符合评分契约，评价结果不可知"
                )
            raise EvalJudgeUnavailableError("批量评估全部条目的评委调用均不可用，评价结果不可知")

        n = max(1, len(results))
        return BatchEvalResult(
            count=len(results),
            avg_faithfulness=round(sum(r.faithfulness for r in results) / n, 4),
            avg_relevancy=round(sum(r.relevancy for r in results) / n, 4),
            avg_context_precision=round(sum(r.context_precision for r in results) / n, 4),
            results=results,
            failed_count=len(failures),
            failures=failures,
        )
