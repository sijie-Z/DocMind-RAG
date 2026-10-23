"""Docker exec PTY 后端（issue #477 §5.2）。

容器模型（每个终端一个**专用**容器，而不是一次性 ``docker run --rm``）：

- ``docker run -d --rm`` + ``sleep infinity`` 承载会话；
- ``exec_create(cmd=["/bin/sh"], stdin=True, tty=True, user=65534:65534)`` 建立 PTY；
- ``exec_start(socket=True)`` 拿原始双向 socket；resize 用 ``exec_resize``；
- 关闭 = ``kill`` + ``remove(force=True)``（清理失败只记日志）。

隔离参数（issue §5.2 红线）：非 root（65534:65534）、``network none``、
``cap-drop ALL``、``no-new-privileges``、cpus=1、memory=512m、pids-limit=128、
不挂载任何宿主路径。默认 compose 不挂 docker.sock，只有显式 overlay
（``docker-compose.terminal.yml``）才启用本后端。

实现注记（docker SDK 7.x 实测）：高层 ``Container.exec_create/exec_start/
exec_resize/exec_inspect`` 已从 SDK 移除，必须走 ``client.api.*``；
``exec_start(socket=True)`` 返回的可能是 ``socket.SocketIO``（POSIX）或
``NpipeSocket``（Windows），读写分别用 ``docker.utils.socket.read`` 与
``sendall``/``write`` 兼容。

**读取模型（issue #477 对抗评审 B1）**：PTY 读取是长阻塞调用，若用
``asyncio.to_thread``，每个空闲终端会永久占住默认线程池的一个线程
（默认池 ``min(32, cpu+4)``，配额 3×3 即可能在 4 核机上饱和，导致所有
终端假死）。这里改为**每终端一个专用后台读线程**：线程阻塞在 socket
上，读到数据经 ``loop.call_soon_threadsafe`` 投递到 asyncio.Queue；
``read()`` 只是 await 队列，空闲时不占任何池线程。写/resize/close 仍是
短暂的 ``asyncio.to_thread`` 调用。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import threading
from typing import Any

from app.agent.terminal.manager import (
    TerminalDockerUnavailable,
    TerminalError,
    TerminalExited,
    normalize_signal,
)

logger = logging.getLogger(__name__)

LABEL_TERMINAL = "docmind.terminal"
LABEL_USER = "docmind.user"

_TERMINAL_USER = "65534:65534"
_CONTAINER_MEMORY = "512m"
_CONTAINER_PIDS_LIMIT = 128
_CONTAINER_NANO_CPUS = 1_000_000_000  # --cpus 1
_EXEC_READ_CHUNK = 65536
# 读线程 → 事件循环的有界缓冲：最多 32 个 64KiB chunk（约 2MiB）。
# 队列满时读线程阻塞在信号量上（真正的背压：PTY 内核缓冲随后填满，
# 进程写阻塞），而不是无界堆积内存（对抗评审 round-2 NEW-ISSUE）。
_OUT_QUEUE_CHUNKS = 32

__all__ = [
    "LABEL_TERMINAL",
    "LABEL_USER",
    "DockerTerminalBackend",
    "docker_available",
    "list_terminal_containers",
    "remove_terminal_container",
]


def _import_docker():
    try:
        import docker  # type: ignore[import-not-found]
    except ImportError as e:  # pragma: no cover — 部署缺 SDK 的诚实失败
        raise TerminalDockerUnavailable(
            "Docker SDK 未安装（requirements.txt 需要 docker>=7.1）"
        ) from e
    return docker


def docker_available(timeout: float = 3.0) -> bool:
    """阻塞探测 Docker daemon；调用方应放进 ``asyncio.to_thread``。"""
    try:
        docker = _import_docker()
        client = docker.from_env(timeout=timeout)
        try:
            return bool(client.ping())
        finally:
            client.close()
    except Exception:
        return False


def _read_socket(sock: Any, max_bytes: int) -> bytes:
    """从 exec socket 读一次（兼容 SocketIO / NpipeSocket / raw socket）。"""
    from docker.utils.socket import read as docker_read

    data = docker_read(sock, max_bytes)
    if isinstance(data, str):  # NpipeSocket 结束时 docker 返回 ''
        return data.encode("utf-8", errors="replace")
    return data or b""


def validate_terminal_image(image: str) -> str:
    """校验终端镜像引用（issue §5.2：必须固定 tag/digest，禁止 latest）。

    Docker 对裸名（如 ``python``）等价于 ``python:latest``，同样拒绝。
    """
    candidate = (image or "").strip()
    if not candidate:
        raise TerminalError("终端镜像为空（配置 TERMINAL_CONTAINER_IMAGE / SANDBOX_DOCKER_IMAGE）")
    if "@" in candidate:  # digest 引用（repo@sha256:...）总是固定
        return candidate
    last_segment = candidate.rsplit("/", 1)[-1]
    if ":" not in last_segment:
        raise TerminalError("终端镜像必须固定 tag 或 digest（裸名等价 latest，已拒绝）")
    tag = last_segment.rsplit(":", 1)[1]
    if not tag or tag == "latest":
        raise TerminalError("终端镜像禁止使用 latest，必须固定 tag 或 digest")
    return candidate


def _write_socket(sock: Any, data: bytes) -> None:
    """向 exec socket 写全量数据（SocketIO 的 write 可能短写，必须循环）。"""
    sendall = getattr(sock, "sendall", None)
    if callable(sendall):
        sendall(data)
        return
    write = getattr(sock, "write", None)
    if callable(write):
        view = memoryview(data)
        while view:
            written = write(view)
            if not written:
                raise OSError("exec socket write returned 0")
            view = view[written:]
        flush = getattr(sock, "flush", None)
        if callable(flush):
            flush()
        return
    os.write(sock.fileno(), data)  # pragma: no cover — 兜底路径


class DockerTerminalBackend:
    """基于 ``docker exec`` PTY 的终端后端（一终端一容器）。"""

    def __init__(
        self,
        *,
        terminal_id: str,
        user_id: int,
        image: str,
        memory: str = _CONTAINER_MEMORY,
        pids_limit: int = _CONTAINER_PIDS_LIMIT,
        nano_cpus: int = _CONTAINER_NANO_CPUS,
        **_ignored: Any,
    ) -> None:
        self.terminal_id = terminal_id
        self.user_id = user_id
        self.image = image
        self._memory = memory
        self._pids_limit = pids_limit
        self._nano_cpus = nano_cpus
        self._client: Any = None
        self._container: Any = None
        self._exec_id: str | None = None
        self._sock: Any = None
        # 专用读线程 + 跨线程输出队列（见模块头"读取模型"）。
        self._loop: asyncio.AbstractEventLoop | None = None
        self._out_queue: asyncio.Queue[bytes | None] | None = None
        self._reader_thread: threading.Thread | None = None
        self._slots = threading.BoundedSemaphore(_OUT_QUEUE_CHUNKS)
        self._closed = threading.Event()
        self._pending = bytearray()
        self._eof = False

    # ── spawn ──
    async def spawn(self, cols: int, rows: int) -> None:
        loop = asyncio.get_running_loop()
        await asyncio.to_thread(self._spawn_sync, cols, rows)
        self._loop = loop
        # maxsize 多留 1 个位置给 EOF 哨兵（EOF 不占背压信号量）。
        self._out_queue = asyncio.Queue(maxsize=_OUT_QUEUE_CHUNKS + 1)
        self._reader_thread = threading.Thread(
            target=self._reader_sync,
            name=f"terminal-pty-{self.terminal_id}",
            daemon=True,
        )
        try:
            self._reader_thread.start()
        except Exception:
            self._reader_thread = None
            await asyncio.to_thread(self._cleanup_sync)
            raise

    def _reader_sync(self) -> None:
        """专用读线程：阻塞读 socket → 投递到事件循环队列；EOF 投递 None。

        队列满时在信号量上阻塞（背压），关闭时经 ``_closed`` 与 0.5s 超时退出。
        """
        loop = self._loop
        sock = self._sock
        if loop is None or sock is None:
            return
        try:
            while True:
                data = _read_socket(sock, _EXEC_READ_CHUNK)
                if not data:
                    break
                while not self._slots.acquire(timeout=0.5):
                    if self._closed.is_set():
                        return
                try:
                    loop.call_soon_threadsafe(self._deliver, data)
                except RuntimeError:  # 事件循环已关闭
                    return
        except (OSError, ValueError):
            pass
        finally:
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(self._deliver, None)

    def _deliver(self, item: bytes | None) -> None:
        if self._out_queue is not None:
            self._out_queue.put_nowait(item)

    def _spawn_sync(self, cols: int, rows: int) -> None:
        docker = _import_docker()
        try:
            client = docker.from_env()
        except Exception as e:
            raise TerminalDockerUnavailable(f"Docker daemon 不可用: {e}") from e
        try:
            client.ping()
        except Exception as e:
            # 失败路径必须关闭 client，否则每失败一次泄漏一个连接/session。
            with contextlib.suppress(Exception):
                client.close()
            raise TerminalDockerUnavailable(f"Docker daemon 不可用: {e}") from e
        self._client = client
        try:
            image = validate_terminal_image(self.image)
            self._container = client.containers.run(
                image,
                ["sleep", "infinity"],
                name=f"docmind-term-{self.terminal_id}",
                detach=True,
                auto_remove=True,
                labels={
                    LABEL_TERMINAL: self.terminal_id,
                    LABEL_USER: str(self.user_id),
                },
                network_mode="none",
                nano_cpus=self._nano_cpus,
                mem_limit=self._memory,
                pids_limit=self._pids_limit,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges"],
                user=_TERMINAL_USER,
                working_dir="/tmp",
            )
            exec_info = client.api.exec_create(
                self._container.id,
                cmd=["/bin/sh"],
                stdin=True,
                tty=True,
                user=_TERMINAL_USER,
            )
            self._exec_id = (exec_info or {}).get("Id") or (exec_info or {}).get("id")
            if not self._exec_id:
                raise TerminalError("docker exec_create 未返回 exec id")
            self._sock = client.api.exec_start(self._exec_id, socket=True)
            # 初始窗口（exec_create 本身不接受尺寸）。
            with contextlib.suppress(Exception):
                client.api.exec_resize(self._exec_id, height=rows, width=cols)
        except TerminalError:
            self._cleanup_sync()
            raise
        except Exception as e:
            self._cleanup_sync()
            raise TerminalError(f"创建终端容器失败: {e}") from e

    # ── I/O ──
    async def send(self, data: bytes) -> int:
        sock = self._sock
        if sock is None:
            raise TerminalError("终端 PTY 未就绪")
        try:
            await asyncio.to_thread(_write_socket, sock, data)
        except (OSError, ValueError) as e:
            # socket 已随进程退出关闭：映射到「终端已退出」，而不是裸 OSError
            # （否则 WS 收到未分类异常、不发 error/exit 帧）。
            raise TerminalExited("终端进程已退出") from e
        return len(data)

    async def read(self, max_bytes: int) -> bytes:
        """await 读线程投递的输出；空闲时不占用任何线程池线程。"""
        if self._out_queue is None:
            return b""
        while not self._pending:
            if self._eof:
                return b""
            item = await self._out_queue.get()
            if item is None:
                self._eof = True
                return b""
            self._slots.release()  # 消费一个 chunk，放开读线程的背压槽位
            self._pending.extend(item)
        take = min(max_bytes, len(self._pending))
        data = bytes(self._pending[:take])
        del self._pending[:take]
        return data

    async def resize(self, cols: int, rows: int) -> None:
        await asyncio.to_thread(self._resize_sync, cols, rows)

    def _resize_sync(self, cols: int, rows: int) -> None:
        if self._client is None or self._exec_id is None:
            return
        self._client.api.exec_resize(self._exec_id, height=rows, width=cols)

    async def signal(self, sig: str) -> None:
        normalized = normalize_signal(sig)
        if normalized == "INT":
            # 真实终端语义：^C 送给前台进程组。
            await self.send(b"\x03")
            return
        if normalized not in ("TERM", "KILL"):
            raise TerminalError(f"不支持的信号: {sig}")
        await asyncio.to_thread(self._signal_sync, normalized)

    def _signal_sync(self, sig: str) -> None:
        if self._container is None or self._exec_id is None:
            raise TerminalError("终端容器不存在")
        pid = None
        with contextlib.suppress(Exception):
            info = self._client.api.exec_inspect(self._exec_id) or {}
            pid = info.get("Pid") or info.get("pid")
        if pid:
            # 同一 uid（65534）内发信号；pid 已转 int，无命令注入面。
            with contextlib.suppress(Exception):
                self._container.exec_run(
                    ["sh", "-c", f"kill -{sig} {int(pid)}"],
                    user=_TERMINAL_USER,
                )
        if sig == "KILL":
            # 兜底：exec 进程不可定位时停容器（PTY 随之 EOF）。
            with contextlib.suppress(Exception):
                self._container.kill()

    async def exit_code(self) -> int | None:
        return await asyncio.to_thread(self._exit_code_sync)

    def _exit_code_sync(self) -> int | None:
        if self._client is None or self._exec_id is None:
            return None
        try:
            info = self._client.api.exec_inspect(self._exec_id) or {}
        except Exception:
            return None
        raw = info.get("ExitCode", info.get("exit_code"))
        try:
            return int(raw) if raw is not None else None
        except (TypeError, ValueError):
            return None

    # ── close ──
    async def close(self) -> None:
        await asyncio.to_thread(self._cleanup_sync)

    def _cleanup_sync(self) -> None:
        self._closed.set()
        sock, self._sock = self._sock, None
        if sock is not None:
            with contextlib.suppress(Exception):
                sock.close()
        # 关闭 socket 会让阻塞中的读线程退出；join 避免线程泄漏。
        # 读线程若卡在背压信号量上，会在 0.5s 超时内看到 _closed 并退出。
        thread, self._reader_thread = self._reader_thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        container, self._container = self._container, None
        if container is not None:
            with contextlib.suppress(Exception):
                container.kill()
            with contextlib.suppress(Exception):
                container.remove(force=True)
        client, self._client = self._client, None
        self._exec_id = None
        if client is not None:
            with contextlib.suppress(Exception):
                client.close()


# ── 回收辅助（reaper 用；全部阻塞，调用方经 to_thread）──────────────────


def list_terminal_containers() -> list[dict[str, Any]]:
    """列出带 ``docmind.terminal`` 标签的容器快照（不持有 SDK 对象）。"""
    docker = _import_docker()
    client = docker.from_env()
    try:
        snapshots: list[dict[str, Any]] = []
        for container in client.containers.list(all=True, filters={"label": LABEL_TERMINAL}):
            attrs = container.attrs or {}
            labels = (attrs.get("Config") or {}).get("Labels") or {}
            snapshots.append(
                {
                    "id": container.id,
                    "name": container.name,
                    "labels": labels,
                    "created": attrs.get("Created") or "",
                    "status": (attrs.get("State") or {}).get("Status") or "",
                }
            )
        return snapshots
    finally:
        client.close()


def remove_terminal_container(container_id: str) -> bool:
    """强制删除一个终端容器（reaper 用）；失败返回 False 并只记 debug。"""
    docker = _import_docker()
    try:
        client = docker.from_env()
    except Exception:
        return False
    try:
        container = client.containers.get(container_id)
        with contextlib.suppress(Exception):
            container.kill()
        container.remove(force=True)
        return True
    except Exception:
        logger.debug("terminal container remove failed id=%s", container_id, exc_info=True)
        return False
    finally:
        with contextlib.suppress(Exception):
            client.close()
