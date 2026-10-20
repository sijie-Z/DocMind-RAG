"""W0-6：结构性 fail-open 默认值清零 —— 红/绿鉴别测试。

历史缺陷（W0-6 证据）：
- ``SemanticCache.get/set(organization_id: int = 0)``；
- ``DocumentParser.parse_local_file(organization_id: str = "0")``；
- ``KnowledgeService.search_knowledge`` 把 ``None`` 组织转成 0；
- 记忆命名空间允许 ``org=None``（拼出 ``"None:..."``）与 ``user=None``；
- ``agent_api`` 的 demo 组织默认 ``"0"``。

本文件钉住修复后的契约：
1. 上述入口的 organization_id 一律必填（漏传 → TypeError）；
2. 显式 0 / 空串 / None / 负数 → ValueError（显式拒绝，绝不静默落到 0 号组织）；
3. 无组织用户调用 memory API / chat parse-upload → 403（AuthorizationError），
   且不产生 ``org:0`` / ``None:`` 命名空间；
4. agent_api 的 AGENT_DEMO_ORG_ID 未设置或非正整数 → 503 拒绝请求；
5. 有合法 org 时行为不变（下游拿到同一个正整数组织）。
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import UploadFile

from app.exceptions import AuthorizationError
from app.rag.cache import SemanticCache
from app.services.document_parser import DocumentParser
from app.services.knowledge_service import KnowledgeService
from app.services.memory_service import (
    build_memory_namespace,
    get_memory_system,
    memory_systems,
)

BACKEND_ROOT = Path(__file__).resolve().parents[2]


# ── 公共夹具 ──────────────────────────────────────────────────────────


class _FakeRedisStore:
    """最小 Redis 双：记录所有写入键，用于断言没有 org:0 / org:None 键。"""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.members: dict[str, dict[str, int]] = {}

    def pipeline(self):
        return self

    def setex(self, key: str, _ttl: int, value: str) -> None:
        self.values[key] = value

    def zadd(self, key: str, mapping: dict[str, int]) -> None:
        self.members.setdefault(key, {}).update(mapping)

    async def execute(self) -> list:
        return []

    async def zrangebyscore(self, key: str, min_score: int, max_score: int) -> list[str]:
        return [
            member
            for member, score in self.members.get(key, {}).items()
            if min_score <= score <= max_score
        ]

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def zrem(self, key: str, member: str) -> None:
        self.members.get(key, {}).pop(member, None)


@pytest.fixture()
def isolated_memory_systems():
    """隔离全局记忆系统缓存，避免断言被他处残留的命名空间污染。"""
    original = dict(memory_systems)
    memory_systems.clear()
    try:
        yield
    finally:
        memory_systems.clear()
        memory_systems.update(original)


def _assert_no_failopen_keys(store: _FakeRedisStore) -> None:
    keys = list(store.values) + list(store.members)
    assert not [k for k in keys if "org:0:" in k], f"不得产生 org:0 键: {keys}"
    assert not [k for k in keys if "org:None:" in k], f"不得产生 org:None 键: {keys}"


# ── 1. SemanticCache ─────────────────────────────────────────────────


class TestSemanticCacheOrgRequired:
    @pytest.mark.asyncio
    async def test_get_without_org_is_rejected(self, monkeypatch):
        cache = SemanticCache()
        store = _FakeRedisStore()
        monkeypatch.setattr("app.rag.cache._get_redis", lambda: store)

        with pytest.raises(TypeError):
            await cache.get([0.1, 0.2, 0.3])  # 漏传 org → 拒绝（旧默认值 0 已删除）

        for bad in (None, 0, -1):
            with pytest.raises(ValueError):
                await cache.get([0.1, 0.2, 0.3], bad)
        _assert_no_failopen_keys(store)

    @pytest.mark.asyncio
    async def test_set_without_org_is_rejected(self, monkeypatch):
        cache = SemanticCache()
        store = _FakeRedisStore()
        monkeypatch.setattr("app.rag.cache._get_redis", lambda: store)

        with pytest.raises(TypeError):
            await cache.set("q", [0.1, 0.2, 0.3], "answer")  # 漏传 org

        for bad in (None, 0, -1):
            with pytest.raises(ValueError):
                await cache.set("q", [0.1, 0.2, 0.3], "answer", organization_id=bad)
        _assert_no_failopen_keys(store)

    @pytest.mark.asyncio
    async def test_valid_org_behavior_unchanged(self, monkeypatch):
        # issue #195：语义缓存默认关闭，合法组织行为用例显式开启并传查询原文。
        monkeypatch.setattr("app.rag.cache.settings.SEMANTIC_CACHE_ANSWER_ENABLED", True)
        cache = SemanticCache()
        store = _FakeRedisStore()
        monkeypatch.setattr("app.rag.cache._get_redis", lambda: store)
        embedding = [0.1, 0.2, 0.3, 0.4] * 8

        await cache.set("query", embedding, "answer", [], organization_id=7)
        assert any(key.startswith("rag:sem:org:7:") for key in store.values)
        hit = await cache.get(embedding, organization_id=7, query="query")
        assert hit is not None and hit["answer"] == "answer"
        assert await cache.get(embedding, organization_id=8, query="query") is None


# ── 2. DocumentParser.parse_local_file ───────────────────────────────


class TestParseLocalFileOrgRequired:
    @pytest.mark.asyncio
    async def test_missing_or_invalid_org_is_rejected(self, tmp_path):
        path = tmp_path / "sample.txt"
        path.write_text("hello", encoding="utf-8")
        parser = DocumentParser()

        with pytest.raises(TypeError):
            await parser.parse_local_file(str(path))  # 旧默认值 "0" 已删除

        for bad in (None, "", "0", "-1", "abc"):
            with pytest.raises(ValueError):
                await parser.parse_local_file(str(path), bad)

    @pytest.mark.asyncio
    async def test_valid_org_is_preserved(self, tmp_path):
        path = tmp_path / "sample.txt"
        path.write_text("hello", encoding="utf-8")
        result = await DocumentParser().parse_local_file(str(path), "42")
        assert result["organization_id"] == "42"
        assert result["content"] == "hello"


# ── 3. KnowledgeService.search_knowledge ─────────────────────────────


class TestSearchKnowledgeOrgRequired:
    @pytest.mark.asyncio
    async def test_missing_or_invalid_org_is_rejected_before_retrieval(self):
        service = KnowledgeService()
        with patch(
            "app.services.rag_service.RagServiceFacade.search_knowledge_base",
            new=AsyncMock(return_value=([], None)),
        ) as facade:
            with pytest.raises(TypeError):
                await service.search_knowledge("q")  # 漏传 org

            for bad in (None, "", "0", "-1", "abc", 0, -1):
                with pytest.raises(ValueError):
                    await service.search_knowledge("q", bad)

            assert facade.await_count == 0, "无有效组织时不得触达检索后端"

    @pytest.mark.asyncio
    async def test_valid_org_reaches_retrieval_verbatim(self):
        captured: dict = {}

        async def _fake(**kwargs):
            captured.update(kwargs)
            return [], None

        with patch(
            "app.services.rag_service.RagServiceFacade.search_knowledge_base",
            new=AsyncMock(side_effect=_fake),
        ):
            out = await KnowledgeService().search_knowledge("q", "42", top_k=5)

        assert out == []
        assert captured["organization_id"] == 42
        assert captured["top_k"] == 5


# ── 4. 记忆命名空间 ──────────────────────────────────────────────────


class TestMemoryNamespaceTenantContext:
    def test_namespace_requires_org_and_user(self):
        for org, user in (
            (None, 77),
            (1, None),
            (0, 77),
            (-2, 77),
            (1, ""),
            (1, "abc"),
            (1, -5),
        ):
            with pytest.raises(ValueError):
                build_memory_namespace(org, user)
        assert build_memory_namespace(1, 77) == "1:77:default"
        assert build_memory_namespace(1, 77, "agent") == "1:77:agent"
        # 系统/演示约定的 user=0 仍可用（不在本任务收紧范围）。
        assert build_memory_namespace(1, 0) == "1:0:default"

    def test_get_memory_system_rejects_and_creates_no_poisoned_namespace(
        self, isolated_memory_systems
    ):
        with pytest.raises(ValueError):
            get_memory_system(None, 77, "agent")
        with pytest.raises(ValueError):
            get_memory_system(1, None, "agent")
        with pytest.raises(ValueError):
            get_memory_system(0, 77, "agent")

        assert memory_systems == {}, f"拒绝后不得残留命名空间: {list(memory_systems)}"

    def test_get_memory_system_valid_context_unchanged(self, isolated_memory_systems):
        system = get_memory_system(1, 77, "agent")
        assert system.agent_id == "1:77:agent"
        assert "1:77:agent" in memory_systems


# ── 5. memory API / chat parse-upload 无组织拒绝 ─────────────────────


class TestOrglessCallersFailClosed:
    @pytest.mark.asyncio
    async def test_memory_endpoint_rejects_orgless_user(self, isolated_memory_systems):
        from app.api.v1.endpoints.memory import _get_user_memory_system

        user = SimpleNamespace(organization_id=None, id=5)
        with pytest.raises(AuthorizationError):
            await _get_user_memory_system(user, "default")
        assert memory_systems == {}, "无组织用户不得创建记忆命名空间"

    @pytest.mark.asyncio
    async def test_parse_upload_rejects_orgless_user(self):
        from app.api.v1.endpoints.chat import chat_parse_upload

        upload = UploadFile(file=io.BytesIO(b"hello"), filename="a.txt")
        user = SimpleNamespace(organization_id=None, id=5, is_superuser=False)
        with pytest.raises(AuthorizationError):
            await chat_parse_upload(file=upload, current_user=user, db=None)

    @pytest.mark.asyncio
    async def test_parse_upload_passes_real_org_unchanged(self, monkeypatch):
        import app.services.document_parser as doc_parser_mod
        from app.api.v1.endpoints.chat import chat_parse_upload

        captured: dict = {}

        async def _fake_parse(file_path, organization_id):
            captured["organization_id"] = organization_id
            return {"content": "hello", "chunks": [], "file_size": 5}

        monkeypatch.setattr(
            doc_parser_mod.document_service, "parse_local_file", _fake_parse
        )

        upload = UploadFile(file=io.BytesIO(b"hello"), filename="a.txt")
        user = SimpleNamespace(organization_id=42, id=5, is_superuser=False)
        result = await chat_parse_upload(file=upload, current_user=user, db=MagicMock())

        assert captured["organization_id"] == "42", "有组织时行为不变，传真实组织"
        assert result["success"] is True


# ── 6. agent_api demo 组织必须显式配置 ───────────────────────────────

_AGENT_API_PROBE = textwrap.dedent(
    """
    import os

    from fastapi import HTTPException

    from app.agent_api import _resolve_demo_org_id

    def probe(raw):
        if raw is None:
            os.environ.pop("AGENT_DEMO_ORG_ID", None)
        else:
            os.environ["AGENT_DEMO_ORG_ID"] = raw
        try:
            value = _resolve_demo_org_id()
        except HTTPException as exc:
            return ("reject", exc.status_code)
        return ("accept", value)

    expected = {
        None: ("reject", 503),
        "": ("reject", 503),
        "0": ("reject", 503),
        "-3": ("reject", 503),
        "abc": ("reject", 503),
        "7": ("accept", 7),
    }
    for raw, want in expected.items():
        got = probe(raw)
        print(f"AGENT_DEMO_ORG_ID={raw!r} -> {got}")
        if got != want:
            raise SystemExit(f"expected {want}, got {got}")
    raise SystemExit(0)
    """
)


def test_agent_api_demo_org_requires_explicit_env(tmp_path):
    """未配置 / 非法 AGENT_DEMO_ORG_ID 时拒绝请求（503），绝不回落 0 号组织。"""
    env = os.environ.copy()
    env.update(
        {
            "DEEPSEEK_API_KEY": "sk-test",
            "DEEPSEEK_API_URL": "http://localhost:9/v1",
            "PYTHONPATH": str(BACKEND_ROOT),
        }
    )
    env.pop("AGENT_DEMO_ORG_ID", None)

    proc = subprocess.run(
        [sys.executable, "-c", _AGENT_API_PROBE],
        cwd=str(tmp_path),
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert proc.returncode == 0, f"stdout={proc.stdout}\nstderr={proc.stderr}"
    assert "-> ('reject', 503)" in proc.stdout
    assert "-> ('accept', 7)" in proc.stdout
