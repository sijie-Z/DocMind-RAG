# DocMind Deployment Guide

## Option 1: Docker Compose (Recommended for Production)

```bash
# 1. Configure environment
cp backend/.env.docker.example backend/.env.docker
# Edit .env.docker with your API keys

# 2. Start all services
cd backend
docker compose up -d

# 3. Verify
curl http://localhost:8000/health
```

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

> 安全提示（1.20 起）：compose 中所有中间件端口仅绑定 127.0.0.1，默认凭据
> 已替换为占位符。部署前必须设置：MYSQL_ROOT_PASSWORD、MYSQL_PASSWORD、
> REDIS_PASSWORD、MINIO_ACCESS_KEY、MINIO_SECRET_KEY、GRAFANA_PASSWORD
> （可在 docker-compose.yml 中按注释生成随机值）。

## Option 2: Kubernetes

```bash
# 1. Create namespace and configs
kubectl apply -f deploy/k8s/00-namespace.yaml
kubectl apply -f deploy/k8s/01-configmap.yaml
kubectl apply -f deploy/k8s/02-secrets.yaml

# 2. Deploy backend + services
kubectl apply -f deploy/k8s/03-backend-deployment.yaml
kubectl apply -f deploy/k8s/04-backend-service.yaml
kubectl apply -f deploy/k8s/05-worker.yaml

# 3. Deploy frontend
kubectl apply -f deploy/k8s/06-frontend-deployment.yaml
kubectl apply -f deploy/k8s/07-frontend-service.yaml

# 4. (Optional) Ingress with TLS
kubectl apply -f deploy/k8s/08-ingress.yaml

# 5. Verify
kubectl get pods -n docmind
kubectl get svc -n docmind
```

### Secret 创建方式

`deploy/k8s/02-secrets.yaml` 全部为 `REPLACE_ME_*` 占位符。数据库连接串
（含口令）、MinIO / Redis 口令与 API Key 只存 Secret；ConfigMap 不含任何凭据。
以下两种方式任选其一：

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

# 方式 B：编辑 02-secrets.yaml 中的 REPLACE_ME_* 后再 apply
kubectl apply -f deploy/k8s/02-secrets.yaml
```

> 方式 A 与 02-secrets.yaml 二选一：已用 `kubectl create` 创建的 Secret 不要再用
> `kubectl apply -f 02-secrets.yaml` 覆盖（会用占位符回写）。backend 与 worker
> 通过 `envFrom.secretRef` 注入 Secret，ConfigMap 只提供非敏感配置。

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

### Demo account

默认禁用（ENABLE_DEMO_ACCOUNT=false）。开启后 guest 账号仅在**首次创建**时
使用随机生成（或 DEMO_PASSWORD 环境变量指定）的初始密码，不再每次启动重置。

## Production Checklist

- [ ] Generate unique SECRET_KEY and JWT_SECRET_KEY
- [ ] Change all default passwords (MySQL, MinIO, Grafana)
- [ ] Set `DEBUG=false` and `EXPOSE_EXCEPTION_DETAIL=false`
- [ ] Configure HTTPS (TLS certificate)
- [ ] Set up database backups
- [ ] Configure resource limits (CPU/memory)
- [ ] Enable monitoring (Prometheus + Grafana)
- [ ] Set up log aggregation (ELK/Loki)
