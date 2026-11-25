"""Agent 工件工具（竞品批次七）：把模型产出的文件固化为可预览/可下载/可复用工件。

与其他工具的分工：
- 本工具是**显式**工件出口——模型必须主动调用并给出 title/kind/content；
  普通工具结果无论多长都不会被自动固化成工件（误判比不升级更伤）。
- 工具结果同时在 ``ToolMeta.extra["artifact"]`` 里带结构化摘要，执行器据此
  落库（``app/services/artifact_service.create_artifact``）并在 SSE 上发
  ``artifact`` 事件；字符串载荷里也带同样的显式标记，供不读 meta 的旧调用方。
- 失败（缺组织/缺标题/超限）走 ``ToolResult.fail``，绝不静默丢内容。

落库口径（谁写行）：
- **执行器接线时**（``run_context.extra["artifact_capture"]`` 为真）：工具只
  **校验**并返回载荷，行由执行器在其 DB 会话里写入（拿到 task_id / call_id，
  能挂版本链与幂等键）；
- **未接线时**（技能内部调用、直接调注册表）：工具自行落库（无 task_id）。
两条路径共用 ``artifact_service.create_artifact``，校验与版本语义同源。

安全：工具结果里只回**回执**（id/title/status），不回整篇正文——正文已固化为
工件，模型下一轮应引用 id 而不是重贴内容；这也避免 512 KiB 正文挤爆上下文。
"""

import json
import logging
from typing import Any

from app.agent.registry import ToolMeta, ToolResult, extended_surface_enabled, register_tool
from app.exceptions import ValidationError

logger = logging.getLogger(__name__)

ARTIFACT_KINDS_HINT = "text, markdown, json, diff, code, html, svg"


def _artifact_payload(
    *,
    title: str,
    content: str,
    kind: str,
    filename: str | None,
) -> dict[str, Any]:
    return {
        "kind": "artifact/v1",
        "artifact_kind": kind,
        "title": title,
        "content": content,
        "filename": filename,
    }


@register_tool(
    name="create_artifact",
    description=(
        "Persist a generated file as a versioned artifact (previewable and downloadable "
        "in the UI). Use this for any deliverable the user should be able to open later: "
        "reports (markdown), data files (json), code, patches (diff), or HTML/SVG pages. "
        "Provide a stable title — re-using the same title in the same conversation creates "
        "a new version rather than a duplicate."
    ),
    parameters={
        "type": "object",
        "properties": {
            "title": {
                "type": "string",
                "description": "Artifact title (also used for the default filename).",
            },
            "content": {
                "type": "string",
                "description": "Full artifact content (UTF-8, up to 512 KiB).",
            },
            "kind": {
                "type": "string",
                "description": f"One of: {ARTIFACT_KINDS_HINT}.",
                "enum": ["text", "markdown", "json", "diff", "code", "html", "svg"],
                "default": "text",
            },
            "filename": {
                "type": "string",
                "description": "Optional download filename (extension is derived from kind).",
            },
        },
        "required": ["title", "content"],
    },
    tags=["artifact", "report"],
    # 默认常驻面之外的扩展工具：工具面预算（issue #509）由显式 tool_tags 解锁，
    # 不挤占默认 10–12 个常驻槽位。
    enabled_predicate=extended_surface_enabled,
    # 写工件是外部可见副作用 → medium：risky 模式不拦（非宿主/代码执行面），
    # always 模式会停下来让人确认「这次要保存哪个工件」。
    risk_level="medium",
)
async def create_artifact(
    title: str,
    content: str,
    kind: str = "text",
    filename: str | None = None,
    organization_id: int | None = None,
    user_id: int | None = None,
    run_context: Any = None,
    **_: Any,
) -> str | ToolResult:
    from app.services import artifact_service

    if organization_id is None:
        return ToolResult.fail(
            code="auth_required",
            message="无法确定组织范围，拒绝创建工件。",
        )

    payload = _artifact_payload(title=title, content=content, kind=kind, filename=filename)

    # 执行器接线：只做校验，行由执行器写（能挂 task_id/call_id，参与版本链）。
    capture = getattr(run_context, "extra", None) or {}
    if capture.get("artifact_capture"):
        try:
            artifact_service.normalize_kind(kind)
            if not (title or "").strip():
                raise ValidationError("工件标题不能为空", field="title")
            if not (content or ""):
                raise ValidationError("工件内容不能为空", field="content")
            size = len((content or "").encode("utf-8"))
            if size > artifact_service.MAX_CONTENT_BYTES:
                raise ValidationError(
                    f"工件内容超过上限（{artifact_service.MAX_CONTENT_BYTES} 字节）",
                    field="content",
                    max_bytes=artifact_service.MAX_CONTENT_BYTES,
                    found=size,
                )
        except ValidationError as exc:
            return ToolResult.fail(code="validation_error", message=exc.message)
        return ToolResult.ok(
            data=artifact_result_text(payload),
            meta=ToolMeta(extra={"artifact": payload}),
        )

    # 未接线（技能/直调）：工具自行落库；run_context.extra 里可取会话 id。
    session_hint = None
    if run_context is not None:
        session_hint = getattr(run_context, "session_id", None) or None
    try:
        from app.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            row, created = await artifact_service.create_artifact(
                session,
                organization_id=organization_id,
                user_id=int(user_id or 0),
                kind=kind,
                title=title,
                content=content,
                filename=filename,
                conversation_id=session_hint,
                source_tool="create_artifact",
            )
            await session.commit()
            await session.refresh(row)
        receipt = artifact_result_text(
            {
                "kind": row.kind,
                "title": row.title,
                "filename": row.filename,
                "artifact_id": row.id,
                "created": created,
            }
        )
        return ToolResult.ok(
            data=receipt,
            meta=ToolMeta(extra={"artifact": payload, "artifactId": row.id}),
        )
    except ValidationError as exc:
        return ToolResult.fail(code="validation_error", message=exc.message)
    except Exception as exc:  # noqa: BLE001 — 工具失败必须结构化，不打断 run
        logger.warning("create_artifact failed: %s: %s", type(exc).__name__, exc)
        return ToolResult.fail(
            code="storage_error",
            message=f"工件保存失败：{type(exc).__name__}",
        )


def artifact_result_text(payload: dict[str, Any]) -> str:
    """工具的可读回执（模型下一轮看到的是这段，而不是整篇正文）。"""
    kind = payload.get("kind")
    if kind == "artifact/v1":  # meta 通道的原始载荷用 artifact_kind 携带真实类型
        kind = payload.get("artifact_kind")
    return json.dumps(
        {
            "kind": "artifact/v1",
            "artifact_kind": kind,
            "title": payload.get("title"),
            "artifact_id": payload.get("artifact_id"),
            "filename": payload.get("filename"),
            "status": "created" if payload.get("created", True) else "reused",
            "note": "Artifact persisted; the user can preview and download it from the artifact panel.",
        },
        ensure_ascii=False,
    )
