"""Docker sandbox executor — isolated code execution in containers.

issue #206：本模块同时提供代码工具（``execute_python``）的 ``SANDBOX_MODE``
解析入口 ``resolve_sandbox_mode``。语义为 **fail-closed**：只有显式
``SANDBOX_MODE=ast`` 才允许进程内执行；``auto`` / ``docker`` 在 Docker 不可用时
拒绝执行，绝不再静默回落到 AST 沙箱。

注意：workflow 代码节点仍保留自己的 Docker-only 判定（节点语义不允许进程内
执行），未复用本解析器。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# 默认镜像：仅当调用方未显式传 image 时使用。仓库内主调用侧（code_execution.py /
# workflow_engine.py）都显式传 settings.SANDBOX_DOCKER_IMAGE —— 此处不再读
# os.environ：宿主直跑时 .env 不注入 os.environ，该通道永远看不到 .env 值，
# 双通道既不一致也无实际消费者（issue #242 疑点 1，统一入口）。
_SANDBOX_IMAGE = "python:3.11-slim"

# ── Detection ────────────────────────────────────────────────────────


def _docker_available() -> bool:
    """Check if Docker daemon is installed and reachable."""
    if not shutil.which("docker"):
        return False
    try:
        import subprocess

        result = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            timeout=3,
            text=True,
        )
        return result.returncode == 0 and result.stdout.strip() != ""
    except Exception:
        return False


# ── Mode resolution (issue #206) ─────────────────────────────────────


class SandboxUnavailableError(RuntimeError):
    """Configured sandbox cannot be honoured — refuse to execute (fail-closed)."""


# 进程级"已记录 resolved mode"标记：启动/首用记录一次，避免每次调用刷屏。
# 测试可直接 monkeypatch 复位。
_resolve_logged = False


def resolve_sandbox_mode() -> str:
    """Resolve ``settings.SANDBOX_MODE`` to ``"docker"`` or ``"ast"``.

    Fail-closed contract (issue #206):

    - ``"ast"``    — explicitly selected in-process AST sandbox; never probes Docker.
    - ``"auto"``   — Docker when reachable, otherwise **refuse** (raise
      :class:`SandboxUnavailableError`). There is no silent downgrade to the
      weaker in-process sandbox.
    - ``"docker"`` — same, Docker is mandatory.

    The resolved mode is logged once per process at WARNING level so operators
    can see the actual execution surface (and a fail-closed deployment) in logs.
    """
    global _resolve_logged
    from app.core.config import settings

    configured = getattr(settings, "SANDBOX_MODE", "auto")
    if configured not in ("auto", "docker", "ast"):
        # Settings 的 Literal 校验应在加载期拒绝未知值；防御性兜底同样 fail-closed。
        if not _resolve_logged:
            _resolve_logged = True
            logger.warning(
                "Sandbox mode resolved to unavailable (configured=%r is not one of "
                "auto/docker/ast); code execution will fail closed.",
                configured,
            )
        raise SandboxUnavailableError(
            f"SANDBOX_MODE={configured!r} 不是受支持的模式（auto/docker/ast），已拒绝执行代码。"
        )

    if configured == "ast":
        resolved = "ast"  # 显式选择：不探测 Docker（也不受 Docker 状态影响）
    elif _docker_available():
        resolved = "docker"
    else:
        resolved = "unavailable"

    if not _resolve_logged:
        _resolve_logged = True
        if resolved == "unavailable":
            logger.warning(
                "Sandbox mode resolved to unavailable (configured=%s, "
                "docker_available=False); code execution will fail closed. "
                "Set SANDBOX_MODE=ast to allow the in-process AST sandbox, or "
                "make Docker reachable.",
                configured,
            )
        else:
            logger.warning(
                "Sandbox mode resolved: %s (configured=%s)",
                resolved,
                configured,
            )

    if resolved == "unavailable":
        raise SandboxUnavailableError(
            f"Docker 沙箱不可用（SANDBOX_MODE={configured}），已拒绝执行代码。"
            "如需进程内 AST 沙箱，请显式设置 SANDBOX_MODE=ast。"
        )
    return resolved


# ── Sandbox runner ────────────────────────────────────────────────────

# 结构化失败（issue #206 评审 M2）：run_in_docker 的默认契约仍返回字符串
# （workflow 代码节点直接消费），但 "Error: ..." 字符串经 registry 会被包成
# success=True。execute_python 因此用 raise_on_error=True 取结构化异常，再转
# ToolResult.fail——Docker 超时/启动失败不再伪装成成功。


class DockerSandboxError(RuntimeError):
    """Docker execution failed. ``code`` is a ToolErrorType-compatible code."""

    code = "unknown"

    def __init__(self, message: str, *, legacy_text: str | None = None):
        super().__init__(message)
        self.legacy_text = legacy_text if legacy_text is not None else message


class DockerSandboxTimeout(DockerSandboxError):
    code = "timeout"


class DockerSandboxUnavailable(DockerSandboxError):
    code = "sandbox_unavailable"


async def _execute_in_docker(
    code: str,
    *,
    image: str = _SANDBOX_IMAGE,
    memory_mb: int = 256,
    network: str = "none",
    timeout: int = 30,
    read_only: bool = True,
) -> str:
    """Run code in a container; raise DockerSandboxError on any failure."""
    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".py",
        encoding="utf-8",
        delete=False,
    ) as f:
        f.write(code)
        script_path = f.name

    try:
        cmd = [
            "docker",
            "run",
            "--rm",
            "--network",
            network,
            f"--memory={memory_mb}m",
            "--cpus=1",
            "--security-opt=no-new-privileges",
            "-v",
            f"{script_path}:/code/user_script.py:ro",
            image,
            "python",
            "-u",
            "/code/user_script.py",
        ]
        if read_only:
            cmd.insert(5, "--read-only")
            cmd.insert(6, "--tmpfs=/tmp:exec")

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except NotImplementedError as e:
            # 评审复核 minor ②：与 AST 路径同一平台限制（Windows SelectorEventLoop
            # 不支持子进程），映射为可操作的结构化失败。
            raise DockerSandboxUnavailable(
                "当前事件循环不支持子进程执行（Windows 下 uvicorn --reload / workers>1 "
                "会使用 SelectorEventLoop）。请以单 worker 方式运行后端（ProactorEventLoop），"
                "或在 Linux/WSL 上运行。",
                legacy_text=(
                    "Error: Docker sandbox unavailable: the event loop does not support "
                    "subprocesses (Windows SelectorEventLoop)"
                ),
            ) from e
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )
        except TimeoutError:
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                with contextlib.suppress(BaseException):
                    await asyncio.shield(proc.wait())
            raise DockerSandboxTimeout(
                f"Docker 沙箱执行超时（{timeout}s）",
                legacy_text="Error: code execution timed out in Docker sandbox.",
            ) from None
        except BaseException:
            # 任务取消（客户端断连/关停）或任意异常：docker CLI 必须被回收
            # （评审 M1 同源）。kill 同步发出，reap 放后台尽力完成。
            if proc.returncode is None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                with contextlib.suppress(BaseException):
                    await asyncio.shield(proc.wait())
            raise

        out = stdout.decode("utf-8", errors="replace").strip()
        err = stderr.decode("utf-8", errors="replace").strip()

        if proc.returncode != 0:
            detail = err or out
            raise DockerSandboxError(
                f"Docker 沙箱执行失败（exit {proc.returncode}）：{detail}",
                legacy_text=f"Error (exit {proc.returncode}):\n{detail}",
            )

        return out if out else "(Code executed successfully with no output)"

    except FileNotFoundError as e:
        raise DockerSandboxUnavailable(
            "Docker 不可用：宿主机上未找到 docker 可执行文件",
            legacy_text="Error: Docker not found — install Docker or switch sandbox_mode=ast",
        ) from e
    except DockerSandboxError:
        raise
    except Exception as e:
        raise DockerSandboxError(
            f"Docker sandbox failure: {type(e).__name__}: {e}",
            legacy_text=f"Error: Docker sandbox failure: {type(e).__name__}: {e}",
        ) from e
    finally:
        Path(script_path).unlink(missing_ok=True)


async def run_in_docker(
    code: str,
    *,
    image: str = _SANDBOX_IMAGE,
    memory_mb: int = 256,
    network: str = "none",
    timeout: int = 30,
    read_only: bool = True,
    raise_on_error: bool = False,
) -> str:
    """Execute Python code in an isolated Docker container.

    Security guarantees:
        - No network access (--network=none)
        - Memory limit enforced by cgroups
        - Read-only rootfs, tmpfs /tmp only
        - No new privileges (cannot escalate via setuid)
        - No host volume mounts
        - 30s hard timeout

    Default contract (``raise_on_error=False``): return stdout on success, or a
    legacy ``"Error: ..."`` string on failure (consumed by workflow code nodes).
    ``raise_on_error=True`` raises :class:`DockerSandboxError` instead, so
    callers that need structured failures (execute_python) do not have error
    text wrapped as success by the tool registry.
    """
    try:
        return await _execute_in_docker(
            code,
            image=image,
            memory_mb=memory_mb,
            network=network,
            timeout=timeout,
            read_only=read_only,
        )
    except DockerSandboxError as e:
        if raise_on_error:
            raise
        return e.legacy_text
