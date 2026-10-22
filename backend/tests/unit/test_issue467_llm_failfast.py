"""issue #467：``app.core.llm_failfast`` 单元契约。

分类器纪律（issue 原文与任务红线）：
- ``APIConnectionError``（非超时）＝连接被拒 / DNS 失败 / 主机不可达 → 不可达；
- ``APITimeoutError`` 只有 cause 链明确为 ``ConnectError``/``ConnectTimeout``
  （连接阶段未建立）才算不可达；
- ``ReadTimeout``/``WriteTimeout``/``PoolTimeout`` 是**可恢复的慢**，
  绝不按不可达快速失败——否则一次慢响应会把正常模型永久降级。

调用助手纪律：
- SDK 级重试关闭（``with_options(max_retries=0)``），连接类不可达零重试；
- 连接已建立后的传输中断（RemoteProtocolError/ReadError/WriteError）与 5xx/429
  保留有限重试（SDK 默认语义）；读/写超时零重试；
- 连接阶段独立短超时（``LLM_CONNECT_TIMEOUT_SECONDS``），读阶段沿用调用方预算；
- 不可达负缓存按 ``client.base_url`` 区分端点，任一调用成功即清除。

变异点：
M1 分类器把任意 APITimeoutError 都判为不可达 →
   test_read_timeout_is_not_unreachable / test_write_timeout... 红；
M2 helper 未关闭 SDK 重试 → test_unreachable_..._without_sdk_retry 的
   with_options(max_retries=0) 断言红（行为上由调用点测试兜底）；
M3 未传连接阶段短超时 → test_connect_timeout_comes_from_settings 红；
M4 瞬时状态不重试 → test_transient_status_error_is_retried 红；
M5 不可达负缓存缺失/读超时也写入缓存 →
   test_unreachable_mark_short_circuits_next_call /
   test_read_timeout_does_not_mark_endpoint_unreachable 红。
"""

from types import SimpleNamespace

import httpx
import openai
import pytest

import app.core.llm_failfast as llm_failfast_mod
from app.core.config import settings
from app.core.llm_failfast import (
    LLMUnreachableError,
    create_chat_completion,
    is_llm_unreachable_error,
)


@pytest.fixture(autouse=True)
def _isolate_reachability_cache():
    """负缓存是进程级状态：每个用例前后清空，避免跨用例污染。"""
    llm_failfast_mod._unreachable_until.clear()
    yield
    llm_failfast_mod._unreachable_until.clear()


def _request() -> httpx.Request:
    return httpx.Request("POST", "http://llm.invalid/v1/chat/completions")


def _chained(exc: BaseException, cause: BaseException) -> BaseException:
    try:
        raise exc from cause
    except BaseException as raised:  # noqa: BLE001 - 构造 cause 链的测试辅助
        return raised


def _status_error(code: int) -> openai.APIStatusError:
    response = httpx.Response(code, request=_request())
    return openai.APIStatusError(f"status {code}", response=response, body=None)


class TestClassifier:
    def test_bare_apiconnectionerror_is_unreachable(self):
        """SDK 对连接被拒/DNS 失败的包装（cause 可能被吞掉）→ 不可达。"""
        assert is_llm_unreachable_error(openai.APIConnectionError(request=_request())) is True

    def test_connect_error_cause_is_unreachable(self):
        exc = _chained(openai.APIConnectionError(request=_request()), httpx.ConnectError("refused"))
        assert is_llm_unreachable_error(exc) is True

    def test_connect_timeout_cause_is_unreachable(self):
        """连接阶段超时＝连接未建立（死主机 SYN 被丢弃的场景）。"""
        exc = _chained(openai.APITimeoutError(request=_request()), httpx.ConnectTimeout("no route"))
        assert is_llm_unreachable_error(exc) is True

    def test_proxy_error_cause_is_unreachable(self):
        """代理不可达＝本地出口路径不可用，属于连接阶段失败。"""
        exc = _chained(
            openai.APIConnectionError(request=_request()), httpx.ProxyError("proxy down")
        )
        assert is_llm_unreachable_error(exc) is True

    def test_post_connect_transport_errors_are_not_unreachable(self):
        """连接已建立后的瞬时中断（keep-alive 失效/连接重置）不得判死端点。

        评审 blocking-1：openai 把 RemoteProtocolError/ReadError/WriteError 也
        映射为 APIConnectionError；SDK 默认会重试并常在第二次成功。判为不可达
        会把一次抖动放大成 5s 冷却窗口内的用户可见故障。
        """
        for cause in (
            httpx.RemoteProtocolError("server disconnected"),
            httpx.ReadError("connection reset"),
            httpx.WriteError("broken pipe"),
        ):
            exc = _chained(openai.APIConnectionError(request=_request()), cause)
            assert is_llm_unreachable_error(exc) is False, type(cause).__name__

    def test_timeout_without_cause_is_not_unreachable(self):
        """无 cause 的超时保守判为可恢复（不得默认当不可达）。"""
        exc = openai.APITimeoutError(request=_request())
        assert is_llm_unreachable_error(exc) is False

    def test_unknown_cause_defaults_to_recoverable(self):
        """白名单策略：未识别的 cause 链不判死端点（宁可多一次重试）。

        评审复核 Finding A：非超时 APIConnectionError 的旧默认分支是黑名单，
        DecodingError / TooManyRedirects / UnsupportedProtocol / CloseError 等
        未识别 cause 会被误判为不可达。现改为「连接阶段证据或无 cause」白名单。
        """
        for cause in (
            httpx.DecodingError("bad encoding"),
            httpx.TooManyRedirects("redirect loop"),
            httpx.UnsupportedProtocol("unsupported scheme"),
            httpx.CloseError("connection closed"),
        ):
            exc = _chained(openai.APIConnectionError(request=_request()), cause)
            assert is_llm_unreachable_error(exc) is False, type(cause).__name__

    def test_read_timeout_is_not_unreachable(self):
        """读阶段超时＝服务慢，可恢复——不得误判（issue #467 红线）。"""
        exc = _chained(openai.APITimeoutError(request=_request()), httpx.ReadTimeout("slow"))
        assert is_llm_unreachable_error(exc) is False

    def test_write_timeout_is_not_unreachable(self):
        exc = _chained(openai.APITimeoutError(request=_request()), httpx.WriteTimeout("slow"))
        assert is_llm_unreachable_error(exc) is False

    def test_pool_timeout_is_not_unreachable(self):
        """连接池排队超时是本地资源瞬时紧张，与端点可达性无关。"""
        exc = _chained(openai.APITimeoutError(request=_request()), httpx.PoolTimeout("busy"))
        assert is_llm_unreachable_error(exc) is False

    def test_status_error_is_not_unreachable(self):
        """5xx/429 是服务端瞬时故障（可重试），不是不可达。"""
        assert is_llm_unreachable_error(_status_error(503)) is False

    def test_arbitrary_exception_is_not_unreachable(self):
        assert is_llm_unreachable_error(ValueError("boom")) is False
        assert is_llm_unreachable_error(TimeoutError("local")) is False


class _RecordingView:
    """with_options 副本视图：与生产 SDK 一样，重试/超时配置只作用于副本。"""

    def __init__(self, parent):
        self._parent = parent
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        return await self._parent._create(**kwargs)


class _RecordingClient:
    """最小 SDK 形状：记录 with_options / create 调用并按脚本返回或抛错。"""

    def __init__(self, results, base_url: str = "http://llm.invalid/v1"):
        self._results = list(results)
        self.base_url = base_url
        self.attempts = 0
        self.with_options_calls: list[dict] = []
        self.create_calls: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs):
        self.create_calls.append(kwargs)
        result = self._results[min(self.attempts, len(self._results) - 1)]
        self.attempts += 1
        if isinstance(result, BaseException):
            raise result
        return result

    def with_options(self, **kwargs):
        self.with_options_calls.append(kwargs)
        return _RecordingView(self)


@pytest.mark.asyncio
async def test_unreachable_raises_domain_error_without_sdk_retry():
    client = _RecordingClient([openai.APIConnectionError(request=_request())])

    with pytest.raises(LLMUnreachableError):
        await create_chat_completion(client, read_timeout=8.0, model="m", messages=[])

    assert client.attempts == 1, "连接类错误零重试（SDK 默认重试必须被关闭）"
    options = client.with_options_calls[0]
    assert options["max_retries"] == 0
    assert options["timeout"].read == pytest.approx(8.0)


@pytest.mark.asyncio
async def test_read_timeout_propagates_as_recoverable_timeout():
    """读超时原样上抛：调用方按既有降级路径处理，不得包装成 LLMUnreachableError。"""
    exc = _chained(openai.APITimeoutError(request=_request()), httpx.ReadTimeout("slow"))
    client = _RecordingClient([exc])

    with pytest.raises(openai.APITimeoutError) as caught:
        await create_chat_completion(client, read_timeout=8.0, model="m", messages=[])

    assert not isinstance(caught.value, LLMUnreachableError)
    assert client.attempts == 1, "读超时不重试（避免 8s×N 放大失败路径）"


@pytest.mark.asyncio
async def test_transient_status_error_is_retried(monkeypatch):
    """5xx 保留有限重试：首败后成功。"""
    monkeypatch.setattr(settings, "LLM_TRANSIENT_MAX_RETRIES", 1)
    monkeypatch.setattr(settings, "LLM_TRANSIENT_RETRY_BACKOFF_SECONDS", 0.0)
    sentinel = object()
    client = _RecordingClient([_status_error(503), sentinel])

    result = await create_chat_completion(client, read_timeout=8.0, model="m", messages=[])

    assert result is sentinel
    assert client.attempts == 2
    assert client.with_options_calls[0]["max_retries"] == 0, "SDK 级重试必须关闭（由 helper 控制）"


@pytest.mark.asyncio
async def test_real_sdk_accepts_helper_timeout_and_retry_options():
    """契约钉（评审 should-fix-5）：真实 openai SDK 必须接受 helper 的
    ``with_options(max_retries=0, timeout=httpx.Timeout(...))`` 组合，并在副本上
    保留分阶段 timeout 属性（connect/read 分离）；副本与原名共享底层连接层。
    SDK 内部的 httpx→httpx2 归一化不在此断言（其实现细节由 SDK 负责）。
    不触网。"""
    client = openai.AsyncOpenAI(api_key="test-key", base_url="http://127.0.0.1:9/v1")
    try:
        fast = client.with_options(
            max_retries=0,
            timeout=httpx.Timeout(connect=1.0, read=8.0, write=8.0, pool=8.0),
        )
        assert fast.max_retries == 0
        assert float(fast.timeout.connect) == pytest.approx(1.0)
        assert float(fast.timeout.read) == pytest.approx(8.0)
        assert fast._client is client._client, "副本共享底层连接池"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_transient_retry_budget_exhausted_still_raises(monkeypatch):
    monkeypatch.setattr(settings, "LLM_TRANSIENT_MAX_RETRIES", 1)
    monkeypatch.setattr(settings, "LLM_TRANSIENT_RETRY_BACKOFF_SECONDS", 0.0)
    client = _RecordingClient([_status_error(500)])

    with pytest.raises(openai.APIStatusError):
        await create_chat_completion(client, read_timeout=8.0, model="m", messages=[])

    assert client.attempts == 2, "初始 + 1 次重试后上抛"


@pytest.mark.asyncio
async def test_non_retryable_status_raises_immediately(monkeypatch):
    """400 等客户端错误重试无意义，立即上抛。"""
    monkeypatch.setattr(settings, "LLM_TRANSIENT_MAX_RETRIES", 2)
    client = _RecordingClient([_status_error(400)])

    with pytest.raises(openai.APIStatusError):
        await create_chat_completion(client, read_timeout=8.0, model="m", messages=[])

    assert client.attempts == 1


@pytest.mark.asyncio
async def test_connect_timeout_comes_from_settings(monkeypatch):
    """连接阶段超时可配置，且不侵占读预算（M3 判别点）。"""
    monkeypatch.setattr(settings, "LLM_CONNECT_TIMEOUT_SECONDS", 0.6)
    sentinel = object()
    client = _RecordingClient([sentinel])

    await create_chat_completion(client, read_timeout=12.5, model="m", messages=[])

    timeout = client.with_options_calls[0]["timeout"]
    assert timeout.connect == pytest.approx(0.6)
    assert timeout.read == pytest.approx(12.5)
    assert timeout.write == pytest.approx(12.5)


@pytest.mark.asyncio
async def test_unreachable_mark_short_circuits_next_call(monkeypatch):
    """一次判定不可达后，冷却窗口内后续调用点不再触达网络（M5 判别点）。

    同一 RAG 请求内改写 → 重排 → 生成是串行的：若每个调用点各等一次连接
    超时，即使单点 <3s，总耗时仍会成倍放大。
    """
    monkeypatch.setattr(settings, "LLM_UNREACHABLE_COOLDOWN_SECONDS", 5.0)
    dead = _RecordingClient([openai.APIConnectionError(request=_request())])
    with pytest.raises(LLMUnreachableError):
        await create_chat_completion(dead, read_timeout=8.0, model="m", messages=[])

    healthy = _RecordingClient([object()])
    with pytest.raises(LLMUnreachableError):
        await create_chat_completion(healthy, read_timeout=8.0, model="m", messages=[])

    assert healthy.attempts == 0, "冷却窗口内不得再触达网络"


@pytest.mark.asyncio
async def test_cooldown_disabled_allows_immediate_retry(monkeypatch):
    """显式关闭负缓存（0）时，下一次调用仍会真实触达客户端。"""
    monkeypatch.setattr(settings, "LLM_UNREACHABLE_COOLDOWN_SECONDS", 0.0)
    dead = _RecordingClient([openai.APIConnectionError(request=_request())])
    with pytest.raises(LLMUnreachableError):
        await create_chat_completion(dead, read_timeout=8.0, model="m", messages=[])

    sentinel = object()
    healthy = _RecordingClient([sentinel])
    assert (
        await create_chat_completion(healthy, read_timeout=8.0, model="m", messages=[]) is sentinel
    )
    assert healthy.attempts == 1


@pytest.mark.asyncio
async def test_read_timeout_does_not_mark_endpoint_unreachable(monkeypatch):
    """可恢复的读超时不得写入不可达负缓存（否则一次慢响应会拖累后续调用）。"""
    monkeypatch.setattr(settings, "LLM_UNREACHABLE_COOLDOWN_SECONDS", 5.0)
    exc = _chained(openai.APITimeoutError(request=_request()), httpx.ReadTimeout("slow"))
    slow = _RecordingClient([exc])
    with pytest.raises(openai.APITimeoutError):
        await create_chat_completion(slow, read_timeout=8.0, model="m", messages=[])

    sentinel = object()
    healthy = _RecordingClient([sentinel])
    assert (
        await create_chat_completion(healthy, read_timeout=8.0, model="m", messages=[]) is sentinel
    ), "读超时后下一次调用必须照常触达客户端"
    assert healthy.attempts == 1


@pytest.mark.asyncio
async def test_unreachable_mark_is_scoped_per_endpoint(monkeypatch):
    """负缓存按端点点缀：一个 base_url 不可达不得封锁另一个端点（评审 should-fix-2）。"""
    monkeypatch.setattr(settings, "LLM_UNREACHABLE_COOLDOWN_SECONDS", 5.0)
    dead = _RecordingClient(
        [openai.APIConnectionError(request=_request())], base_url="http://llm-a.invalid/v1"
    )
    with pytest.raises(LLMUnreachableError):
        await create_chat_completion(dead, read_timeout=8.0, model="m", messages=[])

    sentinel = object()
    other = _RecordingClient([sentinel], base_url="http://llm-b.invalid/v1")
    assert (
        await create_chat_completion(other, read_timeout=8.0, model="m", messages=[]) is sentinel
    ), "不同 base_url 的客户端不得被同一负缓存封锁"
    assert other.attempts == 1


@pytest.mark.asyncio
async def test_post_connect_transport_error_is_retried_not_unreachable(monkeypatch):
    """连接已建立后的传输中断（RemoteProtocolError）→ 有限重试、不写负缓存。

    评审 blocking-1：SDK 默认会重试这类错误且常在第二次成功；本 helper 关闭了
    SDK 重试，必须自己保留同等语义，否则一次 keep-alive 断连会被放大成用户可见故障。
    """
    monkeypatch.setattr(settings, "LLM_TRANSIENT_MAX_RETRIES", 1)
    monkeypatch.setattr(settings, "LLM_TRANSIENT_RETRY_BACKOFF_SECONDS", 0.0)
    monkeypatch.setattr(settings, "LLM_UNREACHABLE_COOLDOWN_SECONDS", 5.0)
    exc = _chained(
        openai.APIConnectionError(request=_request()),
        httpx.RemoteProtocolError("server disconnected"),
    )
    sentinel = object()
    client = _RecordingClient([exc, sentinel])

    result = await create_chat_completion(client, read_timeout=8.0, model="m", messages=[])

    assert result is sentinel, "传输中断重试后成功"
    assert client.attempts == 2, "初始 + 1 次重试（与 5xx 共用预算）"

    # 未写负缓存：同一端点的下一次调用仍触达网络。
    healthy = _RecordingClient([object()])
    await create_chat_completion(healthy, read_timeout=8.0, model="m", messages=[])
    assert healthy.attempts == 1
