"""MCP 结果 → ToolResult 映射（纯函数，便于单测）。

#301 诚实原则的落点：
- 服务器 ``is_error=True`` → ``ToolResult.fail``（绝不伪装成功）；
- 传输断裂 → fail("unreachable")；超时 → fail("timeout")；
- 服务器 JSON-RPC error → fail("api_error")（服务器活着，错误如实转达）；
- 文本为空但成功 → "(empty result)"（与旧 mcp_bridge 行为一致）。
"""

from __future__ import annotations

import json
from typing import Any

from app.agent.mcp.client import (
    MCPCallResult,
    MCPClientError,
    MCPConnectError,
    MCPNotConnectedError,
    MCPProtocolError,
    MCPRemoteError,
    MCPTimeoutError,
    MCPTransportError,
)
from app.agent.registry import ToolResult

# 单个工具结果渲染上限（防止超大文本块把上下文打爆；与 _smart_truncate 顶部留余量）
_MAX_TEXT_CHARS = 100_000


def _clip(text: str) -> str:
    if len(text) <= _MAX_TEXT_CHARS:
        return text
    return text[:_MAX_TEXT_CHARS] + f"\n…[截断，共 {len(text)} 字符]"


def render_content_blocks(blocks: list[dict[str, Any]]) -> list[str]:
    """把归一化 content 块渲染为可读文本片段（每个块一条）。"""
    parts: list[str] = []
    for block in blocks:
        btype = str(block.get("type") or "")
        if btype == "text":
            parts.append(str(block.get("text") or ""))
        elif btype in ("image", "audio"):
            data = block.get("data") or ""
            mime = block.get("mimeType") or block.get("mime_type") or "?"
            parts.append(f"[{btype}: {mime}, base64 {len(str(data))} 字符（未内联）]")
        elif btype == "resource":
            resource = block.get("resource") or {}
            if isinstance(resource, dict) and resource.get("text") is not None:
                parts.append(str(resource["text"]))
            elif isinstance(resource, dict):
                uri = resource.get("uri") or "?"
                blob = resource.get("blob") or ""
                parts.append(f"[resource: {uri}（二进制 {len(str(blob))} 字符未内联）]")
            else:
                parts.append(f"[resource: {resource!r}]")
        elif btype == "resource_link":
            uri = block.get("uri") or "?"
            name = block.get("name") or ""
            parts.append(f"[resource_link: {name} {uri}]".replace("  ", " "))
        else:
            parts.append(json.dumps(block, ensure_ascii=False, default=str))
    return parts


def content_to_data(result: MCPCallResult) -> Any:
    """成功结果 → registry data。

    - 有 structured_content 且无文本 → 直接给结构化对象；
    - 两者都有 → ``{"text": …, "structured_content": …}``；
    - 只有文本 → 拼接字符串；空 → "(empty result)"。
    """
    parts = [p for p in render_content_blocks(result.content) if p != ""]
    text = _clip("\n".join(parts)) if parts else ""
    if result.structured is not None:
        if text:
            return {"text": text, "structured_content": result.structured}
        return result.structured
    return text if text else "(empty result)"


def call_result_to_tool_result(result: MCPCallResult, server: str, tool: str) -> ToolResult:
    """成功的协议交换 → ToolResult（含 is_error 语义）。"""
    if result.is_error:
        parts = [p for p in render_content_blocks(result.content) if p]
        detail = _clip("\n".join(parts)) if parts else "(服务器未提供错误详情)"
        return ToolResult.fail(
            code="api_error",
            message=f"MCP 工具 {server}/{tool} 执行失败: {detail}",
            raw={"content": result.content},
        )
    return ToolResult.ok(data=content_to_data(result))


def error_to_tool_result(exc: BaseException, server: str, tool: str | None = None) -> ToolResult:
    """client 层异常 → 结构化 ToolResult（诚实分类）。"""
    where = f"{server}/{tool}" if tool else server
    if isinstance(exc, MCPTimeoutError):
        return ToolResult.fail(code="timeout", message=f"MCP 调用超时（{where}）: {exc}", raw=exc)
    if isinstance(exc, MCPRemoteError):
        return ToolResult.fail(
            code="api_error",
            message=f"MCP 服务器返回错误（{where}）: {exc}",
            raw=exc,
        )
    if isinstance(exc, (MCPTransportError, MCPConnectError, MCPNotConnectedError)):
        return ToolResult.fail(
            code="unreachable",
            message=f"MCP 服务器不可达（{where}）: {exc}",
            raw=exc,
        )
    if isinstance(exc, MCPProtocolError):
        return ToolResult.fail(
            code="serialization_error",
            message=f"MCP 响应不符合协议（{where}）: {exc}",
            raw=exc,
        )
    if isinstance(exc, MCPClientError):
        return ToolResult.fail(code="unknown", message=f"MCP 调用失败（{where}）: {exc}", raw=exc)
    return ToolResult.fail(
        code="unknown",
        message=f"MCP 调用异常（{where}）: {type(exc).__name__}: {exc}",
        raw=exc,
    )
