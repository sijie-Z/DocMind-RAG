# DocMind Deployment Guide

## Option 1: Docker Compose (Recommended for Production)

```bash
# 1. Configure environment
cp backend/.env.docker.example backend/.env.docker
# Edit .env.docker: API keys + middleware passwords
#   (MYSQL_ROOT_PASSWORD / MYSQL_PASSWORD / MINIO_ACCESS_KEY / MINIO_SECRET_KEY / GRAFANA_PASSWORD)

# 2. Start all services
cd backend
docker compose --env-file .env.docker up -d
# compose 会先启动一次性 migrate 服务（alembic upgrade head），
# backend / worker 只有在它成功退出（exit 0）后才启动；迁移失败则整个启动中断。

# 手动重跑迁移（拉取含新迁移的代码后）：
docker compose --env-file .env.docker run --rm migrate

# 3. Verify
curl http://localhost:8000/readyz   # 就绪（依赖故障 → 503）；/livez 为存活，恒 200
curl http://localhost:8000/health   # 详细诊断视图（恒 200，含 services/ready/版本）
```

### 数据库迁移（compose）

- 迁移**只**由 `migrate` 服务执行一次，`backend` / `worker` 通过
  `depends_on: { migrate: { condition: service_completed_successfully } }` 串行等待；
  镜像入口**不会**自动跑迁移（多副本各自 upgrade 会竞争 Alembic 版本表与 DDL）。
- 后端启动期还有一道 schema guard（`python -m app`）：`alembic_version` 与迁移
  head 不一致时打印 `current=... head=...` 与修复命令并以**退出码 2** 结束进程。
- 两个开关（`.env.docker` / 环境变量）：
  - `SCHEMA_GUARD=true`（默认）：启动期校验 schema；
  - `AUTO_MIGRATE=false`（默认）：仅在显式设为 `true` 时允许启动期兜底执行
    `alembic upgrade head`（审计日志；并发启动多副本时不要开）。
- 既有**非迁移建库**（create_all 建库、没有 `alembic_version` 表）不会被自动接管：
  先按启动日志的修复指引 `alembic stamp <与库现状一致的 revision>` 纳入迁移管理，
  再 `alembic upgrade head`；否则 schema guard / initContainer 会 fail-closed。

### Service Ports

| Service | Port | Notes |
|---------|------|-------|
| Backend API | 8000 | FastAPI + Swagger at /docs |
| Frontend | (build & serve via nginx) | Run separately or via K8s |
| MySQL | 3306 | Internal only |
| Redis | 6379 | Internal only |
| Elasticsearch | 9200 | Internal only |
| MinIO Console | 9001 | Internal only |
| Kafka | 9092 | Internal only |
| Grafana | 3000 | Internal only — set GRAFANA_PASSWORD (no default credentials) |

> 安全提示：compose 中所有中间件端口仅绑定 127.0.0.1；口令类变量全部改为
> `${VAR:?required}` 引用——未在 `.env.docker`（经 `--env-file` 传入）中设置时
> compose 直接报错，不再回退到已知弱默认值。必填：MYSQL_ROOT_PASSWORD、
> MYSQL_PASSWORD、MINIO_ACCESS_KEY、MINIO_SECRET_KEY、GRAFANA_PASSWORD；
> REDIS_PASSWORD 可选（留空时 Redis 无密码，且仅绑定 127.0.0.1）。

## Option 2: Kubernetes

```bash
# 1. Create namespace
kubectl apply -f deploy/k8s/00-namespace.yaml

# 2. Create Secret（见下方「Secret 创建方式」，方式 A / B 二选一）
#    升级既有部署：先更新 Secret（含 DATABASE_URL 键），再 apply ConfigMap，
#    否则新 Pod 会因缺少 DATABASE_URL 启动失败。

# 3. Apply config
kubectl apply -f deploy/k8s/01-configmap.yaml

# 4. Run database migrations (one-shot Job) and wait for completion
kubectl apply -f deploy/k8s/09-migrate-job.yaml
kubectl wait --for=condition=complete job/docmind-migrate -n docmind --timeout=600s

# 5. Apply workloads（必须在 Job 成功之后；Job 失败时不要继续）
kubectl apply -f deploy/k8s/03-backend-deployment.yaml
kubectl apply -f deploy/k8s/04-backend-service.yaml
kubectl apply -f deploy/k8s/05-worker.yaml

# 6. Deploy frontend
kubectl apply -f deploy/k8s/06-frontend-deployment.yaml
kubectl apply -f deploy/k8s/07-frontend-service.yaml

# 7. (Optional) Ingress with TLS
kubectl apply -f deploy/k8s/08-ingress.yaml

# 8. Verify
kubectl get pods -n docmind
kubectl get svc -n docmind
```

### 数据库迁移（Kubernetes）

- `09-migrate-job.yaml` 的 Job 是**唯一**执行 `alembic upgrade head` 的地方，
  迁移串行、只跑一次；Job 失败（`backoffLimit: 2` 内未成功）时**不要**继续
  rollout 后端/worker。
- backend / worker 的 Pod 带 `schema-check` initContainer（只读运行
  `scripts/check_schema.py`）：库未到迁移 head 时 init 失败、Pod 不启动 ——
  即使有人绕过发布顺序，也不会带着落后 schema 提供服务（fail-closed）。
  注意它执行的是**与 CI 相同的完整校验**（表 / 列定义 / collation），比单纯
  「版本 = head」更严：无关漂移同样会阻塞发布。失败时先看 initContainer 日志，
  按 `scripts/check_schema.py` 的列级输出修复库/迁移，不要跳过校验。
- 升级发布：先更新镜像与 Secret/ConfigMap，再重跑 Job，最后 `kubectl rollout
  restart deployment/docmind-backend deployment/docmind-worker -n docmind`。
  重跑前需删除已完成的 Job：
  `kubectl delete job docmind-migrate -n docmind`（再 apply）。
- 也可以只靠启动期 schema guard 兜底：backend 在版本不一致时以退出码 2
  崩溃并打印 `current=... head=...`；但 worker 不经过 HTTP 启动路径，仍以
  Job + initContainer 为准。

### Secret 创建方式

`deploy/k8s/02-secrets.yaml` 中 `SECRET_KEY` / `JWT_SECRET_KEY` / `DATABASE_URL`
为空串（未替换则应用启动即失败，fail-closed），其余条目为 `REPLACE_ME_*` 占位符。
数据库连接串（含口令）、MinIO / Redis 口令与 API Key 只存 Secret；ConfigMap 不含
任何凭据。以下两种方式任选其一：

```bash
# 方式 A（推荐）：直接用 kubectl 创建，跳过 02-secrets.yaml
kubectl create secret generic docmind-secrets -n docmind \
  --from-literal=SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')" \
  --from-literal=JWT_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')" \
  --from-literal=DATABASE_URL="mysql+aiomysql://docmind:<DB_PASSWORD>@docmind-mysql:3306/docmind" \
  --from-literal=MYSQL_ROOT_PASSWORD="<ROOT_PASSWORD>" \
  --from-literal=MINIO_ACCESS_KEY="<ACCESS_KEY>" \
  --from-literal=MINIO_SECRET_KEY="<SECRET_KEY>" \
  --from-literal=DEEPSEEK_API_KEY="<API_KEY>" \
  --from-literal=EMBEDDING_API_KEY="<API_KEY>"

# 方式 B：编辑 02-secrets.yaml 中的空串/占位符后再 apply
kubectl apply -f deploy/k8s/02-secrets.yaml
```

> 方式 A 与 02-secrets.yaml 二选一：已用 `kubectl create` 创建的 Secret 不要再用
> `kubectl apply -f 02-secrets.yaml` 覆盖（会用空串/占位符回写）。backend 与 worker
> 通过 `envFrom.secretRef` 注入 Secret，ConfigMap 只提供非敏感配置。
> 若既有 Secret 中配置过 `RERANK_API_KEY`（云端 Rerank），升级时请保留该键；
> 模板默认走本地 BGE，不包含该键。

## Option 3: Manual (Development)

See [CONTRIBUTING.md](../CONTRIBUTING.md) for dev setup instructions.

## Environment Variables

Key environment variables (set in `.env.docker` or the k8s Secret):

| Variable | Required | Description |
|----------|----------|-------------|
| SECRET_KEY | Yes | 32-byte hex key（部署契约：k8s/样例注入 + 启动非空校验；当前未被代码消费，issue #242） |
| JWT_SECRET_KEY | Yes | 32-byte hex key for JWT signing |
| DEEPSEEK_API_KEY | Yes | LLM API key (DeepSeek / OpenAI-compatible) |
| EMBEDDING_API_KEY | No | Embedding API key（默认本机 Ollama，可复用 DEEPSEEK_API_KEY） |
| RERANK_API_KEY | No | Rerank model API key（默认本地 BGE，无需） |
| DATABASE_URL | Yes | MySQL 连接串（含口令，仅存 k8s Secret） |
| SCHEMA_GUARD | No | 默认 `true`：启动期校验数据库版本是否为迁移 head，落后则 exit 2 |
| AUTO_MIGRATE | No | 默认 `false`：设为 `true` 才允许启动期兜底执行 `alembic upgrade head`（并发副本勿开） |
| MYSQL_ROOT_PASSWORD | Yes | MySQL root password |
| MINIO_ACCESS_KEY | Yes | MinIO access key |
| MINIO_SECRET_KEY | Yes | MinIO secret key |
| REDIS_PASSWORD | No | Redis 口令（启用认证时填入 Secret） |

Generate secure keys:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
python -c "import secrets; print(secrets.token_urlsafe(24))"   # 数据库/中间件密码
```

### Metrics endpoint

设置 METRICS_TOKEN 后，Prometheus 抓取需携带 Bearer 头：
```bash
curl -H "Authorization: Bearer $METRICS_TOKEN" http://localhost:8000/metrics
```

### Monitoring stack（可选）

`deploy/monitoring/docker-compose.monitoring.yml` 需要两个环境变量（Compose 插值，
未设置时直接报错）：

```bash
cd deploy/monitoring
GRAFANA_PASSWORD='<强口令>' ALERT_WEBHOOK_TOKEN='<URL 安全随机串>' \
  docker compose -f docker-compose.monitoring.yml up -d
```

- `ALERT_WEBHOOK_TOKEN` 必须与后端 `ALERT_WEBHOOK_TOKEN` 一致（后端可在
  `.env` / `.env.docker` 中设置）；两端不一致时告警会被后端拒绝。
- Alertmanager v0.26.0 不支持配置文件内环境变量展开：容器启动时用 `sed` 把
  token 渲染到 `/tmp/alertmanager.yml`，仓库内配置只保留
  `__ALERT_WEBHOOK_TOKEN__` 占位符（不落明文 token）。
- `host.docker.internal` 由 compose 的 `extra_hosts: host-gateway` 提供（Linux 可用）。

### Demo account

默认禁用（ENABLE_DEMO_ACCOUNT=false）。开启后 guest 账号仅在**首次创建**时
使用随机生成（或 DEMO_PASSWORD 环境变量指定）的初始密码，不再每次启动重置。

## Production Checklist

- [ ] Run database migrations before rolling out app (compose `migrate` service / k8s `09-migrate-job.yaml`)
- [ ] Generate unique SECRET_KEY and JWT_SECRET_KEY
- [ ] Set all required passwords in `.env.docker` (compose is fail-closed without them)
- [ ] Set matching ALERT_WEBHOOK_TOKEN on backend and monitoring stack (if alerting)
- [ ] Set `DEBUG=false` and `EXPOSE_EXCEPTION_DETAIL=false`
- [ ] Configure HTTPS (TLS certificate)
- [ ] Set up database backups
- [ ] Configure resource limits (CPU/memory)
- [ ] Enable monitoring (Prometheus + Grafana)
- [ ] Set up log aggregation (ELK/Loki)
