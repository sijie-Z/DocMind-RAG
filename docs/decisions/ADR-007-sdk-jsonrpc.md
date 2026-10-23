# ADR-007：SDK 面 —— API Key、SSE 续传、双向 JSON-RPC 与 stdio 身份

- 状态：已采纳（issue #480）
- 日期：2026-10-23
- 关联：Epic #473；#479（Profiles/Bundles/Patch，ADR-003）；#476（会话 fork）；
  #481（ACP，依赖本 ADR 的双向 dispatcher）

## 结论

1. **API Key 存储用 bcrypt + 明文前缀定位**：新表 `api_keys` 只存
   `key_hash`（bcrypt）与 `prefix`（`dm_` + 9 位随机的前 12 字符）；明文仅在
   创建响应出现一次。认证走 `Authorization: Bearer dm_...`，在
   `auth_service.get_current_user` 顶部按前缀分流；JWT 路径逐字节不变。
   旧 `users.api_key`（明文 `sk-`）保留但 deprecated，无认证消费者，新认证
   只接受 `dm_` 前缀。Scope 用 JSON 数组，`require_scope(scope)` 依赖对 JWT
   用户（`api_key_scopes is None`）视为全量，对 `dm_` key 强制子集校验。
2. **SSE 续传用 Redis ring buffer**：统一 `app/core/sse.py` 的
   `format_sse(event, data, event_id)`；`stream_id = session_id or task_id`，
   `id = "{stream_id}:{seq}"`；Redis 键带调用者命名空间（`sse:seq:{scope}:{stream_id}` /
   `sse:buf:{scope}:{stream_id}`，scope=`u{user_id}`），`seq = INCR`，
   `LPUSH` + `LTRIM 0 999` + `EXPIRE 3600`。请求头
   `Last-Event-ID` 回放 `seq > last` 的条目；首条 `seq > last + 1`、流键不匹配、
   未来序号或缓冲为空但序号已前进 → 流内 `event: error` +
   `{"code":"SSE_RESUME_GONE"}`（HTTP 200）。**续传请求只回放、不重跑 pipeline**；
   回放前必须先做会话归属校验（会话 UUID 不是授权凭证）；命名空间隔离同时阻断
   跨用户的读回放与写注入（他人无法写/回放受害者的 buffer 键）。
   续传端点（resume/审批）的回放判定前移到认领/记账之前，避免 GONE 卡住任务。
3. **断连保留有上限**：`SSE_KEEPALIVE_ON_DISCONNECT=true` 时，响应生成器被
   关闭后由 `keepalive_stream` 继续消费 pipeline（事件继续写 ring buffer），
   最长 `SSE_ORPHAN_TTL_SECONDS=600` 后取消；关闭开关则恢复旧行为（立即取消）。
   `chat.py` 的 RAG pipeline 与 `agent.py` 的四个 SSE 端点统一经该包装器。
4. **双向 JSON-RPC dispatcher**：`app/rpc/dispatcher.py` 维护在途请求 id 表
   （重复 id → `-32600`）、并发执行、按 id 乱序回写；服务端→客户端请求
   （`session/request_permission`）用 `pending_server_requests` + Future，
   超时 `RPC_PERMISSION_TIMEOUT_SECONDS=300` 按拒绝处理；`$/cancel_request`
   取消对应服务端请求并转发 `session/cancel`；连接关闭时拒绝全部 pending、
   取消在途任务。stdio（NDJSON，单行 ≤1MB）与 HTTP（`POST /api/v1/rpc`，
   API Key + `rpc` scope，批量 ≤20）共用同一 dispatcher 与方法表。
5. **stdio 身份只用 CLI 参数**：`python -m app --profile sdk --org-id ... --user-id ...`
   缺失即 exit 2（stdout 0 字节）；`--api-key-file` 可选且只从文件读（不进
   argv/日志）；stdout 只允许协议帧——真实 stdout 交给 `ProtocolWriter`，
   `sys.stdout` 重定向到 stderr，已挂 stdout 的日志 handler 一并迁移。
6. **方法面 8 个**：`initialize`（protocolVersion=1）、`session/prompt`
   （`clientMessageId` 幂等：JSON-RPC 用 `rpc:msg:*`、REST 用 `rest:msg:*` TTL 24h）、
   `session/resume`、`session/wait`、`session/cancel`（先回 `{cancelled:true}`，
   之后不再外发该会话的 `session.event`）、`approvals/respond`、
   `tools/list`（`ToolRegistry.get_manifest()`）、`shutdown`。通知为
   `session.event` / `session.status`；`session → 最新非终态 task` 由
   `session_service.get_latest_task_id` 解析，没有则 `-32004`。

## 依据

### 实测（本 worktree，基线 71addabe）

- 派生事实：`python scripts/check_doc_facts.py --print-derived` →
  `endpoint_modules=26`（新增 `/api-keys` 与 `/rpc`）、`http_route_decorators=194`。
  注意 `http_route_decorators` 的口径是 `backend/app/api/v1/endpoints/**`；
  `app/rpc/http.py` 不在该目录，故 `/rpc` 的装饰器不计入该数（模块数计入
  `ROUTER_SPECS` 键）。
- API Key 认证矩阵（`tests/api/test_api_keys.py`，8 passed）：有效 200、
  过期/吊销/未知前缀 401、缺 scope 403、明文只返回一次、列表不含 `key_hash`。
- SSE 续传（`tests/unit/test_sse_format.py` + `tests/api/test_sse_resume.py`，
  17 passed）：事件 id 递增、回放第 3-5 个事件不重复且**不重跑 pipeline**、
  跨用户读回放被归属校验拦截、跨用户写注入只落自己的命名空间、未绑定流/未来
  seq 拒绝回放、淘汰/流键不匹配 → `SSE_RESUME_GONE`、REST `clientMessageId`
  幂等、TTL 前不取消 / TTL 后取消。
- dispatcher（`tests/unit/test_rpc_dispatcher.py`）：并发 20 乱序响应、重复 id
  `-32600`、取消转发 `session/cancel`、超时按拒绝、断连清理、prompt 幂等。
- HTTP RPC（`tests/api/test_rpc_http.py`）：开关关闭 404、无 Authorization 401、
  JWT 403、缺 rpc scope 403、initialize/tools.list/批量与上限。
- stdio 手测（管道）：`initialize`/`tools/list`（33 个工具）/`shutdown` 正常，
  缺失 `--org-id/--user-id` → exit 2 且 stdout 0 字节。
- SDK：Python 9 passed（httpx.MockTransport），TypeScript `tsc --noEmit` 与
  `npm test` 8 passed。

### 代码证据

- `backend/app/services/auth_service.py::get_current_user` 顶部 `dm_` 分支；
  `backend/app/core/security.py::require_scope`；
  `backend/app/models/api_key.py`、`backend/app/services/api_key_service.py`、
  `backend/app/api/v1/endpoints/api_keys.py`、`alembic/versions/014_api_keys.py`。
- `backend/app/core/sse.py`、`backend/app/api/v1/endpoints/{chat,agent}.py`、
  `frontend/src/utils/sseService.ts`（`id:` 解析、`Last-Event-ID`、
  `onEvent` 未知事件透传）。
- `backend/app/rpc/{dispatcher,server,http}.py`、`backend/app/entrypoints/sdk.py`；
  指标 `docmind_rpc_requests_total` / `docmind_rpc_request_duration_ms`。

### 推导

- `users.api_key` 无任何认证消费者（全仓 grep 仅生成/吊销端点与展示脱敏），
  因此"保留列 + 新前缀"不改变任何既有登录路径；若未来出现消费者，必须同一
  迁移改为 bcrypt。
- Redis 不可用时 SSE 事件退化为无 `id`（只记 warning），续传不可用但不阻断
  主流程；API Key 的 `last_used_at` 更新失败不影响认证结果。

## 被否决的方案

| 方案 | 否决理由 |
|---|---|
| 继续用明文 `users.api_key`（`sk-`） | 明文落库、无 scope/过期/吊销语义；泄漏即永久可用。只保留列兼容，不接认证。 |
| API Key 用 `argon2` | 现状密码链路已是 bcrypt（`auth_service.verify_password`），引第二套哈希只增加依赖与口径分裂；issue 明确复用 bcrypt。 |
| 用 JWT 代替 API Key 给 SDK | SDK 需要长期凭据与可独立吊销；JWT 无法表达 per-key scope，且泄漏后只能等过期。 |
| SSE 续传用进程内缓冲 | 多 worker/重启即失效；issue 明确 Redis ring buffer，且有界（1000 条/1h）。 |
| 断连即取消 | 用户网络抖动会白烧一轮 LLM；保留有上限（600s）兼顾成本与体验。 |
| HTTP 批量走独立管线 | 与 stdio 行为分裂；共用 dispatcher，批量上限 20 控制放大面。 |
| 双向请求只在 stdio 实现、HTTP 忽略 | 由 dispatcher 的能力探测表达（`supports_server_requests`），HTTP 客户端改用 `approvals/respond` 主动应答，不伪造成功。 |
| stdio 身份从环境变量读（`DOCMIND_*`） | 与 #479 的"不新增 `DOCMIND_` 前缀"裁定冲突；子进程身份显式从 CLI 传入更可审计。 |

## 已知不确定 / 边界

- **断连重连的语义**：`Last-Event-ID` 请求只回放 `seq > last` 的缓冲并关闭，
  **绝不重跑**；需要继续对话时请不带 `Last-Event-ID` 重新发起 prompt（REST 的
  `payload.clientMessageId` 与 RPC `session/prompt` 各自使用 `rest:msg:*` /
  `rpc:msg:*` 幂等键，互不串扰）。
  没有实现"附着到仍在运行的孤儿流"（需要事件总线化）；孤儿 run 由
  `SSE_ORPHAN_TTL_SECONDS` 兜底取消。
- **agent.py 的四个端点**没有独立的断连取消逻辑（生成器关闭即终止），
  keepalive 包装器只保证"关闭后不立即取消、TTL 兜底"；被包装的 agent 运行
  在 TTL 内继续推进，但客户端重连后拿到的是 ring buffer 中的事件，而非新流。
- **ring buffer 有界**：`SSE_BUFFER_MAXLEN=1000` / TTL 3600s，超出即淘汰，
  客户端会收到 `SSE_RESUME_GONE`。
- **stdio stdout 纯净**依赖进程内所有 `print` 走 `sys.stdout` 动态查找；直接
  持有原始 stdout 引用的第三方库仍可能污染协议（已知风险，未做文件描述符级
  重定向）。单行上限在 `readline(limit+1)` 处生效，超长行不整行读入。
- **API Key 轮换**：当前只支持创建/吊销，不支持原地轮换；轮换 = 建新 key →
  切换 → 吊销旧 key。
- **迁移 head**：本分支基线为 `012_session_fork`；#478 的 `013_webhooks` 合并
  后需把 `014_api_keys.down_revision` 顺延，禁止多 head。
