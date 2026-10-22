"""LLM 连接级快速失败（issue #467）。

故障注入 QA（2026-10-22）确认：LLM 端点不可达时，RAG 聊天要 ~11s 才暴露错误——
查询改写 1.2s + 重排 LLM 兜底 8s + openai SDK 对连接错误的默认重试与退避 ≈2s。
本模块把「连接阶段未建立」与「服务慢 / 连接已建立后的瞬时中断」区分开，让真正
不可达在 3s 内走降级：

- ``is_llm_unreachable_error``：只把**连接阶段未建立**的失败判为不可达——
  ``APIConnectionError`` 中 cause 链为 ``ConnectError`` / ``ConnectTimeout`` /
  ``ProxyError`` / ``gaierror`` 的（连接被拒 / DNS 失败 / 主机不可达 / 代理不可达），
  以及无 cause 的通用 ``APIConnectionError``（SDK 泛化包装，保守按不可达）。
  连接已建立后的瞬时中断（``RemoteProtocolError`` / ``ReadError`` / ``WriteError``
  / ``EndOfStream``）与读/写/连接池超时、以及**任何未识别的 cause 链**都属于
  **可恢复故障**：不判不可达、不写负缓存、保留有限重试——否则一次 keep-alive
  断连会被放大成数秒的用户可见故障。
- ``create_chat_completion``：单次 chat completion 调用。
  * 连接阶段使用独立短超时（``LLM_CONNECT_TIMEOUT_SECONDS``，默认 1.0s，低于
    改写站 1.2s 外层预算，保证首个串行调用点也能判定并写入负缓存），不占用
    读阶段预算——死主机（SYN 被丢弃）不再拖满 8s 读超时；
  * SDK 级重试关闭（``max_retries=0``），重试由本函数按失败类型决定，
    避免与 SDK 内部重试叠加放大退避时间；
  * 不可达 → 立即抛 ``LLMUnreachableError``，零重试；
  * 读/写/连接池超时 → 原样上抛，零重试（读阶段已花掉完整预算，重试只会成倍拖慢）；
  * 连接已建立后的瞬时中断 + HTTP 408/409/429/5xx → 经
    ``LLM_TRANSIENT_MAX_RETRIES`` 有限重试后上抛。

调用方（RAG 查询改写 / 查询分解 / HyDE / 重排 LLM 兜底 / 流式生成入口）捕获
``LLMUnreachableError`` 后直接走既有降级/错误路径，不再等满读超时。

进程级负缓存按**端点**（``client.base_url``）区分：一次判定不可达后在
``LLM_UNREACHABLE_COOLDOWN_SECONDS``（默认 5s）内，同一端点的调用直接快速失败
——同一 RAG 请求内改写 → 重排 → 生成是串行的，若每个调用点各等一次连接超时，
总耗时仍会成倍放大。任一调用成功即清除；0 关闭。多 worker 部署下每进程一份
（与 executor 熔断器同语义）；不同 base_url 的客户端互不影响（例如 chat 端点
与 /rerank 端点不会互相封锁）。

变异点（对应测试转红）：
- M1：``is_llm_unreachable_error`` 把 ``APITimeoutError``（ReadTimeout）判为
  不可达 → ``test_read_timeout_is_not_unreachable`` 红；
- M2：调用方未传 ``max_retries=0``（SDK 继续重试连接错误）→
  ``test_unreachable_raises_domain_error_without_sdk_retry`` 与
  ``test_rewrite_connection_error_does_not_retry`` 的调用次数/耗时上界红；
- M3：连接阶段未使用独立短超时 → ``test_connect_timeout_comes_from_settings``
  与 ``test_connect_timeout_is_independent_of_read_budget`` 红（断言
  ``with_options`` 收到的 timeout.connect）；
- M4：瞬时故障不重试（含 5xx 与连接已建立后的传输中断）→
  ``test_transient_status_error_is_retried`` /
  ``test_post_connect_transport_error_is_retried_not_unreachable`` 红；
- M5：不可达负缓存缺失、未按端点区分或读超时也写入缓存 →
  ``test_unreachable_mark_short_circuits_next_call`` /
  ``test_unreachable_mark_is_scoped_per_endpoint`` /
  ``test_read_timeout_does_not_mark_endpoint_unreachable`` 红。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Iterator
from typing import Any

import httpx
import openai

from app.core.config import settings

logger = logging.getLogger(__name__)

# 连接阶段未建立的证据（按 cause 类名匹配，同时覆盖 httpx 与 SDK 内置的
# httpx2；这些名字只用于**异常 cause 链**，不会匹配包在 APIConnectionError
# 之外的裸异常）。
_CONNECT_PHASE_CAUSE_NAMES = frozenset(
    {
        "ConnectError",
        "ConnectTimeout",
        "ProxyError",
        "gaierror",
        "ConnectionRefusedError",
    }
)

# 状态码口径与 openai SDK 的 `_should_retry` 一致（408/409/429/5xx）；未实现
# SDK 的 Retry-After / x-should-retry 头语义——标准 OpenAI 兼容端点不会设置
# 这些头，若未来需要可在此扩展。
_TRANSIENT_STATUS_CODES = frozenset({408, 409, 429})

_MAX_BACKOFF_SECONDS = 4.0

# 进程级「端点不可达」负缓存（issue #467）：key = client.base_url。
# 一次连接级失败后，在 LLM_UNREACHABLE_COOLDOWN_SECONDS 内同一端点的调用直接
# 快速失败——同一请求内改写 → 重排 → 生成是串行的，若每个调用点各等一次连接
# 超时，总耗时仍会成倍放大。TTL 很短（默认 5s）且仅在真正判定为不可达时写入；
# 任一调用成功即清除。多 worker 部署下每进程一份（与 executor 熔断器同语义）。
_unreachable_until: dict[str, float] = {}


def _endpoint_key(client) -> str:
    """负缓存键：优先 client.base_url；测试替身无 base_url 时归入占位桶。"""
    base_url = getattr(client, "base_url", None)
    return str(base_url) if base_url else "<unknown>"


def _mark_unreachable(endpoint: str) -> None:
    cooldown = float(settings.LLM_UNREACHABLE_COOLDOWN_SECONDS)
    if cooldown > 0:
        _unreachable_until[endpoint] = time.monotonic() + cooldown


def _clear_unreachable(endpoint: str) -> None:
    _unreachable_until.pop(endpoint, None)


def _unreachable_cooldown_active(endpoint: str) -> bool:
    return _unreachable_until.get(endpoint, 0.0) > time.monotonic()


class LLMUnreachableError(RuntimeError):
    """LLM 端点连接级不可达（issue #467）。

    调用方应直接走降级路径（本地改写 / 原始查询 / 原序重排），不要继续等待
    或重试——连接未建立的失败在当次请求内重试必然还是失败。
    """

    error_type = "llm_unreachable"


def _cause_chain(exc: BaseException | None, max_depth: int = 8) -> Iterator[BaseException]:
    """迭代异常的 cause/context 链（去环、限深）。"""
    seen: set[int] = set()
    current = exc
    while current is not None and id(current) not in seen and len(seen) < max_depth:
        seen.add(id(current))
        yield current
        current = current.__cause__ or current.__context__


def _cause_names(exc: BaseException | None) -> set[str]:
    return {type(cause).__name__ for cause in _cause_chain(exc)}


def is_llm_unreachable_error(exc: BaseException) -> bool:
    """判定异常是否属于「连接阶段不可达」（issue #467）。

    契约：
    - ``APITimeoutError``：只有 cause 链明确为 ConnectError/ConnectTimeout/
      ProxyError/DNS 失败（连接阶段未建立）才算不可达——**读/写/连接池超时是
      可恢复的慢，不算**；无 cause 时保守判为可恢复。
    - 其它 ``APIConnectionError``：cause 链为连接阶段错误，或无 cause（SDK 泛化
      包装「Connection error.」）→ 不可达；连接已建立后的传输中断
      （RemoteProtocolError/ReadError/WriteError/EndOfStream）以及**任何未识别的
      cause** → 可恢复（宁可多一次重试，也不误杀健康端点）。
    - 其它异常（含 APIStatusError 5xx/429）→ False（瞬时故障，走重试策略）。
    """
    if not isinstance(exc, openai.APIConnectionError):
        return False
    cause_names = _cause_names(exc.__cause__ or exc.__context__)
    if isinstance(exc, openai.APITimeoutError):
        return bool(cause_names & _CONNECT_PHASE_CAUSE_NAMES)
    # 白名单：只认连接阶段证据或 SDK 的「无 cause 泛化包装」；其余一律可恢复。
    return bool(cause_names & _CONNECT_PHASE_CAUSE_NAMES) or not cause_names


def _build_timeout(read_timeout: float) -> httpx.Timeout:
    """连接阶段短超时 + 读/写阶段沿用调用方预算（互不侵占）。"""
    connect = float(settings.LLM_CONNECT_TIMEOUT_SECONDS)
    read = max(0.1, float(read_timeout))
    return httpx.Timeout(connect=connect, read=read, write=read, pool=read)


def _is_transient_status(exc: openai.APIStatusError) -> bool:
    return exc.status_code in _TRANSIENT_STATUS_CODES or exc.status_code >= 500


def _backoff_seconds(retries_taken: int) -> float:
    backoff = float(settings.LLM_TRANSIENT_RETRY_BACKOFF_SECONDS) * (2 ** (retries_taken - 1))
    return min(backoff, _MAX_BACKOFF_SECONDS)


async def create_chat_completion(client, *, read_timeout: float, **kwargs: Any):
    """调用 ``client.chat.completions.create``，带连接级 fast-fail。

    ``client`` 需为 openai ``AsyncOpenAI``（或其 ``with_options`` 视图）。
    返回 SDK 的 chat completion 对象；异常语义见模块 docstring。
    重试策略（变异点 M2/M4）：
    - 连接类不可达（``LLMUnreachableError``）零重试；
    - 可恢复超时零重试（已花掉完整读预算）；
    - 连接已建立后的瞬时中断与 408/409/429/5xx 按
      ``LLM_TRANSIENT_MAX_RETRIES`` 重试（指数退避）。
    """
    endpoint = _endpoint_key(client)
    if _unreachable_cooldown_active(endpoint):
        # 同请求内后续调用点 / 冷却窗口内的下一请求：直接失败，不再触达网络。
        raise LLMUnreachableError(
            "LLM endpoint recently detected unreachable (cooldown active; skip call)"
        )
    timeout = _build_timeout(read_timeout)
    # SDK 级重试关闭：连接类错误必须零重试；瞬时故障由下方循环控制——
    # 两套重试叠加会让退避时间成倍放大（issue #467 的 11s 路径之一）。
    fast_client = client.with_options(max_retries=0, timeout=timeout)
    max_retries = max(0, int(settings.LLM_TRANSIENT_MAX_RETRIES))
    retries_taken = 0
    while True:
        try:
            result = await fast_client.chat.completions.create(**kwargs)
            _clear_unreachable(endpoint)
            return result
        except openai.APITimeoutError as exc:
            if is_llm_unreachable_error(exc):
                _mark_unreachable(endpoint)
                raise LLMUnreachableError(
                    f"LLM endpoint unreachable (connect phase timeout): {exc}"
                ) from exc
            # 读/写/连接池超时：可恢复的慢，不重试也不误判（调用方原降级路径）。
            raise
        except openai.APIConnectionError as exc:
            if is_llm_unreachable_error(exc):
                _mark_unreachable(endpoint)
                raise LLMUnreachableError(f"LLM endpoint unreachable: {exc}") from exc
            # 连接已建立后的瞬时中断（RemoteProtocolError/ReadError/...）：
            # 与 5xx 共用有限重试预算；SDK 默认也会重试这类错误。
            if retries_taken < max_retries:
                retries_taken += 1
                await asyncio.sleep(_backoff_seconds(retries_taken))
                continue
            raise
        except openai.APIStatusError as exc:
            if retries_taken < max_retries and _is_transient_status(exc):
                retries_taken += 1
                await asyncio.sleep(_backoff_seconds(retries_taken))
                continue
            raise
