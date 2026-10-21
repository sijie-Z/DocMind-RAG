"""MCP 结果/异常 → ToolResult 映射的纯函数测试（无子进程）。"""

from __future__ import annotations

from app.agent.mcp.client import (
    MCPCallResult,
    MCPConnectError,
    MCPNotConnectedError,
    MCPProtocolError,
    MCPRemoteError,
    MCPTimeoutError,
    MCPTransportError,
)
from app.agent.mcp.mapping import (
    call_result_to_tool_result,
    content_to_data,
    error_to_tool_result,
    render_content_blocks,
)


class TestRender:
    def test_text_block(self):
        assert render_content_blocks([{"type": "text", "text": "hello"}]) == ["hello"]

    def test_image_block_placeholder(self):
        [line] = render_content_blocks([{"type": "image", "data": "AAAA", "mimeType": "image/png"}])
        assert "image/png" in line and "AAAA" not in line and "base64" in line

    def test_resource_text_inlined(self):
        blocks = [{"type": "resource", "resource": {"uri": "file://x", "text": "content"}}]
        assert render_content_blocks(blocks) == ["content"]

    def test_resource_blob_placeholder(self):
        blocks = [{"type": "resource", "resource": {"uri": "file://x", "blob": "BBBB"}}]
        [line] = render_content_blocks(blocks)
        assert "file://x" in line and "二进制" in line

    def test_resource_link(self):
        [line] = render_content_blocks([{"type": "resource_link", "uri": "u", "name": "n"}])
        assert "u" in line and "n" in line

    def test_unknown_block_serialized(self):
        [line] = render_content_blocks([{"type": "totally_new", "x": 1}])
        assert "totally_new" in line


class TestContentToData:
    def test_text_joined(self):
        r = MCPCallResult(
            is_error=False, content=[{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]
        )
        assert content_to_data(r) == "a\nb"

    def test_empty_result_placeholder(self):
        assert content_to_data(MCPCallResult(is_error=False, content=[])) == "(empty result)"

    def test_structured_only(self):
        r = MCPCallResult(is_error=False, content=[], structured={"k": 1})
        assert content_to_data(r) == {"k": 1}

    def test_text_plus_structured(self):
        r = MCPCallResult(
            is_error=False, content=[{"type": "text", "text": "t"}], structured={"k": 1}
        )
        assert content_to_data(r) == {"text": "t", "structured_content": {"k": 1}}

    def test_text_clipped(self):
        big = "x" * 200_000
        out = content_to_data(
            MCPCallResult(is_error=False, content=[{"type": "text", "text": big}])
        )
        assert len(out) < 200_000 and "截断" in out


class TestCallResultToToolResult:
    def test_success(self):
        r = call_result_to_tool_result(
            MCPCallResult(is_error=False, content=[{"type": "text", "text": "ok"}]), "srv", "t"
        )
        assert r.success and r.data == "ok"

    def test_is_error_never_faked_as_success(self):
        """诚实原则核心断言：服务器 is_error → ToolResult.fail。"""
        r = call_result_to_tool_result(
            MCPCallResult(is_error=True, content=[{"type": "text", "text": "boom"}]), "srv", "t"
        )
        assert r.success is False
        assert r.error is not None and r.error.code == "api_error"
        assert "boom" in r.error.message
        assert str(r).startswith("Error:")

    def test_is_error_without_content(self):
        r = call_result_to_tool_result(MCPCallResult(is_error=True, content=[]), "srv", "t")
        assert r.success is False and r.error.code == "api_error"


class TestErrorToToolResult:
    def test_timeout(self):
        r = error_to_tool_result(MCPTimeoutError("slow"), "srv", "t")
        assert not r.success and r.error.code == "timeout"

    def test_remote_error(self):
        r = error_to_tool_result(MCPRemoteError(-32602, "bad params"), "srv", "t")
        assert not r.success and r.error.code == "api_error"
        assert "-32602" in r.error.message

    def test_transport_dead(self):
        r = error_to_tool_result(MCPTransportError("closed"), "srv", "t")
        assert not r.success and r.error.code == "unreachable"

    def test_connect_error(self):
        r = error_to_tool_result(MCPConnectError("cannot spawn"), "srv")
        assert not r.success and r.error.code == "unreachable"

    def test_not_connected(self):
        r = error_to_tool_result(MCPNotConnectedError("dead"), "srv", "t")
        assert not r.success and r.error.code == "unreachable"

    def test_protocol(self):
        r = error_to_tool_result(MCPProtocolError("weird"), "srv", "t")
        assert not r.success and r.error.code == "serialization_error"

    def test_unknown_exception_not_faked(self):
        r = error_to_tool_result(RuntimeError("???"), "srv", "t")
        assert not r.success and r.error.code == "unknown"
