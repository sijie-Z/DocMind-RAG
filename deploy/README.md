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
| Grafana | 3000 | admin/admin123 |

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

## Option 3: Manual (Development)

See [CONTRIBUTING.md](../CONTRIBUTING.md) for dev setup instructions.

## Environment Variables

Key environment variables (set in `.env.docker`):

| Variable | Required | Description |
|----------|----------|-------------|
| SECRET_KEY | Yes | 32-byte hex key for encryption |
| JWT_SECRET_KEY | Yes | 32-byte hex key for JWT signing |
| DEEPSEEK_API_KEY | Yes | LLM API key (ZhipuAI / OpenAI-compatible) |
| EMBEDDING_API_KEY | Yes | Embedding model API key |
| RERANK_API_KEY | Yes | Rerank model API key |
| MYSQL_ROOT_PASSWORD | Yes | MySQL root password |
| MINIO_ACCESS_KEY | Yes | MinIO access key |
| MINIO_SECRET_KEY | Yes | MinIO secret key |

Generate secure keys:
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

## Production Checklist

- [ ] Generate unique SECRET_KEY and JWT_SECRET_KEY
- [ ] Change all default passwords (MySQL, MinIO, Grafana)
- [ ] Set `DEBUG=false` and `EXPOSE_EXCEPTION_DETAIL=false`
- [ ] Configure HTTPS (TLS certificate)
- [ ] Set up database backups
- [ ] Configure resource limits (CPU/memory)
- [ ] Enable monitoring (Prometheus + Grafana)
- [ ] Set up log aggregation (ELK/Loki)
