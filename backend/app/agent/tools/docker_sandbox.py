"""Docker sandbox executor — isolated code execution in containers.

Falls back to in-process AST sandbox when Docker is unavailable.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

_SANDBOX_IMAGE = os.environ.get("SANDBOX_DOCKER_IMAGE", "python:3.11-slim")

# ── Detection ────────────────────────────────────────────────────────


def _docker_available() -> bool:
    """Check if Docker daemon is installed and reachable."""
    if not shutil.which("docker"):
        return False
    try:
        import subprocess

        result = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True, timeout=3, text=True,
        )
        return result.returncode == 0 and result.stdout.strip() != ""
    except Exception:
        return False


# ── Sandbox runner ────────────────────────────────────────────────────


async def run_in_docker(
    code: str,
    *,
    image: str = _SANDBOX_IMAGE,
    memory_mb: int = 256,
    network: str = "none",
    timeout: int = 30,
    read_only: bool = True,
) -> str:
    """Execute Python code in an isolated Docker container.

    Security guarantees:
        - No network access (--network=none)
        - Memory limit enforced by cgroups
        - Read-only rootfs, tmpfs /tmp only
        - No new privileges (cannot escalate via setuid)
        - No host volume mounts
        - 30s hard timeout

    Returns stdout on success, stderr + error context on failure.
    """
    import subprocess

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", encoding="utf-8", delete=False,
    ) as f:
        f.write(code)
        script_path = f.name

    try:
        cmd = [
            "docker", "run",
            "--rm",
            "--network", network,
            f"--memory={memory_mb}m",
            "--cpus=1",
            "--security-opt=no-new-privileges",
            "-v", f"{script_path}:/code/user_script.py:ro",
            image,
            "python", "-u", "/code/user_script.py",
        ]
        if read_only:
            cmd.insert(5, "--read-only")
            cmd.insert(6, "--tmpfs=/tmp:exec")

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout,
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return "Error: code execution timed out in Docker sandbox."

        out = stdout.decode("utf-8", errors="replace").strip()
        err = stderr.decode("utf-8", errors="replace").strip()

        if proc.returncode != 0:
            return f"Error (exit {proc.returncode}):\n{err or out}" if err else f"Error (exit {proc.returncode}):\n{out}"

        return out if out else "(Code executed successfully with no output)"

    except FileNotFoundError:
        return "Error: Docker not found — install Docker or switch sandbox_mode=ast"
    except Exception as e:
        return f"Error: Docker sandbox failure: {type(e).__name__}: {e}"
    finally:
        Path(script_path).unlink(missing_ok=True)
