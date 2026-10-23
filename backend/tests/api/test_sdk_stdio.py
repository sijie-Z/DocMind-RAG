"""SDK stdio 端到端（issue #493）。

覆盖：

1. ``configure_stdio_stdout``：root 与具名 logger（``sqlalchemy.engine.Engine`` /
   ``uvicorn`` 等）上绑定真实 stdout 的 handler 必须迁到 stderr，协议写端保持不变；
2. ``DEBUG=true`` 且未设置 ``SQL_ECHO``：真实子进程里 ``settings.SQL_ECHO`` 与
   ``engine.echo`` 均为 False（SQL 回显不再跟随 DEBUG）；
3. 真实子进程 stdout 纯净：``DEBUG=true`` + ``SQL_ECHO=true``（SQL 确实在打）时，
   stdout 每一行都能 ``json.loads``；SQL 行只出现在 stderr；
4. Redis 不可用：真实子进程 stderr 说明 + exit 2，stdout 0 字节（不静默降级）；
   幂等通道不可用时 ``session/prompt`` 经 JSON-RPC 返回 -32603；
5. 缺 ``--org-id/--user-id``：exit 2 且 stdout 0 字节。

环境说明：本机无 Redis/MySQL（Docker 未启动）。子进程用 sqlite（aiosqlite）+
启动器内注入的内存 FakeRedis 替代真栈；真 Redis 端到端留给有栈环境补测
（本文件是替代证据，不是真栈验收）。
"""

import asyncio
import io
import json
import logging
import os
import sys
import textwrap
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2]

# 子进程启动器：默认/echo 模式注入 FakeRedis + sqlite，走真实 sdk 入口。
_LAUNCHER_SOURCE = r'''
"""test_sdk_stdio 子进程启动器（运行时写入临时目录）。

argv[1] = mode:
  echo      —— DEBUG=true + SQL_ECHO=true，注入 FakeRedis 后走真实 sdk 入口；
  preimport —— 同 echo，但在 sdk 重定向前先 import app.core.database（复刻
               issue #493 原始顺序：engine 的 stdout handler 已绑协议管道）；
  defaults  —— DEBUG=true（不设 SQL_ECHO），断言 SQL_ECHO / engine.echo 为 False；
  no_redis  —— 不注入 FakeRedis（REDIS_HOST/PORT 指向关闭端口），走真实 sdk 入口。
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.environ["SDK_TEST_BACKEND"])
os.environ.setdefault("DEBUG", "true")
os.environ["DATABASE_URL"] = os.environ["SDK_TEST_DB_URL"]

mode = sys.argv[1]
if mode in ("echo", "preimport"):
    os.environ["SQL_ECHO"] = "true"

from app.core.config.loader import load_settings

load_settings("sdk")


class FakeRedis:
    """内存替身：覆盖 stdio 入口与 RPC 面用到的命令。"""

    def __init__(self):
        self.kv = {}

    def __bool__(self):
        return True

    async def get(self, key):
        return self.kv.get(key)

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.kv:
            return None
        self.kv[key] = value
        return True

    async def setex(self, key, ttl, value):
        self.kv[key] = value
        return True

    async def delete(self, key):
        return self.kv.pop(key, None)

    async def close(self):
        return None


if mode != "no_redis":
    from app.core import redis as redis_mod

    # 注入必须早于 sdk 入口：init_redis_strict() 对已注入的客户端幂等放行。
    redis_mod._holder._client = FakeRedis()

if mode == "preimport":
    # 复刻 issue #493 原始顺序：engine 在 stdout 重定向之前创建，SQLAlchemy
    # 的 StreamHandler(sys.stdout) 已绑定在协议管道上——必须靠 handler 迁移救回。
    import logging

    import app.core.database  # noqa: F401 - 触发 engine 创建（echo=True）
    from app.core.database import engine

    handler_bound = any(
        handler.stream is sys.stdout
        for handler in logging.getLogger("sqlalchemy.engine.Engine").handlers
    )
    if not (engine.echo and handler_bound):
        print(
            f"PRECONDITION_FAILED echo={engine.echo} handler_bound={handler_bound}",
            file=sys.stderr,
        )
        sys.exit(3)

if mode == "defaults":
    from app.core.config import settings
    from app.core.database import engine

    diagnostic = {"SQL_ECHO": settings.SQL_ECHO, "engine_echo": engine.echo}
    print(json.dumps(diagnostic), file=sys.stderr)
    sys.exit(0 if settings.SQL_ECHO is False and engine.echo is False else 3)

from app.entrypoints.sdk import run  # noqa: E402 - 依赖上面的 env / 注入顺序

run(argparse.Namespace(org_id=1, user_id=1, api_key_file=None, is_superuser=False))
'''


def _write_launcher(tmp_path: Path) -> Path:
    path = tmp_path / "sdk_stdio_launcher.py"
    path.write_text(textwrap.dedent(_LAUNCHER_SOURCE), encoding="utf-8")
    return path


def _subprocess_env(db_path: Path, **extra: str) -> dict[str, str]:
    """干净环境：隔离宿主 .env / Redis / SQL_ECHO 噪声，显式给齐必填配置。"""
    env = os.environ.copy()
    for key in ("DEBUG", "SQL_ECHO", "REDIS_HOST", "REDIS_PORT", "REDIS_PASSWORD", "REDIS_DB"):
        env.pop(key, None)
    database_url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    env.update(
        {
            "DATABASE_URL": database_url,
            "SECRET_KEY": "0123456789abcdef0123456789abcdef",
            "JWT_SECRET_KEY": "fedcba9876543210fedcba9876543210",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "SDK_TEST_BACKEND": str(BACKEND_DIR),
            "SDK_TEST_DB_URL": database_url,
        }
    )
    env.update(extra)
    return env


async def _run_process(
    args: list[str], *, env: dict[str, str], stdin_bytes: bytes = b""
) -> tuple[int, bytes, bytes]:
    process = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(BACKEND_DIR),
        env=env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(stdin_bytes), timeout=90)
    except TimeoutError:  # pragma: no cover - 卡死时给出可诊断的失败
        process.kill()
        await process.wait()
        raise
    return process.returncode, stdout, stderr


# ── 1. configure_stdio_stdout 单测 ──────────────────────────────────────────


def test_configure_stdio_stdout_redirects_and_migrates_named_handlers(monkeypatch):
    """root 与 sqlalchemy/uvicorn 具名 logger 的 stdout handler 都要迁到 stderr。"""
    import app.core.stdio as stdio_mod

    real_out, real_err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", real_out)
    monkeypatch.setattr(sys, "stderr", real_err)
    monkeypatch.setattr(stdio_mod, "_protocol_stdout", None)

    sql_logger = logging.getLogger("sqlalchemy.engine.Engine")
    uvicorn_logger = logging.getLogger("uvicorn.error")
    root = logging.getLogger()
    handlers = [logging.StreamHandler(real_out) for _ in range(3)]
    sql_logger.addHandler(handlers[0])
    uvicorn_logger.addHandler(handlers[1])
    root.addHandler(handlers[2])
    sql_logger.setLevel(logging.INFO)
    uvicorn_logger.setLevel(logging.INFO)
    try:
        returned = stdio_mod.configure_stdio_stdout()
        assert returned is real_out
        assert sys.stdout is real_err
        assert stdio_mod.get_protocol_stdout() is real_out
        assert all(handler.stream is real_err for handler in handlers), (
            "具名 logger（sqlalchemy.engine.Engine / uvicorn）的 handler 也必须迁移"
        )

        sql_logger.info("SELECT 1")
        uvicorn_logger.info("uvicorn log")
        assert "SELECT 1" not in real_out.getvalue()
        assert "SELECT 1" in real_err.getvalue()
        assert "uvicorn log" not in real_out.getvalue()
        assert "uvicorn log" in real_err.getvalue()

        # 幂等：二次调用不覆盖已保存的协议写端。
        assert stdio_mod.configure_stdio_stdout() is real_err
        assert stdio_mod.get_protocol_stdout() is real_out
    finally:
        sql_logger.removeHandler(handlers[0])
        uvicorn_logger.removeHandler(handlers[1])
        root.removeHandler(handlers[2])
        sql_logger.setLevel(logging.NOTSET)
        uvicorn_logger.setLevel(logging.NOTSET)


# ── 2. SQL_ECHO 与 DEBUG 解耦 ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sql_echo_default_off_even_with_debug(tmp_path):
    """DEBUG=true、未设 SQL_ECHO → 真实子进程里 engine.echo 必须为 False。"""
    launcher = _write_launcher(tmp_path)
    rc, out, err = await _run_process(
        [sys.executable, str(launcher), "defaults"],
        env=_subprocess_env(tmp_path / "defaults.db"),
    )
    text = err.decode("utf-8", "replace")
    assert rc == 0, text
    assert out == b"", "defaults 模式不得写 stdout"
    assert '"SQL_ECHO": false' in text
    assert '"engine_echo": false' in text


# ── 3. 真实子进程 stdout 纯净（DEBUG + SQL_ECHO 全开） ─────────────────────


@pytest.mark.asyncio
async def test_sdk_subprocess_stdout_is_pure_protocol_frames(tmp_path):
    """SQL 真的在打（SQL_ECHO=true）时，stdout 仍逐行可 json.loads、零 SQL 行。"""
    launcher = _write_launcher(tmp_path)
    db_path = tmp_path / "echo.db"
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "shutdown", "params": {}},
    ]
    payload = ("\n".join(json.dumps(message) for message in requests) + "\n").encode()

    rc, out, err = await _run_process(
        [sys.executable, str(launcher), "echo"],
        env=_subprocess_env(db_path),
        stdin_bytes=payload,
    )
    stderr_text = err.decode("utf-8", "replace")
    assert rc == 0, stderr_text
    assert "sqlalchemy.engine.Engine" in stderr_text, (
        "SQL_ECHO=true 必须真的产生 SQL 日志（否则本用例空转）"
    )
    assert b"SELECT" not in out and b"sqlalchemy" not in out, "SQL 不得出现在 stdout"

    raw_lines = [line for line in out.decode("utf-8").splitlines() if line.strip()]
    assert len(raw_lines) == 2
    responses: dict[int, dict] = {}
    for line in raw_lines:
        message = json.loads(line)  # 任何非协议行都会在这里炸
        assert message.get("jsonrpc") == "2.0"
        responses[message["id"]] = message
    assert responses[1]["result"]["protocolVersion"] == 1
    assert responses[2]["result"] == {"ok": True}


@pytest.mark.asyncio
async def test_engine_created_before_redirect_still_keeps_stdout_pure(tmp_path):
    """复刻 issue #493 原始故障：engine 先于重定向创建（handler 绑协议管道）。

    迁移必须把它救回 stderr；stdout 只能有协议帧。
    """
    launcher = _write_launcher(tmp_path)
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": "shutdown", "params": {}},
    ]
    payload = ("\n".join(json.dumps(message) for message in requests) + "\n").encode()

    rc, out, err = await _run_process(
        [sys.executable, str(launcher), "preimport"],
        env=_subprocess_env(tmp_path / "preimport.db"),
        stdin_bytes=payload,
    )
    stderr_text = err.decode("utf-8", "replace")
    assert rc == 0, stderr_text
    assert "PRECONDITION_FAILED" not in stderr_text, "预置条件未成立（用例会空转）"
    assert "sqlalchemy.engine.Engine" in stderr_text, "SQL 必须真的在打，只是不得进 stdout"
    lines = [line for line in out.decode("utf-8").splitlines() if line.strip()]
    assert len(lines) == 2
    for line in lines:
        message = json.loads(line)  # 迁移失效时 SQL 行会在这里炸
        assert message.get("jsonrpc") == "2.0"


@pytest.mark.asyncio
async def test_shutdown_exits_cleanly_with_stdin_left_open(tmp_path):
    """客户端发 shutdown 后保持 stdin 打开：进程必须及时 exit 0（评审 blocker B1）。

    旧实现 ``os.close(0)`` 会在阻塞读持有 fd 锁时永久挂起；修复后
    ``sys.stdin.detach()`` + Windows ``CancelSynchronousIo`` 让收尾不阻塞。
    """
    launcher = _write_launcher(tmp_path)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(launcher),
        "echo",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(BACKEND_DIR),
        env=_subprocess_env(tmp_path / "open_stdin.db"),
    )
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    payload = (
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        + "\n"
        + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "shutdown", "params": {}})
        + "\n"
    ).encode()
    process.stdin.write(payload)
    await process.stdin.drain()

    async def _drain(stream: asyncio.StreamReader) -> bytes:
        chunks: list[bytes] = []
        while True:
            chunk = await stream.read(65536)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)

    # stderr 必须并发排干（SQL_ECHO=true 时启动日志很多，管道会满）。
    stderr_task = asyncio.create_task(_drain(process.stderr))

    # 先读两帧响应（启动导入在本机约 8–10s，给足 60s），再单独给 shutdown
    # 10s 的退出窗口——这才是本用例要钉的语义：响应之后不得因收尾挂起。
    responses: list[dict] = []
    for _ in range(2):
        line = await asyncio.wait_for(process.stdout.readline(), timeout=60)
        assert line, "进程在输出两个响应之前就结束了"
        responses.append(json.loads(line))
    assert [response["id"] for response in responses] == [1, 2]

    try:
        await asyncio.wait_for(process.wait(), timeout=10)
    except TimeoutError:  # pragma: no cover - 旧实现（os.close(0)）会挂在这里
        process.kill()
        await process.wait()
        raise AssertionError("shutdown 后 stdin 保持打开时进程未在 10s 内退出") from None
    finally:
        if process.stdin is not None and not process.stdin.is_closing():
            process.stdin.close()

    remaining_stdout = await process.stdout.read()
    stderr = await stderr_task
    stderr_text = stderr.decode("utf-8", "replace")
    assert process.returncode == 0, stderr_text
    assert "Fatal Python error" not in stderr_text
    assert remaining_stdout.strip() == b"", "shutdown 响应后 stdout 不得再有输出"


# ── 4. Redis 不可用：明确失败，不静默降级 ─────────────────────────────────


@pytest.mark.asyncio
async def test_redis_unavailable_exits_2_with_empty_stdout(tmp_path):
    launcher = _write_launcher(tmp_path)
    rc, out, err = await _run_process(
        [sys.executable, str(launcher), "no_redis"],
        env=_subprocess_env(tmp_path / "noredis.db", REDIS_HOST="127.0.0.1", REDIS_PORT="1"),
    )
    text = err.decode("utf-8", "replace")
    assert rc == 2, text
    assert out == b"", "Redis 不可用时不得输出任何协议帧"
    assert "Redis 不可用" in text


@pytest.mark.asyncio
async def test_prompt_idempotency_redis_unavailable_returns_32603(monkeypatch):
    """幂等通道不可用 → JSON-RPC -32603（不得静默继续执行 prompt）。"""

    class BrokenRedis:
        def __bool__(self):
            return True

        async def get(self, *_args, **_kwargs):
            raise ConnectionError("redis down")

        async def set(self, *_args, **_kwargs):
            raise ConnectionError("redis down")

    import app.rpc.dispatcher as dispatcher_module
    from app.rpc.dispatcher import RPCService

    monkeypatch.setattr(dispatcher_module, "redis_client", BrokenRedis())
    service = RPCService(organization_id=1, user_id=7)

    async def _create_session(_prompt: str) -> str:
        return "sess-1"

    service._create_session = _create_session  # type: ignore[assignment]
    dispatcher = service.build_dispatcher(send=None)
    response = await dispatcher.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "session/prompt",
            "params": {"prompt": "hi", "clientMessageId": "m-1"},
        }
    )
    assert response["error"]["code"] == -32603
    assert "Redis" in response["error"]["message"]
    assert service._prompt_tasks == {}


# ── 5. 真实 CLI：缺身份 exit 2、stdout 0 字节 ─────────────────────────────


@pytest.mark.asyncio
async def test_sdk_cli_missing_identity_exits_2_with_empty_stdout(tmp_path):
    rc, out, err = await _run_process(
        [sys.executable, "-m", "app", "--profile", "sdk"],
        env=_subprocess_env(tmp_path / "identity.db"),
    )
    text = err.decode("utf-8", "replace")
    assert rc == 2, text
    assert out == b"", "缺身份时 stdout 不得有协议帧"
    assert "--org-id" in text
