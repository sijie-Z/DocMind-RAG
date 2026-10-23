# DocMind Python SDK

REST + SSE 客户端（issue #480）。Python ≥ 3.10，唯一运行时依赖 `httpx`。

## 安装（仓库内开发）

```bash
cd sdk/python
pip install -e ".[dev]"
```

## 快速开始

```python
from docmind import Client

client = Client("http://localhost:8000", api_key="dm_...")

session = client.sessions.create(title="sdk-demo")
events = list(client.chat.stream(session.id, "你好", client_message_id="msg-1"))

assert events[-1].type == "done"
```

预期：至少一个 `chunk` 事件，最后一个事件是 `done`（SDK 把
`/api/v1/chat/stream` 的终态 `message` 归一化为 `done`）。

## 异步

```python
from docmind import AsyncClient

async with AsyncClient("http://localhost:8000", api_key="dm_...") as client:
    session = await client.sessions.create(title="async-demo")
    async for event in client.chat.stream(session.id, "你好", client_message_id="msg-2"):
        print(event.type, event.content)
```

## 接口

| 调用 | HTTP |
|---|---|
| `client.sessions.create(title)` | `POST /api/v1/agent/sessions` |
| `client.sessions.list(page, page_size)` | `GET /api/v1/agent/sessions` |
| `client.sessions.fork(session_id, ...)` | `POST /api/v1/sessions/{id}/fork`（issue #476） |
| `client.chat.stream(session_id, prompt, client_message_id, last_event_id=...)` | `POST /api/v1/chat/stream`（SSE） |
| `client.approvals.respond(session_id, approval_id, "allow"\|"deny")` | `POST /api/v1/agent/tasks/{id}/approval` |
| `client.tools.list()` | `GET /api/v1/agent/tools` |
| `client.knowledge.search(query, top_k=..., search_mode=...)` | `POST /api/v1/knowledge/search` |

## 错误与重试

- 统一异常 `DocMindError(code, message, status)`；
- 非流式请求仅对 `429/502/503/504` 重试（指数退避 + 抖动，`max_retries=3`，
  尊重 `Retry-After`）；**流式请求不自动重试**（避免重复消息）；
- SSE 断线续传：把上一次事件的 `event.id` 作为 `last_event_id` 重连；服务端
  缓冲已淘汰时返回流内 `error` 事件 `{"code": "SSE_RESUME_GONE"}`。

## 幂等

`clientMessageId` 会随 `/chat/stream` 请求体透传；**服务端幂等**由 JSON-RPC
`session/prompt`（stdio / `POST /api/v1/rpc`）保证（Redis `rpc:msg:*`，TTL 24h）：
同一 `clientMessageId` 重复到达只返回同一个 `messageId`，不产生第二条用户消息。
