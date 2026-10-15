# MCP client（动态外部工具接入）

> 配置驱动把外部 MCP server 的工具注册进 Agent 工具面（`mcp__<server>__<tool>`）。
> 常驻会话 + 崩溃检测 + 惰性重连；服务器起不来只告警不阻塞启动。
> 代码：`backend/app/agent/mcp/`（config / client / mapping / manager）。

---

## 1. 怎么接一个 server（3 步）

1. 在 `backend/mcp_servers.json` 的 `servers` 里加一条（或复制 `servers` 里现成示例）：

```jsonc
{
  "servers": {
    "weather": {
      "transport": "stdio",              // stdio | http | sse
      "command": "npx",                  // stdio：可执行文件（PATH 解析，Windows 下也支持 npx）
      "args": ["-y", "mcp-server-weather"],
      "env": { "API_KEY": "${WEATHER_KEY}" },   // ${VAR} 会做环境变量展开
      "enabled": true,                   // 缺省 false：配置存在 ≠ 主动接入
      "timeout": 30                      // 单次请求秒数，缺省 30
    }
  }
}
```

2. 重启后端（lifespan 里 `mcp_manager.initialize()`）。启动日志出现
   `MCP server 'weather' 已连接（stdio），注册工具 N 个` 即成功。

3. Agent 即可调用 `mcp__weather__<工具名>`。不确定工具名时看启动日志或
   `mcp_manager.get_status()`。

HTTP（streamable HTTP，MCP 现代规范）：

```jsonc
"remote": {
  "transport": "http",
  "url": "https://example.com/mcp",
  "headers": { "Authorization": "Bearer ${TOKEN}" },
  "enabled": true
}
```

环境变量 `DOCMIND_MCP_SERVERS_CONFIG` 可覆盖配置文件路径；文件缺失 = MCP 不启用（零行为变化）。

## 2. 传输覆盖度

| 传输 | 状态 | 说明 |
|---|---|---|
| stdio | ✅ 完整 | initialize 握手 / tools list+call / 通知（list_changed）/ ping / 崩溃检测 / 重连；子进程经官方 SDK 以 Job Object 管理（Windows 树杀） |
| streamable HTTP | ✅ 完整 | 同一套 SDK 客户端：session id、协议版本头、SSE 响应解析、`terminate_on_close` DELETE 收尾。测试用 `httpx2.MockTransport` 全协议演练 |
| HTTP+SSE（旧传输） | ⚠️ 兼容项 | `"transport": "sse"` 走 SDK `sse_client`，未写专项测试（2024-11-05 旧规范，新 server 用 streamable http） |

## 3. 运行语义（故障时的行为）

| 场景 | 行为 |
|---|---|
| 启动时 server 起不来 / initialize 失败 / 超时（≤10s） | 该 server 工具**不注册** + warning，**不阻塞**其他 server 与主启动 |
| 调用时 server 已死（进程退出） | 结构化失败 `unreachable`（`ToolResult.fail`），连接标记 dead；**下一次调用自动重连** |
| 单次请求超时 | `timeout` 失败；会话存活可继续用（SDK 会向服务器发 courtesy cancel） |
| 服务器返回 isError / JSON-RPC error | `api_error` 失败，附原文；**绝不伪装成功**（#301 诚实原则） |
| 参数不符合工具 inputSchema | 本地 `validation_error` 直接拒绝，不发远端 |
| tools/list_changed 通知 | 防抖后重拉列表，增量注册/摘除；刷新失败保留现状 |
| 命名冲突（与其他 server 或既有工具撞名） | 首个注册者保留，后来者 warning 跳过 |
| 后端关闭 | `mcp_manager.shutdown()`：先摘动态工具再关连接（含子进程树） |

## 4. 实现要点（改代码前先读）

- **owner-task 模式**：SDK（mcp 2.x, anyio）要求上下文在同任务进出；每个连接由专属 asyncio
  task 持有 `stdio_client`/`ClientSession` 的生命周期，其他任务只跨任务调 session 方法。
  在别的任务里 close/reconnect 会触发 anyio cancel-scope 楔死（已实测），别改。
- **错误码方言**：`MCPError` 的 code=-32000（Connection closed，SDK 合成）→ 标记 dead；
  -32001 → 超时；其余 code 是服务器真实响应（服务器活着）。
- **stdio 编码**：`encoding_error_handler="replace"` 防止 server 用 GBK 等本地编码写 stdout
  时整个会话以“无响应超时”暴死（本机踩过：`UnicodeDecodeError` 打断读取流）。
- **注册表侧**：工具名为 `mcp__<server>__<tool>`（非法字符归一为 `_`，>64 字符用 sha1 尾缀）；
  tags=`["mcp","external","mcp:<server>"]`——主代理 `tool_tags=None` 可见，子代理
  （search/analysis 白名单）不可见；默认**不**加入 `DEFAULT_DISABLED_TOOLS`（enabled=true
  即主动暴露）；`tool_registry.unregister()` 为其生命周期服务。
- **老 `mcp_call` 工具**（tools/mcp_bridge.py）保持原样（默认禁用、硬编码 2 server），
  属历史通道；新接入一律走本模块。

## 5. 测试

- `tests/unit/test_mcp_config.py` / `test_mcp_mapping.py` — 纯本地单测；
- `tests/unit/test_mcp_client_stdio.py` — 真子进程 E2E（fixture：`tests/fixtures/fake_mcp_server.py`）；
- `tests/unit/test_mcp_manager.py` — 经全局 registry 的注册/冲突/刷新/关机；
- `tests/unit/test_mcp_http.py` — MockTransport 的 HTTP 全协议。

全部不依赖网络与真实 server。假 server 支持 `--silent`（初始化不响应）等故障注入。

## 6. 已知边界

- HTTP 的 server→client GET 流未消费（只走请求-响应 + SSE 响应流）；sampling / elicitation /
  roots 能力未声明（server 不会向我们发起这些请求；万一发起，一律回 method-not-found）。
- 暂未做 API/前端的管理面（服务器状态目前看日志或代码侧 `get_status()`）。
- 每个后端进程各自拉起一份 server 子进程（多 worker 部署 = 多份；未做中心化复用）。
