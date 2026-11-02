"""RC 验收脚本 preflight：必须按目标 /health 的索引身份拦截错栈（评审 A R2）。"""

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from scripts import rc_accept_full_flow as rc


def _fake_health(monkeypatch, payload: dict):
    class _Resp:
        status_code = 200

        def json(self):
            return payload

    monkeypatch.setattr(rc.requests, "get", lambda *a, **k: _Resp())


def test_preflight_accepts_matching_index(monkeypatch):
    _fake_health(
        monkeypatch,
        {
            "status": "healthy",
            "knowledge_base": {"index_name": "docmind_knowledge_rc"},
        },
    )
    assert rc.preflight("docmind_knowledge_rc") is True


def test_preflight_rejects_wrong_index(monkeypatch):
    _fake_health(
        monkeypatch,
        {
            "status": "healthy",
            "knowledge_base": {"index_name": "docmind_knowledge"},
        },
    )
    assert rc.preflight("docmind_knowledge_rc") is False


def test_preflight_rejects_when_target_index_missing(monkeypatch):
    _fake_health(monkeypatch, {"status": "healthy"})
    assert rc.preflight("docmind_knowledge_rc") is False


def test_preflight_rejects_unhealthy_target(monkeypatch):
    _fake_health(
        monkeypatch,
        {"status": "degraded", "knowledge_base": {"index_name": "docmind_knowledge_rc"}},
    )
    assert rc.preflight("docmind_knowledge_rc") is False


def test_preflight_rejects_missing_target_identity_even_with_expectation(monkeypatch):
    """目标 /health 不暴露索引身份时必须拒绝（无法比对）。"""
    monkeypatch.setattr(
        rc.requests,
        "get",
        lambda *a, **k: type(
            "_R", (), {"status_code": 200, "json": lambda self: {"status": "healthy"}}
        )(),
    )
    # 不导入本地 app 配置：直接以 --expect-index 路径断言缺失字段被拒
    assert rc.preflight("docmind_knowledge_rc") is False


def test_preflight_requires_expect_index_when_local_import_fails(monkeypatch):
    """本地 app 配置不可导入时，没有 --expect-index 必须拒绝。"""
    import builtins

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "app.core.config" or name.startswith("app.core.config."):
            raise ImportError("no config in this cwd")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    _fake_health(
        monkeypatch,
        {"status": "healthy", "knowledge_base": {"index_name": "docmind_knowledge_iso"}},
    )
    assert rc.preflight(None) is False
    assert rc.preflight("docmind_knowledge_iso") is True


def test_preflight_rejects_non_object_health(monkeypatch):
    class _Resp:
        status_code = 200

        def json(self):
            return ["not", "a", "dict"]

    monkeypatch.setattr(rc.requests, "get", lambda *a, **k: _Resp())
    assert rc.preflight("docmind_knowledge_iso") is False


def test_preflight_defaults_to_local_effective_index_when_import_succeeds(monkeypatch):
    """本地配置可导入且未传 --expect-index 时，以本地生效索引名（显式优先）比对。

    显式索引与派生值故意不同：若 preflight 误用派生值而非显式配置，会拒绝
    正确的目标栈，此用例即变红。
    """
    monkeypatch.setattr(
        "app.core.config.settings",
        SimpleNamespace(
            DATABASE_URL="mysql+asyncmy://u:p@127.0.0.1:3308/docmind_rc",
            ELASTICSEARCH_INDEX_NAME="docmind_knowledge_custom",
        ),
    )
    monkeypatch.setattr(
        "app.core.config.loader._derive_es_index_name",
        lambda database_url: "docmind_knowledge_rc",
    )
    _fake_health(
        monkeypatch,
        {
            "status": "healthy",
            "knowledge_base": {"index_name": "docmind_knowledge_custom"},
        },
    )
    assert rc.preflight(None) is True


def test_script_makes_app_importable_when_run_directly(tmp_path):
    """按文档直跑 ``python scripts/...`` 时，脚本自身须把 backend 根加入 sys.path。

    否则 Python 只把 ``scripts/`` 放进 sys.path，``from app.core.config import
    settings`` 必失败，preflight 退化为只能依赖 --expect-index（正确栈也会被
    拒绝）。pytest 进程的 rootdir 已在 sys.path 里，故用干净 cwd 的子进程验证。
    """
    backend_root = Path(rc.__file__).resolve().parents[1]
    script = backend_root / "scripts" / "rc_accept_full_flow.py"
    code = (
        "import importlib.util\n"
        f"spec = importlib.util.spec_from_file_location('rcmod', r'{script}')\n"
        "mod = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(mod)\n"
        "from app.core.config import settings\n"
        "print('OK')\n"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(tmp_path),
        timeout=120,
    )
    assert out.returncode == 0, out.stderr
    assert "OK" in out.stdout
