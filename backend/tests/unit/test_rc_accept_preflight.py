"""RC 验收脚本 preflight：必须按目标 /health 的索引身份拦截错栈（评审 A R2）。"""

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
