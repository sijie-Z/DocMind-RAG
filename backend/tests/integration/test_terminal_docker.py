"""Docker exec PTY 集成测试（issue #477 任务 2）。

环境门控：仅当 ``DOCKER_AVAILABLE=1`` 时运行；Docker daemon 不可达时
``pytest.mark.skipif`` 显式 skip（原因写入报告），绝不伪装通过。

覆盖：echo/ls、退出码、resize（stty size）、隔离参数核对、SIGKILL。
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest

DOCKER_AVAILABLE = os.environ.get("DOCKER_AVAILABLE", "") == "1"

pytestmark = pytest.mark.skipif(
    not DOCKER_AVAILABLE,
    reason="需要 Docker daemon：设置 DOCKER_AVAILABLE=1 后运行（issue #477）",
)


def _image() -> str:
    from app.core.config import settings

    return settings.TERMINAL_CONTAINER_IMAGE or settings.SANDBOX_DOCKER_IMAGE


@pytest.fixture
async def backend():
    from app.agent.terminal.docker_backend import DockerTerminalBackend

    instance = DockerTerminalBackend(
        terminal_id=f"itest-{uuid.uuid4().hex}",
        user_id=1,
        image=_image(),
    )
    await instance.spawn(80, 24)
    try:
        yield instance
    finally:
        await instance.close()


async def _collect(backend, needle: bytes, timeout: float = 180.0) -> bytes:
    """读输出直到出现 needle；超时报错并带出已收到的内容（便于排查镜像问题）。"""
    buffer = bytearray()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while needle not in buffer:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise AssertionError(f"超时未等到 {needle!r}（已收到 {bytes(buffer)[-2000:]!r}）")
        chunk = await asyncio.wait_for(backend.read(65536), timeout=remaining)
        if not chunk:
            break
        buffer.extend(chunk)
    return bytes(buffer)


async def _drain_to_eof(backend, timeout: float = 60.0) -> bytes:
    buffer = bytearray()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise AssertionError("等待 PTY EOF 超时")
        chunk = await asyncio.wait_for(backend.read(65536), timeout=remaining)
        if not chunk:
            return bytes(buffer)
        buffer.extend(chunk)


async def test_echo_and_exit_code(backend) -> None:
    await backend.send(b"echo docmind-terminal-ok\n")
    output = await _collect(backend, b"docmind-terminal-ok")
    assert b"docmind-terminal-ok" in output

    await backend.send(b"exit 7\n")
    tail = await _drain_to_eof(backend)
    assert b"exit" not in tail.splitlines()[0:1]  # 只是确保读到了尾部，不校验内容
    # exec_inspect 的 ExitCode 可能晚于 socket EOF 一个调度周期就绪。
    loop = asyncio.get_running_loop()
    deadline = loop.time() + 10
    code = await backend.exit_code()
    while code is None and loop.time() < deadline:
        await asyncio.sleep(0.1)
        code = await backend.exit_code()
    assert code == 7


async def test_ls_runs(backend) -> None:
    await backend.send(b"ls / >/dev/null 2>&1; echo ls-status=$?\n")
    output = await _collect(backend, b"ls-status=")
    assert b"ls-status=0" in output


async def test_resize_changes_pty_size(backend) -> None:
    await backend.resize(cols=100, rows=40)
    await asyncio.sleep(0.2)  # 让 resize 在 PTY 生效
    await backend.send(b"stty size\n")
    output = await _collect(backend, b"40 100")
    assert b"40 100" in output


async def test_signal_kill_terminates_pty(backend) -> None:
    await backend.send(b"sleep 60\n")
    await asyncio.sleep(0.2)
    await backend.signal("KILL")
    await asyncio.wait_for(_drain_to_eof(backend, timeout=30), timeout=45)


async def test_container_isolation_parameters() -> None:
    """核对容器确实按 issue §5.2 的隔离参数创建。"""
    docker = pytest.importorskip("docker")
    from app.agent.terminal.docker_backend import DockerTerminalBackend

    terminal_id = f"itest-iso-{uuid.uuid4().hex}"
    instance = DockerTerminalBackend(terminal_id=terminal_id, user_id=7, image=_image())
    await instance.spawn(80, 24)
    try:
        client = docker.from_env()
        try:
            container = client.containers.get(f"docmind-term-{terminal_id}")
            attrs = container.attrs
            host = attrs["HostConfig"]
            assert host["NetworkMode"] == "none"
            assert host["CapDrop"] == ["ALL"]
            assert any("no-new-privileges" in opt for opt in host["SecurityOpt"])
            assert host["PidsLimit"] == 128
            assert host["Memory"] == 512 * 1024 * 1024
            assert host["NanoCpus"] == 1_000_000_000
            assert attrs["Config"]["User"] == "65534:65534"
            assert attrs["Config"]["WorkingDir"] == "/tmp"
            assert not host.get("Binds")  # 无宿主挂载
            assert attrs["Config"]["Labels"]["docmind.terminal"] == terminal_id
            assert attrs["Config"]["Labels"]["docmind.user"] == "7"
        finally:
            client.close()
    finally:
        await instance.close()
