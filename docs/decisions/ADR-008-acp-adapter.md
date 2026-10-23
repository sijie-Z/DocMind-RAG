# ADR-008：ACP v1 适配器（Zed Agent Client Protocol）

- 状态：已采纳（issue #481）
- 日期：2026-10-23
- 关联：Epic #473；#479（Profiles/Bundles/Patch 与统一入口）；#480（双向
  JSON-RPC dispatcher / API Key / `session_service` 助手，ADR-007）；#476
  （`agent_tasks.session_id`、`PAUSED` 状态）；#475（用户命令事件）；#474（hook_error 错误码）

> 命名：本 ADR 的 ACP 指 Zed 发起的 **Agent Client Protocol**
> （agentclientprotocol.com），不是本仓库 `AGENTS.md` 的 AI Collaboration Protocol。

## 结论

1. **进程形态**：`python -m app --profile acp --org-id <id> --user-id <id>
   [--api-key-file <file>]`，stdio 上的 NDJSON（单行 ≤ 1MB），stdout 只允许
   协议帧——真实 stdout 交给 #480 的 `ProtocolWriter`（写锁 + flush），
   `sys.stdout` 在入口即指向 stderr；协议版本锁定 **v1**，不实现 v2。
2. **帧与双向请求全部复用 #480 的 `app/rpc/dispatcher.py`**：请求 id 表、
   重复 id → `-32600`、并发执行、双向服务端请求、`$/cancel_request`
   均在 dispatcher 内；ACP 层只做方法映射、事件转换与身份校验，不另写
   JSON-RPC 解析。`session/cancel` 是通知（无 result）：连接循环单独拦截
   id-less 的 `session/cancel` 交给处理器，dispatcher 的 `$/cancel_request`
   转发通道复用同一个处理器。
3. **方法面 9 个**：`initialize` / `authenticate` / `session/new` / `session/list`
   / `session/resume` / `session/close` / `session/set_config_option` /
   `session/prompt` / `session/cancel`。`session/load` 不在方法表 →
   dispatcher 自动返回 **-32601**；未知方法同。`session/list|resume|close|
   set_config_option` 是 DSH 扩展方法（`agentCapabilities.dshExtensions=true`）。
4. **能力声明**：`initialize` 返回 `protocolVersion=1`、
   `agentCapabilities:{loadSession:false, promptCapabilities:{image:false,
   audio:false, embeddedContext:false}, dshExtensions:true}`、
   `agentInfo:{name:"DocMind", version:<APP_VERSION>}`。客户端 FS / terminal /
   MCP-over-ACP / fork / 命令交互 / elicitation 一律不支持（不声明、不实现）。
5. **身份显式注入**：`--org-id/--user-id` 必填；校验用户存在、
   `is_active=true`、属于该组织（主组织字段命中或 `user_organization` 成员
   行命中）；`--api-key-file` 提供时 key 必须能认证为同一用户，且 key 自带
   组织（若有）必须等于 `--org-id`。任何失败 → stderr 原因 + **exit 2**，
   stdout 0 字节（协议未启动）。
6. **会话映射**：`session/new` 调 `get_or_create_session(db, None, user_id,
   "ACP session", org_id)` 创建 `ChatSession` 并返回其 id；`session/list`
   返回当前用户最多 50 条（`sessionId/title/updatedAt`，本组织 + 历史
   `organization_id IS NULL`）；`session/resume` 用 #480 的
   `get_latest_task_id` + `prepare_resume` 认领任务后**后台续跑**，不回放
   历史；`session/close` 只解除连接映射、不删库；`session/set_config_option`
   的 `model` / `plan_mode` 写入 `ChatSession.settings["model"]` /
   `settings["plan_approval_mode"]`，下一轮 prompt 读取。
7. **事件映射**（当前 `EventType` 共 **23** 种；issue 文本写 24 是过时口径，
   `hook_error` 是工具错误码不是事件）：`chunk→agent_message_chunk`、
   `thinking→agent_thought_chunk`、`tool_call→tool_call(pending)`、
   `tool_result→tool_call_update(completed, content)`、`tool_error→
   tool_call_update(failed)`、`plan_step/plan_complete→plan(entries)`、
   `approval_required→session/request_permission`、`warning→仅日志`、
   `done→stopReason:"end_turn"`、`run_cancelled→"cancelled"`、
   `error→-32603（脱敏）`、`plan_approval_required→策略见 §8`。issue 表未
   覆盖的 `command_result/command_error` 复用消息块通道（否则命令输出在
   ACP 客户端不可见）；其余诊断类事件仅日志（见 `app/acp/mapping.py` 的
   逐类型审计）。
8. **串行更新与结算**：同一会话的 `session/update` 进 `asyncio.Queue`，
   由每会话单 sender 任务按序写出；prompt 返回前 `await queue.join()`，
   再落库（`save_session_interaction`）→ 最后返回 `stopReason`。每会话一把
   `asyncio.Lock`，并发 prompt → **-32009**；未知/关闭的会话 → **-32004**；
   参数错误 → **-32602**；`error` 事件 → **-32603**（白名单脱敏文案）。
9. **取消**：`session/cancel` 置 `cancel_event`、清空队列中未发送的 update、
   取消在途审批等待，并（若有任务行）`WAITING_APPROVAL/WAITING_PLAN_APPROVAL
   → CANCELLED`，运行中任务置协作式取消旗；此后该会话不再外发任何 update，
   prompt 最终返回 `stopReason:"cancelled"`（即使底层 run 恰好自然结束）。
10. **审批（双向请求）**：`approval_required` → 服务端请求
    `session/request_permission`（wire 形状严格按 issue §5.4：
    `toolCall:{toolCallId,title,status}` + `options:[{optionId,name,kind}]`）；
    客户端 `{outcome:{outcome:"selected",optionId:"allow_once"}}` → approve，
    `reject_once` → deny；`{outcome:{outcome:"cancelled"}}` / dispatch 断连
    载荷 / **`ACP_PERMISSION_TIMEOUT_SECONDS=300` 超时** → deny。
    deny/approve 都经 `checkpoint.apply_approval_decision` 记账并续跑
    `resume_chat(claim=False)`；客户端断连时所有待审批按"取消"收尾
    （任务终态 CANCELLED），**绝不悬空**。
11. **计划审批**：ACP 不支持交互式计划审批，策略由
    `ACP_PLAN_APPROVAL_MODE` 决定：`reject`（默认）→ `mark_terminal(
    REJECTED, error_type="plan_approval_unsupported")` 并让 prompt 返回
    **-32009**；`auto` → 记录审计日志（session/task/plan）后
    `apply_plan_approval_decision("approve")` 并续跑。两者都保证任务不
    停在 `WAITING_PLAN_APPROVAL`。

## 依据（实测，本 worktree / 基线 7d62000）

- `python -m pytest tests/unit/test_acp_mapping.py tests/api/test_acp_stdio.py`
  → **49 passed**（33 单测 + 16 stdio e2e；sqlite + fake service + 内存管道，
  无网络/无真实 LLM）。
- e2e 覆盖：握手形状（fixture 校验）、`session/new` 落库、`session/list`、
  `session/close` 仅解映射、`session/load→-32601`、更新顺序
  （message→tool pending→completed→thought）、并发 prompt `-32009`、取消后
  无迟到 update 且 `stopReason=cancelled`、审批 allow/reject/超时三路径、
  计划审批 reject(`-32009`+REJECTED)/auto(approve+续跑)、`session/resume`
  不回放历史、`set_config_option` 持久化、身份校验四类失败、真实入口子进程
  （无身份 exit 2 且 stdout 0 字节；sqlite 身份下完成握手/new/list/close、
  退出码 0）。
- 变异试验（手动改码 → 用例红 → 还原）：
  - `session/load` 伪造成成功 → `test_session_load_returns_method_not_found` 红；
  - 审批超时改成放行 → `test_permission_timeout_denies_and_task_not_dangling` 红。
- `python scripts/check_doc_facts.py` 派生字段无变化（ACP 是 stdio 入口，
  不新增 HTTP 端点/前端路由/工具/SSE 事件类型），故未改 `docs/facts.json`
  与 README 数字。

## 被否决的方案

1. **本地 HTTP 桥接**（起 FastAPI 再让 ACP 适配器用 HTTP/SSE 调自己）：
   多一层故障点与进程生命周期，stdio 进程内直接调 `AgentService` 更短。
   issue §6 亦明确不推荐。
2. **直接复用 `RPCService` 作为 ACP 方法面**：两者的 wire 契约不同
   （RPC 用 `clientMessageId`、`session.event`、`approvals/respond`；ACP 用
   `prompt:[{type,text}]`、`session/update`、`session/request_permission`），
   硬套会把两套语义搅在一起；只复用其下层 `Dispatcher`。
3. **参数化 `app/rpc/server.py::run_stdio` 以共用连接循环**：需要在 #480
   的已评审文件里扩参（超出 #481 白名单，放大回归面）；改为在
   `app/acp/server.py` 组合 `ProtocolWriter` / `Dispatcher`，stdin 泵与
   信号收尾保持同构。
4. **支持 `session/load` / 交互式计划审批 / `allow_always`**：issue 非目标，
   ACP 模式下没有"回放历史"和"计划批准 UI"的位置；`session/load` 明确
   `-32601`，权限只给一次性 allow/deny。
5. **审批超时按"保持等待"**：会让任务永久停在 `WAITING_APPROVAL`，违背
   "任务不得悬空"；按 deny 收尾并在审批账本留痕。
6. **客户端 FS/terminal/MCP-over-ACP**：安全面与实现成本都不在本期范围，
   `promptCapabilities` 与能力位全部显式 false。

## Zed 接入（操作步骤）

1. 准备一个有效的 DocMind 身份：`<org_id>`（组织）与 `<user_id>`（用户），
   用户须属于该组织且 `is_active=true`；如需 API Key（`dm_` 前缀），写入
   仅本人可读的文件（如 `~/docmind-acp.key`）。
2. Zed `settings.json` 增加 agent server（把 `cwd` / python 换成本机绝对路径；
   建议用后端虚拟环境的 python，并确保 `DATABASE_URL` 等配置与该进程一致）：

   ```json
   {
     "agent_servers": {
       "DocMind": {
         "command": "/path/to/DocMind/backend/.venv/bin/python",
         "args": [
           "-m", "app", "--profile", "acp",
           "--org-id", "1", "--user-id", "7",
           "--api-key-file", "/home/me/docmind-acp.key"
         ],
         "cwd": "/path/to/DocMind/backend"
       }
     }
   }
   ```

3. 在 Zed 的 Agent 面板选择 DocMind 新建会话；`session/new` 会创建
   `chat_sessions` 行，标题固定为 `ACP session`（首轮问答后沿用既有逻辑
   以首条消息补标题）。
4. 冒烟：发送一句普通问题；若配置了高风险工具（如 `execute_python`），
   应出现一次权限确认；取消后不应再收到迟到消息。

**支持 / 不支持清单**

| 能力 | 状态 |
|---|---|
| `initialize` / `authenticate` | 支持（本地信任；可选 API Key 一致性校验） |
| `session/new` / `list` / `resume` / `close` / `set_config_option` | 支持（DSH 扩展） |
| `session/prompt` 流式消息 / 思考 / 工具 / 计划更新 | 支持 |
| `session/request_permission`（一次性 allow/deny） | 支持（超时 300s → deny） |
| `session/cancel` / `$/cancel_request` | 支持 |
| `session/load`、ACP v2、fork、客户端 FS/terminal、MCP-over-ACP、图片/音频/内嵌上下文、交互式计划审批 | **不支持**（`session/load` 返回 `-32601`；能力位 false） |

**故障排查**

| 现象 | 原因 / 处理 |
|---|---|
| 进程立刻退出、退出码 2、stdout 无帧 | 缺 `--org-id/--user-id`、id 非正整数、用户不存在/被禁用/不属于该组织、`--api-key-file` 不可读或与身份不一致。stderr 有具体原因。 |
| JSON 解析错误 / Zed 显示协议异常 | 有第三方 `print` 写到了真实 stdout。入口已把 `sys.stdout` 指向 stderr；自建包装脚本不要绕过入口直接运行 `AcpServer`。 |
| `-32004` | 会话未在本连接中打开（先 `session/new` 或 `session/resume`），或会话不存在/不属于当前身份。 |
| `-32009` | 同会话已有进行中的 prompt；或计划审批在 `ACP_PLAN_APPROVAL_MODE=reject` 下被拒绝。 |
| 审批一直不出现/超时 | 客户端未实现 `session/request_permission` 响应；300s 后服务端按 deny 继续（任务不悬空）。 |
| `session/resume` 返回 `-32004` | 会话没有非终态任务（已完成/已取消的任务不恢复）。 |
| DB/Redis 不可用 | 会话创建与任务检查点依赖 MySQL；取消旗依赖 Redis（不可用时协作式取消会如实失败，不会伪报成功）。 |

## 已知不确定项与风险

- **协议版本锁定 v1**：v2 仍是草案；升级需重做 fixture 与 wire 测试。
- **fixture 的字段口径**：`tests/fixtures/acp_v1_schema.json` 按 issue §5.2/
  §5.4 与 ACP v1 公开 schema 的字段名编写，未在 Zed 真机做端到端联调
  （本环境无 Zed）；若真机字段有出入，以 fixture 与官方 schema 的 diff 为准。
- **真实 LLM e2e**：本层用 fake `AgentService` 验证协议面；真实模型/工具链
  的 e2e 由监督方在有 `DEEPSEEK_API_KEY` 的环境执行。
- **resume 的后台失败**：`session/resume` 是"快速返回 + 后台续跑"，后台
  失败只能记日志（没有 prompt 请求可以承载错误响应）；客户端会看到流停止
  而不再有 `session/update`。
- **tool_result 内容截断**：超过 8000 字符的工具有效输出在 update 中截断
  （防止单行 NDJSON 过大）；完整内容仍按既有 SSE/持久化通道处理。
- **事件类型计数**：issue 文本写 24 种，代码 `EventType` 为 23 种
  （`hook_error` 是工具错误码，不是事件）；以代码为准，并在单测中做
  逐类型审计，新增事件不登记会拉红。
