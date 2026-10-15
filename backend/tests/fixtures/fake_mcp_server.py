"""仓内测试装置：一个最小可用的 MCP server（stdio，裸 JSON-RPC）。

用途：MCP client 的 E2E 测试（真子进程）。特性：
- 工具：``echo`` / ``echo2``（可被 set_tools 增删）/ ``fail``（isError）/ ``slow``（
  睡眠，测超时）/ ``die``（结果之后立即 os._exit，测崩溃）/ ``set_tools``（热
  替换工具列表并发 ``notifications/tools/list_changed``）。
- ``--silent``：对 ``initialize`` 永不响应（测连接超时）。
- 绝不访问网络；消息一行一条（MCP stdio 帧格式）。

本文件不是 pytest 用例（无 test_ 前缀）；由测试以子进程方式拉起。
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from typing import Any

SILENT = "--silent" in sys.argv

_write_lock = threading.Lock()

# 工具全集：名字 → (描述, 是否默认启用)
_ALL_TOOLS: dict[str, tuple[str, bool]] = {
    "echo": ("回显 text（前缀 echo:）", True),
    "echo2": ("第二个回显工具（默认不出现在 tools/list，可被 set_tools 打开）", False),
    "fail": ("总是返回 isError 结果（业务失败）", True),
    "slow": ("睡眠 seconds 秒后返回（测超时）", True),
    "die": ("返回结果后立即退出进程（测崩溃检测）", True),
    "set_tools": ("热替换工具列表；参数 {name: enabled}，随后发 tools/list_changed", True),
}

_ACTIVE: dict[str, bool] = {name: default for name, (_, default) in _ALL_TOOLS.items()}


def _send(message: dict[str, Any]) -> None:
    # ensure_ascii=True：输出纯 ASCII（非 ASCII 转义为 \uXXXX）。
    # Windows 上管道 stdout 默认按本地编码（GBK）写出，而 MCP 客户端按
    # UTF-8 解码——中文描述会直接打爆读取流（本机实测）。转义输出最稳。
    with _write_lock:
        sys.stdout.write(json.dumps(message, ensure_ascii=True) + "\n")
        sys.stdout.flush()


def _tool_def(name: str) -> dict[str, Any]:
    description, _ = _ALL_TOOLS[name]
    if name == "echo":
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        }
    elif name == "echo2":
        schema = {"type": "object", "properties": {"value": {"type": "string"}}}
    elif name == "slow":
        schema = {"type": "object", "properties": {"seconds": {"type": "number"}}}
    elif name == "set_tools":
        schema = {"type": "object", "properties": {"name": {"type": "string"}, "enabled": {"type": "boolean"}}}
    else:
        schema = {"type": "object", "properties": {}}
    return {"name": name, "description": description, "inputSchema": schema}


def _handle(msg: dict[str, Any]) -> None:
    method = msg.get("method")
    mid = msg.get("id")

    if method == "initialize":
        if SILENT:
            return  # 永不响应 → 客户端 connect 超时路径
        _send({
            "jsonrpc": "2.0", "id": mid,
            "result": {
                "protocolVersion": msg["params"]["protocolVersion"],
                "capabilities": {"tools": {"listChanged": True}},
                "serverInfo": {"name": "docmind-fake-mcp", "version": "0.0.1"},
            },
        })
        return

    if method == "notifications/initialized":
        return

    if method == "tools/list":
        _send({
            "jsonrpc": "2.0", "id": mid,
            "result": {"tools": [_tool_def(n) for n, on in _ACTIVE.items() if on]},
        })
        return

    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        if name == "echo":
            _send({"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": "echo:" + str(args.get("text", ""))}]}})
        elif name == "echo2":
            _send({"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": "echo2:" + str(args.get("value", ""))}]}})
        elif name == "fail":
            _send({"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": "boom"}], "isError": True}})
        elif name == "slow":
            # 后台线程执行，不阻塞读取循环（真实 MCP server 均为并发语义；
            # 否则一次 slow 调用会把后续所有请求卡在队里，超时语义失真）。
            delay = float(args.get("seconds", 1))

            def _later() -> None:
                time.sleep(delay)
                _send({"jsonrpc": "2.0", "id": mid, "result": {
                    "content": [{"type": "text", "text": "slept"}]}})

            threading.Thread(target=_later, daemon=True).start()
        elif name == "die":
            _send({"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": "dying"}]}})
            sys.stdout.flush()
            os._exit(3)
        elif name == "set_tools":
            target = str(args.get("name") or "")
            if target in _ACTIVE:
                _ACTIVE[target] = bool(args.get("enabled", True))
                _send({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
                _send({"jsonrpc": "2.0", "id": mid, "result": {
                    "content": [{"type": "text", "text": f"set_tools:{target}={_ACTIVE[target]}"}]}})
            else:
                _send({"jsonrpc": "2.0", "id": mid, "error": {
                    "code": -32602, "message": f"Unknown tool name: {target}"}})
        else:
            _send({"jsonrpc": "2.0", "id": mid, "error": {
                "code": -32602, "message": f"Unknown tool: {name}"}})
        return

    if method == "ping":
        _send({"jsonrpc": "2.0", "id": mid, "result": {}})
        return

    if mid is not None:
        _send({"jsonrpc": "2.0", "id": mid, "error": {
            "code": -32601, "message": f"Method not found: {method}"}})


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(msg, dict):
            _handle(msg)


if __name__ == "__main__":
    main()
