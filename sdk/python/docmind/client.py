"""DocMind Python SDK：REST + SSE 客户端（issue #480 §5.6）。

- ``Client`` / ``AsyncClient``：同名接口；
- 错误统一 ``DocMindError(code, message, status)``；
- 重试仅对 429/502/503/504（指数退避 + 抖动，最多 3 次）；**流式请求不重试**
  （避免重复消息）；
- 事件流把 ``/chat/stream`` 的终态 ``message`` 归一化为 ``done``（README 契约：
  最后一个事件是 ``done``）。
"""

from __future__ import annotations

import asyncio
import json
import random
import time
from collections.abc import AsyncIterator, Iterator, Mapping
from typing import Any

import httpx

from docmind.models import DocMindError, Session, SessionEvent

RETRY_STATUS = frozenset({429, 502, 503, 504})
DEFAULT_TIMEOUT = 60.0
DEFAULT_MAX_RETRIES = 3


# ── SSE 解析 ──────────────────────────────────────────────────────────────────


def _iter_sse_frames(lines) -> Iterator[SessionEvent]:  # noqa: ANN001 - str iterable
    event: str | None = None
    event_id: str | None = None
    data_lines: list[str] = []
    for raw_line in lines:
        line = raw_line.rstrip("\r")
        if line == "":
            if event is not None or data_lines:
                payload_text = "\n".join(data_lines)
                try:
                    data = json.loads(payload_text) if payload_text else {}
                except json.JSONDecodeError:
                    data = {"raw": payload_text}
                if not isinstance(data, dict):
                    data = {"value": data}
                yield SessionEvent(
                    type=event or str(data.get("type") or "message"),
                    data=data,
                    id=event_id,
                )
            event, event_id, data_lines = None, None, []
            continue
        if line.startswith("event:"):
            event = line[len("event:") :].strip()
        elif line.startswith("id:"):
            event_id = line[len("id:") :].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:") :].lstrip())
    # 容忍末尾没有空行的最后一段。
    if event is not None or data_lines:
        payload_text = "\n".join(data_lines)
        try:
            data = json.loads(payload_text) if payload_text else {}
        except json.JSONDecodeError:
            data = {"raw": payload_text}
        if not isinstance(data, dict):
            data = {"value": data}
        yield SessionEvent(type=event or str(data.get("type") or "message"), data=data, id=event_id)


class _Base:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_backoff: float = 0.5,
        transport: httpx.BaseTransport | httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key:
            raise DocMindError("AUTHENTICATION_ERROR", "api_key 不能为空", 401)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.max_retries = max(1, int(max_retries))
        self.retry_backoff = retry_backoff
        self._transport = transport

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"}

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    @staticmethod
    def _error(resp) -> DocMindError:  # noqa: ANN001
        try:
            body = resp.json()
        except Exception:  # noqa: BLE001
            body = {}
        code = str(body.get("error_code") or body.get("code") or f"HTTP_{resp.status_code}")
        message = str(body.get("message") or body.get("detail") or resp.text[:300] or code)
        return DocMindError(code, message, resp.status_code)

    @staticmethod
    def _payload(resp) -> Any:  # noqa: ANN001
        if resp.status_code >= 400:
            raise _Base._error(resp)
        try:
            body = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise DocMindError("INVALID_RESPONSE", "服务端返回非 JSON 响应", resp.status_code) from exc
        if isinstance(body, dict) and "data" in body and body.get("success") is True:
            return body["data"]
        return body

    @staticmethod
    def _retry_delay(attempt: int, resp, backoff: float) -> float:  # noqa: ANN001
        retry_after = resp.headers.get("Retry-After") if resp is not None else None
        if retry_after:
            try:
                return float(retry_after)
            except ValueError:
                pass
        # 指数退避 + 抖动（0.5x..1.5x），避免惊群。
        return backoff * (2 ** (attempt - 1)) * (0.5 + random.random())


# ── 资源接口 ─────────────────────────────────────────────────────────────────


class _Sessions:
    def __init__(self, client: "_Base") -> None:
        self._c = client

    def create(self, title: str = "New Agent Session"):
        data = self._c._request_json(  # noqa: SLF001
            "POST", "/api/v1/agent/sessions", json={"title": title}
        )
        return Session.from_dict(data if isinstance(data, dict) else {})

    def list(self, page: int = 1, page_size: int = 20) -> list[Session]:
        data = self._c._request_json(  # noqa: SLF001
            "GET",
            "/api/v1/agent/sessions",
            params={"page": page, "page_size": page_size},
        )
        items = data.get("sessions", []) if isinstance(data, dict) else []
        return [Session.from_dict(item) for item in items]

    def fork(
        self,
        session_id: str,
        *,
        message_id: str | None = None,
        title: str | None = None,
        include_checkpoint: bool = False,
        seed: str | None = None,
    ) -> Session:
        body: dict[str, Any] = {
            "message_id": message_id,
            "title": title,
            "include_checkpoint": include_checkpoint,
            "seed": seed,
        }
        body = {key: value for key, value in body.items() if value is not None}
        data = self._c._request_json(  # noqa: SLF001
            "POST", f"/api/v1/sessions/{session_id}/fork", json=body
        )
        if not isinstance(data, dict):
            raise DocMindError("INVALID_RESPONSE", "fork 响应格式非法")
        if "session" in data and isinstance(data["session"], dict):
            return Session.from_dict(data["session"])
        return Session.from_dict(data)


class _ChatSync:
    def __init__(self, client: "_Base") -> None:
        self._c = client

    def stream(
        self,
        session_id: str,
        prompt: str,
        client_message_id: str | None = None,
        *,
        last_event_id: str | None = None,
    ) -> Iterator[SessionEvent]:
        yield from _stream_events_sync(
            self._c,
            session_id,
            prompt,
            client_message_id,
            last_event_id,
        )


class _ChatAsync:
    def __init__(self, client: "_Base") -> None:
        self._c = client

    async def stream(
        self,
        session_id: str,
        prompt: str,
        client_message_id: str | None = None,
        *,
        last_event_id: str | None = None,
    ) -> AsyncIterator[SessionEvent]:
        async for event in _stream_events_async(
            self._c,
            session_id,
            prompt,
            client_message_id,
            last_event_id,
        ):
            yield event


def _stream_body(
    session_id: str, prompt: str, client_message_id: str | None
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "content": prompt,
        "conversationId": session_id,
        "useAgent": True,
    }
    if client_message_id is not None:
        # 服务端 /chat/stream 目前忽略该字段；JSON-RPC session/prompt 用它做
        # 服务端幂等（见 README「幂等」一节）。
        body["clientMessageId"] = client_message_id
    return body


def _stream_events_sync(
    client: _Base,
    session_id: str,
    prompt: str,
    client_message_id: str | None,
    last_event_id: str | None,
) -> Iterator[SessionEvent]:
    headers = client._headers  # noqa: SLF001
    headers["Accept"] = "text/event-stream"
    if last_event_id:
        headers["Last-Event-ID"] = last_event_id
    saw_done = False
    with httpx.Client(
        timeout=client.timeout,
        transport=client._transport,  # noqa: SLF001
    ) as http:
        with http.stream(
            "POST",
            client._url("/api/v1/chat/stream"),  # noqa: SLF001
            json=_stream_body(session_id, prompt, client_message_id),
            headers=headers,
        ) as resp:
            if resp.status_code >= 400:
                resp.read()
                raise _Base._error(resp)
            for event in _iter_sse_frames(resp.iter_lines()):
                if event.type == "done":
                    saw_done = True
                yield event
    if not saw_done:
        # /chat/stream 的终态是 message；SDK 统一归一化为 done。
        yield SessionEvent(type="done", data={"conversationId": session_id})


async def _stream_events_async(
    client: _Base,
    session_id: str,
    prompt: str,
    client_message_id: str | None,
    last_event_id: str | None,
) -> AsyncIterator[SessionEvent]:
    headers = client._headers  # noqa: SLF001
    headers["Accept"] = "text/event-stream"
    if last_event_id:
        headers["Last-Event-ID"] = last_event_id
    saw_done = False
    async with httpx.AsyncClient(
        timeout=client.timeout,
        transport=client._transport,  # noqa: SLF001
    ) as http:
        async with http.stream(
            "POST",
            client._url("/api/v1/chat/stream"),  # noqa: SLF001
            json=_stream_body(session_id, prompt, client_message_id),
            headers=headers,
        ) as resp:
            if resp.status_code >= 400:
                await resp.aread()
                raise _Base._error(resp)
            async for event in _astream_frames(resp):
                if event.type == "done":
                    saw_done = True
                yield event
    if not saw_done:
        yield SessionEvent(type="done", data={"conversationId": session_id})


def _astream_frames(resp) -> AsyncIterator[SessionEvent]:
    async def _lines() -> AsyncIterator[str]:
        async for line in resp.aiter_lines():
            yield line

    # 用异步迭代器手工做状态机（与 _iter_sse_frames 语义一致）。
    event: str | None = None
    event_id: str | None = None
    data_lines: list[str] = []

    async def _gen() -> AsyncIterator[SessionEvent]:
        nonlocal event, event_id, data_lines
        async for raw_line in _lines():
            line = raw_line.rstrip("\r")
            if line == "":
                if event is not None or data_lines:
                    yield _frame(event, event_id, data_lines)
                event, event_id, data_lines = None, None, []
                continue
            if line.startswith("event:"):
                event = line[len("event:") :].strip()
            elif line.startswith("id:"):
                event_id = line[len("id:") :].strip()
            elif line.startswith("data:"):
                data_lines.append(line[len("data:") :].lstrip())
        if event is not None or data_lines:
            yield _frame(event, event_id, data_lines)

    return _gen()


def _frame(event: str | None, event_id: str | None, data_lines: list[str]) -> SessionEvent:
    payload_text = "\n".join(data_lines)
    try:
        data = json.loads(payload_text) if payload_text else {}
    except json.JSONDecodeError:
        data = {"raw": payload_text}
    if not isinstance(data, dict):
        data = {"value": data}
    return SessionEvent(type=event or str(data.get("type") or "message"), data=data, id=event_id)


class _Approvals:
    def __init__(self, client: "_Base") -> None:
        self._c = client

    def respond(
        self,
        session_id: str,
        approval_id: str,
        decision: str,
        *,
        remember: bool = False,
    ) -> dict[str, Any]:
        if decision not in ("allow", "deny", "approve"):
            raise DocMindError("VALIDATION_ERROR", "decision 必须是 allow 或 deny")
        http_decision = "approve" if decision in ("allow", "approve") else "deny"
        return self._c._request_json(  # noqa: SLF001
            "POST",
            f"/api/v1/agent/tasks/{approval_id}/approval",
            json={"decision": http_decision, "note": "remember" if remember else None},
        )


class _Tools:
    def __init__(self, client: "_Base") -> None:
        self._c = client

    def list(self) -> list[dict[str, Any]]:
        data = self._c._request_json("GET", "/api/v1/agent/tools")  # noqa: SLF001
        return list(data) if isinstance(data, list) else []


class _Knowledge:
    def __init__(self, client: "_Base") -> None:
        self._c = client

    def search(
        self,
        query: str,
        *,
        top_k: int | None = None,
        search_mode: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"query": query}
        if top_k is not None:
            body["top_k"] = top_k
        if search_mode is not None:
            body["search_mode"] = search_mode
        data = self._c._request_json("POST", "/api/v1/knowledge/search", json=body)  # noqa: SLF001
        return data if isinstance(data, dict) else {"results": data}


# ── 客户端 ───────────────────────────────────────────────────────────────────


class Client(_Base):
    """同步客户端。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_backoff: float = 0.5,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        super().__init__(
            base_url,
            api_key,
            timeout=timeout,
            max_retries=max_retries,
            retry_backoff=retry_backoff,
            transport=transport,
        )
        self.sessions = _Sessions(self)
        self.chat = _ChatSync(self)
        self.approvals = _Approvals(self)
        self.tools = _Tools(self)
        self.knowledge = _Knowledge(self)

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        last_resp = None
        with httpx.Client(timeout=self.timeout, transport=self._transport) as http:
            for attempt in range(1, self.max_retries + 1):
                last_resp = http.request(
                    method, self._url(path), json=json, params=params, headers=self._headers
                )
                if last_resp.status_code in RETRY_STATUS and attempt < self.max_retries:
                    time.sleep(self._retry_delay(attempt, last_resp, self.retry_backoff))
                    continue
                return self._payload(last_resp)
        return self._payload(last_resp)  # pragma: no cover - 循环必然 return

    def close(self) -> None:  # 兼容 with 用法
        return None

    def __enter__(self) -> "Client":
        return self

    def __exit__(self, *_exc) -> None:
        self.close()


class AsyncClient(_Base):
    """异步客户端（方法同名）。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_backoff: float = 0.5,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(
            base_url,
            api_key,
            timeout=timeout,
            max_retries=max_retries,
            retry_backoff=retry_backoff,
            transport=transport,
        )
        self.sessions = _AsyncSessions(self)
        self.chat = _ChatAsync(self)
        self.approvals = _AsyncApprovals(self)
        self.tools = _AsyncTools(self)
        self.knowledge = _AsyncKnowledge(self)

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: Mapping[str, Any] | None = None,
    ) -> Any:
        last_resp = None
        async with httpx.AsyncClient(timeout=self.timeout, transport=self._transport) as http:
            for attempt in range(1, self.max_retries + 1):
                last_resp = await http.request(
                    method, self._url(path), json=json, params=params, headers=self._headers
                )
                if last_resp.status_code in RETRY_STATUS and attempt < self.max_retries:
                    await asyncio.sleep(
                        self._retry_delay(attempt, last_resp, self.retry_backoff)
                    )
                    continue
                return self._payload(last_resp)
        return self._payload(last_resp)  # pragma: no cover

    async def aclose(self) -> None:
        return None

    async def __aenter__(self) -> "AsyncClient":
        return self

    async def __aexit__(self, *_exc) -> None:
        await self.aclose()


class _AsyncSessions:
    def __init__(self, client: AsyncClient) -> None:
        self._c = client

    async def create(self, title: str = "New Agent Session") -> Session:
        data = await self._c._request_json(
            "POST", "/api/v1/agent/sessions", json={"title": title}
        )
        return Session.from_dict(data if isinstance(data, dict) else {})

    async def list(self, page: int = 1, page_size: int = 20) -> list[Session]:
        data = await self._c._request_json(
            "GET",
            "/api/v1/agent/sessions",
            params={"page": page, "page_size": page_size},
        )
        items = data.get("sessions", []) if isinstance(data, dict) else []
        return [Session.from_dict(item) for item in items]

    async def fork(
        self,
        session_id: str,
        *,
        message_id: str | None = None,
        title: str | None = None,
        include_checkpoint: bool = False,
        seed: str | None = None,
    ) -> Session:
        body: dict[str, Any] = {
            "message_id": message_id,
            "title": title,
            "include_checkpoint": include_checkpoint,
            "seed": seed,
        }
        body = {key: value for key, value in body.items() if value is not None}
        data = await self._c._request_json(
            "POST", f"/api/v1/sessions/{session_id}/fork", json=body
        )
        if isinstance(data, dict) and isinstance(data.get("session"), dict):
            return Session.from_dict(data["session"])
        return Session.from_dict(data if isinstance(data, dict) else {})


class _AsyncApprovals:
    def __init__(self, client: AsyncClient) -> None:
        self._c = client

    async def respond(
        self,
        session_id: str,
        approval_id: str,
        decision: str,
        *,
        remember: bool = False,
    ) -> dict[str, Any]:
        if decision not in ("allow", "deny", "approve"):
            raise DocMindError("VALIDATION_ERROR", "decision 必须是 allow 或 deny")
        http_decision = "approve" if decision in ("allow", "approve") else "deny"
        return await self._c._request_json(
            "POST",
            f"/api/v1/agent/tasks/{approval_id}/approval",
            json={"decision": http_decision, "note": "remember" if remember else None},
        )


class _AsyncTools:
    def __init__(self, client: AsyncClient) -> None:
        self._c = client

    async def list(self) -> list[dict[str, Any]]:
        data = await self._c._request_json("GET", "/api/v1/agent/tools")
        return list(data) if isinstance(data, list) else []


class _AsyncKnowledge:
    def __init__(self, client: AsyncClient) -> None:
        self._c = client

    async def search(
        self,
        query: str,
        *,
        top_k: int | None = None,
        search_mode: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"query": query}
        if top_k is not None:
            body["top_k"] = top_k
        if search_mode is not None:
            body["search_mode"] = search_mode
        data = await self._c._request_json("POST", "/api/v1/knowledge/search", json=body)
        return data if isinstance(data, dict) else {"results": data}
