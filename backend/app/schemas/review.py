"""核验请求/响应 schema（合同核验场景 Batch 1/2 + 模板库批次五）。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

# 与服务层同值的上限（服务层重复校验，防内部调用绕过 schema）。
MAX_TEMPLATE_ITEMS = 50
MAX_TEMPLATE_KEYWORDS = 30
MAX_TEMPLATE_KEYWORD_CHARS = 64
MAX_TEMPLATE_LABEL_CHARS = 100
MAX_TEMPLATE_NAME_CHARS = 100
MAX_TEMPLATE_DESCRIPTION_CHARS = 500
MAX_TEMPLATE_CHANGE_NOTE_CHARS = 200
# 与服务层同值：请求级关键词总字节上限，避免 schema 合法但服务 payload 超限。
MAX_TEMPLATE_REQUEST_KEYWORD_BYTES = 48 * 1024


class ReviewMetaUpsert(BaseModel):
    """注册/更新文档核验身份；content_sha256 由服务端从存储对象计算。"""

    doc_role: Literal["policy", "template", "contract"] = Field(..., description="文档角色")
    family_key: str | None = Field(None, max_length=128, description="版本族键（合同必填）")
    version_label: str | None = Field(None, max_length=64, description="版本标签（合同必填）")
    version_seq: int | None = Field(None, ge=1, le=2**31 - 1, description="版本序（合同必填）")
    supersedes_document_id: str | None = Field(
        None, max_length=36, description="被本版本替代的同组织文档 id"
    )
    effective_from: datetime | None = Field(None, description="生效起点（可选）")
    effective_to: datetime | None = Field(None, description="生效终点（可选）")


class ReviewCaseCreate(BaseModel):
    """建案：一份制度/模板 + 合同基准版 + 修订版。

    ``template_id`` 可选：给出时从该模板版本读取规则项（缺省版本=最新版），
    并继续生成不可变的 ``ReviewRuleSet`` 快照；不给则沿用进程级 checklist。
    """

    title: str = Field(..., min_length=1, max_length=255, description="案件标题")
    policy_document_id: str = Field(
        ..., min_length=1, max_length=36, description="制度/模板文档 id"
    )
    base_document_id: str = Field(..., min_length=1, max_length=36, description="合同基准版 id")
    revised_document_id: str = Field(..., min_length=1, max_length=36, description="合同修订版 id")
    checklist_version: str = Field("v1", max_length=16, description="核验清单版本")
    assignee_id: int | None = Field(None, ge=1, description="指定审核人（可选）")
    template_id: str | None = Field(
        None, min_length=1, max_length=36, description="核验模板 id（可选）"
    )
    template_version_no: int | None = Field(
        None, ge=1, le=2**31 - 1, description="核验模板版本号（缺省取最新版）"
    )

    @model_validator(mode="after")
    def _template_version_requires_template(self) -> "ReviewCaseCreate":
        if self.template_version_no is not None and self.template_id is None:
            raise ValueError("指定 template_version_no 时必须同时提供 template_id")
        return self


class ReviewTemplateItemInput(BaseModel):
    """模板规则项：key 落库，keywords 用于候选证据抓取，label 供人读。"""

    key: str = Field(..., min_length=2, max_length=32, description="核验项 key（小写下划线）")
    keywords: list[str] = Field(
        ...,
        min_length=1,
        max_length=MAX_TEMPLATE_KEYWORDS,
        description="关键词（非空，最多 30 个）",
    )
    label: str | None = Field(None, max_length=MAX_TEMPLATE_LABEL_CHARS, description="展示名称")

    @field_validator("keywords")
    @classmethod
    def _validate_keywords(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for raw in value:
            word = raw.strip()
            if not word:
                raise ValueError("关键词不能为空字符串")
            if len(word) > MAX_TEMPLATE_KEYWORD_CHARS:
                raise ValueError(f"单个关键词不能超过 {MAX_TEMPLATE_KEYWORD_CHARS} 字符")
            if word not in cleaned:
                cleaned.append(word)
        if not cleaned:
            raise ValueError("关键词不能全为空")
        return cleaned


class ReviewTemplateCreate(BaseModel):
    """创建模板头；首版可选在同一请求里写入（items 给出时创建即 v1）。

    ``items`` 只需提交**自定义追加项**：四个内置核验项（付款期限/起算/验收/
    例外）由服务端按其固定定义自动补齐，客户端既不能删也不能改。
    """

    name: str = Field(..., min_length=1, max_length=MAX_TEMPLATE_NAME_CHARS, description="模板名称")
    description: str | None = Field(
        None, max_length=MAX_TEMPLATE_DESCRIPTION_CHARS, description="模板说明（可选）"
    )
    items: list[ReviewTemplateItemInput] | None = Field(
        None,
        max_length=MAX_TEMPLATE_ITEMS,
        description="首版自定义追加项（内置 4 项自动补齐；省略=只建模板头）",
    )
    change_note: str | None = Field(
        None, max_length=MAX_TEMPLATE_CHANGE_NOTE_CHARS, description="首版变更说明（可选）"
    )

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("模板名称不能为空")
        return stripped

    @field_validator("items")
    @classmethod
    def _validate_items(cls, value: list[ReviewTemplateItemInput] | None):
        if value is not None and len(value) > MAX_TEMPLATE_ITEMS:
            raise ValueError(f"最多 {MAX_TEMPLATE_ITEMS} 个自定义项")
        return value

    @model_validator(mode="after")
    def _byte_budget(self) -> "ReviewTemplateCreate":
        if len(self.name.encode("utf-8")) > MAX_TEMPLATE_NAME_CHARS * 4:
            raise ValueError("模板名称过长")
        if (
            self.description
            and len(self.description.encode("utf-8")) > MAX_TEMPLATE_DESCRIPTION_CHARS * 4
        ):
            raise ValueError("模板说明过长")
        if (
            self.change_note
            and len(self.change_note.encode("utf-8")) > MAX_TEMPLATE_CHANGE_NOTE_CHARS * 4
        ):
            raise ValueError("变更说明过长")
        if self.items:
            budget = sum(
                len(item.key.encode("utf-8"))
                + sum(len(word.encode("utf-8")) for word in item.keywords)
                + len((item.label or "").encode("utf-8"))
                for item in self.items
            )
            if budget > MAX_TEMPLATE_REQUEST_KEYWORD_BYTES:
                raise ValueError("模板首版内容超过大小上限")
        return self

    @model_validator(mode="after")
    def _change_note_requires_items(self) -> "ReviewTemplateCreate":
        if self.change_note is not None and self.items is None:
            raise ValueError("change_note 仅用于首版创建，必须同时提供 items")
        return self


class ReviewTemplateUpdate(BaseModel):
    """更新模板头；description=null 且 clear_description=true 时显式清空。"""

    name: str | None = Field(None, min_length=1, max_length=MAX_TEMPLATE_NAME_CHARS)
    description: str | None = Field(None, max_length=MAX_TEMPLATE_DESCRIPTION_CHARS)
    clear_description: bool = Field(False, description="显式清空说明")

    @field_validator("name")
    @classmethod
    def _validate_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("模板名称不能为空")
        return stripped

    @model_validator(mode="after")
    def _at_least_one_field(self) -> "ReviewTemplateUpdate":
        if self.name is None and self.description is None and not self.clear_description:
            raise ValueError("至少提供一个要更新的字段")
        return self


class ReviewTemplateVersionCreate(BaseModel):
    """新增模板版本：自定义追加项列表 + 可选变更说明（编辑=新增不可变版本）。

    内置 4 项由服务端原样保留并补齐；``items=[]`` 合法（版本=仅内置基线），
    提交内置 key 会被 400 拒绝。
    """

    items: list[ReviewTemplateItemInput] = Field(
        default_factory=list,
        max_length=MAX_TEMPLATE_ITEMS,
        description="自定义追加项（内置 4 项自动补齐；空数组=仅内置基线）",
    )
    change_note: str | None = Field(
        None, max_length=MAX_TEMPLATE_CHANGE_NOTE_CHARS, description="变更说明（可选）"
    )

    @model_validator(mode="after")
    def _byte_budget(self) -> "ReviewTemplateVersionCreate":
        budget = sum(
            len(item.key.encode("utf-8"))
            + sum(len(word.encode("utf-8")) for word in item.keywords)
            + len((item.label or "").encode("utf-8"))
            for item in self.items
        )
        if budget > MAX_TEMPLATE_REQUEST_KEYWORD_BYTES:
            raise ValueError("模板版本内容超过大小上限")
        return self


class ReviewTemplateBootstrap(BaseModel):
    """从进程级 checklist 初始化模板（存量部署的平滑入口）。"""

    name: str = Field(
        "默认核验清单", min_length=1, max_length=MAX_TEMPLATE_NAME_CHARS, description="模板名称"
    )


class ReviewEvidenceSelect(BaseModel):
    """人工选择/改选某核验项某一侧的证据（chunk 必须属于该侧文档）。"""

    side: Literal["policy", "base", "revised"] = Field(..., description="证据侧")
    chunk_id: str = Field(..., min_length=1, max_length=36, description="文档分块 id")
    role: Literal["main", "condition", "exception", "cross_ref"] = Field(
        "main", description="证据角色"
    )
    selected: bool = Field(True, description="选中=true；取消选择=false")


class ReviewDecisionUpsert(BaseModel):
    """逐项人工决策：只允许三态之一，pending/insufficient 等不能手工选择。"""

    status: Literal["verified", "needs_human", "not_applicable"] = Field(
        ..., description="决策状态"
    )
    note: str | None = Field(None, max_length=2000, description="决策说明（可选）")
