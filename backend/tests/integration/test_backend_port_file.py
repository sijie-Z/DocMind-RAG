"""``.backend-port`` 契约的端到端回归测试（issue #170）。

契约：``backend/.backend-port`` 的内容 == 后端**实际监听**的端口 ——
``frontend/vite.config.mts`` 的 ``/api`` 代理目标由这个文件决定。
issue #170 的根因：lifespan 写入的是 ``settings.PORT``（配置端口），
只要命令行 ``--port`` 与配置端口不同，dev 前端就会代理到无人监听的端口
（``ECONNREFUSED``）。

这里**真起 uvicorn**（integration：沿用 tests/conftest.py 的测试环境变量，
需要真实 DB），覆盖两条启动形态：

1. ``--port <X>``（配置 PORT 故意设成另一个值）→ 文件必须是 X；
2. README 教的 ``--reload`` 形态同样成立 —— lifespan 跑在 spawn 出的子进程里，
   而 stdlib ``multiprocessing.spawn`` 会把父进程的 ``sys.argv`` 传给子进程。

就绪判据是「**文件内容正确 + 该端口可 TCP 连接**」双判据（issue #176）：
文件内容 == 实际监听端口，拆开看就是「写侧」与「生效侧」两条同时成立 ——
lifespan 在启动早期就把「自己要绑的端口」写进文件（写侧），uvicorn 完成
startup 后真的在该端口上监听（生效侧）。只等文件不够（写了不等于在听），
只等端口也不够（在听不等于文件写对），两者都等到才完整覆盖契约。

旧版拿 ``/health`` 返回 200 当就绪，把纯本地契约耦合到了完整基础设施：
CI 的 integration job 只提供 MySQL+Redis（Kafka/ES/MinIO 无服务），而
``/health``（``app/main.py``）对 ES / MinIO / Redis 的探测各带 ``asyncio.wait_for``
超时（最长 3s），无服务依赖下整体响应耗时超过测试客户端的 2s urlopen 超时 ——
200 永远观察不到（issue #176）。端口是否能连与这些外部服务毫无关系，本文件
不再依赖它们。

变异验证（交付时人工执行并留原始输出）：把 ``resolve_backend_port()`` 改回
``int(os.environ.get("DOCMIND_PORT", settings.PORT))`` —— 上述两条必须变红。

与同目录其它需要真实服务的用例一致，本文件默认跳过，给真实 MySQL 后才执行
（仓库约定：``RUN_INTEGRATION=1`` 且 ``DATABASE_URL`` 指向测试库；CI 的
integration-mysql job 两者都提供）。
"""

import contextlib
import os
import random
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import psutil
import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION") != "1",
    reason="需要真实 MySQL（设置 RUN_INTEGRATION=1，并把 DATABASE_URL 指向测试库）",
)

BACKEND_DIR = Path(__file__).resolve().parents[2]
PORT_FILE = BACKEND_DIR / ".backend-port"

_PORT_TIMEOUT = 90.0
_FILE_TIMEOUT = 30.0


def _free_port() -> int:
    """返回一个当前在 0.0.0.0 上可绑定的空闲端口。"""
    for _ in range(200):
        port = random.randint(20000, 45000)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("0.0.0.0", port))
            except OSError:
                continue
        return port
    raise RuntimeError("找不到空闲端口")


def _tail(log_path: Path, n: int = 40) -> str:
    try:
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return "<无日志>"
    return "\n".join(lines[-n:])


def _kill_tree(proc: subprocess.Popen) -> None:
    """杀掉 uvicorn 进程树（``--reload`` 时是 reloader + server 子进程）。"""
    try:
        root = psutil.Process(proc.pid)
        procs = [root, *root.children(recursive=True)]
    except psutil.NoSuchProcess:
        return
    for p in procs:
        with contextlib.suppress(psutil.NoSuchProcess):
            p.terminate()
    _, alive = psutil.wait_procs(procs, timeout=10)
    for p in alive:
        with contextlib.suppress(psutil.NoSuchProcess):
            p.kill()
    psutil.wait_procs(alive, timeout=5)
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=5)


@contextlib.contextmanager
def _running_backend(extra_args: list[str], configured_port: int):
    """把 uvicorn 真起起来，yield (proc, log_path)；收尾时杀进程树并还原端口文件。"""
    saved = PORT_FILE.read_text(encoding="utf-8") if PORT_FILE.exists() else None
    PORT_FILE.unlink(missing_ok=True)

    env = os.environ.copy()
    # 端口来源必须是显式的：清掉继承环境里的遗留值，配置端口用 PORT 指定。
    env.pop("DOCMIND_PORT", None)
    env.pop("UVICORN_PORT", None)
    env.update(
        {
            "PYTHONUTF8": "1",
            "PORT": str(configured_port),
            "DEBUG": "false",
            "ENABLE_DEMO_ACCOUNT": "false",
            "ENABLE_MONITORING": "false",
        }
    )

    tmpdir = Path(tempfile.mkdtemp(prefix="ws170_port_file_"))
    log_path = tmpdir / "uvicorn.log"
    with open(log_path, "wb") as log:
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", *extra_args],
            cwd=str(BACKEND_DIR),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            yield proc, log_path
        finally:
            _kill_tree(proc)
            if saved is None:
                PORT_FILE.unlink(missing_ok=True)
            else:
                PORT_FILE.write_text(saved, encoding="utf-8")


def _wait_file_written(proc: subprocess.Popen, log_path: Path) -> str:
    """等 lifespan 写出 .backend-port（启动的第一步），返回其内容。

    不比对具体值 —— 值本身由用例断言，这样回归失败时能立刻看到写错的值。
    """
    deadline = time.monotonic() + _FILE_TIMEOUT
    while time.monotonic() < deadline:
        if PORT_FILE.exists():
            content = PORT_FILE.read_text(encoding="utf-8").strip()
            if content:
                return content
        if proc.poll() is not None:
            break
        time.sleep(0.2)
    raise AssertionError(
        f".backend-port 未在 {_FILE_TIMEOUT}s 内写入内容\n"
        f"进程状态: {'alive' if proc.poll() is None else f'exited rc={proc.returncode}'}\n"
        f"---- uvicorn 输出 ----\n{_tail(log_path)}"
    )


def _wait_port_listening(cli_port: int, proc: subprocess.Popen, log_path: Path) -> None:
    """等 uvicorn 真的在该端口上监听（TCP 三次握手成功）—— 契约的生效侧。

    刻意不发任何 HTTP 请求：契约只关心「文件记录的端口 == 进程实际绑定的端口」，
    与后端依赖的外部服务（MySQL/Redis/ES/Kafka/MinIO）健康与否无关（issue #176）。
    """
    deadline = time.monotonic() + _PORT_TIMEOUT
    last_err: OSError | None = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            break
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1.0)
            try:
                s.connect(("127.0.0.1", cli_port))
                return
            except OSError as e:
                last_err = e
        time.sleep(0.2)
    raise AssertionError(
        f"127.0.0.1:{cli_port} 未在 {_PORT_TIMEOUT}s 内接受 TCP 连接（最后错误: {last_err!r}）\n"
        f"进程状态: {'alive' if proc.poll() is None else f'exited rc={proc.returncode}'}\n"
        f"---- uvicorn 输出 ----\n{_tail(log_path)}"
    )


def _assert_port_file_matches(cli_port: int, configured_port: int, proc, log_path: Path) -> None:
    assert cli_port != configured_port, "用例前提：命令行端口必须与配置端口不同"

    # 写侧：lifespan 启动早期写出 .backend-port（早于所有外部服务连接）。
    content = _wait_file_written(proc, log_path)
    # 生效侧：uvicorn 完成 startup 后在 --port 上真正监听。
    _wait_port_listening(cli_port, proc, log_path)

    assert content == str(cli_port), (
        f".backend-port 应记录实际监听端口 {cli_port}，实际写入 {content!r}"
        f"（配置 PORT={configured_port}）\n---- uvicorn 输出 ----\n{_tail(log_path)}"
    )


def test_port_file_records_cli_port_not_configured_port():
    """--port 与配置端口不同时，文件必须写命令行端口（issue #170 的核心断言）。"""
    cli_port = _free_port()
    with _running_backend(["--port", str(cli_port)], configured_port=cli_port + 1) as (
        proc,
        log_path,
    ):
        _assert_port_file_matches(cli_port, cli_port + 1, proc, log_path)


def test_port_file_records_cli_port_with_reload():
    """README 教的是 --reload 形态：spawn 出的子进程（跑 lifespan）也要拿到 --port。"""
    cli_port = _free_port()
    with _running_backend(["--port", str(cli_port), "--reload"], configured_port=cli_port + 1) as (
        proc,
        log_path,
    ):
        _assert_port_file_matches(cli_port, cli_port + 1, proc, log_path)
