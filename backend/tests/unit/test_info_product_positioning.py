"""`/info` 的自我描述必须与产品定位同口径。

现场（2026-11-17 核查队列第 ④ 项时发现）：同一个 `/info` 响应里，

- `name` = `settings.APP_NAME` = 「DocMind 文档问答与合同核验平台」（已对齐）
- `description` = 「基于DeepSeek的AI知识库系统」（**旧定位**）

对外的自我描述自相矛盾；而且这个 description 与 `features` 用的是 README / 首屏
`tagline` / `docs/product-definition.md` 都已不再使用的「AI 知识库系统」口径，
`features` 里也完全没有合同核验这条主线。

这不是文案洁癖：`/info` 是对外声明"这个服务是什么"的端点，说错等于对外错标产品。
"""

from __future__ import annotations

from app.core.config import settings


def test_info_description_uses_current_positioning(client) -> None:
    payload = client.get("/info").json()

    assert payload["name"] == settings.APP_NAME

    description = payload["description"]
    assert "知识库系统" not in description, f"/info 仍是旧定位：{description}"
    assert "合同核验" in description, f"/info 没体现产品主线（合同核验）：{description}"


def test_info_features_mention_contract_review(client) -> None:
    features = client.get("/info").json()["features"]

    assert features, "/info.features 不能为空"
    assert any("合同核验" in item for item in features), f"features 缺合同核验主线：{features}"
