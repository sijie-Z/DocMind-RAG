# DocMind 真栈验证记录（2026-10-25）

> 本文记录 DocMind 在**真实基础设施**上的一次端到端验证：真实容器（MySQL / Redis /
> Elasticsearch / MinIO / Kafka / Zookeeper）、真实后端与 worker 进程、真实嵌入服务，
> 以及真实 MySQL 上的 Webhook 集成测试。
> 只写当日实际执行并读回的结果；未执行的项目在第 8 节逐项列出。
> **本文是功能闭环验证，不是压测，也不构成「生产可用」结论。**

| # | 检查 | 结果 |
|---|---|---|
| 1 | 数据库迁移 `014` → `016`（head） | 通过 |
| 2 | `GET /health` 六项依赖 | 200，全部 healthy |
| 3 | 注册 → 登录 → 当前用户 | 全部 200 |
| 4 | 上传 → worker → 嵌入 → ES indexed → SSE 问答 | 闭环通过 |
| 5 | Webhook e2e（真实 MySQL） | 3 passed |

## 1. 环境

| 项 | 值 |
|---|---|
| 日期 | 2026-10-25 |
| 运行环境 | WSL2 Ubuntu 内 dockerd（不依赖 Docker Desktop） |
| 数据库 | 容器 MySQL 8.0 |
| 缓存 | 容器 Redis |
| 检索 | 容器 Elasticsearch 8.11.0 |
| 对象存储 | 容器 MinIO |
| 消息队列 | 容器 Kafka + Zookeeper |
| 容器状态 | 上述容器全部 healthy |
| 后端 | `python -m app --profile web`，监听 8010 端口 |
| Worker | `python -m app --profile worker`，消费 Kafka |
| 嵌入 | 本机 Ollama `qwen3-embedding:0.6b`，1024 维 |

## 2. 检查 1：数据库迁移

真实 MySQL 上的库停在 `014_api_keys`，本次升级到 head。

```bash
# 在 backend/ 下执行；连接信息由运行环境注入
alembic current
alembic upgrade head
alembic current
```

读回结果：

| 阶段 | `alembic current` 读回 |
|---|---|
| 升级前 | `014_api_keys` |
| 迁移链 | `014_api_keys` → `015_webhook_lease_fencing` → `016_fork_seq_idempotency` |
| 升级后 | `016_fork_seq_idempotency (head)` |

结论：两条增量迁移按顺序应用成功，`alembic current` 读回 head，无降级。

## 3. 检查 2：健康检查

命令：`GET /health`（后端监听 8010 端口）。

结果：HTTP 200；整体 `status = healthy`；`services` 六项全部 `healthy`：

| 依赖 | 状态 |
|---|---|
| database | healthy |
| redis | healthy |
| elasticsearch | healthy |
| minio | healthy |
| kafka | healthy |
| knowledge_base | healthy |

## 4. 检查 3：认证链路

按「注册 → 登录 → 取当前用户」顺序调用，全部成功：

| 步骤 | 请求 | 结果 |
|---|---|---|
| 1 | `POST /api/v1/auth/register`（新用户 `<verify-user>`，自动创建个人组织） | 200 |
| 2 | `POST /api/v1/auth/login/json` | 200，返回访问令牌 |
| 3 | `GET /api/v1/users/me`（携带令牌） | 200 |

说明：访问令牌仅在内存中用于后续请求，本文不记录其值。

## 5. 检查 4：文档处理与问答闭环

一条真实上传链路，按处理顺序逐步读回：

| 步骤 | 读回点 | 结果 |
|---|---|---|
| 1 | 上传 Markdown 文档 | HTTP 201 |
| 2 | Kafka 投递 | 消息投递到 `file-processing` topic |
| 3 | worker 消费 | worker 消费该消息并执行处理 |
| 4 | 嵌入 | 本机 Ollama `qwen3-embedding:0.6b` 生成 1024 维向量，读回 `hasVector=True` |
| 5 | 索引状态 | Elasticsearch 中文档状态 `indexed` |
| 6 | 对该文档提问（SSE） | 102 个 chunk 事件 + 1 个 message 事件 |
| 7 | 回答内容 | 正确引用「数据负责人审批」「24 小时内完成删除」「审计日志记录操作人、时间、原因」 |
| 8 | 来源引用 | `sources` 第一条为上传文件（rank 1） |

结论：上传 → 消息投递 → worker 处理 → 真实嵌入 → 索引 → 检索问答的闭环在真栈上
走通，回答引用内容与上传文档一致，且来源指向该文件。

## 6. 检查 5：Webhook 端到端集成测试

测试使用一次性 MySQL 8.0（独立端口、独立数据卷，用后即销毁），以真实 MySQL 跑
Webhook 全链路（HTTP 端点 / 幂等 / outbox 状态机 / 会话与消息落库）；按测试文件
头部说明，仅 LLM 以 fake agent 替代。

```bash
cd backend
RUN_INTEGRATION=1 python -m pytest tests/integration/test_webhook_e2e.py
```

结果：**3 passed**。三个用例分别覆盖：

| 用例 | 覆盖行为 | 结果 |
|---|---|---|
| `test_signed_delivery_end_to_end` | 合法签名投递 → 202 → `poll_once` → 投递记录 `SUCCEEDED`，会话与消息（seq 1/2）落库；同一 webhook-id 重投返回 200 duplicate，不重复建会话 | passed |
| `test_large_signed_payload_succeeds_end_to_end` | 大于 64 KiB（且小于 1 MiB）的合法签名 body 可完整落库：`webhook_deliveries.payload`、`chat_messages.content`、`agent_tasks.query` 三列在 MySQL 上为 `MEDIUMTEXT` | passed |
| `test_webhook_disabled_rejects_delivery` | Webhook 总开关关闭（`WEBHOOKS_ENABLED=false`）时，入站投递被拒绝（404） | passed |

## 7. 失败与恢复记录：一次 Ollama GPU OOM

首次 worker 消费上传任务时，本机 Ollama 嵌入出现一次瞬时 GPU OOM。worker 没有吞掉
这次失败：按 `RedeliveryHalt` 处理，**不提交 Kafka offset**，任务保持待重投状态。

重启 worker 后，Kafka 重新投递该消息，嵌入成功，任务最终 `SUCCESS`。

这条记录本身是「至少一次投递、失败不丢任务」语义的现场证据：失败时宁可重投，
也不把未完成的任务当作完成。恢复手段仅为重启 worker（Ollama 重新加载模型），
未修改代码或数据。

## 8. 局限与未验证项

本次验证覆盖的是单机 WSL2 环境下的功能闭环。以下项目**没有验证**，本文不做任何
推断：

1. **Terminal 真 Docker**：未验证。
2. **ACP 真实 Redis 取消**：未验证。
3. **live rerank**：未验证，本次未测量任何 rerank 指标。
4. **真实 K8s**：未验证，本次未在 Kubernetes 集群上部署或运行。

另需明确的口径边界：

- **不是压测**：未测量吞吐、并发或延迟分位数，文中没有性能结论。
- **不是生产可用性结论**：单机、单次运行，结果绑定当日环境与模型版本。
- **嵌入为本机 Ollama**：`qwen3-embedding:0.6b`（1024 维）；换模型或换机器需重测。
- **验证后已清理**：容器已停止、嵌入模型已卸载；本文不声称这些服务仍在运行。
