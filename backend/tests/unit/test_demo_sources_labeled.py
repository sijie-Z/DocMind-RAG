"""T4 回归（第二轮 §7.2④）：demo 假分数标注 —— 每条 source 带 demo 标记。

DEMO_CONVERSATION 的 sources 是写死的假检索结果（relevance_score 0.79~0.95
与真实检索无关，纯为首次体验演示）。数字保留（体验价值），但必须逐条标注
`"demo": True`——前端据此渲染「演示数据」角标，用户不再把假分数当真。
另需保证经 /demo/seed 落库的 ChatMessage.meta_data["sources"] 逐条带标记
（chat 端点 get_conversation 原样透传 meta_data["sources"]，前端读的就是
这份数据）。

变异点：去掉任一 source 的 `"demo": True`（或 seed 落库时剥掉该键）→ 本文件红。
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.chat import ChatMessage, MessageType


def _demo_user():
    return SimpleNamespace(id=1, organization_id=1, is_superuser=False, role="user", is_active=True)


def _demo_db():
    db = MagicMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    res = MagicMock()
    res.scalar = MagicMock(return_value=0)  # 无既有示例数据
    db.execute = AsyncMock(return_value=res)
    return db


def _capture(db):
    added = []
    db.add.side_effect = added.append
    return added


async def _seed_and_capture():
    import app.api.v1.endpoints.demo as demo_mod

    db = _demo_db()
    added = _capture(db)

    async def _embeddings_ok(texts):
        return [[0.1, 0.2] for _ in texts]

    async def _fake_index(doc_id, document):
        return None

    class _VerifyES:
        class _Indices:
            async def refresh(self, index):
                pass

        indices = _Indices()

        async def count(self, index=None, query=None):
            return {"count": 1}

    async def _fake_get_elasticsearch():
        return _VerifyES()

    with (
        patch.object(demo_mod.embedding_service, "get_embeddings", _embeddings_ok),
        patch.object(demo_mod.ElasticsearchTools, "index_document", _fake_index),
        patch("app.services.document_status.get_elasticsearch", _fake_get_elasticsearch),
    ):
        body = await demo_mod.seed_demo_data(current_user=_demo_user(), db=db)
    return added, body


class TestDemoSourcesLabeled:
    def test_every_demo_source_is_flagged_and_keeps_score(self):
        """结构钉死：每条 source 带 demo=True，且相关度数字保留（只标注、不删）。

        数字必须以**前端真实读点键**（camelCase `relevanceScore`，对齐
        build_sources_list 契约）存放——修前用 snake `relevance_score` 时前端
        `source.relevanceScore` 恒 undefined、相关度永不渲染（「保留」落空）。
        """
        import app.api.v1.endpoints.demo as demo_mod

        sources = [
            src
            for msg in demo_mod.DEMO_CONVERSATION["messages"]
            for src in (msg.get("metadata") or {}).get("sources", [])
        ]
        assert sources, "DEMO_CONVERSATION 必须包含来源数据，否则本测试失去意义"
        for src in sources:
            assert src.get("demo") is True, (
                f"source {src.get('filename')} 缺 demo 标记 —— 假分数不得裸奔"
            )
            assert isinstance(src.get("relevanceScore"), (int, float)), (
                "相关度数字保留（体验价值）—— 且键必须是前端读点 relevanceScore"
            )
            assert "relevance_score" not in src, (
                "不得再用 snake 键：前端只读 camelCase，数字会静默消失"
            )

    @pytest.mark.asyncio
    async def test_seeded_conversation_messages_carry_demo_sources(self):
        """seed 后落库的每条带来源消息，其 sources 逐条带 demo 标记。

        关键契约（真链路实测发现）：`ChatMessage.meta_data` 列是 SQLAlchemy JSON，
        chat.py 读路径却用 `json.loads(msg.meta_data)` —— 落库必须是 JSON 字符串，
        否则 json.loads(dict) 抛 TypeError 被静默吞掉、sources 恒为空
        （demo 会话在 UI 里连来源都看不见，角标/数字全部落空）。
        """
        added, body = await _seed_and_capture()
        assert body["success"] is True

        msgs = [o for o in added if isinstance(o, ChatMessage)]
        assert msgs, "seed 必须落库消息"

        assistant_msgs = [
            m for m in msgs if m.meta_data and m.message_type == MessageType.ASSISTANT
        ]
        assert assistant_msgs, "示例对话应至少有一条带 meta 的助手消息"
        for m in assistant_msgs:
            # 非 str 在此 TypeError（json.loads(dict)）——先红点，对齐 chat.py:486
            meta = json.loads(m.meta_data)
            for src in meta["sources"]:
                assert src.get("demo") is True, "落库 sources 缺 demo 标记 —— 前端角标会漏渲染"
                assert isinstance(src.get("relevanceScore"), (int, float)), (
                    "落库数字必须用前端读点键 relevanceScore（camelCase）"
                )
