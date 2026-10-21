"""B5 · 「连模型」onboarding 步骤的数据源 —— LLM 配置状态端点。

口径（任务书「四、B 批」B5）：清单步骤完成态优先由真实数据推导；本基线内
没有可靠的「模型已连接」前端数据源（/llm-config 已按 B3 标记 deprecated 且
不得新增调用方，Profile 的 Provider 区块是禁用实验项），故新增只读端点：

    GET /api/v1/monitoring/llm-status

返回当前进程实际生效的 LLM 配置（DeepSeek 环境变量或本地模型），
``configured`` 可直接作为「连模型」步骤的完成态。

变异点：删除端点 / 隐瞒未配置状态（恒 True）/ 本地模型开关被忽略 → 红。
"""

from types import SimpleNamespace

from app.api.v1.endpoints import monitoring


def _user() -> SimpleNamespace:
    return SimpleNamespace(id=1)


async def test_deepseek_configured_reports_ready(monkeypatch):
    monkeypatch.setattr(monitoring.settings, "ENABLE_LOCAL_LLM", False)
    monkeypatch.setattr(monitoring.settings, "DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setattr(monitoring.settings, "DEEPSEEK_MODEL", "deepseek-v4-flash")

    payload = await monitoring.get_llm_status(current_user=_user())

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

    payload = await monitoring.get_llm_status(current_user=_user())

    assert payload["data"]["configured"] is False
    assert payload["data"]["provider"] == "deepseek"


async def test_local_llm_switch_is_the_effective_provider(monkeypatch):
    monkeypatch.setattr(monitoring.settings, "ENABLE_LOCAL_LLM", True)
    monkeypatch.setattr(monitoring.settings, "LOCAL_LLM_MODEL", "qwen2.5")
    monkeypatch.setattr(monitoring.settings, "DEEPSEEK_API_KEY", "")

    payload = await monitoring.get_llm_status(current_user=_user())

    assert payload["data"] == {
        "configured": True,
        "provider": "local",
        "model": "qwen2.5",
        "source": "local",
    }


def test_endpoint_is_registered():
    from app.main import app

    schema = app.openapi()
    assert "/api/v1/monitoring/llm-status" in schema["paths"], (
        "前端 onboarding「连模型」步骤依赖该端点，缺失会让步骤永远无法完成"
    )
