"""体验第四波 @引用 —— API 接线判别：/agent/chat 接受 referenced_document_ids
并原样转交 service.chat（变异点：endpoint 不透传 → 用例红）。

（服务层解析/注入语义见 tests/unit/test_referenced_documents.py；
此处只守请求模型与端点的透传一环。）
"""

from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

import app.models  # noqa: F401 — 注册全部表
from app.agent.events import AgentEvent
from app.agent.exec_context import ExecutionContext
from app.api.v1.endpoints import agent as agent_api
from app.core.security import get_current_user
from app.rag.retriever import HybridRetriever


def _mock_user(user_id: int = 7, org_id: int = 1):
    class _User:
        pass

    user = _User()
    user.id = user_id
    user.organization_id = org_id
    user.is_active = True
    return user


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    async def _save(self, ttl=86400 * 7):
        return False

    monkeypatch.setattr(ExecutionContext, "save", _save)
    monkeypatch.setattr(HybridRetriever, "get_embedding", AsyncMock(return_value=[]))


@pytest.fixture
def captured(monkeypatch):
    box: dict = {}

    async def _fake_chat(**kwargs):
        box.update(kwargs)
        yield AgentEvent(type="done", content="")

    monkeypatch.setattr(agent_api.agent_service, "chat", _fake_chat)
    return box


@pytest.fixture
async def client():
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: _mock_user()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://refdocs") as c:
        yield c
    app.dependency_overrides.pop(get_current_user, None)


BODY_BASE = {
    "query": "查预算",
    "enable_planning": False,
    "enable_reflection": False,
    "enable_memory": False,
    "enable_thinking": False,
}


class TestReferencedDocumentIdsPassthrough:
    async def test_ids_forwarded_to_service(self, client, captured):
        r = await client.post(
            "/api/v1/agent/chat",
            json={**BODY_BASE, "referenced_document_ids": ["d1", "d2"]},
        )
        assert r.status_code == 200
        assert captured.get("referenced_document_ids") == ["d1", "d2"]

    async def test_absent_field_defaults_none(self, client, captured):
        r = await client.post("/api/v1/agent/chat", json=BODY_BASE)
        assert r.status_code == 200
        assert captured.get("referenced_document_ids") is None
