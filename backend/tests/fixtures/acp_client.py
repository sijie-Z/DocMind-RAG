"""ACP v1 参考客户端（issue #481 §7 任务 6）。

可复用客户端：握手 → ``session/new`` → ``session/prompt`` → 收 ``session/update``
→ 审批响应（``session/request_permission``）→ ``session/cancel``。

传输无关：构造时给一对 ``write_line`` / ``read_line`` 协程，既可接真实子进程
（:meth:`AcpClient.spawn`，``python -m app --profile acp ...``），也可接测试内的
内存管道（:class:`InMemoryAcpTransport`）。

服务端→客户端请求（审批）与通知都进同一个 ``inbox``；测试/客户端用
:meth:`next_message` / :meth:`wait_for_update` 消费，跳过项进 ``deferred``
（不丢消息）。
"""

from __future__ import annotations

import asyncio
import itertools
import json
import os
import sys
from collections.abc import Awaitable, Callable
from typing import Any

WriteLine = Callable[[str], Awaitable[None]]
ReadLine = Callable[[], Awaitable[str | None]]


class AcpClient:
    """ACP v1 参考客户端（JSON-RPC over 任意行式传输）。"""

    def __init__(self, *, write_line: WriteLine, read_line: ReadLine) -> None:
        self._write_line = write_line
        self._read_line = read_line
        self._ids = itertools.count(1)
        self._pending: dict[Any, asyncio.Future] = {}
        self._inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.deferred: list[dict[str, Any]] = []
        self.updates: list[dict[str, Any]] = []
        self._reader_task = asyncio.create_task(self._reader())

    # ── 读循环 ────────────────────────────────────────────────────────────

    async def _reader(self) -> None:
        while True:
            try:
                line = await self._read_line()
            except (asyncio.CancelledError, GeneratorExit):
                raise
            except Exception:  # noqa: BLE001 - 读端关闭按 EOF 处理
                line = None
            if line is None:
                return
            line = line.strip()
            if not line:
                continue
            message = json.loads(line)
            if "id" in message and ("result" in message or "error" in message):
                future = self._pending.pop(message["id"], None)
                if future is not None and not future.done():
                    future.set_result(message)
                continue
            if message.get("method") == "session/update":
                self.updates.append(message["params"]["update"])
            self._inbox.put_nowait(message)

    # ── 发送 ──────────────────────────────────────────────────────────────

    async def request(
        self, method: str, params: dict[str, Any] | None = None, *, timeout: float = 15.0
    ) -> dict[str, Any]:
        """发请求并等待完整响应帧（``{"jsonrpc","id","result"|"error"}``）。"""
        request_id = next(self._ids)
        future: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        await self._write_line(
            json.dumps(
                {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}},
                ensure_ascii=False,
            )
        )
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._pending.pop(request_id, None)

    async def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        await self._write_line(
            json.dumps({"jsonrpc": "2.0", "method": method, "params": params or {}})
        )

    async def respond(self, request_id: Any, result: dict[str, Any]) -> None:
        await self._write_line(
            json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}, ensure_ascii=False)
        )

    async def respond_error(
        self, request_id: Any, code: int, message: str, data: Any = None
    ) -> None:
        """以 JSON-RPC error 帧响应服务端请求（审批失败路径测试用）。"""
        error: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            error["data"] = data
        await self._write_line(
            json.dumps({"jsonrpc": "2.0", "id": request_id, "error": error}, ensure_ascii=False)
        )

    # ── 便捷方法 ──────────────────────────────────────────────────────────

    async def initialize(self) -> dict[str, Any]:
        return await self.request("initialize", {"protocolVersion": 1, "clientCapabilities": {}})

    async def new_session(self, cwd: str | None = None) -> str:
        response = await self.request("session/new", {"cwd": cwd or os.getcwd(), "mcpServers": []})
        return response["result"]["sessionId"]

    async def prompt(self, session_id: str, text: str, *, timeout: float = 30.0) -> dict:
        return await self.request(
            "session/prompt",
            {"sessionId": session_id, "prompt": [{"type": "text", "text": text}]},
            timeout=timeout,
        )

    async def cancel(self, session_id: str) -> None:
        """``session/cancel`` 是通知（无 result）。"""
        await self.notify("session/cancel", {"sessionId": session_id})

    async def next_message(self, *, timeout: float = 15.0) -> dict[str, Any]:
        return await asyncio.wait_for(self._inbox.get(), timeout=timeout)

    async def wait_for_update(
        self, *, session_id: str | None = None, timeout: float = 15.0
    ) -> dict[str, Any]:
        """等待下一条 ``session/update`` 的 update 载荷（跳过项进 deferred）。"""
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError("timed out waiting for session/update")
            message = await self.next_message(timeout=remaining)
            if message.get("method") == "session/update":
                if session_id is None or message["params"].get("sessionId") == session_id:
                    return message["params"]["update"]
            self.deferred.append(message)

    async def wait_for_server_request(
        self, method: str, *, timeout: float = 15.0
    ) -> dict[str, Any]:
        """等待服务端发起的请求帧（如 ``session/request_permission``）。"""
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError(f"timed out waiting for server request {method}")
            message = await self.next_message(timeout=remaining)
            if message.get("method") == method and "id" in message:
                return message
            self.deferred.append(message)

    async def close(self) -> None:
        self._reader_task.cancel()
        await asyncio.gather(self._reader_task, return_exceptions=True)

    # ── 子进程模式（真实入口）────────────────────────────────────────────

    @classmethod
    async def spawn(
        cls,
        *,
        backend_dir: str,
        org_id: int,
        user_id: int,
        api_key_file: str | None = None,
        env: dict[str, str] | None = None,
    ) -> AcpClient:
        """以子进程运行 ``python -m app --profile acp ...``（stdin/stdout 即协议）。"""
        args = [
            sys.executable,
            "-m",
            "app",
            "--profile",
            "acp",
            "--org-id",
            str(org_id),
            "--user-id",
            str(user_id),
        ]
        if api_key_file:
            args.extend(["--api-key-file", api_key_file])
        process = await asyncio.create_subprocess_exec(
            *args,
            cwd=backend_dir,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env={**os.environ, **(env or {})},
        )

        async def _write(line: str) -> None:
            assert process.stdin is not None
            process.stdin.write(line.encode("utf-8") + b"\n")
            await process.stdin.drain()

        async def _read() -> str | None:
            assert process.stdout is not None
            raw = await process.stdout.readline()
            return raw.decode("utf-8", errors="replace") if raw else None

        client = cls(write_line=_write, read_line=_read)
        client._process = process  # type: ignore[attr-defined]
        assert process.stderr is not None
        # 独立消费 stderr（避免管道写满阻塞子进程；terminate 时取回）。
        client._stderr_task = asyncio.create_task(process.stderr.read())  # type: ignore[attr-defined]
        return client

    async def terminate(self, *, timeout: float = 10.0) -> tuple[int, str]:
        """关闭子进程 stdin 并等待退出；返回 (exit_code, stderr 文本)。"""
        process = getattr(self, "_process", None)
        if process is None:
            await self.close()
            return 0, ""
        if process.stdin is not None and not process.stdin.is_closing():
            process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), timeout=timeout)
        except TimeoutError:
            process.kill()
            await process.wait()
        # 进程已退出：stdout 读端会 EOF，读循环自然结束；再收 stderr。
        await self.close()
        stderr_task = getattr(self, "_stderr_task", None)
        stderr = b""
        if stderr_task is not None:
            try:
                stderr = await asyncio.wait_for(stderr_task, timeout=5.0)
            except (TimeoutError, asyncio.CancelledError):
                stderr_task.cancel()
        return process.returncode or 0, stderr.decode("utf-8", errors="replace")


class InMemoryAcpTransport:
    """测试用内存管道：一端接 :class:`AcpServer` 的 stdin/stdout，一端接客户端。"""

    def __init__(self) -> None:
        import queue  # 服务端 stdin 由后台线程同步读取

        self._stdin_queue: queue.Queue[bytes] = queue.Queue()
        self._stdout_queue: asyncio.Queue[str | None] = asyncio.Queue()
        self._stdout_buffer = ""
        self.server_stdin = self
        self.server_stdout = self

    # ── 服务端 stdin（线程侧）────────────────────────────────────────────
    def readline(self, _limit: int = -1) -> bytes:
        return self._stdin_queue.get()

    def feed(self, line: str) -> None:
        self._stdin_queue.put(line.encode("utf-8") + b"\n")

    def close_stdin(self) -> None:
        self._stdin_queue.put(b"")

    # ── 服务端 stdout（事件循环侧）───────────────────────────────────────
    def write(self, text: str) -> int:
        self._stdout_buffer += text
        while "\n" in self._stdout_buffer:
            line, self._stdout_buffer = self._stdout_buffer.split("\n", 1)
            if line:
                self._stdout_queue.put_nowait(line)
        return len(text)

    def flush(self) -> None:
        return None

    # ── 客户端侧 ──────────────────────────────────────────────────────────
    async def client_write(self, line: str) -> None:
        self.feed(line)

    async def client_read(self) -> str | None:
        return await self._stdout_queue.get()

    def close(self) -> None:
        self._stdout_queue.put_nowait(None)
