"""入站 MCP Server 的只读知识库工具（批次十六）。

只暴露两个工具，且都不产生写副作用：

- ``search_knowledge_base``：组织范围检索。**必须**把认证用户的 id 作为
  ``viewer_user_id`` 传给检索层（否则私有文档会进入结果，且共享检索缓存
  会按无 viewer 命名空间串味）。``top_k`` 被钳制到 ``MCP_SERVER_MAX_TOP_K``。
- ``get_document``：先按 key 绑定组织 + viewer ACL 取文档；跨组织、私有不可见
  统一返回同一条 not-found 文案（不泄露存在性）。正文截断到
  ``MCP_SERVER_MAX_CHARS`` 并显式标记 ``truncated``。

审计：每次工具调用写 ``mcp_tool_call``，只含 tool / org / 结果数 / latency /
outcome，绝不含查询正文、文档正文或原始 API key。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from app.core.config import settings
from app.core.security import get_document_for_user
from app.exceptions import AuthorizationError, NotFoundError
from app.mcp_server.auth import MCPIdentity, get_mcp_identity
from app.rag.errors import RetrievalUnavailableError
from app.rag.source_provenance import SourceProvenanceUnavailableError
from app.services.audit_service import audit_service
from app.services.document_preview import load_document_preview_text
from app.services.knowledge_service import knowledge_service

logger = logging.getLogger(__name__)

DEFAULT_TOP_K = 5
DEFAULT_MAX_CHARS = 4_000
SNIPPET_MAX_CHARS = 500
MAX_DOCUMENT_IDS = 50
NOT_FOUND_MESSAGE = "Document not found"

_READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


@dataclass(frozen=True)
class _MCPViewer:
    """传给 ``get_document_for_user`` 的最小 viewer 投影。

    MCP 入口不允许 superuser 旁路：即使 key 属于平台超管，也只按 key 绑定
    组织 + 文档 ACL 判定。这里显式把 ``is_superuser`` 固定为 False，避免
    ``get_document_for_user`` 的 superuser 分支绕过 viewer ACL。
    """

    id: int
    organization_id: int
    is_superuser: bool = False


def _configured_max_top_k() -> int:
    return max(1, int(getattr(settings, "MCP_SERVER_MAX_TOP_K", 10) or 10))


def _configured_max_chars() -> int:
    return max(1, int(getattr(settings, "MCP_SERVER_MAX_CHARS", 20_000) or 20_000))


def _require_identity() -> MCPIdentity:
    identity = get_mcp_identity()
    if identity is None:
        # 理论上只在接线错误时出现；不把内部状态/堆栈暴露给客户端。
        raise ToolError("MCP request context is unavailable")
    return identity


async def _audit_tool_call(
    identity: MCPIdentity,
    *,
    tool: str,
    started_at: float,
    result_count: int,
    outcome: str,
) -> None:
    """写一条最小审计；失败只告警，不影响工具响应。"""
    detail = json.dumps(
        {
            "tool": tool,
            "organization_id": identity.organization_id,
            "result_count": result_count,
            "latency_ms": max(0, int((time.perf_counter() - started_at) * 1000)),
            "outcome": outcome,
        },
        ensure_ascii=False,
    )
    try:
        await audit_service.log_activity(
            user_id=identity.user_id,
            action="mcp_tool_call",
            target_type="mcp_tool",
            target_id=tool,
            detail=detail,
        )
    except Exception as exc:  # noqa: BLE001 — 审计失败不得反噬只读工具
        logger.warning("mcp tool audit failed: %s", type(exc).__name__)


def _project_search_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """只保留 plan 约定的最小字段，并对 snippet 做有界截断。"""
    projected: list[dict[str, Any]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        document_id = item.get("id") or item.get("document_id") or ""
        source = item.get("source") if isinstance(item.get("source"), dict) else {}
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        title = source.get("filename") or metadata.get("filename") or item.get("filename") or ""
        snippet = item.get("content") or metadata.get("snippet") or item.get("text") or ""
        if not isinstance(snippet, str):
            snippet = str(snippet)
        score = item.get("score")
        projected.append(
            {
                "document_id": str(document_id),
                "title": str(title),
                "snippet": snippet[:SNIPPET_MAX_CHARS],
                "score": float(score) if isinstance(score, int | float) else None,
            }
        )
    return projected


async def _load_visible_document(
    identity: MCPIdentity,
    document_id: str,
) -> str:
    """按 key 绑定组织 + viewer ACL 取文档；返回 filename（session 关闭后仍安全）。"""
    from app.models.document import Document

    async with identity.session_factory() as db:
        document = await db.get(Document, document_id)
        if document is None or document.organization_id != identity.organization_id:
            raise NotFoundError(NOT_FOUND_MESSAGE)

        viewer = _MCPViewer(
            id=identity.user_id,
            organization_id=identity.organization_id,
        )
        try:
            visible = await get_document_for_user(db, viewer, document_id)
        except (NotFoundError, AuthorizationError):
            # 跨组织 / 私有不可见统一成同一条文案，不泄露文档是否存在。
            raise NotFoundError(NOT_FOUND_MESSAGE) from None
        return visible.filename or ""


def register_readonly_tools(server: MCPServer) -> None:
    """把只读知识库工具注册到入站 MCPServer。"""

    @server.tool(
        name="search_knowledge_base",
        description=(
            "Search the organization's knowledge base. Read-only. Returns the "
            "smallest usable result set: document_id, title, snippet, score."
        ),
        annotations=_READ_ONLY,
        structured_output=False,
    )
    async def search_knowledge_base(
        query: Annotated[
            str,
            Field(
                description="Natural-language search query.",
                min_length=1,
                max_length=2_000,
            ),
        ],
        top_k: Annotated[
            int,
            Field(
                description=(
                    "Maximum number of chunks to return. Values above the server limit are clamped."
                ),
                ge=1,
            ),
        ] = DEFAULT_TOP_K,
        mode: Annotated[
            Literal["hybrid", "vector"],
            Field(description="Retrieval mode: hybrid (default) or vector."),
        ] = "hybrid",
        document_ids: Annotated[
            list[str] | None,
            Field(
                description=(
                    "Optional document-id allow-list. Only documents in the "
                    "authenticated organization can match."
                ),
                max_length=MAX_DOCUMENT_IDS,
            ),
        ] = None,
    ) -> dict[str, Any]:
        identity = _require_identity()
        started_at = time.perf_counter()
        effective_query = query.strip()
        if not effective_query:
            await _audit_tool_call(
                identity,
                tool="search_knowledge_base",
                started_at=started_at,
                result_count=0,
                outcome="invalid_query",
            )
            raise ToolError("query must not be empty")

        effective_top_k = min(top_k, _configured_max_top_k())
        scoped_document_ids = [
            value for value in (document_ids or []) if isinstance(value, str) and value.strip()
        ] or None

        try:
            results, _ = await knowledge_service.search_knowledge_with_meta(
                query=effective_query,
                organization_id=identity.organization_id,
                top_k=effective_top_k,
                search_mode=mode,
                document_ids=scoped_document_ids,
                # 必传：私有文档 ACL 与检索缓存都依赖真实 viewer。
                viewer_user_id=identity.user_id,
                strict=True,
            )
            projected = _project_search_results(results)
        except RetrievalUnavailableError:
            await _audit_tool_call(
                identity,
                tool="search_knowledge_base",
                started_at=started_at,
                result_count=0,
                outcome="retrieval_unavailable",
            )
            raise ToolError("Knowledge retrieval is temporarily unavailable") from None
        except SourceProvenanceUnavailableError:
            await _audit_tool_call(
                identity,
                tool="search_knowledge_base",
                started_at=started_at,
                result_count=0,
                outcome="source_original_unavailable",
            )
            raise ToolError(
                "Knowledge retrieval is unavailable until source documents are rebuilt"
            ) from None
        except Exception:
            await _audit_tool_call(
                identity,
                tool="search_knowledge_base",
                started_at=started_at,
                result_count=0,
                outcome="error",
            )
            raise ToolError("Knowledge retrieval failed") from None

        await _audit_tool_call(
            identity,
            tool="search_knowledge_base",
            started_at=started_at,
            result_count=len(projected),
            outcome="ok",
        )
        return {"count": len(projected), "results": projected}

    @server.tool(
        name="get_document",
        description=(
            "Read a document's text from the authenticated organization. Read-only. "
            "Returns a bounded content excerpt and an explicit truncated flag."
        ),
        annotations=_READ_ONLY,
        structured_output=False,
    )
    async def get_document(
        document_id: Annotated[
            str,
            Field(description="Document id returned by search_knowledge_base.", min_length=1),
        ],
        max_chars: Annotated[
            int,
            Field(
                description=(
                    "Maximum characters to return. Values above the server limit are clamped."
                ),
                ge=1,
            ),
        ] = DEFAULT_MAX_CHARS,
    ) -> dict[str, Any]:
        identity = _require_identity()
        started_at = time.perf_counter()
        effective_max_chars = min(max_chars, _configured_max_chars())

        try:
            document_filename = await _load_visible_document(identity, document_id)
        except (NotFoundError, AuthorizationError):
            await _audit_tool_call(
                identity,
                tool="get_document",
                started_at=started_at,
                result_count=0,
                outcome="not_found",
            )
            raise ToolError(NOT_FOUND_MESSAGE) from None
        except Exception:
            await _audit_tool_call(
                identity,
                tool="get_document",
                started_at=started_at,
                result_count=0,
                outcome="error",
            )
            raise ToolError("Document could not be read") from None

        try:
            async with identity.session_factory() as db:
                preview = await load_document_preview_text(db, document_id)
            content = preview.content or ""
        except Exception:
            await _audit_tool_call(
                identity,
                tool="get_document",
                started_at=started_at,
                result_count=0,
                outcome="error",
            )
            raise ToolError("Document content is unavailable") from None

        truncated = bool(preview.truncated) or len(content) > effective_max_chars
        await _audit_tool_call(
            identity,
            tool="get_document",
            started_at=started_at,
            result_count=1,
            outcome="ok",
        )
        return {
            "document_id": document_id,
            "title": document_filename,
            "content": content[:effective_max_chars],
            "truncated": truncated,
            "total_chars": len(content),
            "text_provenance": getattr(preview, "text_provenance", None),
        }
