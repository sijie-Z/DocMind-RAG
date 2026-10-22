"""issue #467：LLM 不可达 fail-fast —— RAG 调用点判别性测试。

修前行为（origin/develop @ e9990bee，故障注入 QA 2026-10-22）：
- ``query_processor.rewrite_query_llm`` 与 ``reranker._rerank_llm`` 直接调用
  openai SDK，SDK 默认对连接错误重试 2 次（退避合计 ≈1.5s）；重排 LLM 兜底
  再叠加 8s 读超时，RAG 聊天要 ~11s 才暴露「LLM 不可达」；
- 本文件的假客户端按 SDK 语义对连接错误重试（``max_retries`` 默认 2），
  修前调用点不传 ``with_options(max_retries=0)`` → 三次触达、耗时成倍。

修复后契约（``app/core/llm_failfast.create_chat_completion`` 接线）：
- 连接类错误（APIConnectionError 非超时 / ConnectTimeout）→ 零 SDK 重试，
  立即降级：改写返回原始查询，重排返回 ``reason="llm_unreachable"``，
  生成链路如实返回错误文案（不伪造答案）；
- 一次判定不可达后进入短冷却（``LLM_UNREACHABLE_COOLDOWN_SECONDS``）：同一
  请求内串行的改写 → 重排 → 生成不再各自等一次连接超时；
- 读阶段超时（ReadTimeout）**不得**误判为不可达（见
  test_issue467_llm_failfast.py 的分类器用例）——调用点只按原有语义降级；
- 连接建立后的传输中断（RemoteProtocolError / ReadError / WriteError）不判不可达：
  保留有限重试（SDK 默认语义），一次瞬时断连不得放大成用户可见故障；
- HTTP 5xx/429 等瞬时状态错误保留有限重试（``LLM_TRANSIENT_MAX_RETRIES``）；
- 连接阶段使用独立短超时（``LLM_CONNECT_TIMEOUT_SECONDS``），不占用读预算。

变异点（对应测试转红）：
M1 调用点去掉 ``with_options(max_retries=0)``（回到 SDK 默认重试）→
   test_rewrite_connection_error_does_not_retry /
   test_rerank_llm_connection_error_returns_unreachable 的 attempts==1 与
   耗时上界红；
M2 把 APITimeoutError（ReadTimeout）判为不可达 → 分类器用例红（另一文件），
   本文件 test_rerank_read_timeout... 会得到 llm_unreachable 而非 llm_error；
M3 连接阶段未用独立短超时（随读预算）→
   test_connect_timeout_is_independent_of_read_budget 红；
M4 删掉 5xx 重试 → test_transient_status_error_is_retried 红。
"""

import asyncio
import time
from types import SimpleNamespace

import httpx
import openai
import pytest

import app.core.llm_failfast as llm_failfast_mod
from app.core.config import settings
from app.rag.pipeline import RAGPipeline
from app.rag.query_processor import rewrite_query_llm
from app.rag.reranker import _rerank_llm, rerank

_MODEL = "deepseek-chat-test"


@pytest.fixture(autouse=True)
def _isolate_reachability_cache():
    """不可达负缓存是进程级状态：每个用例前后清空，避免跨用例污染。"""
    llm_failfast_mod._unreachable_until.clear()
    yield
    llm_failfast_mod._unreachable_until.clear()


def _request() -> httpx.Request:
    return httpx.Request("POST", "http://llm.invalid/v1/chat/completions")


def _connection_error() -> openai.APIConnectionError:
    """连接被拒 / 主机不可达（SDK 对 httpx ConnectError 的包装）。"""
    return openai.APIConnectionError(request=_request())


def _connection_error_with_cause(cause: BaseException) -> openai.APIConnectionError:
    """把 openai 连接异常与底层传输 cause 串起来（与 SDK raise ... from 同形）。"""
    try:
        raise openai.APIConnectionError(request=_request()) from cause
    except openai.APIConnectionError as exc:
        return exc


def _timeout_error(cause: BaseException) -> openai.APITimeoutError:
    """把 openai 超时异常与底层 httpx cause 串起来（与 SDK raise ... from 同形）。"""
    try:
        raise openai.APITimeoutError(request=_request()) from cause
    except openai.APITimeoutError as exc:
        return exc


def _status_error(code: int) -> openai.APIStatusError:
    response = httpx.Response(code, request=_request())
    return openai.APIStatusError(f"status {code}", response=response, body=None)


def _ok_response(content: str = "[1]"):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
        usage=None,
        model=_MODEL,
    )


class _SDKView:
    """with_options 副本：与生产 SDK 一样，重试配置只作用于副本，原名不变。"""

    def __init__(self, parent, max_retries: int):
        self._parent = parent
        self.max_retries = max_retries
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        return await self._parent._create_with_retries(kwargs, self.max_retries)


class _FakeSDKClient:
    """按 openai SDK 语义模拟重试的假客户端。

    - ``create`` 在 ``max_retries`` 次失败后继续重试（默认 2，与 SDK 一致）；
    - ``with_options(max_retries=0, ...)`` 返回**独立副本**（生产语义）并记录参数；
    - 记录每次 ``create`` 的 kwargs，供判别性断言。
    """

    def __init__(
        self,
        results,
        max_retries: int = 2,
        attempt_delay: float = 0.0,
        base_url: str = "http://llm.invalid/v1",
    ):
        self._results = list(results)
        self.max_retries = max_retries
        self.attempt_delay = attempt_delay
        self.base_url = base_url
        self.attempts = 0
        self.with_options_calls: list[dict] = []
        self.create_calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        return await self._create_with_retries(kwargs, self.max_retries)

    async def _create_with_retries(self, kwargs, max_retries: int):
        self.create_calls.append(kwargs)
        for retry in range(max_retries + 1):
            result = self._results[min(self.attempts, len(self._results) - 1)]
            self.attempts += 1
            if self.attempt_delay:
                await asyncio.sleep(self.attempt_delay)
            if not isinstance(result, BaseException):
                return result
            if retry < max_retries:
                continue
            raise result
        raise AssertionError("unreachable")  # pragma: no cover

    def with_options(self, **kwargs):
        self.with_options_calls.append(kwargs)
        return _SDKView(self, kwargs.get("max_retries", self.max_retries))


def _hits() -> list[dict]:
    return [
        {"chunk_id": "d1_c1", "filename": "a.md", "content": "第一段正文"},
        {"chunk_id": "d2_c2", "filename": "b.md", "content": "第二段正文"},
    ]


# ── 查询改写 ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rewrite_connection_error_does_not_retry(monkeypatch):
    """连接不可达：改写站零重试，立即返回原始查询。

    变异点 M1：调用点不传 ``max_retries=0`` → SDK 语义下触达 3 次、耗时 ≈3×
    单次延迟，本用例的 attempts==1 与耗时上界同时红。
    """
    monkeypatch.setattr(settings, "RAG_ENABLE_QUERY_REWRITE", True)
    client = _FakeSDKClient([_connection_error()], max_retries=2, attempt_delay=0.2)

    started = time.perf_counter()
    result = await rewrite_query_llm("问题", client, _MODEL)
    elapsed = time.perf_counter() - started

    assert result == ["问题"], "不可达时降级为原始查询"
    assert client.attempts == 1, (
        f"连接类错误必须零 SDK 重试（实际触达 {client.attempts} 次；"
        "变异点 M1：去掉 with_options(max_retries=0)）"
    )
    assert client.with_options_calls, "必须通过 with_options 关闭 SDK 重试并设置连接阶段超时"
    assert client.with_options_calls[0]["max_retries"] == 0
    # 主判据是 attempts（确定性，修前为 3 次）；墙钟仅作粗略上界——阈值放宽
    # 到 1s 以避免 CI 调度抖动 flake，时序判别由 attempts 承担。
    assert elapsed < 1.0, f"连接不可达应在 <1.0s 内降级，实际 {elapsed:.2f}s"


@pytest.mark.asyncio
async def test_rewrite_read_timeout_is_not_fast_failed(monkeypatch):
    """读阶段超时（可恢复的慢）→ 仍降级为原始查询，但不得被当成「不可达」。

    判别方式：底层 ``create`` 只触达 1 次（helper 不重试超时），且调用点
    走通用失败分支而非 fast-fail 分支——由返回结果（同为 [query]）无法区分，
    这里用 client 记录 + helper 分类器用例（另一文件）共同钉住。
    """
    monkeypatch.setattr(settings, "RAG_ENABLE_QUERY_REWRITE", True)
    client = _FakeSDKClient(
        [_timeout_error(httpx.ReadTimeout("read timed out"))], max_retries=2, attempt_delay=0.05
    )

    result = await rewrite_query_llm("问题", client, _MODEL)

    assert result == ["问题"]
    assert client.attempts == 1, "读超时已花掉完整读预算，helper 不再叠加 SDK 重试"


# ── 重排 LLM 兜底 ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rerank_llm_connection_error_returns_unreachable():
    """连接不可达：重排 LLM 兜底零重试并给出可判别的 reason。

    变异点 M1：不关闭 SDK 重试 → attempts=3、耗时超上界；
    变异点 M2：误把超时当不可达不影响本用例（本用例是 APIConnectionError）。
    """
    client = _FakeSDKClient([_connection_error()], max_retries=2, attempt_delay=0.2)

    started = time.perf_counter()
    ordered, reason = await _rerank_llm("查询", _hits(), top_n=2, llm_client=client)
    elapsed = time.perf_counter() - started

    assert ordered is None
    assert reason == "llm_unreachable", f"需要可判别的降级原因，实际 {reason!r}"
    assert client.attempts == 1, f"连接类错误必须零 SDK 重试（实际 {client.attempts} 次）"
    # 主判据是 attempts（确定性，修前为 3 次）；墙钟仅作粗略上界——阈值放宽
    # 到 1s 以避免 CI 调度抖动 flake，时序判别由 attempts 承担。
    assert elapsed < 1.0, f"连接不可达应在 <1.0s 内降级，实际 {elapsed:.2f}s"


@pytest.mark.asyncio
async def test_rerank_llm_read_timeout_degrades_as_llm_error():
    """读阶段超时是可恢复的慢：不得归入 llm_unreachable，按原语义降级。"""
    client = _FakeSDKClient(
        [_timeout_error(httpx.ReadTimeout("slow model"))], max_retries=2, attempt_delay=0.0
    )

    ordered, reason = await _rerank_llm("查询", _hits(), top_n=2, llm_client=client)

    assert ordered is None
    assert reason is not None and reason.startswith("llm_error:"), (
        f"读超时必须按可恢复失败处理（llm_error），实际 {reason!r}"
    )
    assert client.attempts == 1, "helper 不对读超时重试（避免 8s×N 的放大）"


@pytest.mark.asyncio
async def test_rerank_full_chain_surfaces_unreachable_reason(monkeypatch):
    """全链视角：本地重排关闭 + 智谱无凭据 + LLM 不可达 → degraded 且原因可判。"""
    monkeypatch.setattr(settings, "RAG_ENABLE_RERANKER", True)
    monkeypatch.setattr(settings, "RERANK_USE_LOCAL", False)
    monkeypatch.setattr(settings, "RERANK_API_KEY", None)
    monkeypatch.setattr(settings, "RERANK_API_URL", None)
    monkeypatch.setattr(settings, "RAG_RERANK_API_KEY", None)
    monkeypatch.setattr(settings, "RAG_RERANK_API_URL", None)

    client = _FakeSDKClient([_connection_error()], max_retries=2, attempt_delay=0.2)
    hits = _hits()

    started = time.perf_counter()
    result, info = await rerank("查询", hits, llm_client=client)
    elapsed = time.perf_counter() - started

    assert result == hits, "降级必须保持原始顺序"
    assert info["status"] == "degraded"
    assert info["attempts"][-1]["path"] == "llm"
    assert info["attempts"][-1]["reason"] == "llm_unreachable"
    # 主判据是 attempts（确定性，修前为 3 次）；墙钟仅作粗略上界——阈值放宽
    # 到 1s 以避免 CI 调度抖动 flake，时序判别由 attempts 承担。
    assert elapsed < 1.0, f"整链应在 <1.0s 内降级，实际 {elapsed:.2f}s"


@pytest.mark.asyncio
async def test_transient_status_error_is_retried(monkeypatch):
    """瞬时服务端错误（503）保留有限重试：首次 503、第二次成功。"""
    monkeypatch.setattr(settings, "LLM_TRANSIENT_MAX_RETRIES", 1)
    monkeypatch.setattr(settings, "LLM_TRANSIENT_RETRY_BACKOFF_SECONDS", 0.0)
    client = _FakeSDKClient([_status_error(503), _ok_response("[2, 1]")], max_retries=2)

    ordered, reason = await _rerank_llm("查询", _hits(), top_n=2, llm_client=client)

    assert reason is None and ordered is not None, "503 属瞬时故障，重试后应成功"
    assert client.attempts == 2, f"应恰好重试一次（实际触达 {client.attempts} 次）"
    assert [h["chunk_id"] for h in ordered] == ["d2_c2", "d1_c1"]


@pytest.mark.asyncio
async def test_connect_timeout_is_independent_of_read_budget(monkeypatch):
    """连接阶段短超时与读预算分离，且随配置变化（M3 判别点）。"""
    monkeypatch.setattr(settings, "LLM_CONNECT_TIMEOUT_SECONDS", 0.7)
    monkeypatch.setattr(settings, "RAG_RERANK_TIMEOUT_SECONDS", 8.0)
    client = _FakeSDKClient([_ok_response("[1]")], max_retries=2)

    await _rerank_llm("查询", _hits(), top_n=2, llm_client=client)

    timeout = client.with_options_calls[0]["timeout"]
    assert timeout.connect == pytest.approx(0.7), "连接阶段必须使用独立可配置短超时"
    assert timeout.read == pytest.approx(8.0), "读阶段沿用重排超时预算（不压缩慢模型空间）"


# ── 进程级负缓存 & 生成链路（用户可见的错误暴露点）──────────────────


@pytest.mark.asyncio
async def test_detected_unreachable_short_circuits_later_call_sites(monkeypatch):
    """改写站判定不可达后，同一请求内的重排调用点不再各自等一次连接超时。

    变异点 M5（负缓存缺失）：重排替身会被触达一次（attempts == 1），红。
    """
    monkeypatch.setattr(settings, "LLM_UNREACHABLE_COOLDOWN_SECONDS", 5.0)
    rewrite_client = _FakeSDKClient([_connection_error()], max_retries=2, attempt_delay=0.2)
    assert await rewrite_query_llm("问题", rewrite_client, _MODEL) == ["问题"]
    assert rewrite_client.attempts == 1

    healthy = _FakeSDKClient([_ok_response("[1]")], max_retries=2)
    ordered, reason = await _rerank_llm("查询", _hits(), top_n=2, llm_client=healthy)

    assert ordered is None and reason == "llm_unreachable"
    assert healthy.attempts == 0, "负缓存生效时不得再触达 LLM"


@pytest.mark.asyncio
async def test_chat_stream_connection_error_fails_fast(monkeypatch):
    """生成链路同样快速失败：零 SDK 重试、不伪造回答（issue #467 修复前为
    SDK 默认重试 + 长读超时）。"""
    monkeypatch.setattr(settings, "LLM_UNREACHABLE_COOLDOWN_SECONDS", 5.0)
    client = _FakeSDKClient([_connection_error()], max_retries=2, attempt_delay=0.2)
    pipeline = RAGPipeline(openai_client=client)

    started = time.perf_counter()
    chunks = [
        chunk
        async for chunk in pipeline.chat_stream(
            "问题", [], enable_compression=False, enable_masking=False
        )
    ]
    elapsed = time.perf_counter() - started

    assert chunks == ["系统处理异常，请重试"], "失败必须如实暴露，不得伪造答案"
    assert client.attempts == 1, "连接类错误零 SDK 重试"
    # 主判据是 attempts（确定性，修前为 3 次）；墙钟仅作粗略上界——阈值放宽
    # 到 1s 以避免 CI 调度抖动 flake，时序判别由 attempts 承担。
    assert elapsed < 1.0, f"生成链路连接失败应在 <1.0s 内暴露，实际 {elapsed:.2f}s"


@pytest.mark.asyncio
async def test_hyde_connection_error_fast_fails(monkeypatch):
    """HyDE 也是可选增强：连接不可达时立即跳过，不等满 1.5s 外层预算。"""
    from app.rag.query_processor import generate_hyde_doc

    client = _FakeSDKClient([_connection_error()], max_retries=2, attempt_delay=0.2)

    started = time.perf_counter()
    doc = await generate_hyde_doc("问题", "factual", client, _MODEL)
    elapsed = time.perf_counter() - started

    assert doc == "", "不可达时 HyDE 降级为空文档"
    assert client.attempts == 1, "连接类错误零 SDK 重试"
    assert elapsed < 1.0, f"HyDE 应在 <1.0s 内降级，实际 {elapsed:.2f}s"


@pytest.mark.asyncio
async def test_rerank_post_connect_disconnect_is_retried(monkeypatch):
    """连接已建立后的传输中断（keep-alive 失效）→ 有限重试后成功，不得判死端点。

    评审 blocking-1 的调用点判别：SDK 默认会重试 RemoteProtocolError 并常在
    第二次成功；helper 必须保留该语义（reason 不应是 llm_unreachable）。
    """
    monkeypatch.setattr(settings, "LLM_TRANSIENT_MAX_RETRIES", 1)
    monkeypatch.setattr(settings, "LLM_TRANSIENT_RETRY_BACKOFF_SECONDS", 0.0)
    exc = _connection_error_with_cause(httpx.RemoteProtocolError("server disconnected"))
    client = _FakeSDKClient([exc, _ok_response("[2, 1]")], max_retries=2)

    ordered, reason = await _rerank_llm("查询", _hits(), top_n=2, llm_client=client)

    assert reason is None and ordered is not None, "传输中断重试后应成功"
    assert [h["chunk_id"] for h in ordered] == ["d2_c2", "d1_c1"]
    assert client.attempts == 2, "初始 + 1 次重试"
