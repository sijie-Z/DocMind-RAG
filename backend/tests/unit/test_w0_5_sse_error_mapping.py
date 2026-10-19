"""W0-5：SSE 错误帧脱敏 + 无组织 Agent 模式用户可读拒绝。

问题（修前）：
- ``/chat/stream`` 的兜底 ``except`` 把 ``str(e)`` 原样拼进 error 帧；
- agent 模式下无组织用户在 ``AgentService.chat`` 入口抛 ``ValueError``，该内部
  文本（含参数名 organization_id、内部类名）直达客户端；
- ``agent_event.content``（error / tool_error / thinking 重试上下文 /
  run_report / execution_context）原样转发，工具/LLM 异常文本同样可外泄。

修复后的对外口径：
1. 无组织 → 403 语义的拒绝帧（与 ``AuthorizationError`` 同一用户可读文案），
   绝不出现内部异常文本；
2. 未预期异常 → 通用文案（带请求编号），完整异常与堆栈只进服务端日志；
3. AgentEvent 的 error / tool_error 内容按**白名单**放行本项目自撰文案
   （白名单外一律降级）；thinking / 诊断载荷做内部特征脱敏。

先红后绿：本文件中 ``*_redacted`` / ``*_refus*`` / ``*_mapped`` 用例在修前会
看到 ``secret-token-xyz`` / ``organization_id`` / ``ValueError`` 等内部文本。
"""

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.agent.events import AgentEvent
from app.agent.executor import Executor
from app.core.database import get_db
from app.core.error_mapping import (
    GENERIC_RAG_ERROR,
    public_diagnostic_content,
    public_exception_message,
    public_rag_error_content,
    resolve_request_id,
)
from app.core.security import get_current_user
from app.exceptions import AuthorizationError, ValidationError

SECRET = "secret-token-xyz"
GENERIC_SERVER_ERROR = "服务器处理出错"
ORG_REFUSAL = "当前用户未加入任何组织"


class _AsyncCtx:
    """支持 ``async with`` 的 mock 上下文管理器。"""

    def __init__(self, obj):
        self._obj = obj

    async def __aenter__(self):
        return self._obj

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return False


def _user(user_id: int = 7, org: int | None = 5) -> SimpleNamespace:
    return SimpleNamespace(
        id=user_id,
        organization_id=org,
        token_organization_id=None,
        is_superuser=False,
        role="user",
        is_active=True,
    )


def _override(user: SimpleNamespace, db: object | None = None):
    """挂 get_db / get_current_user 覆盖；返回清理函数。"""
    from app.main import app

    async def _db():
        return db if db is not None else MagicMock()

    async def _current_user():
        return user

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_current_user] = _current_user

    def _cleanup():
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)

    return _cleanup


# 同时携带内网路径 / SQL / 内部类名形态的异常原文：修前会整段进 SSE。
LEAKY_EXCEPTION = (
    f"{SECRET} at C:\\Users\\lenovo\\secret.py: "
    "ValueError: SELECT * FROM users WHERE token = 'x'"
)


async def _raising_agent(**kwargs):
    """agent_service.chat 替身：首次迭代时抛未预期异常（异步生成器）。"""
    raise RuntimeError(LEAKY_EXCEPTION)
    yield  # pragma: no cover — 仅为让函数成为 async generator


async def _orgless_agent(**kwargs):
    """agent_service.chat 替身：复现无组织入口的 ValueError 文本。"""
    raise ValueError(
        "AgentService.chat 缺少 organization_id，拒绝在无组织上下文下运行"
    )
    yield  # pragma: no cover


# ══════════════════════════════════════════════════════════════════════════
# 1 · 兜底异常：客户端只见通用文案 + 请求编号，完整异常/堆栈只进日志
# ══════════════════════════════════════════════════════════════════════════
class TestUnexpectedExceptionRedaction:
    def test_agent_stream_unexpected_exception_is_redacted(self, client, caplog):
        """Agent 模式未预期异常 → 通用文案；secret/路径/SQL/堆栈/类名不外泄。"""
        from app.agent.service import agent_service

        caplog.set_level(logging.ERROR)
        cleanup = _override(_user())
        try:
            with patch.object(agent_service, "chat", new=_raising_agent):
                r = client.post(
                    "/api/v1/chat/stream",
                    json={"content": "你好", "useAgent": True},
                )
        finally:
            cleanup()

        assert r.status_code == 200, r.text
        body = r.text
        assert "event: error" in body, f"必须收到 error 帧，实际: {body[:400]}"
        assert GENERIC_SERVER_ERROR in body, f"必须给出通用文案: {body[:400]}"
        assert SECRET not in body, f"异常原文外泄: {body[:400]}"
        assert "C:\\Users" not in body, f"文件路径外泄: {body[:400]}"
        assert "SELECT * FROM" not in body, f"SQL 外泄: {body[:400]}"
        assert "ValueError" not in body, f"内部类名外泄: {body[:400]}"
        assert "Traceback" not in body, f"堆栈外泄: {body[:400]}"

        # 通用文案带请求编号（客户端可据此报障），日志带完整上下文
        request_id = r.headers.get("x-request-id")
        assert request_id, "RequestIDMiddleware 必须给响应盖 X-Request-ID"
        assert f"请求编号：{request_id}" in body, "通用文案必须携带请求编号"
        assert SECRET in caplog.text, "日志必须保留完整异常文本（排障需要）"
        assert "Traceback" in caplog.text, "日志必须保留完整堆栈"
        assert f"request_id={request_id}" in caplog.text, "日志必须带 request_id"
        assert "user=7" in caplog.text, "日志必须带用户上下文"

    def test_rag_stream_unexpected_exception_is_redacted(self, client, caplog):
        """RAG 模式未预期异常 → 同一脱敏口径（retrieval/管线异常不得外泄）。"""
        import app.api.v1.endpoints.chat as chat_mod

        async def _boom_pipeline(**kwargs):
            raise RuntimeError(LEAKY_EXCEPTION)

        caplog.set_level(logging.ERROR)
        cleanup = _override(_user())
        try:
            with (
                patch.object(chat_mod, "AsyncSessionLocal", new=lambda: _AsyncCtx(MagicMock())),
                patch.object(chat_mod, "run_rag_pipeline", new=_boom_pipeline),
            ):
                r = client.post("/api/v1/chat/stream", json={"content": "你好"})
        finally:
            cleanup()

        assert r.status_code == 200, r.text
        body = r.text
        assert "event: error" in body, f"必须收到 error 帧，实际: {body[:400]}"
        assert GENERIC_SERVER_ERROR in body
        assert SECRET not in body, f"异常原文外泄: {body[:400]}"
        assert "Traceback" not in body
        assert "ValueError" not in body
        assert SECRET in caplog.text and "Traceback" in caplog.text

    def test_non_authorization_4xx_message_is_not_passed_through(self, client):
        """4xx AppError 不盲信 message：仓库存在 ValidationError(f"...{str(e)}") 形态。"""
        from app.agent.service import agent_service

        async def _validation(**kwargs):
            raise ValidationError(f"参数错误: {SECRET} {LEAKY_EXCEPTION}")
            yield  # pragma: no cover

        cleanup = _override(_user())
        try:
            with patch.object(agent_service, "chat", new=_validation):
                r = client.post(
                    "/api/v1/chat/stream",
                    json={"content": "你好", "useAgent": True},
                )
        finally:
            cleanup()

        assert GENERIC_SERVER_ERROR in r.text
        assert SECRET not in r.text, f"非授权类 4xx 文案被直通: {r.text[:400]}"
        assert "ValidationError" not in r.text


# ══════════════════════════════════════════════════════════════════════════
# 2 · 无组织 Agent：403 语义拒绝帧（用户可读），不透传内部 ValueError
# ══════════════════════════════════════════════════════════════════════════
class TestOrglessAgentRefusal:
    def test_orgless_agent_request_returns_refusal_frame(self, client: TestClient):
        """无组织 Agent 请求 → error 帧承载 403 语义文案，绝不外发内部文本。"""
        from app.agent.service import agent_service

        cleanup = _override(_user(org=None))
        try:
            with patch.object(agent_service, "chat", new=_orgless_agent):
                r = client.post(
                    "/api/v1/chat/stream",
                    json={"content": "你好", "useAgent": True},
                )
        finally:
            cleanup()

        assert r.status_code == 200, r.text
        body = r.text
        assert "event: error" in body, f"必须收到拒绝帧，实际: {body[:400]}"
        assert ORG_REFUSAL in body, f"缺少用户可读拒绝文案: {body[:400]}"
        assert "organization_id" not in body, f"内部参数名外泄: {body[:400]}"
        assert "ValueError" not in body, f"内部类名外泄: {body[:400]}"
        assert "AgentService" not in body, f"内部类名外泄: {body[:400]}"
        assert GENERIC_SERVER_ERROR not in body, "无组织是预期拒绝，不是服务器故障"


# ══════════════════════════════════════════════════════════════════════════
# 3 · AgentEvent 转发的 error / tool_error / thinking 文本同样过白名单/映射
# ══════════════════════════════════════════════════════════════════════════
class TestAgentEventErrorContent:
    def test_error_event_with_raw_exception_is_mapped(self, client):
        """``error`` 事件内容带原始异常（LLM Error: …）→ 通用模型失败文案。"""
        from app.agent.service import agent_service

        async def _fake_chat(**kwargs):
            yield AgentEvent(type="error", content=f"LLM Error: {SECRET}")

        cleanup = _override(_user())
        try:
            with patch.object(agent_service, "chat", new=_fake_chat):
                r = client.post(
                    "/api/v1/chat/stream",
                    json={"content": "你好", "useAgent": True},
                )
        finally:
            cleanup()

        assert "event: error" in r.text
        assert SECRET not in r.text, f"AgentEvent 异常文本外泄: {r.text[:400]}"
        assert "LLM Error" not in r.text

    def test_tool_error_event_with_raw_exception_is_mapped(self, client):
        """``tool_error`` 内容为 ``类名: 异常`` → 通用工具失败文案，保留工具名。"""
        from app.agent.service import agent_service

        async def _fake_chat(**kwargs):
            yield AgentEvent(
                type="tool_error",
                tool_name="search_knowledge_base",
                tool_call_id="call-1",
                content=f"ValueError: {SECRET}",
            )

        cleanup = _override(_user())
        try:
            with patch.object(agent_service, "chat", new=_fake_chat):
                r = client.post(
                    "/api/v1/chat/stream",
                    json={"content": "你好", "useAgent": True},
                )
        finally:
            cleanup()

        assert "event: tool_error" in r.text
        assert SECRET not in r.text, f"tool_error 异常文本外泄: {r.text[:400]}"
        assert "ValueError" not in r.text
        assert "search_knowledge_base" in r.text, "工具名属于可对外上下文"

    def test_error_whitelist_is_anchored_not_prefix(self, client):
        """固定文案白名单必须完整匹配：``Executor Agent: <secret>`` 不得穿透。"""
        from app.agent.service import agent_service

        async def _fake_chat(**kwargs):
            yield AgentEvent(type="error", content=f"Executor Agent: {SECRET}")

        cleanup = _override(_user())
        try:
            with patch.object(agent_service, "chat", new=_fake_chat):
                r = client.post(
                    "/api/v1/chat/stream",
                    json={"content": "你好", "useAgent": True},
                )
        finally:
            cleanup()

        assert SECRET not in r.text, f"前缀白名单被拼接尾部穿透: {r.text[:400]}"
        assert GENERIC_SERVER_ERROR in r.text

    def test_thinking_retry_context_is_redacted(self, client):
        """thinking 事件的重试上下文（原 last_error 拼接）不得外泄异常原文。"""
        from app.agent.service import agent_service

        async def _fake_chat(**kwargs):
            yield AgentEvent(
                type="thinking",
                content=(
                    f"重试步骤 (第 1/3 次) — timeout: RuntimeError: {SECRET} "
                    "at C:\\Users\\lenovo\\x.py"
                ),
                thinking_type="correction",
                retry_hint=f"RuntimeError: {SECRET}",
                retry_attempt=1,
            )

        cleanup = _override(_user())
        try:
            with patch.object(agent_service, "chat", new=_fake_chat):
                r = client.post(
                    "/api/v1/chat/stream",
                    json={"content": "你好", "useAgent": True},
                )
        finally:
            cleanup()

        assert "event: thinking" in r.text
        assert "重试步骤" in r.text, "重试提示本身应保留"
        assert "timeout" in r.text, "受控错误码应保留"
        assert SECRET not in r.text, f"thinking 重试上下文外泄: {r.text[:400]}"
        assert "RuntimeError" not in r.text
        assert "C:\\Users" not in r.text

    def test_honest_error_copy_is_preserved(self, client):
        """既有诚实文案（LLM 未配置）必须原样送达，不得被映射误伤。"""
        from app.agent.service import agent_service

        honest = "LLM 未配置，请检查 DEEPSEEK_API_KEY 环境变量。"

        async def _fake_chat(**kwargs):
            yield AgentEvent(type="error", content=honest)

        cleanup = _override(_user())
        try:
            with patch.object(agent_service, "chat", new=_fake_chat):
                r = client.post(
                    "/api/v1/chat/stream",
                    json={"content": "你好", "useAgent": True},
                )
        finally:
            cleanup()

        assert honest in r.text, f"诚实配置诊断被误伤: {r.text[:400]}"

    def test_honest_tool_denial_copy_is_preserved(self, client):
        """HITL 审批拒绝文案（含人工备注）必须保留——白名单不得误伤安全可读信息。"""
        from app.agent.service import agent_service

        denial = (
            "人工审批拒绝执行工具 execute_sql（approval_denied）：生产环境禁止操作"
        )

        async def _fake_chat(**kwargs):
            yield AgentEvent(
                type="tool_error",
                tool_name="execute_sql",
                tool_call_id="call-2",
                content=denial,
                error_type="approval_denied",
            )

        cleanup = _override(_user())
        try:
            with patch.object(agent_service, "chat", new=_fake_chat):
                r = client.post(
                    "/api/v1/chat/stream",
                    json={"content": "你好", "useAgent": True},
                )
        finally:
            cleanup()

        assert "生产环境禁止操作" in r.text, f"HITL 拒绝备注被误伤: {r.text[:400]}"


# ══════════════════════════════════════════════════════════════════════════
# 4 · /agent/chat 出口同一口径（AgentEvent.to_sse_dict 全量转发路径）
# ══════════════════════════════════════════════════════════════════════════
class TestAgentChatEndpointRedaction:
    def test_agent_chat_error_event_is_mapped(self, client):
        """/agent/chat 的 error 事件同样不得外发原始异常文本。"""
        from app.agent.service import agent_service

        async def _fake_chat(**kwargs):
            yield AgentEvent(type="error", content=f"LLM Error: {SECRET}")

        cleanup = _override(_user())
        try:
            with patch.object(agent_service, "chat", new=_fake_chat):
                r = client.post("/api/v1/agent/chat", json={"query": "你好"})
        finally:
            cleanup()

        assert r.status_code == 200, r.text
        assert SECRET not in r.text, f"/agent/chat 异常文本外泄: {r.text[:400]}"
        assert "LLM Error" not in r.text

    def test_agent_chat_thinking_retry_hint_is_redacted(self, client):
        """/agent/chat 的 thinking.retry_hint 同样不得携带异常原文。"""
        from app.agent.service import agent_service

        async def _fake_chat(**kwargs):
            yield AgentEvent(
                type="thinking",
                content=f"重试步骤 (第 1/3 次) — timeout: RuntimeError: {SECRET}",
                thinking_type="correction",
                retry_hint=f"RuntimeError: {SECRET}",
                retry_attempt=1,
            )

        cleanup = _override(_user())
        try:
            with patch.object(agent_service, "chat", new=_fake_chat):
                r = client.post("/api/v1/agent/chat", json={"query": "你好"})
        finally:
            cleanup()

        assert r.status_code == 200, r.text
        assert "重试步骤" in r.text
        assert "timeout" in r.text
        assert SECRET not in r.text, f"/agent/chat retry_hint 外泄: {r.text[:400]}"
        assert "RuntimeError" not in r.text


# ══════════════════════════════════════════════════════════════════════════
# 5 · 诊断载荷（run_report / execution_context）脱敏
# ══════════════════════════════════════════════════════════════════════════
class TestDiagnosticRedaction:
    def test_execution_context_failures_and_decisions_are_redacted(self, client):
        """execution_context 保留结构/token，失败细节与反思异常原文替换。"""
        from app.agent.service import agent_service

        async def _fake_chat(**kwargs):
            yield AgentEvent(
                type="execution_context",
                content={
                    "failures": [f"[s1] RuntimeError: {SECRET}"],
                    "decisions": [
                        {
                            "phase": "reflect",
                            "action": "error",
                            "reasoning": f"Error code: 401 - {SECRET}",
                        }
                    ],
                    "total_tokens": 12,
                },
                plan_id="p1",
                plan_progress=1.0,
            )

        cleanup = _override(_user())
        try:
            with patch.object(agent_service, "chat", new=_fake_chat):
                r = client.post(
                    "/api/v1/chat/stream",
                    json={"content": "你好", "useAgent": True},
                )
        finally:
            cleanup()

        assert "event: execution_context" in r.text
        assert '"total_tokens": 12' in r.text, "可观测字段必须保留"
        assert "内部失败细节已省略" in r.text
        assert SECRET not in r.text, f"诊断载荷异常原文外泄: {r.text[:500]}"
        assert "Error code: 401" not in r.text
        assert "RuntimeError" not in r.text

    def test_run_report_traceback_path_sql_and_failure_lines_are_redacted(self, client):
        """run_report 文本：堆栈/路径/SQL/失败行脱敏，报告结构保留。"""
        from app.agent.service import agent_service

        report = (
            "=== Run Report [t1] ===\n"
            "Traceback (most recent call last):\n"
            '  File "C:\\Users\\lenovo\\app\\x.py", line 1, in f\n'
            f"RuntimeError: {SECRET}\n"
            "\n"
            "## Tool calls\n"
            f"  - tool (s1)  ERROR=ValueError: {SECRET}\n"
            f"  FAILURE: Connection error. {SECRET}\n"
            "=== end report ==="
        )

        async def _fake_chat(**kwargs):
            yield AgentEvent(type="run_report", content=report)

        cleanup = _override(_user())
        try:
            with patch.object(agent_service, "chat", new=_fake_chat):
                r = client.post(
                    "/api/v1/chat/stream",
                    json={"content": "你好", "useAgent": True},
                )
        finally:
            cleanup()

        assert "event: run_report" in r.text
        assert "=== end report ===" in r.text, "报告结构必须保留"
        assert "内部堆栈已省略" in r.text
        assert "内部失败细节已省略" in r.text
        assert SECRET not in r.text, f"run_report 异常原文外泄: {r.text[:600]}"
        assert "Traceback" not in r.text
        assert "C:\\Users" not in r.text
        assert "ValueError" not in r.text
        assert "Connection error" not in r.text


# ══════════════════════════════════════════════════════════════════════════
# 6 · 映射/脱敏函数的单元判别（正则边界 + 诚实文案保真）
# ══════════════════════════════════════════════════════════════════════════
class TestMappingUnits:
    def test_real_traceback_with_colon_is_redacted(self):
        text = (
            "Traceback (most recent call last):\n"
            '  File "x.py", line 1, in <module>\n'
            f"RuntimeError: {SECRET}\n"
            "\n"
            "keep me"
        )
        out = public_diagnostic_content(text)
        assert "Traceback" not in out
        assert SECRET not in out
        assert "keep me" in out, "空行之后的正文不得被吞掉"

    def test_sql_prose_is_not_false_positive(self):
        text = "please select the file from the list"
        assert public_diagnostic_content(text) == text

    def test_relative_source_path_is_redacted(self):
        out = public_diagnostic_content("backend/app/agent/executor.py")
        assert "[路径已省略]" in out
        assert "executor.py" not in out

    def test_rag_error_whitelist(self):
        honest = "当前用户未加入任何组织，无法进行知识库问答；请先加入组织后重试"
        assert public_rag_error_content(honest) == honest
        assert public_rag_error_content(f"ValueError: {SECRET}") == GENERIC_RAG_ERROR

    def test_authorization_message_passes_but_other_4xx_does_not(self):
        assert (
            public_exception_message(AuthorizationError("无权访问该会话"))
            == "无权访问该会话"
        )
        leaked = public_exception_message(ValidationError(f"bad {SECRET}"))
        assert SECRET not in leaked
        assert leaked == "服务器处理出错，请稍后重试"

    def test_resolve_request_id_falls_back_to_request_state(self):
        request = SimpleNamespace(state=SimpleNamespace(request_id="rid-fallback"))
        assert resolve_request_id(request) == "rid-fallback"

    def test_error_whitelist_rejects_prefixed_injection(self):
        from app.core.error_mapping import public_agent_event_content

        assert (
            public_agent_event_content("error", f"Executor Agent: {SECRET}")
            == "服务器处理出错，请稍后重试"
        )
        assert (
            public_agent_event_content(
                "error", "Executor Agent: 等待执行消息超时"
            )
            == "Executor Agent: 等待执行消息超时"
        )

    def test_run_report_renderer_masks_failure_decision_reasoning(self):
        """真实渲染器：reflect/unavailable 的任意异常原文不得进入 run_report。"""
        from app.agent.exec_context import ExecutionContext
        from app.agent.run_report import build_run_report, render_run_report_text

        raw = "peer closed connection without sending complete message body"
        ctx = ExecutionContext(query="q")
        ctx.record_decision(
            "reflect", "unavailable", f"反思评估跳过（评估服务异常）: {raw}"
        )
        ctx.record_decision("review", "error", f"对抗审查未完成（审查服务异常）：{raw}")

        text = render_run_report_text(build_run_report(ctx, "answer"))
        assert raw not in text, "渲染器必须掩码失败态 decision reasoning"
        assert "内部失败细节已省略" in text
        # 出口脱敏（纵深防御）同样不得让原文复活
        assert raw not in public_diagnostic_content(text)
        # review/error 走结构化 execution_context 路径（_decision_lines 只渲染
        # plan/execute/reflect）——同样整体占位，不得外发原文。
        structured = public_diagnostic_content(ctx.to_dict())
        assert raw not in str(structured)
        assert "内部失败细节已省略" in str(structured)

    def test_invalid_request_id_is_rejected(self):
        request = SimpleNamespace(state=SimpleNamespace(request_id="bad id\ninject"))
        assert resolve_request_id(request) is None

    def test_url_with_app_path_is_not_over_redacted(self):
        text = "see https://example.com/app/foo for details"
        assert public_diagnostic_content(text) == text


# ══════════════════════════════════════════════════════════════════════════
# 7 · 生产端：重试提示不得把上一条错误原文拼进 thinking/retry_hint
# ══════════════════════════════════════════════════════════════════════════
class _LeakyRetryExecutor(Executor):
    """最小 Executor 替身：产出可重试的 tool_error（content 含异常原文）。"""

    async def _execute_step_once(
        self,
        step,
        plan,
        history,
        organization_id,
        user_id,
        enable_thinking,
        error_context="",
        ctx=None,
    ):
        yield AgentEvent(
            type="tool_error",
            tool_name="fake_tool",
            content=f"LLM 调用失败: RuntimeError: {SECRET}",
            error_type="timeout",
            plan_id=plan.id,
            plan_step_id=step.id,
        )


@pytest.mark.asyncio
async def test_retry_thinking_and_retry_hint_do_not_embed_raw_error(monkeypatch):
    """executor 重试提示只带受控错误码；异常原文只进日志（B1 生产端修复）。"""
    from app.agent.config import AgentConfig
    from app.agent.memory_bridge import AgentMemoryBridge
    from app.agent.planner import Plan, PlanStep

    monkeypatch.setattr("app.agent.executor.compute_backoff", lambda policy, n: 0)

    executor = _LeakyRetryExecutor(
        openai_client=MagicMock(),
        config=AgentConfig(max_retries_per_step=1),
        memory_bridge=AgentMemoryBridge(
            agent_id="w0-5-retry", organization_id=1, user_id=0
        ),
    )
    plan = Plan(id="p1", goal="g", steps=[PlanStep(id="s1", description="step")])

    events = [
        event
        async for event in executor._execute_step_with_retry(
            step=plan.steps[0],
            plan=plan,
            history=None,
            organization_id=1,
            user_id=0,
            enable_thinking=True,
        )
    ]

    thinking = [e for e in events if e.type == "thinking" and "重试步骤" in e.content]
    assert thinking, "应产生重试提示"
    content = thinking[0].content
    hint = thinking[0].retry_hint
    assert "timeout" in content, "受控错误码应保留"
    assert SECRET not in content, f"thinking 外泄异常原文: {content}"
    assert "RuntimeError" not in content
    assert SECRET not in hint and "RuntimeError" not in hint, (
        f"retry_hint 外泄异常原文: {hint}"
    )
