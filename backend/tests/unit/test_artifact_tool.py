"""create_artifact 工具（批次七）：两条落库路径 + 校验 + 工具面门控。

判别点：
- 执行器接线（``artifact_capture``）→ 工具**不**落库，只回结构化载荷 + 回执；
- 未接线 → 工具自行落库（真 SQLite + 假 MinIO），回执带 artifact_id；
- 缺组织 → ``ToolResult.fail(auth_required)``（fail-closed）；
- 非法 kind / 超限 → ``validation_error``，不静默截断；
- 工具是扩展面（``enabled_predicate``）：默认 schema 不注入，显式 tool_tags 才出现。
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.agent.service  # noqa: F401 — 触发全部工具注册
import app.models  # noqa: F401
from app.agent.config import AgentConfig
from app.agent.registry import tool_registry
from app.agent.run_context import RunContext
from app.agent.tools.artifacts import create_artifact
from app.core.database import Base
from app.models.artifact import AgentArtifact
from app.models.organization import Organization
from app.services import artifact_service

ORG = 3
USER = 7


@pytest.fixture
async def factory(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'artifact-tool.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        db.add(Organization(id=ORG, name="Org", owner_id=USER))
        await db.commit()
    import app.core.database as db_module

    monkeypatch.setattr(db_module, "AsyncSessionLocal", maker)
    monkeypatch.setattr("app.agent.tools.artifacts.AsyncSessionLocal", maker, raising=False)
    yield maker
    await engine.dispose()


@pytest.fixture(autouse=True)
def fake_storage(monkeypatch):
    def _put(object_name, data, length, content_type="application/octet-stream", **kwargs):
        return object_name

    monkeypatch.setattr(artifact_service.minio_client, "put_object", _put)


async def test_capture_path_returns_payload_without_persisting(factory):
    """执行器接线路径：工具只校验并回载荷（行由执行器写，能挂 task/call）。"""
    ctx = RunContext(user_id=USER, organization_id=ORG, extra={"artifact_capture": True})
    result = await create_artifact(
        title="报告",
        content="# 正文",
        kind="markdown",
        organization_id=ORG,
        user_id=USER,
        run_context=ctx,
    )
    assert result.success is True
    payload = result.meta.extra["artifact"]
    assert payload["kind"] == "artifact/v1"
    assert payload["artifact_kind"] == "markdown"
    assert payload["content"] == "# 正文"
    receipt = json.loads(result.data)
    assert receipt["kind"] == "artifact/v1"
    assert receipt["artifact_kind"] == "markdown"
    # 接线路径不落库（执行器负责）。
    async with factory() as db:
        rows = (await db.execute(select(AgentArtifact))).scalars().all()
    assert rows == []


async def test_unwired_path_persists_and_returns_receipt(factory):
    result = await create_artifact(
        title="数据文件",
        content='{"a": 1}',
        kind="json",
        organization_id=ORG,
        user_id=USER,
    )
    assert result.success is True
    receipt = json.loads(result.data)
    assert receipt["artifact_id"]
    assert receipt["artifact_kind"] == "json"
    async with factory() as db:
        rows = (await db.execute(select(AgentArtifact))).scalars().all()
    assert len(rows) == 1
    assert rows[0].id == receipt["artifact_id"]
    assert rows[0].source_tool == "create_artifact"


async def test_missing_organization_fails_closed(factory):
    result = await create_artifact(title="t", content="c", organization_id=None)
    assert result.success is False
    assert result.error.code == "auth_required"


async def test_validation_failures_are_structured(factory):
    bad_kind = await create_artifact(
        title="t", content="c", kind="exe", organization_id=ORG, user_id=USER
    )
    assert bad_kind.success is False and bad_kind.error.code == "validation_error"

    oversize = await create_artifact(
        title="t",
        content="a" * (artifact_service.MAX_CONTENT_BYTES + 1),
        organization_id=ORG,
        user_id=USER,
    )
    assert oversize.success is False and oversize.error.code == "validation_error"

    empty_title = await create_artifact(title="   ", content="c", organization_id=ORG, user_id=USER)
    assert empty_title.success is False and empty_title.error.code == "validation_error"


def test_tool_is_extended_surface_and_not_in_default_schema():
    """扩展面门控：默认 schema 不注入；显式 tool_tags=["artifact"] 才出现。"""
    default_names = {
        schema["function"]["name"] for schema in tool_registry.build_tool_schemas(AgentConfig())
    }
    assert "create_artifact" not in default_names

    extended = {
        schema["function"]["name"]
        for schema in tool_registry.build_tool_schemas(AgentConfig(tool_tags=["artifact"]))
    }
    assert extended == {"create_artifact"}

    entry = tool_registry.get("create_artifact")
    assert entry is not None
    assert entry.risk_level == "medium"
