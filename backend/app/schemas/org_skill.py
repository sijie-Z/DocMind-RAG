"""组织技能库请求 schema（批次十）。

字段上限在 schema 层只做防滥用粗拦；真正的语法/安全校验（控制字符、URL/
路径/命令、秘密模式、工具注册表约束）在 ``org_skill_service.normalize_payload``
统一执行——内部调用同样绕不过去。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

MAX_NAME_CHARS = 100
MAX_DESCRIPTION_CHARS = 300
MAX_REVIEW_NOTE_CHARS = 200


class SkillProposalCreate(BaseModel):
    """提议一个 pending 版本：手工 payload 或从本人完成的任务派生。"""

    skill_id: str | None = Field(default=None, max_length=36, description="已有技能 id（追加版本）")
    name: str | None = Field(
        default=None, max_length=MAX_NAME_CHARS, description="新技能名称（skill_id 缺省时必填）"
    )
    description: str | None = Field(
        default=None, max_length=MAX_DESCRIPTION_CHARS, description="技能说明（仅 UI）"
    )
    payload: dict[str, Any] | None = Field(
        default=None, description="结构化 payload（schema_version/trigger_phrases/tool_sequence）"
    )
    source_task_id: str | None = Field(
        default=None, max_length=36, description="来源任务 id（必须本人+本组织+COMPLETED）"
    )

    @model_validator(mode="after")
    def _require_payload_or_task(self) -> SkillProposalCreate:
        if self.payload is None and not self.source_task_id:
            raise ValueError("必须提供 payload 或 source_task_id")
        return self


class SkillReviewRequest(BaseModel):
    """审核备注（批准/拒绝共用；仅元数据，不参与运行时）。"""

    note: str | None = Field(default=None, max_length=MAX_REVIEW_NOTE_CHARS)


class SkillEnableRequest(BaseModel):
    """启用某个 approved 版本。"""

    version_no: int = Field(..., ge=1, description="要启用的已批准版本号")


class SkillRollbackRequest(BaseModel):
    """回滚到更早的 approved 版本；version_no 缺省 = 当前版本前最近的批准版本。"""

    version_no: int | None = Field(default=None, ge=1, description="目标版本号（必须更早）")


SkillStatus = Literal["active", "archived", "all"]
