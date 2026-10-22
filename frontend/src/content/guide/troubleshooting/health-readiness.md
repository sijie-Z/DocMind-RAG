`/health` 与 `/readyz` 是根路径上的运维探针（不带 `/api/v1` 前缀），用途不同：一个报告整体状态，一个决定是否接收流量。

## `/health`：始终返回 200

它并发执行六项检查：database、redis、elasticsearch、minio、kafka、knowledge_base，并在响应中给出总体 `status`：

- `healthy`：所有服务都健康。
- `degraded`：没有 unhealthy，但存在非健康状态（包括降级或未启用，例如 Redis 超时、MinIO 桶不存在、Kafka 未配置）。
- `unhealthy`：至少一项 unhealthy（例如数据库异常、Elasticsearch 集群状态异常、知识库覆盖率过低）。

响应里还有 `ready`、`version`、`uptime_seconds`、`services` 明细；知识库检查会给出 `coverage_ratio`、`indexed_chunks`、`documents_marked_indexed` 等字段，覆盖率低于 0.95 判为 unhealthy。

注意：`/health` 即使整体 unhealthy 也返回 HTTP 200，所以不要只看状态码，要看 `status` 字段。

## `/readyz`：200 或 503

只检查三项：database、elasticsearch、knowledge_base。

- 数据库不健康 → 503。
- Elasticsearch 不是 `healthy`（`disabled`、`degraded`、`unhealthy` 都算）→ 503。
- 知识库 `unhealthy` → 503；`degraded`（覆盖率 95%–100%）不影响就绪。

响应包含 `ready`、`status`（`ready` / `not_ready`）与三项明细。

## `/livez`

始终返回 200 `{"status": "alive"}`，只表示进程还活着，不检查依赖。

## 还有 `/api/v1/knowledge/health`

它额外做一次 `ping` 与测试 embedding，用于确认知识库检索链路（而非仅集群状态）可用。

## 怎么读

1. 服务起不来或反复重启：先看 `/livez`，再看 `/readyz` 的 503 明细。
2. 功能异常但进程正常：看 `/health` 的 `status` 与各服务项。
3. 检索异常：重点看 `knowledge_base` 的覆盖率与 mismatch 字段。

## 依据（代码事实）

- `backend/app/main.py`（`/health`、`/readyz`、`/livez` 的实现与判定）
- `backend/app/api/v1/endpoints/knowledge.py`（知识库健康检查）
- `backend/app/api/v1/endpoints/monitoring.py`（`/monitoring/health`）
