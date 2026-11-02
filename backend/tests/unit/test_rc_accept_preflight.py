"""RC 验收脚本 preflight：必须按目标 /health 的索引身份拦截错栈（评审 A R2）。"""

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
