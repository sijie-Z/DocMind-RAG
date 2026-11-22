"""检索评测测试集请求/响应 schema（竞品对标 P2）。"""

from pydantic import BaseModel, Field, field_validator, model_validator


def _strip_items(values: list[str], field: str, *, max_len: int) -> list[str]:
    """去掉首尾空白、拒绝空串、按出现顺序去重（金标重复会让 recall 口径含糊）。"""
    cleaned: list[str] = []
    for raw in values:
        item = raw.strip()
        if not item:
            raise ValueError(f"{field} 不能包含空字符串")
        if len(item) > max_len:
            raise ValueError(f"{field} 单项不能超过 {max_len} 字符")
        if item not in cleaned:
            cleaned.append(item)
    return cleaned


class RetrievalEvalCaseCreate(BaseModel):
    """创建一条评测用例：query + 期望命中的文档 id 列表（文档级金标）。"""

    name: str = Field(..., min_length=1, max_length=255, description="用例名称")
    query: str = Field(..., min_length=1, max_length=2000, description="检索 query")
    expected_document_ids: list[str] = Field(
        ..., min_length=1, max_length=50, description="期望命中的文档 id（至少 1 个，最多 50）"
    )
    tags: list[str] = Field(
        default_factory=list, max_length=20, description="标签（可选，最多 20 个）"
    )

    @field_validator("name", "query")
    @classmethod
    def _strip_required(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("不能为空白")
        return stripped

    @field_validator("expected_document_ids")
    @classmethod
    def _validate_document_ids(cls, value: list[str]) -> list[str]:
        return _strip_items(value, "expected_document_ids", max_len=36)

    @field_validator("tags")
    @classmethod
    def _validate_tags(cls, value: list[str]) -> list[str]:
        return _strip_items(value, "tags", max_len=50)


class RetrievalEvalCaseUpdate(BaseModel):
    """更新用例：至少提供一个字段；None 表示显式清空可选字段 tags。"""

    name: str | None = Field(None, min_length=1, max_length=255, description="用例名称")
    query: str | None = Field(None, min_length=1, max_length=2000, description="检索 query")
    expected_document_ids: list[str] | None = Field(
        None, min_length=1, max_length=50, description="期望命中的文档 id"
    )
    tags: list[str] | None = Field(None, max_length=20, description="标签；[] 表示清空")

    @field_validator("name", "query")
    @classmethod
    def _strip_optional(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("不能为空白")
        return stripped

    @field_validator("expected_document_ids")
    @classmethod
    def _validate_optional_ids(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _strip_items(value, "expected_document_ids", max_len=36)

    @field_validator("tags")
    @classmethod
    def _validate_optional_tags(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _strip_items(value, "tags", max_len=50)

    @model_validator(mode="after")
    def _at_least_one_field(self) -> "RetrievalEvalCaseUpdate":
        if not self.model_fields_set:
            raise ValueError("至少需要提供一个可更新字段")
        # 非可空字段显式传 null 会让 DB 写 NULL 撞 NOT NULL 约束（500），
        # 必须在 schema 层拒绝；tags=null 单独出现也是空操作，同样拒绝。
        for field in ("name", "query", "expected_document_ids"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} 不能为 null")
        if self.model_fields_set == {"tags"} and self.tags is None:
            raise ValueError("至少需要提供一个可更新字段")
        return self


class RetrievalEvalRunRequest(BaseModel):
    """运行评测：缺省跑本组织全部用例（最多 100 条）；默认不写库。

    ``top_k=5`` 是**有意固定的 rank-5 产品评测口径**（衡量期望文档是否进入
    前 5 名），与组织设置 ``retrieval_top_k``（线上检索实际返回条数）无关：
    本字段只决定本次评测检索/截断深度，不读取、不写入组织设置。
    """

    case_ids: list[str] | None = Field(
        None,
        max_length=50,
        description="只跑指定用例（最多 50 条）；缺省 = 本组织全部用例（最多 100 条）",
    )
    top_k: int = Field(
        5,
        ge=1,
        le=50,
        description="本次评测的检索/截断深度（1..50，默认 5=rank-5 产品口径；与组织 retrieval_top_k 无关）",
    )

    @field_validator("case_ids")
    @classmethod
    def _validate_case_ids(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        return _strip_items(value, "case_ids", max_len=36)
