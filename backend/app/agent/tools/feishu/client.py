"""飞书 Open API 客户端 — 多维表格 (Bitable) 相关接口。

当前工具:
    FeishuBitableClient.list_records()  — 查询记录列表

飞书 API 参考:
    https://open.feishu.cn/document/server-docs/docs/bitable-v1/app-table-record/list

错误码参考:
    99991663 — token 过期
    99991400 — 参数错误
    99991661 — 权限不足
    99991672 — 请求频率超限
    -1       — 未知系统错误
"""

import logging
from typing import Any

import httpx

from app.agent.tools.feishu.auth import FeishuTenantAuthHandler

logger = logging.getLogger(__name__)

# 飞书多维表格 API 基础路径
BITABLE_BASE = (
    "https://open.feishu.cn/open-apis/bitable/v1/apps/{app_token}/tables/{table_id}/records"
)

# 飞书错误码 → 标准化 error code 映射
# 供 Tool 层直接使用（也可作为 error_mapper hook 的补充）
FEISHU_ERROR_MAP: dict[int, tuple[str, str]] = {
    99991663: ("token_expired", "飞书 tenant_access_token 已过期"),
    99991400: ("invalid_param", "飞书 API 参数错误，请检查输入"),
    99991661: ("permission_denied", "没有权限访问该多维表格"),
    99991672: ("rate_limited", "飞书 API 请求过于频繁"),  # 频率超限
    99991401: ("invalid_param", "请求体格式错误"),
    99991402: ("invalid_param", "app_token 或 table_id 不存在"),
    99991403: ("permission_denied", "无权限操作该资源"),
    10001: ("token_expired", "飞书 token 已失效，请重新授权"),
}


class FeishuAPIError(Exception):
    """飞书 API 返回的业务错误（code != 0）。"""

    def __init__(self, feishu_code: int, message: str, response: Any = None):
        self.feishu_code = feishu_code
        self.message = message
        self.response = response
        # 映射到标准化 code
        std_code, _ = FEISHU_ERROR_MAP.get(feishu_code, ("api_error", message))
        self.standard_code = std_code
        super().__init__(f"[{std_code}] (飞书 code={feishu_code}) {message}")


class FeishuBitableClient:
    """飞书多维表格 API 客户端。

    职责:
        - 持有认证 token
        - 实际的 HTTP 请求
        - 错误封装（FeishuAPIError）
        - 不处理业务策略（重试、降级等由 Tool 层处理）
    """

    def __init__(
        self,
        auth_handler: FeishuTenantAuthHandler | None = None,
    ):
        self._auth = auth_handler or FeishuTenantAuthHandler()
        self._http: httpx.AsyncClient | None = None

    async def _get_headers(self) -> dict[str, str]:
        token = await self._auth.get_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        }

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=15.0)
        return self._http

    async def list_records(
        self,
        app_token: str,
        table_id: str,
        *,
        page_size: int = 20,
        page_token: str | None = None,
        field_names: list[str] | None = None,
        filter_expr: str | None = None,
    ) -> dict[str, Any]:
        """查询多维表格记录列表。

        Args:
            app_token:  多维表格的 app_token。
            table_id:   数据表 ID。
            page_size:  每页记录数 (1-500, 默认 20)。
            page_token: 分页游标（首次查询不传）。
            field_names: 指定返回的字段名列表（为空则返回全部字段）。
            filter_expr: 筛选条件（飞书公式语法）。

        Returns:
            飞书 API 的原始响应 json (已 decode)。

        Raises:
            FeishuAPIError: 飞书 API 返回业务错误。
            httpx.HTTPError: HTTP 层面错误（超时、连接失败等）。
        """
        url = BITABLE_BASE.format(app_token=app_token, table_id=table_id)
        headers = await self._get_headers()
        client = await _get_client()

        params: dict[str, Any] = {"page_size": max(1, min(page_size, 500))}
        if page_token:
            params["page_token"] = page_token
        if field_names:
            params["field_names"] = field_names  # type: ignore[assignment]
            # 飞书 API 接受重复的 field_names query param
            # httpx 的 params 传 list 会序列化为多个同名参数，符合要求
        if filter_expr:
            params["filter"] = filter_expr

        try:
            resp = await client.get(url, headers=headers, params=params)
            resp.raise_for_status()
        except httpx.TimeoutException:
            raise FeishuAPIError(-1, "飞书 API 请求超时")
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            text = (await e.response.aread()).decode("utf-8", errors="replace")[:200]
            if code == 401:
                raise FeishuAPIError(10001, f"认证失败 (HTTP {code}): {text}")
            if code == 429:
                raise FeishuAPIError(99991672, f"请求频率超限 (HTTP {code})")
            if code >= 500:
                raise FeishuAPIError(-1, f"飞书服务异常 (HTTP {code}): {text}")
            raise FeishuAPIError(-1, f"HTTP 请求失败 (HTTP {code}): {text}")

        body = resp.json()
        feishu_code = body.get("code", -1)
        if feishu_code != 0:
            msg = body.get("msg", "未知错误")
            raise FeishuAPIError(feishu_code, msg, response=body)

        return body.get("data", {})

    async def close(self) -> None:
        if self._http:
            await self._http.aclose()
            self._http = None


# 模块级 httpx 客户端复用（减少连接建立开销）
_shared_client: httpx.AsyncClient | None = None


async def _get_client() -> httpx.AsyncClient:
    """获取共享的 httpx 客户端实例。"""
    global _shared_client
    if _shared_client is None:
        _shared_client = httpx.AsyncClient(
            timeout=15.0, limits=httpx.Limits(max_keepalive_connections=5)
        )
    return _shared_client
