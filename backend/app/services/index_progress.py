"""Stable index-stage and error-code taxonomy for document processing.

This module is deliberately *additive*: it does not redefine ``Document.status``,
``index_status`` (the empty-result search diagnosis) or the legacy free-text
``parse_error`` / job ``error_message`` fields. Those keep their existing values
and raw diagnostic text. The taxonomy below gives clients a closed set of
machine codes plus stage progress that can be mapped to localised, actionable
messages.

Design contract
---------------
* ``IndexStage`` is a closed four-value set for parse -> chunk -> embed -> index.
* ``IndexErrorCode`` is a closed set. Clients may switch exhaustively on it;
  new failures map onto an existing code instead of inventing per-exception
  strings.
* ``build_error_detail`` produces a *bounded, sanitised* operator note. It never
  includes an exception message, a search query or document text — only the
  exception class name, the stage and caller-supplied numeric context.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from app.core.log_safety import log_safe


class IndexStage(str, Enum):
    """处理阶段闭集：parse -> chunk -> embed -> index。"""

    PARSE = "parse"
    CHUNK = "chunk"
    EMBED = "embed"
    INDEX = "index"


# 阶段入口进度（0-100）：progress 表示「进入当前阶段前已完成的流水线比例」。
# 这是粗粒度显示信号，不承诺精确到百分比；完成与否仍由 Document.status /
# job.status 决定（既有语义不变）。成功终态写 SUCCESS_PROGRESS=100。
STAGE_PROGRESS: dict[IndexStage, int] = {
    IndexStage.PARSE: 0,
    IndexStage.CHUNK: 25,
    IndexStage.EMBED: 50,
    IndexStage.INDEX: 75,
}
SUCCESS_PROGRESS = 100


class IndexErrorCode(str, Enum):
    """索引失败原因闭集（稳定契约；前端按此映射 i18n 文案）。"""

    PARSE_PROFILE_INVALID = "PARSE_PROFILE_INVALID"
    PARSE_UNSUPPORTED_FORMAT = "PARSE_UNSUPPORTED_FORMAT"
    PARSE_FAILED = "PARSE_FAILED"
    CHUNK_EMPTY = "CHUNK_EMPTY"
    CHUNK_FAILED = "CHUNK_FAILED"
    EMBED_COUNT_MISMATCH = "EMBED_COUNT_MISMATCH"
    EMBED_FAILED = "EMBED_FAILED"
    INDEX_WRITE_FAILED = "INDEX_WRITE_FAILED"
    INDEX_VERIFICATION_FAILED = "INDEX_VERIFICATION_FAILED"
    INDEX_FAILED = "INDEX_FAILED"
    DOCUMENT_NOT_FOUND = "DOCUMENT_NOT_FOUND"
    JOB_SCHEDULE_FAILED = "JOB_SCHEDULE_FAILED"
    UNEXPECTED = "UNEXPECTED"


# 阶段兜底码：未知异常按发生阶段收敛到稳定码，绝不把异常文本当码。
_STAGE_FALLBACK: dict[IndexStage, IndexErrorCode] = {
    IndexStage.PARSE: IndexErrorCode.PARSE_FAILED,
    IndexStage.CHUNK: IndexErrorCode.CHUNK_FAILED,
    IndexStage.EMBED: IndexErrorCode.EMBED_FAILED,
    IndexStage.INDEX: IndexErrorCode.INDEX_FAILED,
}

# 结构化字段长度上限（与迁移 036 列宽一致）。
ERROR_CODE_MAX = 64
ERROR_DETAIL_MAX = 256
STAGE_MAX = 16


def normalize_stage(value: str | IndexStage | None) -> str | None:
    """把 DB/API 中的阶段值归一为闭集 value；未知/缺省返回 None。"""
    if value is None:
        return None
    try:
        return IndexStage(value).value
    except ValueError:
        return None


def normalize_error_code(value: str | IndexErrorCode | None) -> str | None:
    """把 DB/API 中的错误码归一为闭集 value；未知/缺省返回 None。"""
    if value is None:
        return None
    try:
        return IndexErrorCode(value).value
    except ValueError:
        return None


def fallback_code(stage: str | IndexStage | None) -> IndexErrorCode:
    """按阶段给兜底码；阶段未知时用 UNEXPECTED（仍属闭集）。"""
    try:
        normalized = IndexStage(stage) if stage is not None else None
    except ValueError:
        normalized = None
    if normalized is None:
        return IndexErrorCode.UNEXPECTED
    return _STAGE_FALLBACK[normalized]


def build_error_detail(
    stage: str | IndexStage | None,
    exc: BaseException | None = None,
    **context: Any,
) -> str:
    """生成有界、脱敏的运维提示。

    只写异常**类名**（不含 message）、阶段名和调用方显式给出的上下文值。
    调用方传入的上下文会被转换为字符串并过滤：禁止把查询、文档正文或原始
    异常文本塞进这里——它们只能留在日志/旧 ``parse_error`` 中。
    """
    parts = [f"stage={normalize_stage(stage) or 'unknown'}"]
    if exc is not None:
        # 类名可能来自第三方库；限长 + log_safe 过滤控制字符，防止日志注入。
        parts.append(f"exception={log_safe(type(exc).__name__, max_len=64)}")
    for key, value in sorted(context.items()):
        # 上下文只接受标量；容器/长文本直接拒绝，避免夹带正文。
        if isinstance(value, bool | int | float):
            rendered = str(value)
        elif isinstance(value, str) and len(value) <= 32:
            rendered = log_safe(value, max_len=32)
        else:
            continue
        parts.append(f"{log_safe(key, max_len=24)}={rendered}")
    return log_safe(" ".join(parts), max_len=ERROR_DETAIL_MAX)


def public_index_fields(obj: Any) -> dict[str, Any]:
    """从 ORM 行（或测试替身）提取可安全序列化的公开结构化字段。

    返回的是 ``index_pipeline`` 嵌套结构的扁平等价物（stage/progress/error_code/
    error_detail）；调用方需要外层 key 时用 :func:`pipeline_payload`。
    """
    stage = normalize_stage(getattr(obj, "index_stage", None))
    code = normalize_error_code(getattr(obj, "index_error_code", None))
    raw_progress = getattr(obj, "index_progress", 0)
    progress = (
        raw_progress if isinstance(raw_progress, int) and not isinstance(raw_progress, bool) else 0
    )
    raw_detail = getattr(obj, "index_error_detail", None)
    detail = (
        raw_detail if isinstance(raw_detail, str) and len(raw_detail) <= ERROR_DETAIL_MAX else None
    )
    return {
        "index_stage": stage,
        "index_progress": max(0, min(SUCCESS_PROGRESS, progress)),
        "index_error_code": code,
        "index_error_detail": detail,
    }


def pipeline_payload(obj: Any) -> dict[str, Any]:
    """构造 ``index_pipeline`` 响应子对象（闭集 + 类型安全）。"""
    fields = public_index_fields(obj)
    return {
        "stage": fields["index_stage"],
        "progress": fields["index_progress"],
        "error_code": fields["index_error_code"],
        "error_detail": fields["index_error_detail"],
    }


class IndexPipelineError(Exception):
    """带稳定码的流水线失败；``str(exc)`` 仍是旧 parse_error 需要的原文。"""

    def __init__(self, message: str, *, code: IndexErrorCode) -> None:
        super().__init__(message)
        self.index_error_code = code
