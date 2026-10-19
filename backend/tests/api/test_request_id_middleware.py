"""W0-9：RequestIDMiddleware 语义 —— 入站头透传 / 校验 / contextvar 可见性。

契约（与 W0-5 / C6 协作点）：
- 合法入站 ``X-Request-ID``（1~64 字符，仅 ``[A-Za-z0-9._-]``）原样透传到响应头，
  并在 handler 内可经 ``app.core.request_id.get_request_id()`` 取得；
- 非法（超长 / 非法字符 / 非 ASCII 字节）或缺失时生成新的 UUID；
- scope state 仍保留 ``request_id``（``request.state.request_id``，异常 handler 依赖）；
- 中间件在链路上位于 CORS 之后（Starlette 里 ``user_middleware`` 越靠后越内层）。
"""

import json
import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.core.request_id import RequestIDMiddleware, get_request_id

VALID_ID = "abc-123_XYZ.789"
MAX_LEN_ID = "a" * 64
# 真实 app 上不依赖 DB/Redis/ES/MinIO 的最小路径，仍走完整中间件链。
PROBE_PATH = "/api/v1/openapi.json"


def _mini_app() -> FastAPI:
    """最小可控 app：一个 async 路由 + 一个 sync 路由（线程池）读 contextvar。"""
    app = FastAPI()

    @app.get("/probe")
    async def probe(request: Request) -> dict:
        return {
            "request_id": get_request_id(),
            "state_request_id": request.state.request_id,
        }

    @app.get("/probe-sync")
    def probe_sync(request: Request) -> dict:
        return {
            "request_id": get_request_id(),
            "state_request_id": request.state.request_id,
        }

    app.add_middleware(RequestIDMiddleware)
    return app


@pytest.fixture()
def mini_client() -> TestClient:
    return TestClient(_mini_app())


def _assert_probe_ids(response, expected: str) -> None:
    assert response.status_code == 200
    body = response.json()
    assert body["request_id"] == expected
    assert body["state_request_id"] == expected


def test_valid_inbound_id_is_echoed_and_visible_in_async_handler(mini_client: TestClient):
    response = mini_client.get("/probe", headers={"X-Request-ID": VALID_ID})

    assert response.headers["X-Request-ID"] == VALID_ID
    _assert_probe_ids(response, VALID_ID)


def test_valid_inbound_id_is_visible_in_sync_handler(mini_client: TestClient):
    """同步路由跑在 anyio 线程池里，contextvars 必须随上下文一起复制过去。"""
    response = mini_client.get("/probe-sync", headers={"X-Request-ID": VALID_ID})

    assert response.headers["X-Request-ID"] == VALID_ID
    _assert_probe_ids(response, VALID_ID)


def test_max_length_inbound_id_is_accepted(mini_client: TestClient):
    response = mini_client.get("/probe", headers={"X-Request-ID": MAX_LEN_ID})

    assert response.headers["X-Request-ID"] == MAX_LEN_ID
    _assert_probe_ids(response, MAX_LEN_ID)


@pytest.mark.parametrize(
    "bad_id",
    [
        "a" * 65,  # 超长
        "has space",  # 空格不在字符集
        "bad/slash",  # 斜杠不在字符集
        "semi;colon",  # 分号不在字符集
        "",  # 空串
    ],
)
def test_invalid_inbound_id_is_regenerated(mini_client: TestClient, bad_id: str):
    response = mini_client.get("/probe", headers={"X-Request-ID": bad_id})

    generated = response.headers["X-Request-ID"]
    assert generated != bad_id
    uuid.UUID(generated)  # invalid 时必须重新生成合法 UUID
    _assert_probe_ids(response, generated)


def test_missing_header_generates_uuid(mini_client: TestClient):
    response = mini_client.get("/probe")

    generated = response.headers["X-Request-ID"]
    uuid.UUID(generated)
    _assert_probe_ids(response, generated)


def test_downstream_response_header_is_not_overwritten():
    """原有行为保留：下游已写 X-Request-ID 时不覆盖（中间件只补缺）。"""
    app = FastAPI()

    @app.get("/custom")
    async def custom() -> JSONResponse:
        return JSONResponse({"ok": True}, headers={"X-Request-ID": "downstream-id"})

    app.add_middleware(RequestIDMiddleware)
    response = TestClient(app).get("/custom", headers={"X-Request-ID": VALID_ID})
    assert response.headers["X-Request-ID"] == "downstream-id"


def test_get_request_id_defaults_to_none():
    """请求上下文之外调用返回 None（供 W0-5/C6 安全消费）。"""
    assert get_request_id() is None


def test_logging_formatter_reads_the_same_contextvar():
    """结构化日志与中间件必须共用同一个 ContextVar，不能两份同名变量各自为政。"""
    from app.core import logging as app_logging
    from app.core.request_id import request_id_var

    assert app_logging.request_id_var is request_id_var


# ---------------------------------------------------------------------------
# 直接驱动 ASGI 层：覆盖 httpx 构造不出来的头（非 ASCII 字节 / CRLF）与 reset 语义
# ---------------------------------------------------------------------------


def _asgi_scope(headers: list[tuple[bytes, bytes]]) -> dict:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }


async def _drive(middleware, headers: list[tuple[bytes, bytes]]):
    scope = _asgi_scope(headers)
    messages: list[dict] = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    await middleware(scope, receive, send)
    return scope, messages


def _response_header(messages: list[dict], name: bytes = b"x-request-id") -> bytes | None:
    for message in messages:
        if message["type"] == "http.response.start":
            for key, value in message["headers"]:
                if key == name:
                    return value
    return None


@pytest.mark.asyncio
async def test_contextvar_is_set_inside_and_reset_after_request():
    """同一 asyncio context 内验证 set/reset（TestClient 跑在 portal 线程，证明不了）。"""
    seen = {}

    async def ok_app(scope, receive, send):
        seen["request_id"] = get_request_id()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    await _drive(RequestIDMiddleware(ok_app), [(b"x-request-id", b"reset-ok")])
    assert seen["request_id"] == "reset-ok"
    assert get_request_id() is None

    async def boom_app(scope, receive, send):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await _drive(RequestIDMiddleware(boom_app), [(b"x-request-id", b"reset-boom")])
    assert get_request_id() is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        # 重复头：第一个决定，后面的忽略
        ([(b"x-request-id", b"first-one"), (b"x-request-id", b"second-two")], "first-one"),
        # 第一个非法时不被后续合法值「救回」，直接重新生成
        ([(b"x-request-id", b"has space"), (b"x-request-id", b"second-two")], None),
        # 非 ASCII 原始字节（httpx 构造不出来）
        ([(b"x-request-id", b"\xff\xfe")], None),
        # CRLF / 头注入尝试
        ([(b"x-request-id", b"evil\r\nx-injected: 1")], None),
        # ASGI 要求小写头名，但大写也应能识别
        ([(b"X-Request-ID", b"upper-header")], "upper-header"),
    ],
)
async def test_scope_level_header_matrix(headers, expected: str | None):
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    scope, messages = await _drive(RequestIDMiddleware(app), headers)
    request_id = scope["state"]["request_id"]

    if expected is None:
        uuid.UUID(request_id)  # 非法头 → 重新生成合法 UUID
    else:
        assert request_id == expected

    echoed = _response_header(messages)
    assert echoed == request_id.encode("ascii")
    assert b"\r" not in echoed and b"\n" not in echoed


def _boom_app() -> FastAPI:
    """mini app：未处理异常走 catch-all handler（ServerErrorMiddleware，RequestID 外层）。"""
    app = FastAPI()

    @app.get("/boom")
    async def boom():
        raise RuntimeError("boom")

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):
        request_id = getattr(request.state, "request_id", None)
        return JSONResponse(
            {"ctx_request_id": get_request_id(), "state_request_id": request_id},
            status_code=500,
            headers={"X-Request-ID": request_id} if request_id else None,
        )

    app.add_middleware(RequestIDMiddleware)
    return app


def test_catch_all_exception_handler_uses_state_not_contextvar():
    """500 兜底 handler 在 ServerErrorMiddleware 里执行，contextvar 已被 reset。

    W0-5 的全局兜底错误帧必须用 ``request.state.request_id``；本测试钉住该契约。
    """
    client = TestClient(_boom_app(), raise_server_exceptions=False)
    response = client.get("/boom", headers={"X-Request-ID": VALID_ID})

    assert response.status_code == 500
    body = response.json()
    assert body["state_request_id"] == VALID_ID
    assert body["ctx_request_id"] is None
    assert response.headers["X-Request-ID"] == VALID_ID


# ---------------------------------------------------------------------------
# 真实 app 接线：中间件确实挂在 main.py 的栈里
# ---------------------------------------------------------------------------


def test_real_app_echoes_valid_inbound_id(client: TestClient):
    response = client.get(PROBE_PATH, headers={"X-Request-ID": VALID_ID})

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == VALID_ID


def test_real_app_generates_id_when_header_missing(client: TestClient):
    response = client.get(PROBE_PATH)

    uuid.UUID(response.headers["X-Request-ID"])


def test_real_app_regenerates_invalid_inbound_id(client: TestClient):
    response = client.get(PROBE_PATH, headers={"X-Request-ID": "a" * 65})

    generated = response.headers["X-Request-ID"]
    assert generated != "a" * 65
    uuid.UUID(generated)


@pytest.mark.asyncio
async def test_real_app_catch_all_handler_sets_header_and_body_request_id():
    """main.py 的兜底 handler 由 ServerErrorMiddleware 直接发响应，需自行补头。"""
    from app.main import app

    handler = app.exception_handlers[Exception]
    request = SimpleNamespace(state=SimpleNamespace(request_id=VALID_ID))

    response = await handler(request, RuntimeError("w0rid-boom"))

    assert response.status_code == 500
    assert response.headers["X-Request-ID"] == VALID_ID
    assert json.loads(response.body)["request_id"] == VALID_ID


@pytest.mark.asyncio
async def test_real_app_catch_all_handler_log_carries_request_id():
    """5xx 日志也必须能按 request_id 关联（handler 内临时绑定 state 的 id）。"""
    import logging as std_logging

    from app.core.logging import JsonFormatter
    from app.main import app

    payloads: list[dict] = []

    class Capture(std_logging.Handler):
        def emit(self, record):
            # JsonFormatter 在 emit 时读 contextvar，模拟真实日志管线。
            payloads.append(json.loads(self.format(record)))

    capture = Capture()
    capture.setFormatter(JsonFormatter())
    target_logger = std_logging.getLogger("app.main")
    target_logger.addHandler(capture)
    try:
        handler = app.exception_handlers[Exception]
        request = SimpleNamespace(state=SimpleNamespace(request_id=VALID_ID))
        await handler(request, RuntimeError("w0rid-log-boom"))
    finally:
        target_logger.removeHandler(capture)

    matching = [p for p in payloads if "未处理的异常" in p.get("message", "")]
    assert matching, "兜底 handler 应记录一条错误日志"
    assert matching[-1]["request_id"] == VALID_ID
    assert get_request_id() is None  # 临时绑定已复位，不泄漏到后续上下文


def test_request_id_middleware_runs_after_cors():
    """Starlette：``user_middleware[0]`` 最外层，索引越大越内层。

    RequestID 注册在 CORS 之前（main.py 里先 add），因此列表里排在 CORS 之后，
    执行时位于 CORS 内侧 —— 即「在 CORS 之后」运行。断言的是相对顺序而非
    具体索引，Starlette 若改变 insert/reversed 语义，本测试会先报警。
    """
    from app.main import app

    names = [middleware.cls.__name__ for middleware in app.user_middleware]
    assert "RequestIDMiddleware" in names
    assert "CORSMiddleware" in names
    assert names.index("RequestIDMiddleware") > names.index("CORSMiddleware")
