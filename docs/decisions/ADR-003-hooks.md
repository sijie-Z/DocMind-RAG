# ADR-003：Hooks 统一 agent/tool 事件扩展点（issue #474）

- 日期：2026-10-22
- 状态：已实现（feat/i474-hooks）
- 关联：Epic #473、issue #474、相关 ADR-002（User Commands 的配置/作用域范式）

## 结论（先读这里）

1. 新增进程内 **HookBus**（`backend/app/agent/hooks/`），用 **waterfall** 与
   **serial** 两种语义覆盖 7 个事件：`agent/turn_start`、`agent/pre_step`、
   `agent/post_step`、`agent/turn_stopping`、`agent/turn_end`、
   `tools/pre_execute`、`tools/post_execute`。
2. **默认关闭**：`HOOKS_ENABLED=false`（默认）时不读 `hooks.yaml`、不注册任何
   hook、`emit` 直接 allow——行为与改造前逐字节一致；这也是回滚开关。
3. **配置化加载**：`backend/config/hooks.yaml`（dotted path 白名单、事件、
   priority、timeout、failure_policy、enabled）。开关打开时严格校验，
   非法配置在 lifespan **fail-closed**（进程退出，错误信息含字段路径）。
4. **隔离**：单个 hook 超时（默认 2s，0.1–30s）/异常/非法决策都产出
   `HookError`（日志 + `docmind_hook_*` 指标 + `hook_error` SSE），按
   `failure_policy` 收敛；只有显式 `fail_closed` 才阻断主流程。
5. **安全边界不变**：hook 只能更严格，不能绕过组织上下文、禁用工具、
   工具白名单与人工审批；`ask` 走既有审批门，无审批通道按 deny。

## 事件与 payload（与 issue §5.3 对齐）

| 事件 | payload 关键字段 | 可改写 | 可阻断 | 语义 |
|---|---|---|---|---|
| `agent/turn_start` | session_id, task_id, query | 否 | 否 | waterfall（只观察） |
| `agent/pre_step` | step_index, messages, step | messages | 是（deny → 本轮不执行） | waterfall |
| `agent/post_step` | step_index, result{status,result,error} | 否 | 否 | serial |
| `agent/turn_stopping` | reason | 否 | 是（deny → 继续循环） | waterfall |
| `agent/turn_end` | status | 否 | 否 | serial（status ∈ completed/failed/cancelled/paused） |
| `tools/pre_execute` | tool_name, arguments, risk_level, tool_call_id | arguments | 是（allow/deny/ask） | waterfall |
| `tools/post_execute` | tool_name, arguments, result{success,content,error_type} | result | 是（deny → 拒绝结果替换） | waterfall |

waterfall 语义：handler 收到 `(ctx, next)`；`await next(payload)` 继续链路
（下游从 `ctx.payload` 读到新 payload）；不调用 next 即短路，自己的返回值就是
最终决策；返回 `None` 视为 allow 且 payload 不变。payload 是最终载体——
`action=allow` 携带的改写（`next(modified_payload)` 的正常写法）与
`action=modify` 的短路式改写都会被消费方应用。

serial 语义：`next=None`，顺序 await，返回值忽略；只用于 `agent/post_step`、
`agent/turn_end` 这类纯观察事件。serial 的 fail_closed 仍返回 deny 记录
（观察事件没有可阻断的主流程，调用方忽略该决策）。

会话口径：所有事件的 `session_id` 是**裸会话 id**（executor 会剥离
`config.agent_id` 的 `session:` 前缀），便于 turn_* 与 tool/step 事件关联。

## 插入点（真实边界，不是事件名的装饰）

| 事件 | 代码位置 | 证据 |
|---|---|---|
| `agent/turn_start` / `turn_stopping` / `turn_end` | `loop.py::PERAgentLoop.run` | run 起点 / `while` 自然结束前的 break 点 / Phase 4 收尾 |
| `agent/pre_step` | `executor.py::_execute_step_once`，messages 构建完成后、StepRunner 调用前 | **messages 只在这里存在**；loop 层看不到每步提示词 |
| `agent/post_step` | `_execute_single_step` 与 `_execute_steps_parallel` 的步终态落点 | 与 memory/checkpoint 回写同点 |
| `tools/pre_execute` | `_execute_step_once`，`_filter_executable_calls` 之后、`_approval_precheck` **之前** | 审批预检在 executor 且早于 registry（issue §5.5 关键修正） |
| `tools/post_execute` | `_execute_step_once`，`raw_result` 返回后、`_ledger_result` 与 SSE 事件**之前**；工具抛异常/超时的分支同样 emit | ledger 记录最终结果、事件内容同步替换 |

审批顺序（安全关键）：`tools/pre_execute` 的 `ask` 通过
`_approval_precheck(forced_reasons=...)` 强制进入既有审批门——即使
`requires_approval(mode, tool)` 为假；没有 `approval_check` 通道时该调用直接
按 `hook_denied` 拒绝。`deny` 的调用不参与审批暂停、handler 零调用，
`ToolResult.fail(code="hook_denied")` 结构化失败进入反思链路。

## 失败策略与可观测性

- `fail_open`（默认）：记录 `HookError` → `hook_error` SSE + WARNING 日志 +
  `docmind_hook_executions_total{event,hook,outcome}` /
  `docmind_hook_duration_seconds{event,hook}`；链路继续。
- `fail_closed`：返回 deny（工具不执行 / 步骤不执行）；同样记录三件套。
- 日志 logger 为 `docmind.hooks`，字段 event/hook/action/elapsed_ms/
  error_type；`arguments` 经 `redact_arguments`（password/token/secret/**key**/
  authorization/cookie/credential 等字段打码，递归）后截断 500 字符。
  `messages`/`result` 正文不进日志。HookError 的 message 与 `hook_error` SSE
  content 只含**稳定文案 + 异常类型**，绝不拼接异常原文（异常文本可能内嵌
  入参/密钥，原文只由 hook 自己负责记录）。
- `hook_error` 事件新增 4 个可选字段（`hook/event_name/action/elapsed_ms`），
  并复用既有 `error_type` 字段携带失败种类（timeout/error/invalid_decision）；
  仅该事件类型序列化这些可选项——既有事件载荷零变化。`sse_event_types` 23→24。
  content 为失败消息，**不包含**未脱敏的参数；参数只以脱敏形式进服务端日志。

## 与 ToolRegistry 既有 hook 管线的关系

ToolRegistry 的 `register_pre_hook/register_post_hook`（auth/error_mapper 等
**内置**钩子）保持原样，不迁移、不暴露给外部配置。外部 hook 只走 executor 层
的 HookBus：
- registry 层在 `execute_detailed` 内部、审批**之后**执行，`ask` 在那里无法再
  进入审批门；
- 两套语义混用会让"审批对象是最终参数"这一保证失效。
因此 `tools/pre_execute` / `tools/post_execute` 只在 executor 接一次，registry
不感知外部 hook。

## post 事件也按低 priority 先执行的理由

`tools/post_execute` 与 waterfall 的 pre 事件共用一条排序规则（priority 升序、
同优先级按注册顺序）。如果 post 反序，审计类 hook（先注册、priority 小）会
在"结果改写"之后才看到结果，日志与最终 ledger 不一致；统一升序让"观察者先
看到原始形态、改写者后覆盖"成为可推理的固定顺序。纯观察事件（post_step/
turn_end）为 serial，顺序同理。

## 被否决的方案

1. **外部进程 hook（Claude Code 式 shell 命令）**：进程隔离与协议成本高，
   issue 明确列为非目标（P3）。
2. **在 ToolRegistry 内接外部 hook**：审批预检在 executor 且早于 registry，
   `ask` 到达时审批已过——会造出"hook 要求审批但审批门不可达"的假安全。
3. **hook 失败一律 fail_closed**：hook 是增强不是安全边界（安全边界仍是
   组织/禁用工具/审批），一律 fail_closed 会让一个审计 hook 的 bug 拖垮全部
   工具调用；显式策略把选择权交给配置者。
4. **hooks.yaml 坏配置降级为空配置**：hook 会改写/阻断执行，带病运行比不启动
   更危险；与 commands.json 的"增强型配置"定位不同。启动期 fail-closed。

## 与 issue 文本的三处显式偏差（均有验收依据）

1. **`HookDecision.action` 增加 `"ask"`**：issue §5.2 的 Literal 是
   allow/deny/modify，但 §5.3 与 §8 验收明确要求 `tools/pre_execute` 返回
   allow/deny/**ask**；封闭 Literal 会让 ask 无路可走。其它事件收到 ask 视为
   非法决策（按 failure_policy 处理）。
2. **`HookBus.emit` 返回 `(HookDecision, list[HookError])`**：§5.7 明确要求
   二元组以便 executor/loop 外发 `hook_error`；§5.2 的单返回值签名是早期
   草案。调用方解构二元组，语义不变。
3. **`agent/pre_step` / `agent/post_step` 在 executor 插入**：issue §5.5 说
   "loop 五个阶段 emit"，但 pre_step 的 `messages` 与 post_step 的 `step.result`
   只存在于 `_execute_step_once` / 步终态落点；loop 层在没有重构执行器的前提
   下拿不到每步提示词。turn_start/turn_stopping/turn_end 仍在 loop emit。
   这与 §5.5 自己"审批预检在 executor，所以外置 hook 必须前移"的推导同构。

另：`turn_stopping` deny 的"继续循环"有硬上限
`HOOK_TURN_STOPPING_MAX_DENIALS=3`——issue 要求继续循环但未给终止条件，确定性
deny 的 hook 会把 run 变成无限循环；上限保证基础设施自带终止（超限记录决策
并强制结束，不静默）。

## 回滚

- `HOOKS_ENABLED=false`（默认）或删除 `hooks.yaml`：零读取、零行为变化；
- 代码保留不影响基线；`sse_event_types` 的 24 是闭集事实，回滚 hook 开关
  不需要回滚文档数字。
- 冒烟：普通聊天、工具调用、审批、取消各跑一遍（现有 HITL/agent 测试已覆盖）。

## 已知不确定项

- 并行步骤批次里，某个 `pre_step` deny 时，已越过入口护栏的兄弟步骤可能已经
  发起 LLM 调用；deny 后它们在 `_execute_step_with_retry` 的边界收敛为 skipped，
  但无法撤回已发生的调用。单步（serial / 单 ready step）路径无此窗口。
- hook 内新建的 asyncio task 不会被 `_hook_depth` contextvar 自动继承
  （contextvars 只随 `create_task` 复制——Python 3.12 中 `asyncio.create_task`
  默认继承当前 context，`run_in_executor`/线程池不继承）；线程内触发的工具调用
  不享受递归防护。本期只保证"同协程链路内的工具调用不递归"。
