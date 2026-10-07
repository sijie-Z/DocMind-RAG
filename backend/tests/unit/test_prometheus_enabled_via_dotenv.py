"""issue #207：PROMETHEUS_ENABLED 支持 .env 配置（此前只看 `os.environ`，
.env 里的开关无效——与 #232 同源的静默覆盖）。

子进程模式（与 test_auto_tier_env_precedence 同款）：给临时目录一个只含必需键的
`.env`，断言进程内总开关与导出行为；对照组不设开关。
"""
import json
import os
import pathlib
import subprocess
import sys

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[2]

_PROBE = (
    "import json;"
    "from app.core import prometheus as p;"
    "p.AGENT_TOOL_CALLS.labels(tool='t', result='success').inc();"
    "text = p.get_prometheus_metrics().decode();"
    "print(json.dumps({'enabled': p._METRICS_ENABLED,"
    " 'has_agent': 'agent_tool_calls_total' in text}))"
)


def _run_probe(tmp_path, extra_env_line: str) -> dict:
    (tmp_path / ".env").write_text(
        "\n".join(
            [
                "DATABASE_URL=mysql+asyncmy://u:p@localhost:3306/whatever",
                "SECRET_KEY=" + "a" * 32,
                "JWT_SECRET_KEY=" + "b" * 32,
                extra_env_line,
            ]
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    env = {k: v for k, v in os.environ.items() if k != "PROMETHEUS_ENABLED"}
    env["PYTHONPATH"] = str(BACKEND_DIR)
    r = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert r.returncode == 0, r.stderr[-1500:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_prometheus_enabled_via_dotenv(tmp_path):
    got = _run_probe(tmp_path, "PROMETHEUS_ENABLED=true")
    assert got["enabled"] is True, ".env 里的总开关必须生效（issue #207）"
    assert got["has_agent"] is True, "启用后 /metrics 文本里应出现真实指标"


def test_prometheus_disabled_by_default_without_flag(tmp_path):
    got = _run_probe(tmp_path, "# PROMETHEUS_ENABLED 未设置")
    assert got["enabled"] is False
    assert got["has_agent"] is False, "未启用时不应导出任何指标"
