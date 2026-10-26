"""``python -m app`` CLI 与入口验收（issue #479 §5.5 / §8）。

全部用子进程执行，避免 CLI 的配置缓存/import 顺序影响测试进程；环境变量显式
给出 ``SECRET_KEY`` / ``JWT_SECRET_KEY`` / ``DATABASE_URL`` / ``DEEPSEEK_API_KEY``，
``cwd=backend`` 保证 ``python -m app`` 可导入。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2]

_BASE_ENV = {
    "SECRET_KEY": "a" * 32,
    "JWT_SECRET_KEY": "b" * 32,
    "DATABASE_URL": "mysql+asyncmy://docmind:secret@localhost:3306/docmind",
    "DEEPSEEK_API_KEY": "sk-test-secret",
}


def _env() -> dict[str, str]:
    env = os.environ.copy()
    env.update(_BASE_ENV)
    env["PYTHONUTF8"] = "1"
    return env


def _run(*args: str, timeout: int = 240) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "app", *args],
        cwd=BACKEND_DIR,
        env=_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=timeout,
    )


def test_acp_missing_identity_exits_2():
    # issue #481 起 acp 不再是桩：入口要求显式 --org-id/--user-id，缺失即
    # exit 2 且 stdout 0 字节（协议未启动）。原 #479 的桩文案断言由本用例
    # 取代（桩已被 #481 替换，见 app/entrypoints/acp.py）。
    result = _run("--profile", "acp")
    assert result.returncode == 2
    assert "--org-id" in result.stderr and "--user-id" in result.stderr
    assert result.stdout == ""


def test_headless_dry_run_requires_no_http_or_llm():
    result = _run(
        "--profile",
        "headless",
        "--query",
        "ping",
        "--user-id",
        "1",
        "--org-id",
        "1",
        "--json",
        "--dry-run",
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout.strip().splitlines()[-1]) == {"status": "ready"}


def test_headless_missing_ids_exits_2():
    result = _run("--profile", "headless", "--query", "ping")
    assert result.returncode == 2
    assert "--user-id" in result.stderr or "--org-id" in result.stderr


def test_web_dump_config_is_sorted_masked_and_deterministic():
    first = _run("--profile", "web", "--dump-config")
    second = _run("--profile", "web", "--dump-config")
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    # 确定性：同一 profile + env 两次输出逐字节一致。
    assert first.stdout == second.stdout

    payload = json.loads(first.stdout)
    assert list(payload) == sorted(payload)
    assert payload["profile"] == "web"
    assert payload["entrypoint"] == "web"
    assert payload["bundles"] == ["core", "rag", "agent", "observability"]
    settings = payload["settings"]
    assert list(settings) == sorted(settings)
    assert settings["DEEPSEEK_API_KEY"] == "***"
    assert settings["JWT_SECRET_KEY"] == "***"
    assert settings["DATABASE_URL"] == "mysql+asyncmy://docmind:***@localhost:3306/docmind"
    assert "secret@localhost" not in first.stdout


def test_worker_dump_config():
    result = _run("--profile", "worker", "--dump-config")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["profile"] == "worker"
    assert payload["entrypoint"] == "worker"


def test_unknown_profile_exits_2_with_config_error_json():
    result = _run("--profile", "nope")
    assert result.returncode == 2
    lines = [line for line in result.stderr.strip().splitlines() if line.startswith("{")]
    assert lines, result.stderr
    payload = json.loads(lines[-1])
    assert payload["error"] == "CONFIG_INVALID"
    assert payload["path"] == "profile.nope"


def _mounted_prefixes(router) -> set[str]:
    """收集 APIRouter 挂载的路径前缀（兼容 FastAPI 新旧 include 实现）。

    FastAPI ≥0.142 把 include_router 记为惰性 ``_IncludedRouter``（带
    ``include_context.prefix``）；旧版本直接展开为带完整 path 的路由记录。
    """
    prefixes: set[str] = set()
    for route in router.routes:
        context = getattr(route, "include_context", None)
        prefix = getattr(context, "prefix", None)
        if prefix:
            prefixes.add(prefix)
            continue
        path = getattr(route, "path", "")
        if path and path.startswith("/"):
            prefixes.add("/" + path.split("/")[1])
    return prefixes


def test_build_router_filters_by_bundle():
    """headless/sdk 只挂 core/rag/agent；core-only 必须不含 observability/rag。"""
    from app.api.v1.router import build_router

    core_prefixes = _mounted_prefixes(build_router(["core"]))
    assert "/auth" in core_prefixes
    assert "/users" in core_prefixes
    assert "/user" in core_prefixes
    assert "/monitoring" not in core_prefixes
    assert "/documents" not in core_prefixes

    assert _mounted_prefixes(build_router([])) == set()

    with pytest.raises(ValueError):
        build_router(["nope"])


def test_web_app_health_endpoint(client):
    """web 组装冒烟：app.main.app 的 /health 恒 200（诊断视图），无需 lifespan。"""
    response = client.get("/health")
    assert response.status_code == 200
    assert "status" in response.json()


def test_build_router_all_bundles_covers_every_router_spec():
    """全量 bundle：28 个模块全部挂载（#477 terminal + #478 webhooks + #480 api-keys/rpc），前缀集合与 ROUTER_SPECS 一致。"""
    from app.api.v1.router import ALL_BUNDLES, ROUTER_SPECS, build_router

    assert len(ROUTER_SPECS) == 28
    all_router = build_router(ALL_BUNDLES)
    expected_prefixes = {prefix for _, prefix, _, _ in ROUTER_SPECS.values()}
    assert _mounted_prefixes(all_router) == expected_prefixes

    lazy_nodes = [route for route in all_router.routes if hasattr(route, "include_context")]
    if lazy_nodes:  # FastAPI ≥0.142 惰性 include：每个模块一个节点
        assert len(lazy_nodes) == 28


def test_headless_error_event_exits_1_with_log_safe_stderr(monkeypatch, capsys):
    """headless 非 dry-run：error 事件 → exit 1，stderr 经 log_safe（不可注入换行）。"""
    from argparse import Namespace

    from app.agent import service as service_mod
    from app.agent.events import AgentEvent
    from app.entrypoints import headless

    async def fake_chat(*args, **kwargs):
        yield AgentEvent(type="error", content="provider boom\nINJECTED-LINE")

    monkeypatch.setattr(service_mod.agent_service, "chat", fake_chat)
    args = Namespace(query="ping", user_id=1, org_id=1, dry_run=False, json=True)

    assert headless.run(args) == 1
    captured = capsys.readouterr()
    assert "provider boom" in captured.err
    assert "INJECTED-LINE" in captured.err
    # log_safe 把换行转义成字面 \n：stderr 只有一个物理行（print 的尾换行）
    assert captured.err.count("\n") == 1
    assert r"\nINJECTED-LINE" in captured.err


def _stub_web_runtime(monkeypatch, port_free: bool | list[bool]):
    """隔离端口、uvicorn 与端口文件；测试不得写真实 .backend-port。"""
    from app.entrypoints import web

    is_port_free = (
        Mock(return_value=port_free) if isinstance(port_free, bool) else Mock(side_effect=port_free)
    )
    write_port_file = Mock()
    uvicorn_run = Mock()
    monkeypatch.setattr(web, "_port_helpers", lambda: (is_port_free, write_port_file))
    monkeypatch.setattr(web, "setup_logging", Mock())
    monkeypatch.setattr(web, "build_app", Mock(return_value=object()))
    monkeypatch.setattr(web.uvicorn, "run", uvicorn_run)
    monkeypatch.setattr(web.settings, "PORT", 8000)
    monkeypatch.setattr(web.settings, "HOST", "127.0.0.1")
    monkeypatch.setattr(web.settings, "LOG_LEVEL", "INFO")
    monkeypatch.delenv("DOCMIND_PORT", raising=False)
    return web, is_port_free, write_port_file, uvicorn_run


def test_web_preserves_nonzero_system_exit_without_retrying(monkeypatch):
    """空闲端口上的真实启动错误不得被伪装成整段端口冲突。"""
    web, is_port_free, write_port_file, uvicorn_run = _stub_web_runtime(monkeypatch, True)
    failure = SystemExit(3)
    uvicorn_run.side_effect = failure

    with pytest.raises(SystemExit) as caught:
        web.run()

    assert caught.value is failure
    assert caught.value.code == 3
    is_port_free.assert_called_once_with(8000)
    write_port_file.assert_called_once_with(str(web.BACKEND_DIR), 8000)
    uvicorn_run.assert_called_once()
    assert uvicorn_run.call_args.kwargs["port"] == 8000


def test_web_still_tries_next_port_when_precheck_finds_occupied(monkeypatch):
    """对照：只有端口预检占用才切换到下一端口；文件写入仍为测试替身。"""
    web, is_port_free, write_port_file, uvicorn_run = _stub_web_runtime(monkeypatch, [False, True])

    web.run()

    assert [call.args[0] for call in is_port_free.call_args_list] == [8000, 8001]
    write_port_file.assert_called_once_with(str(web.BACKEND_DIR), 8001)
    uvicorn_run.assert_called_once()
    assert uvicorn_run.call_args.kwargs["port"] == 8001
