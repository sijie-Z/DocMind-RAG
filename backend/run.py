# -*- coding: utf-8 -*-
import os
import sys
import socket
import subprocess

def is_port_free(port: int, host: str = "0.0.0.0") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def write_port_file(backend_dir: str, port: int):
    port_file = os.path.join(backend_dir, ".backend-port")
    with open(port_file, "w") as f:
        f.write(str(port))


def main():
    backend_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(backend_dir)
    sys.path.insert(0, backend_dir)

    from app.core.config import settings

    preferred = settings.PORT
    max_tries = 10

    for offset in range(max_tries):
        port = preferred + offset
        if not is_port_free(port):
            continue

        write_port_file(backend_dir, port)

        if port != preferred:
            print(f"\n{'='*50}")
            print(f"  端口 {preferred} 被占用，已自动切换到 {port}")
            print(f"{'='*50}\n")

        # 用 subprocess 启动 uvicorn，这样绑定失败时进程自然退出
        # 不会因为 reload 模式下子进程的 socket 竞争导致崩溃
        cmd = [
            sys.executable, "-m", "uvicorn",
            "app.main:app",
            "--host", settings.HOST,
            "--port", str(port),
            "--log-level", settings.LOG_LEVEL.lower(),
        ]
        if settings.DEBUG:
            cmd.append("--reload")

        env = os.environ.copy()
        env["DOCMIND_PORT"] = str(port)
        result = subprocess.run(cmd, cwd=backend_dir, env=env)
        if result.returncode == 0:
            return

        # 端口绑定失败 (exit code 1)，重试下一个端口
        print(f"端口 {port} 启动失败，尝试下一个端口...")

    raise RuntimeError(f"无法在 {preferred}-{preferred + max_tries - 1} 范围内启动服务")


if __name__ == "__main__":
    main()
