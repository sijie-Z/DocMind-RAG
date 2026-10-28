"""`/api/v1/chat/stream` 的入口守卫与回归锚点（issue #508，方案 B）。

覆盖：
- 旧客户端仍发 ``useAgent=true`` → 流内显式 ``error`` 事件，错误码
  ``DEEP_ANALYSIS_MOVED_TO_AGENT`` + ``redirect=/agent``；**不**进入 Agent
  链路（agent_service.chat 零调用），也**不**静默降级成 RAG；
- 回滚阀：``CHAT_DEEP_ANALYSIS_GUARD=false`` 时 legacy Agent 分支仍可用
  （部署窗口期回滚；既有 #480/#301/#475 等用例依赖该分支继续可测）；
- 普通 RAG 聊天（无 useAgent）行为不变：chunk/message 照常投影；
- 静态契约：``frontend/src/types/agent.ts::AGENT_EVENT_TYPES`` 与后端
  ``app/agent/events.py::EventType`` 逐字一致——Chat 不再消费 Agent 事件，
  但事件分类漂移必须在两侧同时可见（配合前端
  ``chatEventCoverage.test.ts`` 的显式豁免表，防止 approval 类事件无声丢失）。
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import get_args

import pytest
from httpx import ASGITransport, AsyncClient

import app.api.v1.endpoints.chat as chat_api
from app.agent.events import AgentEvent, EventType
from app.agent.service import agent_service
from app.core.database import get_db
from app.core.security import get_current_user
from app.main import app

USER_ID = 1
STREAM_URL = "/api/v1/chat/stream"


def _fake_user() -> SimpleNamespace:
    return SimpleNamespace(
        id=USER_ID,
        organization_id=1,
        is_active=True,
        is_superuser=False,
        token_organization_id=None,
    )


def _parse_frames(text: str) -> list[dict]:
    frames: list[dict] = []
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        event = None
        data_lines: list[str] = []
        for line in block.split("\n"):
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data_lines.append(line[6:])
        frames.append(
            {
                "event": event,
                "data": json.loads("\n".join(data_lines)) if data_lines else None,
            }
        )
    return frames


@pytest.fixture
async def client():
    async def _override_db():
        yield SimpleNamespace()

    app.dependency_overrides[get_current_user] = lambda: _fake_user()
    app.dependency_overrides[get_db] = _override_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://chat-stream") as c:
        yield c
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(get_db, None)


@pytest.mark.asyncio
async def test_use_agent_true_gets_explicit_moved_error(client, monkeypatch):
    """issue #508：旧客户端的 useAgent=true 必须显式失败，不得跑 Agent 链路。"""
    monkeypatch.setattr(chat_api, "DEEP_ANALYSIS_GUARD_ENABLED", True)

    agent_calls = {"n": 0}

    async def _fake_agent_chat(**_kwargs):
        agent_calls["n"] += 1
        yield AgentEvent(type="chunk", content="SHOULD_NOT_RUN")

    monkeypatch.setattr(agent_service, "chat", _fake_agent_chat)

    resp = await client.post(
        STREAM_URL,
        json={"content": "帮我做深度分析", "useAgent": True},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")

    frames = _parse_frames(resp.text)
    assert [f["event"] for f in frames] == ["error"], frames
    payload = frames[0]["data"]
    assert payload["code"] == "DEEP_ANALYSIS_MOVED_TO_AGENT"
    assert payload["redirect"] == "/agent"
    assert "Agent 工作台" in payload["content"]
    assert "SHOULD_NOT_RUN" not in resp.text
    assert agent_calls["n"] == 0, "守卫必须阻止进入无审批 UI 的 Agent 链路"


@pytest.mark.asyncio
async def test_use_agent_true_is_not_silently_downgraded_to_rag(client, monkeypatch):
    """守卫命中后必须立刻结束流——不得回退到 RAG 继续产出回答。"""
    monkeypatch.setattr(chat_api, "DEEP_ANALYSIS_GUARD_ENABLED", True)

    rag_calls = {"n": 0}

    async def _fake_rag_pipeline(**_kwargs):
        rag_calls["n"] += 1

    monkeypatch.setattr(chat_api, "run_rag_pipeline", _fake_rag_pipeline)

    resp = await client.post(STREAM_URL, json={"content": "x", "useAgent": True})

    assert "event: chunk" not in resp.text
    assert "event: message" not in resp.text
    assert rag_calls["n"] == 0, "不得静默降级为普通 RAG"


@pytest.mark.asyncio
async def test_guard_off_keeps_legacy_agent_branch_for_rollback(client, monkeypatch):
    """回滚阀：guard 关闭时旧 Agent 分支仍按原样转发（既有用例依赖它）。"""
    monkeypatch.setattr(chat_api, "DEEP_ANALYSIS_GUARD_ENABLED", False)
    monkeypatch.setattr(chat_api, "save_session_interaction", _noop_save)

    async def _fake_agent_chat(**_kwargs):
        yield AgentEvent(type="chunk", content="legacy-chunk")
        yield AgentEvent(type="done", content="")

    monkeypatch.setattr(agent_service, "chat", _fake_agent_chat)

    resp = await client.post(
        STREAM_URL,
        json={"content": "旧客户端", "useAgent": True},
    )

    assert resp.status_code == 200
    assert "event: chunk" in resp.text
    assert "legacy-chunk" in resp.text


async def _noop_save(*_args, **_kwargs):
    return None


@pytest.mark.asyncio
async def test_rag_stream_unchanged_without_use_agent(client, monkeypatch):
    """普通 RAG 聊天不回归：chunk/message 事件照常投影（守卫不触碰 RAG 分支）。"""
    from app.services.chat_service import RAGEvent

    monkeypatch.setattr(chat_api, "DEEP_ANALYSIS_GUARD_ENABLED", True)
    monkeypatch.setattr(chat_api, "AsyncSessionLocal", lambda: _FakeSessionCtx())

    async def _fake_rag_pipeline(**kwargs):
        queue = kwargs["event_queue"]
        await queue.put(
            RAGEvent(
                type="chunk",
                content="RAG 片段",
                conversation_id="conv-508",
                message_id="msg-508",
            )
        )
        await queue.put(
            RAGEvent(
                type="message",
                content="RAG 完整回答",
                conversation_id="conv-508",
                message_id="msg-508",
            )
        )

    monkeypatch.setattr(chat_api, "run_rag_pipeline", _fake_rag_pipeline)

    resp = await client.post(STREAM_URL, json={"content": "普通问题"})

    assert resp.status_code == 200
    frames = _parse_frames(resp.text)
    chunk_frames = [f for f in frames if f["event"] == "chunk"]
    message_frames = [f for f in frames if f["event"] == "message"]
    # 事件数/内容不回归：恰好一条 chunk + 一条 message，且无 error 帧
    assert [f["data"]["content"] for f in chunk_frames] == ["RAG 片段"]
    assert [f["data"]["content"] for f in message_frames] == ["RAG 完整回答"]
    assert "error" not in [f["event"] for f in frames]
    assert all(f["data"].get("code") != "DEEP_ANALYSIS_MOVED_TO_AGENT" for f in frames)


class _FakeSessionCtx:
    async def __aenter__(self):
        return SimpleNamespace()

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, True),  # 生产默认：未设置环境变量时必须拒绝旧客户端
        ("", True),  # 空串（如 compose 里留空）同样按默认开启
        ("true", True),
        ("1", True),
        ("0", False),
        ("false", False),
        ("FALSE", False),
        ("off", False),
        ("no", False),
    ],
)
def test_guard_env_parser_defaults_to_rejecting_legacy_clients(raw, expected):
    """钉住守卫的默认值语义（conftest 全局关闭后，防止默认值被静默反转）。"""
    assert chat_api._guard_enabled_from_env(raw) is expected


def test_guard_module_constant_defaults_true_without_env():
    """无 ``CHAT_DEEP_ANALYSIS_GUARD`` 环境变量时，模块常量必须是 True。

    上一条只钉解析函数语义；本用例清空该 env 后起**子进程**重新导入模块，
    钉住真实调用点（``os.getenv(...)`` 的缺省路径）——把缺省值改成 false /
    硬编码 False 都会让本用例红。子进程继承父进程 conftest 已注入的其余测试 env。
    """
    env = dict(os.environ)
    env.pop("CHAT_DEEP_ANALYSIS_GUARD", None)
    backend_dir = Path(__file__).resolve().parents[2]
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import app.api.v1.endpoints.chat as c; print(c.DEEP_ANALYSIS_GUARD_ENABLED)",
        ],
        cwd=backend_dir,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "True"


def test_frontend_agent_event_taxonomy_matches_backend_literal():
    """跨语言静态契约：前端 AGENT_EVENT_TYPES 与后端 EventType 必须逐字一致。"""
    repo_root = Path(__file__).resolve().parents[3]
    ts_path = repo_root / "frontend" / "src" / "types" / "agent.ts"
    source = ts_path.read_text(encoding="utf-8")

    block = re.search(
        r"export const AGENT_EVENT_TYPES\s*=\s*\[(.*?)\]\s*as const",
        source,
        re.DOTALL,
    )
    assert block is not None, "frontend/src/types/agent.ts 缺少 AGENT_EVENT_TYPES 常量"

    frontend_types = set(re.findall(r"'([a-z_]+)'", block.group(1)))
    backend_types = set(get_args(EventType))

    assert frontend_types == backend_types, (
        "Agent 事件分类漂移："
        f"仅前端={sorted(frontend_types - backend_types)}，"
        f"仅后端={sorted(backend_types - frontend_types)}；"
        "新增/删除事件时必须同步 types/agent.ts 与后端 EventType，并更新 "
        "chatEventCoverage.test.ts 的豁免表"
    )

    # 审批类事件必须在两侧都可见（防止「无声丢失」的回归起点）。
    for required in ("approval_required", "plan_approval_required", "run_cancelled"):
        assert required in backend_types
        assert required in frontend_types


@pytest.mark.asyncio
async def test_regenerate_flag_forwarded_and_warning_visible(client, monkeypatch):
    """payload.regenerate 必须透传给 pipeline；保存失败 warning 必须成帧。"""
    from app.services.chat_service import RAGEvent

    monkeypatch.setattr(chat_api, "DEEP_ANALYSIS_GUARD_ENABLED", True)
    monkeypatch.setattr(chat_api, "AsyncSessionLocal", lambda: _FakeSessionCtx())

    seen: dict = {}

    async def _fake_rag_pipeline(**kwargs):
        seen.update(kwargs)
        queue = kwargs["event_queue"]
        await queue.put(
            RAGEvent(
                type="message",
                content="新答案",
                conversation_id="conv-regenerate",
                message_id="msg-a1",
            )
        )
        await queue.put(
            RAGEvent(
                type="warning",
                content="回答已生成，但会话记录保存失败；刷新后可能仍显示旧回答",
                conversation_id="conv-regenerate",
            )
        )

    monkeypatch.setattr(chat_api, "run_rag_pipeline", _fake_rag_pipeline)

    resp = await client.post(
        STREAM_URL,
        json={
            "content": "本轮问题",
            "conversationId": "conv-regenerate",
            "payload": {"regenerate": True},
        },
    )

    assert resp.status_code == 200
    assert seen["regenerate"] is True
    assert seen["conversation_id"] == "conv-regenerate"
    frames = _parse_frames(resp.text)
    assert [f["event"] for f in frames] == ["message", "warning"], frames
    assert frames[1]["data"]["content"].startswith("回答已生成")
    assert frames[0]["data"]["messageId"] == "msg-a1"


@pytest.mark.asyncio
async def test_regenerate_defaults_to_false(client, monkeypatch):
    """普通发送不得被误标成重新生成。"""
    from app.services.chat_service import RAGEvent

    monkeypatch.setattr(chat_api, "DEEP_ANALYSIS_GUARD_ENABLED", True)
    monkeypatch.setattr(chat_api, "AsyncSessionLocal", lambda: _FakeSessionCtx())

    seen: dict = {}

    async def _fake_rag_pipeline(**kwargs):
        seen.update(kwargs)
        await kwargs["event_queue"].put(
            RAGEvent(type="message", content="A", conversation_id="c", message_id="m")
        )

    monkeypatch.setattr(chat_api, "run_rag_pipeline", _fake_rag_pipeline)

    resp = await client.post(STREAM_URL, json={"content": "普通问题"})
    assert resp.status_code == 200
    assert seen["regenerate"] is False
