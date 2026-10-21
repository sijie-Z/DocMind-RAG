"""B3 · 死设定清理 —— 端点弃用契约（任务书「四、B 批」B3）。

三处后端契约：

1. 个人 API Key（``POST/DELETE /api/v1/users/api-key``）：Profile UI 入口已移除，
   端点保留一个 minor 供已集成调用方迁移，但必须在 OpenAPI 标记 deprecated。
2. 工作流「全局 LLM 配置」（``/api/v1/llm-config`` 全部操作）：编辑器入口与
   「使用全局配置」开关已移除；端点在统一 Provider 层（B4 L1）落地前保留，
   同样必须标记 deprecated。
3. 聊天 ``activeSystemPrompt`` 是前端历史死字段：后端从未读取
   ``payload.systemPrompt``。这里锁定「schema 仍接收任意 payload 但不会因该字段
   报错」= 继续忽略、不接线。

变异点：移除任一 ``deprecated=True`` → 对应用例红。
"""

from app.main import app
from app.schemas.chat import ChatStreamRequest

_HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def _operations(schema: dict, path: str) -> dict:
    return {method: op for method, op in schema["paths"][path].items() if method in _HTTP_METHODS}


def test_personal_api_key_endpoints_are_deprecated():
    """个人 API Key 生成/撤销端点：保留但标 deprecated（一个 minor 后删除）。"""
    schema = app.openapi()
    path = "/api/v1/users/api-key"
    assert path in schema["paths"], "端点需保留一个 minor，不应直接删除"

    ops = _operations(schema, path)
    assert {"post", "delete"} <= set(ops), f"预期 POST/DELETE，实际 {sorted(ops)}"
    for method, op in ops.items():
        assert op.get("deprecated") is True, (
            f"{method.upper()} {path} 未标记 deprecated——Profile UI 已移除入口，"
            "未标记会让调用方误以为仍是受支持通道"
        )


def test_all_llm_config_endpoints_are_deprecated():
    """工作流全局 LLM 配置端点：全部保留但标 deprecated，等待 B4 Provider 层删除。"""
    schema = app.openapi()
    llm_paths = sorted(p for p in schema["paths"] if p.startswith("/api/v1/llm-config"))
    assert llm_paths, "llm-config 路由应仍注册（保留到 B4 L1 Provider 落地）"

    missing: list[str] = []
    for path in llm_paths:
        for method, op in _operations(schema, path).items():
            if op.get("deprecated") is not True:
                missing.append(f"{method.upper()} {path}")
    assert not missing, f"以下 llm-config 端点未标记 deprecated：{missing}"


def test_chat_payload_schema_tolerates_legacy_system_prompt():
    """旧客户端仍发送 systemPrompt 时 schema 不得报错（容忍契约）。后端忽略语义
    目前由 chat.py 消费点注释记录（无运行时守卫）；前端停发由
    chatSendNoSystemPrompt.test.ts 锁定——本用例不证明忽略。"""
    body = ChatStreamRequest(
        content="hi",
        payload={
            "strictMode": False,
            "privacyMode": True,
            "systemPrompt": "历史死字段，应被忽略",
        },
    )
    assert body.payload["systemPrompt"] == "历史死字段，应被忽略"
    assert body.payload["strictMode"] is False
    assert body.payload["privacyMode"] is True
