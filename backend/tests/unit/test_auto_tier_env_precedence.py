"""issue #232：`.env` 里显式配置的字段，不得被 hardware 自动档位静默覆盖。

`_auto_tier_defaults` 发生在 **import 时**（`app/core/config/__init__.py` 对模块级
settings 应用硬件档位），所以诚实的测法是子进程：给它一个只含 `.env` 的工作目录，
断言进程内拿到的 settings 值仍是 `.env` 的值。

修复前（只查 `os.environ`，.env 的值不在其中）：
`EMBEDDING_MODEL` 被覆盖成硬件档默认（cpu 档 = bge-m3）→ 本用例红。
"""
import json
import os
import pathlib
import subprocess
import sys

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[2]


def test_dotenv_values_survive_auto_tier(tmp_path):
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "DATABASE_URL=mysql+asyncmy://u:p@localhost:3306/whatever",
                "SECRET_KEY=" + "a" * 32,
                "JWT_SECRET_KEY=" + "b" * 32,
                "EMBEDDING_MODEL=qwen3-embedding:0.6b",
                "VECTOR_DIMENSION=1024",
            ]
        ),
        encoding="utf-8",
    )
    probe = (
        "import json; from app.core.config import settings;"
        "print(json.dumps({'m': settings.EMBEDDING_MODEL,"
        " 'd': settings.VECTOR_DIMENSION}))"
    )
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("EMBEDDING_MODEL", "VECTOR_DIMENSION")  # 隔离：只让 .env 说话
    }
    env["PYTHONPATH"] = str(BACKEND_DIR)

    r = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert r.returncode == 0, r.stderr[-1500:]
    got = json.loads(r.stdout.strip().splitlines()[-1])
    assert got["m"] == "qwen3-embedding:0.6b", (
        f".env 的 EMBEDDING_MODEL 被自动档位覆盖了（issue #232）：实际 {got['m']!r}"
    )
    assert got["d"] == 1024, (
        f".env 的 VECTOR_DIMENSION 被动了（issue #232）：实际 {got['d']!r}"
    )
