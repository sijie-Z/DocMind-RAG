"""评审必修 2：搜索 API 边界必须透传 manual_override，且不把人工文本当证据原文。

变异点：
- 删掉 knowledge.py 投影里的 ``retrieval_text_provenance`` → API 用例红；
- normalize_result_for_evidence 把 manual 标记当 parser_original → provenance 用例红；
- 把 manual 文本写进 original_text/prompt_text → strict 用例红。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import get_current_user
from app.rag.source_provenance import (
    COMPAT,
    LEGACY_UNVERIFIED,
    MANUAL_OVERRIDE,
    PARSER_ORIGINAL,
    STRICT,
    is_manual_override_result,
    normalize_result_for_evidence,
)

ORIGINAL = "第一条 付款期限为30天。"
MANUAL = "第一条 人工修正后的检索文本"


def _admin():
    user = MagicMock()
    user.id = 1
    user.is_superuser = True
    user.is_active = True
    user.organization_id = 1
    return user


class _Ctx:
    def __init__(self, user, mock_db):
        self.user = user
        self.mock_db = mock_db

    async def _db(self):
        yield self.mock_db

    def __enter__(self):
        from app.main import app

        app.dependency_overrides[get_current_user] = lambda: self.user
        app.dependency_overrides[get_db] = self._db
        self.client = TestClient(app)
        return self.client

    def __exit__(self, *_args):
        from app.main import app

        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def search_result():
    return {
        "id": "chunk-1",
        "text": MANUAL,
        "content": MANUAL,
        "original_text": ORIGINAL,
        "text_provenance": PARSER_ORIGINAL,
        "retrieval_text_provenance": MANUAL_OVERRIDE,
        "score": 0.9,
        "source": {"filename": "contract.pdf"},
        "metadata": {},
    }


def test_search_api_preserves_manual_override_marker(search_result):
    mock_db = MagicMock()
    with (
        _Ctx(_admin(), mock_db) as client,
        patch("app.api.v1.endpoints.knowledge.knowledge_service") as svc,
    ):
        svc.search_knowledge_with_meta = AsyncMock(return_value=([search_result], None))
        response = client.post(
            "/api/v1/knowledge/search",
            json={"query": "付款", "top_k": 5, "organization_id": 1},
            headers={"Authorization": "Bearer test_token"},
        )

    assert response.status_code == 200
    first = response.json()["results"][0]
    assert first["retrieval_text_provenance"] == MANUAL_OVERRIDE
    # 展示正文是人工文本，但证据原文标记仍是 parser_original 且正文独立。
    assert first["content"] == MANUAL
    assert first["text_provenance"] == PARSER_ORIGINAL


@pytest.mark.parametrize("mode", [COMPAT, STRICT])
def test_manual_marker_does_not_promote_manual_text_to_evidence(search_result, mode):
    normalized = normalize_result_for_evidence(
        search_result, query="付款", mode=mode, snippet_builder=lambda text, _q: text
    )
    assert is_manual_override_result(normalized) is True
    assert normalized["text_provenance"] == PARSER_ORIGINAL
    assert normalized["original_text"] == ORIGINAL  # 不是人工文本
    assert normalized["prompt_text"] == ORIGINAL  # 不是人工文本
    assert normalized["snippet"] == ORIGINAL


def test_manual_marker_cannot_rescue_legacy_original(search_result):
    legacy = {
        **search_result,
        "text_provenance": LEGACY_UNVERIFIED,
        "original_text": None,
    }
    strict = normalize_result_for_evidence(
        legacy, query="付款", mode=STRICT, snippet_builder=lambda text, _q: text
    )
    assert strict["prompt_text"] == ""
    assert strict["snippet"] == ""
