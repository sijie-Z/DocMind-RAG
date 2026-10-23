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
``sendall``/``write`` 兼容。所有阻塞调用都经 ``asyncio.to_thread``。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from typing import Any

from app.agent.terminal.manager import TerminalDockerUnavailable, TerminalError

logger = logging.getLogger(__name__)

LABEL_TERMINAL = "docmind.terminal"
LABEL_USER = "docmind.user"

_TERMINAL_USER = "65534:65534"
_CONTAINER_MEMORY = "512m"
_CONTAINER_PIDS_LIMIT = 128
_CONTAINER_NANO_CPUS = 1_000_000_000  # --cpus 1
_EXEC_READ_CHUNK = 65536

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


def _write_socket(sock: Any, data: bytes) -> None:
    """向 exec socket 写全量数据（SocketIO 只有 write；raw socket 是 sendall）。"""
    sendall = getattr(sock, "sendall", None)
    if callable(sendall):
        sendall(data)
        return
    write = getattr(sock, "write", None)
    if callable(write):
        write(data)
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

    # ── spawn ──
    async def spawn(self, cols: int, rows: int) -> None:
        await asyncio.to_thread(self._spawn_sync, cols, rows)

    def _spawn_sync(self, cols: int, rows: int) -> None:
        docker = _import_docker()
        try:
            client = docker.from_env()
            client.ping()
        except Exception as e:
            raise TerminalDockerUnavailable(f"Docker daemon 不可用: {e}") from e
        self._client = client
        try:
            self._container = client.containers.run(
                self.image,
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
        await asyncio.to_thread(_write_socket, sock, data)
        return len(data)

    async def read(self, max_bytes: int) -> bytes:
        sock = self._sock
        if sock is None:
            return b""
        try:
            return await asyncio.to_thread(_read_socket, sock, min(max_bytes, _EXEC_READ_CHUNK))
        except (OSError, ValueError):
            # 关闭/容器退出时 socket 被断开 —— 对上层的语义就是 EOF。
            return b""

    async def resize(self, cols: int, rows: int) -> None:
        await asyncio.to_thread(self._resize_sync, cols, rows)

    def _resize_sync(self, cols: int, rows: int) -> None:
        if self._client is None or self._exec_id is None:
            return
        self._client.api.exec_resize(self._exec_id, height=rows, width=cols)

    async def signal(self, sig: str) -> None:
        normalized = sig.upper().lstrip("SIG")
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
        sock, self._sock = self._sock, None
        if sock is not None:
            with contextlib.suppress(Exception):
                sock.close()
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
