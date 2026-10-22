# ADR-002 · User Commands：命令与提示词模板的边界（2026-10-22）

- 状态：已实现（issue #475，#473 Epic Wave 1）
- 基线：`origin/develop` @ `4be6f0d7`
- 关联：`backend/app/agent/commands/`、`backend/app/api/v1/endpoints/commands.py`、`docs/decisions/ADR-001-路线冻结.md`（D3 管理员语义）

---

## 决策

### D1 · 命令是服务端对象，不是提示词模板

- 命令由后端 `Command` 描述符声明（name / aliases / description / args /
  permission / handler），经 `GET /api/v1/commands` 下发；前端本地斜杠菜单的
  模板文本只是**普通提示词**，与命令通道彻底分离。
- 只有注册到服务端 registry 的命令才走无模型通道；模板文本永远走模型。
- 否决方案：继续由前端本地展开模板。否决理由：模板无法执行系统动作，且前端
  文案与后端语义必然漂移（issue #475 §1 现状证据）。

### D2 · 命令不创建模型消息或轮次

- `execute(agent, line, ...)` 在进入 PER 之前短路（`/agent/chat`、
  `/chat/stream` useAgent 两个入口），不写用户消息、不建任务行、不调用
  `agent_service.chat`；输出经既有 SSE 信封（`command_start` /
  `command_result` / `command_error`）。`/agent/chat` 与普通路径一样以
  `done` 收尾（命令路径 `saved=false`）；`/chat/stream` 与它的普通路径一致
  不发 `done`，以 `message` 事件收尾（兼容聊天页现有渲染）。
- 兼容层：当前 Agent 页只消费 `chunk`，因此 `/agent/chat` 的命令结果在
  `command_*` 之后**同时**以 `chunk` 外发（不落库）；前端接入 `command_*`
  后可移除该兜底（issue #475 任务 4）。`/chat/stream` 同理以 `message` 兜底。
- 命令通道不处理附件 / @引用：附件不会进入模型，也不会参与命令；`/chat/stream`
  对携带附件的命令发 `warning` 如实提示，绝不静默假装已处理。
- 隐藏前提「命令文本绝不能作为 prompt 漏给模型」按 fail-closed 实现：
  `/` 前缀无法解析 → 显式错误，**不回落**给模型。关闭 `COMMANDS_ENABLED`
  时整段回退旧行为（斜杠文本按普通提示词处理），这是运维回滚口径，
  不是运行时语义。

### D3 · 作用域与遮蔽

- 全局作用域 + `session:<会话 id>` agent 作用域。同层 name/alias 共用一个
  命名空间，任何重名 → 注册期报错（不静默覆盖）。
- 查找先 agent 后全局；agent 命令的 name 或任一 alias 与全局命令的任一键
  冲突时，该全局命令在**这个作用域内**整体不可达且列表不出现——避免
  「列表有、执行打到另一个命令」的错位。
- 配置（`backend/commands.json`，`DOCMIND_COMMANDS_CONFIG` 覆盖）只能
  引用已注册的代码 handler（enable 内置命令、block 全局命令），不执行
  配置内任意代码——与 MCP server 配置同一信任边界。

### D4 · 权限与多租户

- 两个端点都先 `require_organization`（无组织 403，fail-closed）；携带
  `session_id` 时先过与详情/导出同一套会话归属条件（不存在/他人/跨组织
  一律 404，避免枚举）。
- `permission="admin"` 的可见性与执行都走 `is_org_admin`（组织管理员 /
  平台超管）；判定失败 fail-closed 为非管理员。`users.role` 不参与判定
  （沿用 ADR-001 D3）。
- 命令 handler 不经过工具注册表，因此**不构成对 HITL 审批或默认禁用工具
  清单的绕过**：命令只能执行显式实现的 handler；`/tools` 只读列出工具与
  默认禁用状态。

### D5 · 并发与超时

- 同一会话同一时间只允许一个命令：进程内 `asyncio.Lock`（`session_id` 为键）。
- **诚实边界**：这是进程内锁，不是分布式互斥；多 worker 部署需要 sticky
  session 或后续换 Redis 租约。与 skills/scheduler 的单实例语义一致。
- 每个命令自带 timeout（默认 10s，`asyncio.wait_for`）；调用方取消按
  `CancelledError` 传播，不吞。

## 已知限制 / 后续

- 前端菜单从后端拉取（issue #475 任务 4）与 `/clear`、`/fork` 接入
  （依赖会话 fork）不在本 ADR 范围；当前前端对 `command_*` 事件按未知事件
  忽略，命令结果在 `/agent/chat` 出口以既有 `chunk`、在 `/chat/stream` 出口
  以既有 `message` 事件兜底外发，保证现有 UI 可见（不做持久化）。
- 命令入口只覆盖 Agent 通道（`/agent/chat`、`/chat/stream?useAgent=true`）；
  普通 RAG 聊天（`useAgent=false`）当前没有斜杠入口，`/` 开头文本仍按普通
  提问处理。若产品未来把命令入口扩到普通聊天，需要把拦截前移。
- 进程内锁的多 worker 边界见 D5。
- 配置加载是启动期一次性应用；运行期动态注册（SDK/ACP）需要调用方串行化
  并自行解决跨进程一致性。
