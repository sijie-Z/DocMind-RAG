"""飞书多维表格查询工具 — MCP Tool 注册。

工具:
    feishu_bitable_query — 读取多维表格记录（只读，支持分页/过滤/字段筛选）

使用示例 (Agent 调用):
    await feishu_bitable_query(
        app_token="OIB...VAb",
        table_id="tbl...Xc2",
        page_size=50,
        filter='CurrentValue.[状态]="待审批"',
    )

依赖:
    httpx
    环境变量: FEISHU_APP_ID, FEISHU_APP_SECRET
"""

import logging
import os
from typing import Any

from app.agent.registry import ToolResult, register_tool
from app.agent.tools.feishu.auth import FeishuAuthError
from app.agent.tools.feishu.client import FEISHU_ERROR_MAP, FeishuAPIError, FeishuBitableClient

logger = logging.getLogger(__name__)


def _feishu_configured(config) -> bool:
    """issue #509：飞书工具只在凭据齐备时注入 schema（默认关闭）。

    凭据读取与 ``feishu/auth.py`` 同源（FEISHU_APP_ID / FEISHU_APP_SECRET），
    调用时读取，避免 import 期快照。
    """
    return bool(os.getenv("FEISHU_APP_ID") and os.getenv("FEISHU_APP_SECRET"))


@register_tool(
    name="feishu_bitable_query",
    description=(
        "查询飞书多维表格记录（只读）。"
        "支持分页、字段筛选和条件过滤。"
        "适用于读取审批列表、项目数据、任务看板等结构化数据。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "app_token": {
                "type": "string",
                "description": "飞书多维表格的 app_token（可从多维表格 URL 中获取）",
            },
            "table_id": {
                "type": "string",
                "description": "数据表 ID（格式: tblxxxx）",
            },
            "page_size": {
                "type": "integer",
                "description": "每页记录数，最大 500，默认 20",
                "default": 20,
            },
            "page_token": {
                "type": "string",
                "description": "分页游标。首次查询不传，后续查询从上一次返回的 page_token 获取",
            },
            "field_names": {
                "type": "array",
                "items": {"type": "string"},
                "description": "指定要返回的字段名列表。为空则返回全部字段（推荐只传需要的字段以减少 token 消耗）",
            },
            "filter": {
                "type": "string",
                "description": ('筛选条件，使用飞书公式语法。示例: `CurrentValue.[状态]="待审批"`'),
            },
        },
        "required": ["app_token", "table_id"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "records": {
                "type": "array",
                "description": "记录列表，每条记录包含 fields 和 record_id",
            },
            "page_token": {
                "type": "string",
                "description": "下一页游标。为空时表示已无更多数据",
            },
            "has_more": {
                "type": "boolean",
                "description": "是否还有更多数据",
            },
            "total": {
                "type": "integer",
                "description": "当前页返回的记录数",
            },
        },
    },
    # ── v2.1 语义定义 ──
    category="feishu",
    enabled_predicate=_feishu_configured,
    semantic={
        "type": "read_only",
        "domain": "feishu_bitable",
        "idempotent": True,
        "retry_safe": True,
        "consistency": "eventual",
        "parallel_safe": True,
    },
    # ── 认证与管控 ──
    auth_handler="feishu_tenant_token",
    rate_limit=10,
    timeout=8,
)
async def feishu_bitable_query(
    app_token: str,
    table_id: str,
    page_size: int = 20,
    page_token: str | None = None,
    field_names: list[str] | None = None,
    **_: Any,
) -> ToolResult:
    """查询飞书多维表格记录（只读）。

    返回 ToolResult:
        success=True:
            data = {
                "records":    [...],    # 记录列表
                "page_token": "..." or None,
                "has_more":   True/False,
                "total":      int,
            }
        success=False:
            error = { "code": "...", "message": "..." }
    """
    # 从 kwargs 中提取 filter 参数（避免遮蔽 Python 内置函数 filter）
    filter_expr = _.get("filter")

    client = FeishuBitableClient()

    try:
        # Step 1: 调用飞书 API
        data = await client.list_records(
            app_token=app_token,
            table_id=table_id,
            page_size=page_size,
            page_token=page_token,
            field_names=field_names,
            filter_expr=filter_expr,
        )

        # Step 2: 提取并精简返回数据
        items = data.get("items", [])
        records = [_simplify_record(r) for r in items]
        next_page_token = data.get("page_token")
        has_more = data.get("has_more", False)

        return ToolResult.ok(
            data={
                "records": records,
                "page_token": next_page_token,
                "has_more": has_more,
                "total": len(records),
            },
        )

    except FeishuAuthError as e:
        # 认证配置问题 → 不重试，返回可读提示
        return ToolResult.fail(
            code="auth_config_error",
            message=f"飞书认证失败: {e}",
        )

    except FeishuAPIError as e:
        # 飞书 API 业务错误 → 映射为标准 code
        std_code, hint = FEISHU_ERROR_MAP.get(
            e.feishu_code,
            ("api_error", f"飞书 API 错误 (code={e.feishu_code})"),
        )

        return ToolResult.fail(
            code=std_code,
            message=f"{hint}: {e.message}",
            raw=e.response,
        )

    except Exception as e:
        # HTTP 层面错误（超时、连接失败等）→ 由 built-in error_mapper hook 进一步分类
        logger.error("飞书工具执行异常: %s", e, exc_info=True)
        return ToolResult.fail(
            code="unknown",
            message=f"飞书 API 调用异常: {type(e).__name__}: {e}",
            raw=e,
        )


def _simplify_record(record: dict[str, Any]) -> dict[str, Any]:
    """精简单条飞书记录，只保留关键字段，移除飞书元数据。

    原始飞书记录格式:
        {
            "record_id": "rec...",
            "fields": {
                "姓名": "张三",
                "状态": "待审批",
                ...
            },
            "created_by": {...},
            "created_time": 1234567890,
        }

    精简后:
        {
            "id": "rec...",
            "fields": {...},
        }
    """
    return {
        "id": record.get("record_id", ""),
        "fields": record.get("fields", {}),
    }
