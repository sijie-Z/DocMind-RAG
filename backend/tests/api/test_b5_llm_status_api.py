"""B5 · 「连模型」onboarding 步骤的数据源 —— LLM 配置状态端点。

口径（任务书「四、B 批」B5）：清单步骤完成态优先由真实数据推导；个人 AI
模型设置生效后，端点必须以个人配置为准（source=user），否则回退服务端/本地
模型口径。``configured`` 可直接作为「连模型」步骤的完成态。

变异点：删除端点 / 隐瞒未配置状态（恒 True）/ 本地模型开关被忽略 /
个人配置未被消费 → 红。
"""

from types import SimpleNamespace

import pytest

from app.api.v1.endpoints import monitoring


def _user() -> SimpleNamespace:
    return SimpleNamespace(id=1)


@pytest.fixture(autouse=True)
def _default_server_resolution(monkeypatch):
    """默认无个人配置：resolve_for_user 返回服务端口径。"""

    async def _server(_db, _user):
        return SimpleNamespace(source="server", client=None, model=None, provider=None)

    monkeypatch.setattr("app.services.llm_settings_service.resolve_for_user", _server)


async def test_deepseek_configured_reports_ready(monkeypatch):
    monkeypatch.setattr(monitoring.settings, "ENABLE_LOCAL_LLM", False)
    monkeypatch.setattr(monitoring.settings, "DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setattr(monitoring.settings, "DEEPSEEK_MODEL", "deepseek-v4-flash")

    payload = await monitoring.get_llm_status(current_user=_user(), db=SimpleNamespace())

    assert payload["success"] is True
    assert payload["data"] == {
        "configured": True,
        "provider": "deepseek",
        "model": "deepseek-v4-flash",
        "source": "server",
    }


async def test_missing_api_key_reports_not_configured(monkeypatch):
    monkeypatch.setattr(monitoring.settings, "ENABLE_LOCAL_LLM", False)
    monkeypatch.setattr(monitoring.settings, "DEEPSEEK_API_KEY", "")
    monkeypatch.setattr(monitoring.settings, "DEEPSEEK_MODEL", "deepseek-v4-flash")

    payload = await monitoring.get_llm_status(current_user=_user(), db=SimpleNamespace())

    assert payload["data"]["configured"] is False
    assert payload["data"]["provider"] == "deepseek"


async def test_local_llm_switch_is_the_effective_provider(monkeypatch):
    monkeypatch.setattr(monitoring.settings, "ENABLE_LOCAL_LLM", True)
    monkeypatch.setattr(monitoring.settings, "LOCAL_LLM_MODEL", "qwen2.5")
    monkeypatch.setattr(monitoring.settings, "DEEPSEEK_API_KEY", "")

    payload = await monitoring.get_llm_status(current_user=_user(), db=SimpleNamespace())

    assert payload["data"] == {
        "configured": True,
        "provider": "local",
        "model": "qwen2.5",
        "source": "local",
    }


async def test_personal_config_wins(monkeypatch):
    async def _user_resolution(_db, _user):
        return SimpleNamespace(source="user", client=object(), model="my-model", provider="openai")

    monkeypatch.setattr("app.services.llm_settings_service.resolve_for_user", _user_resolution)

    payload = await monitoring.get_llm_status(current_user=_user(), db=SimpleNamespace())

    assert payload["data"] == {
        "configured": True,
        "provider": "openai",
        "model": "my-model",
        "source": "user",
    }


def test_endpoint_is_registered():
    from app.main import app

    schema = app.openapi()
    assert "/api/v1/monitoring/llm-status" in schema["paths"], (
        "前端 onboarding「连模型」步骤依赖该端点，缺失会让步骤永远无法完成"
    )
