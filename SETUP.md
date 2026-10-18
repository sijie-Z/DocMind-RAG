# DocMind 本地启动指南

> 基础设施的端口 / 账号 / 库名以 `backend/docker-compose.yml` 为**唯一真源**，本文只做引述；
> 调整端口或凭据请改 compose 与 `.env.docker`，不要把本文当成第二套配置源。

## 前置依赖

- Python 3.11+
- Node.js 20.19+
- Docker Desktop（推荐，用于启动基础设施），或本机安装：
  MySQL 8 · Redis 7 · Elasticsearch 8 · MinIO · Kafka

---

## 方式一：Docker 启动基础设施（推荐）

```bash
cd backend

# 0) 配置文件先行：缺少 .env.docker 时，compose 的任意命令都会直接报错
cp .env.docker.example .env.docker    # Windows: copy .env.docker.example .env.docker
# 至少填写：SECRET_KEY / JWT_SECRET_KEY、DEEPSEEK_API_KEY、EMBEDDING_API_KEY、
#          MYSQL_ROOT_PASSWORD / MYSQL_PASSWORD
# 密码生成: python -c "import secrets; print(secrets.token_hex(24))"

# 1) 只启动基础设施（后端 / Worker 在宿主机跑，见下文）
docker compose up -d mysql redis elasticsearch minio kafka zookeeper

# 2) 等待所有服务健康（约 1-2 分钟）
docker compose ps
```

### 基础设施端口（与 compose 一致）

| 服务 | 宿主机 | 容器内 | 说明 |
|------|--------|--------|------|
| MySQL | 3308 | 3306 | 库 `docmind`、用户 `docmind` 由 compose 自动创建 |
| Redis | 6390 | 6379 | 可用 `REDIS_PASSWORD` 启用密码（默认无密码、仅绑定 127.0.0.1） |
| Elasticsearch | 9200 | 9200 | 单节点、关闭安全 |
| MinIO API / Console | 9000 / 9001 | 9000 / 9001 | 控制台 :9001 |
| Kafka | 9092 | 29092 | 宿主机应用连 localhost:9092 |
| Prometheus / Grafana | 9090 / 3001 | — | 完整 compose（10 服务）启动时才拉起 |

### 数据库连接（无需手动建库）

compose 依据 `.env.docker` 的 `MYSQL_DATABASE` / `MYSQL_USER` / `MYSQL_PASSWORD`
自动创建库与账号。用本机工具连接：

```bash
mysql -h 127.0.0.1 -P 3308 -u docmind -p    # 密码 = .env.docker 中的 MYSQL_PASSWORD
```

宿主机应用侧的连接串（写进 `backend/.env`）：

```
DATABASE_URL=mysql+aiomysql://docmind:<MYSQL_PASSWORD>@localhost:3308/docmind
```

---

## 方式二：本机服务

MySQL / Redis / ES / MinIO / Kafka 都装在本机时，不需要 Docker：
把 `backend/.env` 里的地址指向本机实例即可（端口以你的本机安装为准）。
对应配置键：

| 服务 | 配置键 |
|------|--------|
| MySQL | `DATABASE_URL` |
| Redis | `REDIS_HOST` / `REDIS_PORT` / `REDIS_PASSWORD` |
| Elasticsearch | `ELASTICSEARCH_HOSTS` |
| MinIO | `MINIO_ENDPOINT` / `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` |
| Kafka | `KAFKA_BOOTSTRAP_SERVERS` |

---

## 启动后端

```bash
cd backend

# 首次：创建本地环境文件（SECRET_KEY / JWT_SECRET_KEY 必填，留空服务无法启动）
cp .env.example .env    # Windows: copy .env.example .env

# 激活虚拟环境（首次需先 python -m venv venv）
venv\Scripts\activate        # Windows
# source venv/bin/activate   # Linux/Mac

# 安装依赖（如未安装）
pip install -r requirements.txt

# 启动 API 服务
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

启动成功后：
- API: http://localhost:8000
- Swagger 文档: http://localhost:8000/docs
- 健康检查: http://localhost:8000/health

---

## 启动 Worker（文档处理）

```bash
# 新开一个终端
cd backend
venv\Scripts\activate
python start_worker.py
```

---

## 启动前端

```bash
cd frontend

# 安装依赖（如未安装）
npm install

# 启动开发服务器
npm run dev
```

前端地址: http://localhost:5173

---

## 测试账号

| 账号 | 密码 | 说明 |
|------|------|------|
| guest | 随机生成（首次创建时打印一次，或 DEMO_PASSWORD 指定） | 演示账号（ENABLE_DEMO_ACCOUNT=true 时） |

---

## 环境配置文件

| 文件 | 用途 |
|------|------|
| `backend/.env` | 本地开发环境配置（宿主机跑后端 / Worker） |
| `backend/.env.docker` | Docker 部署配置（compose 的 env_file；基础设施凭据同此来源） |
| `backend/.env.example` / `.env.docker.example` | 配置模板（仓库不含真实密钥） |

### AI 模型配置

模型/向量维度默认值以代码真源 `backend/app/core/config/ai.py` 为准：

| 配置项 | 默认值 | 用途 |
|--------|--------|------|
| `DEEPSEEK_MODEL` | `deepseek-v4-flash` | Chat 对话 |
| `EMBEDDING_MODEL` | `nomic-embed-text`（本机 Ollama） | 向量化 |
| `VECTOR_DIMENSION` | `768` | 向量维度 |

Embedding 默认走本机 Ollama（`EMBEDDING_API_URL=http://localhost:11434/v1`），
使用云端 Embedding 时 URL / 模型 / 维度三键必须配套修改（例如智谱
`embedding-3` 为 2048 维）。Rerank 默认使用本地 BGE，无需 API Key。
Chat API Key 需自行填入 `.env` / `.env.docker`（模板不含真实密钥）。

---

## 常见问题

### 1. MySQL 连接失败
确认 MySQL 已启动；Docker 基础设施模式下端口是 **3308**（不是 3306），库 `docmind` 与用户 `docmind` 由 compose 自动创建，无需手动建库。

### 2. Elasticsearch 连接失败
ES 启动较慢，等待 30 秒后重试。

### 3. MinIO bucket 不存在
后端启动时会自动创建 `docmind` bucket，无需手动创建。

### 4. Kafka 相关错误
Kafka 仅用于异步文档处理。如果 Kafka 未启动，文档上传后需要手动触发解析，但聊天功能不受影响。

### 5. 前端白屏
检查后端是否在 8000 端口运行。前端 proxy 配置将 `/api` 请求转发到 `http://127.0.0.1:8000`。

---

## 一键启动（Windows）

```bash
# 双击运行
start_windows.bat
```

会依次清理端口并启动**后端与前端两个窗口**（Worker 需按上文单独启动）。
