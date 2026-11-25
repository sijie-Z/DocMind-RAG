"""Rubric 评测请求/响应 schema（评分表 + 不可变版本 + 运行）。"""

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

MAX_RUBRIC_ITEMS_SCHEMA = 30
MAX_RUBRIC_ITEM_KEY_CHARS = 32
MAX_RUBRIC_QUESTION_CHARS = 300
MAX_RUBRIC_NAME_CHARS = 100
MAX_RUBRIC_DESCRIPTION_CHARS = 500
MAX_RUBRIC_CHANGE_NOTE_CHARS = 200
MAX_RUN_CASES = 50
MAX_CASE_ANSWER_CHARS = 20_000
MAX_CASE_ID_CHARS = 64
MAX_CASE_NAME_CHARS = 100

# 与 ``rubric_eval_service.RUBRIC_KEY_PATTERN`` 同模式：schema 先拦（422），
# 服务层再拦一次（防内部调用绕过 schema）。
_RUBRIC_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,31}$")

Strictness = Literal["neutral", "strict", "lenient"]
Verdict = Literal["yes", "no", "unsure"]
RunMode = Literal["judge", "fake"]


class RubricItemInput(BaseModel):
    """一个评分项：key 落库、question 给评委看、weight 参与加权、strictness 折算 unsure。"""

    key: str = Field(
        ...,
        min_length=2,
        max_length=MAX_RUBRIC_ITEM_KEY_CHARS,
        description="评分项 key（小写字母开头）",
    )
    question: str = Field(
        ..., min_length=1, max_length=MAX_RUBRIC_QUESTION_CHARS, description="yes/no 问题"
    )
    weight: int = Field(1, ge=1, le=10, description="权重（1-10 整数）")
    strictness: Strictness = Field(
        "neutral", description="unsure 折算：neutral=0.5 / strict=0 / lenient=1"
    )

    @field_validator("key")
    @classmethod
    def _validate_key(cls, value: str) -> str:
        stripped = value.strip()
        if not _RUBRIC_KEY_PATTERN.match(stripped):
            raise ValueError("评分项 key 需匹配小写字母开头、2-32 位的小写下划线规则")
        return stripped

    @field_validator("question")
    @classmethod
    def _strip_question(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("问题不能为空白")
        return stripped


class RubricCreate(BaseModel):
    """创建评分表；首版可选同时写入（items 给出时创建即 v1）。"""

    name: str = Field(..., min_length=1, max_length=MAX_RUBRIC_NAME_CHARS, description="评分表名称")
    description: str | None = Field(
        None, max_length=MAX_RUBRIC_DESCRIPTION_CHARS, description="评分表说明（可选）"
    )
    items: list[RubricItemInput] | None = Field(
        None,
        min_length=1,
        max_length=MAX_RUBRIC_ITEMS_SCHEMA,
        description="首版评分项（省略=只建表头，之后不能运行；给出时至少 1 项）",
    )
    change_note: str | None = Field(
        None, max_length=MAX_RUBRIC_CHANGE_NOTE_CHARS, description="首版变更说明（可选）"
    )

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("评分表名称不能为空")
        return stripped

    @model_validator(mode="after")
    def _items_written_with_note(self) -> "RubricCreate":
        if self.change_note and self.items is None:
            raise ValueError("没有首版评分项时不能填写变更说明")
        return self


class RubricUpdate(BaseModel):
    """更新评分表头；description=null 且 clear_description=true 时显式清空。"""

    name: str | None = Field(None, min_length=1, max_length=MAX_RUBRIC_NAME_CHARS)
    description: str | None = Field(None, max_length=MAX_RUBRIC_DESCRIPTION_CHARS)
    clear_description: bool = Field(False, description="显式清空说明")

    @field_validator("name")
    @classmethod
    def _strip_optional_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("评分表名称不能为空")
        return stripped

    @model_validator(mode="after")
    def _at_least_one_field(self) -> "RubricUpdate":
        if self.name is None and self.description is None and not self.clear_description:
            raise ValueError("至少提供一个要更新的字段")
        return self


class RubricVersionCreate(BaseModel):
    """新增不可变版本（编辑=新增，旧版本永不改写）。"""

    items: list[RubricItemInput] = Field(
        ..., min_length=1, max_length=MAX_RUBRIC_ITEMS_SCHEMA, description="完整的评分项列表"
    )
    change_note: str | None = Field(
        None, max_length=MAX_RUBRIC_CHANGE_NOTE_CHARS, description="变更说明（可选）"
    )


class RubricBootstrap(BaseModel):
    """用服务端默认评分项初始化评分表（存量组织的平滑入口）。"""

    name: str = Field(
        "默认回答质量评分表",
        min_length=1,
        max_length=MAX_RUBRIC_NAME_CHARS,
        description="评分表名称",
    )


class RubricRunCase(BaseModel):
    """一条候选答案 + 可选人工金标 + fake 模式的确定性判定。"""

    case_id: str = Field(
        ..., min_length=1, max_length=MAX_CASE_ID_CHARS, description="用例 id（本次运行内唯一）"
    )
    name: str | None = Field(None, max_length=MAX_CASE_NAME_CHARS, description="用例名称（可选）")
    answer: str = Field(
        ..., min_length=1, max_length=MAX_CASE_ANSWER_CHARS, description="待评分的候选答案"
    )
    gold_verdicts: dict[str, Verdict] | None = Field(
        None, description="人工金标判定（{评分项 key: yes/no/unsure}），用于 judge 校准"
    )
    fake_verdicts: dict[str, Verdict] | None = Field(
        None, description="fake 模式的确定性判定（覆盖全部评分项；judge 模式忽略）"
    )

    # 判定键集合必须能在**选中版本**的评分项之间校验（schema 没有版本上下文），
    # 故逐项约束放在服务层 normalize_cases；这里只限条数上限，防止单次
    # 请求携带无界 JSON（键集合的合法性由服务层按版本严格比对）。
    @field_validator("gold_verdicts", "fake_verdicts")
    @classmethod
    def _verdict_map_bounded(cls, value: dict[str, Verdict] | None) -> dict[str, Verdict] | None:
        if value is not None and len(value) > MAX_RUBRIC_ITEMS_SCHEMA:
            raise ValueError(f"判定映射最多 {MAX_RUBRIC_ITEMS_SCHEMA} 项")
        return value

    @field_validator("case_id")
    @classmethod
    def _strip_case_id(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("case_id 不能为空白")
        return stripped

    @field_validator("answer")
    @classmethod
    def _strip_answer(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("answer 不能为空白")
        return value


class RubricRunRequest(BaseModel):
    """发起一次评分表运行：选择版本 + 候选答案集合 + judge/fake 模式。

    ``rubric_version_no`` 缺省取评分表最新版本。``mode=judge`` 用 LLM 评委
    （模型判定，非确定性真值）；``mode=fake`` 回放调用方提供的确定性判定
    （测试/CI 用，不是模型判定）。两者产出的 ``aggregate`` 口径一致。

    不提供 ``persist`` 开关：rubric 运行**总是**持久化。成功运行是可比的
    基线快照，失败运行是「这次为什么没有分数」的唯一证据；二选一的临时
    试跑没有产品语义（要试跑就选 fake 模式或先别建运行）。
    """

    rubric_version_no: int | None = Field(None, ge=1, description="评分表版本号；缺省=最新版本")
    mode: RunMode = Field("judge", description="judge=LLM 评委；fake=确定性回放")
    cases: list[RubricRunCase] = Field(
        ..., min_length=1, max_length=MAX_RUN_CASES, description="候选答案集合（最多 50 条）"
    )

    @model_validator(mode="after")
    def _unique_case_ids(self) -> "RubricRunRequest":
        ids = [case.case_id for case in self.cases]
        if len(ids) != len(set(ids)):
            raise ValueError("case_id 不能重复")
        return self
