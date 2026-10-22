# ADR：会话 Fork / Seed 的边界语义、记忆隔离与 checkpoint 克隆取舍

**状态**：已实施（issue #476，Epic #473）
**日期**：2026-10-22
**范围**：`backend/app/services/session_fork_service.py`、迁移 `012_session_fork`

## 结论（先读这里）

1. **fork 是"从历史已结算点开新分支"，不是复制运行中的任务。** 复制前缀必须为空
   或以 ASSISTANT 消息结尾；`message_id` 指向 USER 时表示"在该消息之前分叉"
   （该消息不复制）。默认边界 = 最后一条已结算消息（尾部未答复的 USER 自动排除）。
2. **边界判定只看消息轮次结构，不看任务状态表**：assistant 中间消息（同一轮内
   后面还有 assistant）与"以 USER 结尾的前缀"一律 409。源会话存在非终态任务时，
   消息级分叉被禁止，必须走 checkpoint 克隆（否则 409）。
3. **记忆隔离默认严格**：子会话使用新的 `session:<child_id>` 命名空间，母记忆
   一格不复制；只有调用方显式传入 `seed` 才写入子命名空间（reflective insight）。
   无组织上下文的会话无法建立记忆命名空间（memory_service 按 A2 纪律 fail-closed
   拒绝 None/0），此时 seed **如实标记为未生效**（`is_seeded=false` +
   `seed_skipped_reason=no_organization`），不谎报；写入失败同样下调标记
   （`seed_write_failed=true`）。seed 在 fork 的 DB 提交**之前**写入：降级标记与
   子会话同一次 commit 落库，彻底避免"二次提交失败 → rollback 过期 ORM"的
   双故障路径；若 DB 提交最终失败，最多留下一个孤儿记忆命名空间（全新 id，
   永不复用，无跨会话污染）。
4. **checkpoint 克隆是可选的"续跑起点"**，不是消息历史的一部分：深拷最近一个
   已结算快照为新 `AgentTask`（新 id、绑定子会话、状态 `PAUSED`），
   `run.fork_origin` 记录来源任务/会话/切点；待审批（工具或计划）或执行中工具
   一律拒绝克隆。幂等重放返回**克隆任务** id（`checkpoint_task_id`）而非母任务
   id，来源另存 `checkpoint_source_task_id`；PAUSED 无活跃 run，取消端点直接
   收敛为 CANCELLED（200），不返回不会落地的 202。

## 被否决的方案及理由

| 方案 | 否决理由 |
|---|---|
| 只做 checkpoint fork（issue §5 备选） | 用户不可见：无法回答"从这条消息开个分支"；谱系/隔离/seed 都无从谈起 |
| 在 assistant 中间消息处分叉 | 该消息不是轮次终态，复制出的前缀会截断一个未完成的回答；语义上等于伪造历史 |
| 子会话共享母会话记忆命名空间 | 子会话的后续交互会污染母会话的召回；"分支"必须能独立演化 |
| 默认复制母会话全部记忆 | 与"上下文隔离"的验收直接冲突；需要摘要时由调用方显式传 `seed`，来源可审计 |
| 用 Redis 存幂等键 | Redis 抖动会造成重复分叉；幂等事实必须与子会话同事务落库，因此存进子会话 `settings.fork.idempotency_key`（sha256） |
| 克隆时保留源 `task_id` | 会把 `agent:replay:<task_id>` 的母 run 产物带进子会话（伪历史）；克隆必须换新 task id，replay 命名空间自然为空 |
| 给 `fork_point_message_id` 加外键 | 父会话删除/归档会触发 SET NULL 或阻止删除，与"子会话谱系保持可查"冲突；该列是谱系事实，不是引用完整性 |

## 关键设计细节

### 边界与开放轮次

- 一条轮次 = 一条 USER 消息及其后直到下一条 USER 的消息。
- **assistant "最终消息"**：从目标 assistant 往后、到下一条 USER 之前，不再出现
  assistant。出现第二条 assistant 即"中间消息"→ 409。
- **USER 边界**：前缀 = 该 USER 之前的消息；前缀以 USER/SYSTEM 结尾 → 409
  （更早的开放轮次）。第一条 USER 之前 = 空前缀，合法。
- **SYSTEM 边界**：409（系统消息不是轮次边界）。
- 不存在/不属于源会话的 `message_id`：404（与越权同语义，避免枚举）。
- 默认边界：最后一条 assistant；完全没有 assistant → 空前缀（允许空子会话）。

### 活跃任务与 checkpoint

- "运行中任务" = `agent_tasks.session_id = 源会话` 且状态属于
  PENDING / RUNNING / WAITING_TOOL / WAITING_APPROVAL / WAITING_PLAN_APPROVAL /
  WAITING_HUMAN。`PAUSED`（本特性的克隆产物）与终态不算——否则子会话永远
  无法再分叉。
- 活跃任务存在时：不带 `include_checkpoint` → 409；带了但没有可用快照 → 409。
- **可用快照** = 可被 `parse_snapshot` 解析、状态不是 WAITING_TOOL /
  WAITING_APPROVAL / WAITING_PLAN_APPROVAL、且 `run.pending_approval` 与
  `run.pending_plan_approval` 均为空。快照列用 `defer` + 单独容错读取
  （#278/R1 毒丸防线）：损坏的行按"无快照"跳过，绝不把 fork 变成 500。
- 快照与消息前缀是**两个独立事实**：checkpoint 的 `history` 没有消息 id/seq，
  无法映射回消息边界；克隆语义是"从母 run 的最后已提交断点继续"，消息前缀
  是"新分支的可见历史"。两者各自如实记录（`fork_point_message_id` 与
  `run.fork_origin`），不假装存在一条统一的"事件位置"。

### 迁移与兼容

- `chat_messages.seq`：先加可空列 → 按 `(created_at, id)` 逐会话回填 1..N →
  收紧 NOT NULL → 建唯一约束 `(session_id, seq)`。窗口函数/自连接 UPDATE 在
  MySQL/SQLite 上不通用，故用逐方言安全的 Python 侧回填。
- `chat_sessions.root_session_id` 存量回填为自身 id（根会话 = 自身）；fork 时
  子会话指向 `source.root_session_id or source.id`。
- `agent_tasks.session_id` 可空且不回填：历史任务的 session_id 只在快照信封里，
  不是可查询列；把这些任务当作"无会话归属"，不影响新数据。
- `agent_tasks.status` 枚举追加 `PAUSED`（MySQL ENUM 顺序参与 schema 比对，
  必须追加在末尾）；downgrade 时 PAUSED → WAITING_HUMAN（旧可恢复态），
  绝不回落成 RUNNING。

## 已知取舍与不确定性

- **前端入口不在本 PR**：消息级"从此处分叉"按钮与谱系徽标（issue 任务 6）
  未实现；API/契约字段（`parent_session_id`/`root_session_id`/`fork_point_message_id`/
  `is_fork`/`is_seeded`）与 API 使用说明（USER_GUIDE §5.2.1）已就绪，UI 操作
  说明随前端 PR 补齐。
- **幂等只保证顺序重试**：同一 `Idempotency-Key` 的并发双请求可能各建一个子会话
  （无分布式锁）；客户端串行重试（最常见的超时重试形态）稳。
- **克隆后 resume 的落库语义沿用 #201**：resume 收尾会把任务的 query + 最终回答
  追加进子会话（`envelope.session_id` 已改写为子会话）。如果切点早于母 run 的
  提问，这段追加内容会包含母 run 的原始问题——它是"续跑"的真实记录，不视为
  假历史；需要严格隔离时不要克隆 checkpoint。
- **配额**：单次 fork 最多复制 2000 条消息（`FORK_MAX_COPIED_MESSAGES`），超限
  409 并在 `detail` 给出 limit/count；未实现存储增长配额（按组织/用户）。
- **并发分叉同一源会话**：各自复制前缀，互不影响；唯一约束只保证同一子会话内
  seq 唯一。
