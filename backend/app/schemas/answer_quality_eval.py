"""答案质量评测请求 schema（运行创建）。

与 ``app.rag.evaluator`` 的评分核心一致：一条用例是
``question + answer + context``；``fake`` 模式额外回放 ``fake_scores``。
原始文本只用于本次请求的评分，不进入响应或持久化结果。
"""

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

MAX_CASES = 20
MAX_CASE_ID_CHARS = 64
MAX_QUESTION_CHARS = 2000
MAX_ANSWER_CHARS = 10_000
MAX_CONTEXT_ITEMS = 50

RunMode = Literal["judge", "fake"]


class AnswerQualityCase(BaseModel):
    """一条待评分的问答：问题 + 候选答案 + 检索上下文。"""

    case_id: str = Field(
        ..., min_length=1, max_length=MAX_CASE_ID_CHARS, description="用例 id（本次运行内唯一）"
    )
    question: str = Field(..., min_length=1, max_length=MAX_QUESTION_CHARS, description="用户问题")
    answer: str = Field(
        ..., min_length=1, max_length=MAX_ANSWER_CHARS, description="待评分的候选答案"
    )
    context: list[dict[str, Any]] = Field(
        default_factory=list,
        max_length=MAX_CONTEXT_ITEMS,
        description="检索上下文（与 RAGEvaluator 同形状）",
    )
    fake_scores: dict[str, float] | None = Field(
        None,
        description="fake 模式的确定性分数：faithfulness/relevancy/context_precision（0..1）",
    )

    @field_validator("case_id")
    @classmethod
    def _strip_case_id(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("case_id 不能为空白")
        return stripped

    @field_validator("question", "answer")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("内容不能为空白")
        return value


class AnswerQualityRunRequest(BaseModel):
    """发起一次答案质量评测运行（judge=LLM 评委 / fake=确定性回放）。

    运行总是持久化：成功快照是可比基线，失败快照是「为什么没有分数」的
    唯一证据。没有 ``persist`` 开关。
    """

    mode: RunMode = Field("judge", description="judge=LLM 评委；fake=确定性回放")
    cases: list[AnswerQualityCase] = Field(
        ..., min_length=1, max_length=MAX_CASES, description="待评分用例（最多 20 条）"
    )

    @field_validator("cases")
    @classmethod
    def _unique_case_ids(cls, value: list[AnswerQualityCase]) -> list[AnswerQualityCase]:
        ids = [case.case_id for case in value]
        if len(ids) != len(set(ids)):
            raise ValueError("case_id 不能重复")
        return value
