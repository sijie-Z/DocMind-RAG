# DocMind 使用手册

> 面向「拿到仓库、想在本机跑起来并用起来」的读者：从零启动 → 上传第一份文档 → 问出第一个带引用的回答 → 用上 Agent 工作台。
>
> 本文只做**引述**，不做第二套真源：
> - 端口、凭据、服务编排以 `backend/docker-compose.yml` 与 `backend/.env.docker.example` 为准；
> - 关键数字（33 个内置工具 / 23 个端点模块 / 34 条前端路由 / 20 种 SSE 事件）以 `docs/facts.json` 为单一事实源；README、架构页等既有锚点由 `backend/scripts/check_doc_facts.py` 在 CI 逐 PR 校验，本文数字与之一致（本文自身当前不在锚点清单内）；
> - 关键数字（33 个内置工具 / 23 个端点模块 / 34 条前端路由 / 23 种 SSE 事件）以 `docs/facts.json` 为单一事实源；README、架构页等既有锚点由 `backend/scripts/check_doc_facts.py` 在 CI 逐 PR 校验，本文数字与之一致（本文自身当前不在锚点清单内）；
> - 架构与设计动机见 `README.md`、`AGENT_ARCHITECTURE.md`、`docs/decisions/`。
>
> 英文版：[USER_GUIDE_EN.md](USER_GUIDE_EN.md)

---

## 目录

1. [5 分钟跑起来](#1-5-分钟跑起来)
2. [配置](#2-配置)
3. [核心概念](#3-核心概念)
4. [功能实战](#4-功能实战)
5. [API 与集成](#5-api-与集成)
6. [运维](#6-运维)
7. [排错 FAQ](#7-排错-faq)
8. [开发与贡献](#8-开发与贡献)
9. [事实来源索引](#9-事实来源索引)

---

## 1. 5 分钟跑起来

### 1.1 前置要求

| 依赖 | 要求 | 依据 |
|------|------|------|
| Python | 3.11+ | `backend/pyproject.toml` 的 `target-version = "py311"`；README 徽标 `Python 3.11+` |
| Node.js | **22.12+**（CI 使用 Node 22） | `frontend/package-lock.json` 中 Vite 8 要求 `^20.19.0 \|\| >=22.12.0`、Vitest 5 要求 `^22.12.0 \|\| ^24.0.0 \|\| >=26.0.0`；`.github/workflows/ci-fast.yml` 使用 Node 22。根 `README.md` 的「Node.js 18+」是旧口径，以 lockfile 的 engines 为准 |
| Docker Desktop | 含 Compose v2（Windows 建议启用 WSL2 backend） | `backend/docker-compose.yml`；`SETUP.md` |
| Ollama | 默认 Embedding 走本机 `nomic-embed-text`（768 维） | `backend/app/core/config/ai.py` 的 `EMBEDDING_API_URL` / `EMBEDDING_MODEL` 默认值 |
| 端口 | 8000、5173；基础设施 3308、6390、9200/9300、9000/9001、9092/29092、2181、9090、3001 | `backend/docker-compose.yml` |

准备 Ollama（默认 Embedding 路径需要它；改用云端 Embedding 可跳过）：

```bash
ollama pull nomic-embed-text
```

Ollama 是**可选基础设施**（compose 里没有它），但默认 Embedding 配置指向它；没有 Ollama 又没改
Embedding 三键时，文档向量化会失败，向量检索臂会降级或失败（关键词检索仍可能返回结果）。启动探测在服务不可达时只告警、不阻止启动，
所以「能启动」不等于「能检索」——用 `/readyz` 与一次真实检索确认。

> 默认值可以用环境变量整体替换，但 **URL / 模型 / 维度三个键必须配套**：例如改用云端
> `text-embedding-3-small` 时，同时设置 `EMBEDDING_API_URL`、`EMBEDDING_MODEL` 和
> `VECTOR_DIMENSION=1536`。只换 URL 不换维度，会在启动探测或首次写入索引时被拒绝
> （见 2.3）。

### 1.2 选择运行姿态

| 姿态 | 适用 | 启动内容 |
|------|------|----------|
| **A. 宿主机开发**（推荐给第一次上手） | 改代码、看日志、调试 | Docker 只跑基础设施；后端 / Worker / 前端在宿主机跑 |
| **B. 全 Docker** | 快速体验、接近部署形态 | Compose 启动 backend + worker + 全部基础设施（共 10 个服务）；前端需另行构建/托管 |

两种姿态都先准备配置文件；姿态 A 还需要宿主机侧的 `.env`（见 1.3）。

### 1.3 先准备配置文件

环境文件必须在**启动任何服务之前**创建——缺少 `backend/.env.docker` 时，compose 的任意命令都会直接报错。

```bash
cd backend

# Docker 侧（compose 插值 + 容器 env_file）
cp .env.docker.example .env.docker      # Windows: copy .env.docker.example .env.docker

# 宿主机侧（后端 / Worker 在宿主机跑时使用）
cp .env.example .env                    # Windows: copy .env.example .env
```

生成密钥：

```bash
python -c "import secrets; print(secrets.token_hex(32))"
python -c "import secrets; print(secrets.token_hex(24))"   # 数据库/中间件口令
```

`.env.docker` 的「必填」分三档，不要混为一谈：

- **Compose 插值 fail-closed**（`:?`；缺失时任何 compose 命令直接失败）：`MYSQL_ROOT_PASSWORD`、`MYSQL_PASSWORD`、`MINIO_ACCESS_KEY`、`MINIO_SECRET_KEY`、`GRAFANA_PASSWORD`。
- **后端启动 fail-closed**：`SECRET_KEY`、`JWT_SECRET_KEY` 为空会拒绝启动。
- **功能必需但不在启动时强制**：`DEEPSEEK_API_KEY`——缺失时服务仍能启动，Chat 调用才失败。

`.env`（宿主机侧）至少要设置 `SECRET_KEY`、`JWT_SECRET_KEY` 和 `DEEPSEEK_API_KEY`。另外，`.env.example` 的
`DATABASE_URL` 默认指向 `localhost:3306`，而 **Docker 基础设施对外映射的是 3308**；使用姿态 A 时请改成：

```dotenv
DATABASE_URL=mysql+aiomysql://docmind:<MYSQL_PASSWORD>@localhost:3308/docmind
REDIS_HOST=localhost
REDIS_PORT=6390
```

> `SETUP.md`「基础设施端口」一节给出了同样的宿主机口径；改端口请改 compose 与 `.env`，不要把某份文档当第二配置源。

### 1.4 启动基础设施

```bash
cd backend
docker compose --env-file .env.docker up -d mysql redis elasticsearch minio kafka zookeeper
docker compose --env-file .env.docker ps        # 等各服务就绪（首次约 1–2 分钟；zookeeper/kafka 无 healthcheck，显示 running 而非 healthy）
```

说明：

- `--env-file .env.docker` 同时提供 `MYSQL_PASSWORD` 等 **compose 插值**变量；`.env.docker` 还会通过 `env_file` 注入 backend/worker 容器。两者不是同一机制，别只配一半（Redis 密码尤其如此，见 compose 注释）。
- 只跑基础设施时 **不要**拉起 backend/worker（它们在宿主机跑）；要全 Docker 则去掉服务名列表：`docker compose --env-file .env.docker up -d`。
- 全 Docker 姿态不包含前端容器；前端按 `frontend/README.md` 或 `deploy/README.md` 的方式单独构建/托管。

### 1.5 启动后端

```bash
cd backend
python -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install -r requirements.txt

# 首次建表（推荐显式跑迁移；空库启动时应用也会 create_all）
alembic upgrade head

python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

也可使用统一入口（issue #479，推荐；profile = web | worker | headless | sdk | acp）：

```bash
python -m app --profile web
python -m app --profile web --patch config/patches/local.yaml
python -m app --profile web --dump-config   # 键排序 JSON，secret 打码
```

`uvicorn app.main:app` 与 `python run.py` 仍是兼容入口（reload 场景用后者）。配置分层为 `config/base.yaml` → bundle → `profiles/<profile>.yaml` → `--patch` → 环境变量。

Windows 也可以直接用仓库根目录的 `start_windows.bat`（自动识别 `.venv` 或 `venv`），但它不会替你启动 Worker。

启动时会发生三件值得知道的事：

1. **密钥校验**：`SECRET_KEY` / `JWT_SECRET_KEY` 为空会直接启动失败（`backend/app/core/config/security.py` 的 `_check_secrets`）。
2. **Embedding 一致性探测**：默认 `EMBEDDING_STARTUP_PROBE=true`，启动时真实调用一次 embedding。返回维度 ≠ `VECTOR_DIMENSION` → **拒绝启动**；服务不可达 → 只告警（离线开发属预期）。见 `backend/app/core/config/ai.py` 注释与 issue #196。
3. **建表策略**：`init_db()` 只在**空库**执行 `create_all`；已有数据的库必须走 `alembic upgrade head`（`backend/app/core/database.py`）。生产/升级场景一律以迁移为准。

### 1.6 启动 Worker（文档处理）

```bash
# 新开一个终端
cd backend
source venv/bin/activate            # Windows: venv\Scripts\activate
python start_worker.py          # 等价于：python -m app --profile worker
```

统一入口同样可用：`python -m app --profile worker`。

Worker 是 Kafka 消费者，负责「解析 → 分块 → 嵌入 → ES 索引」。**只要 Kafka 可用，就必须有 Worker**，否则文档会停在待处理状态；Kafka 不可用时，上传与知识库重建接口会自动切换为进程内处理（`README.md`「异步处理」）。

### 1.7 启动前端

```bash
cd frontend
npm ci            # 只安装依赖用 npm ci；需要改依赖时按 CONTRIBUTING 用 npx npm@12 install
npm run dev       # http://localhost:5173
```

> 根 `README.md` 的 `npm install` 与 `CONTRIBUTING.md` 的说明冲突：npm 10 在本项目依赖树上会触发 Arborist 缺陷，安装依赖请用 `npm ci`，改依赖用 `npx npm@12 install`。

前端开发服务器把 `/api`、`/static`、`/health` 与 WebSocket 代理到本机后端（`frontend/vite.config.mts`）。后端启动时会写
`backend/.backend-port`，Vite 以该文件为准；只有文件缺失时才回退到 8010。因此**先启动后端、再启动前端**最稳。

### 1.8 健康检查怎么读

| 端点 | 语义 | 什么时候看 |
|------|------|------------|
| `GET /livez` | **存活**，进程在就恒 200，返回 `{"status":"alive"}` | 容器编排的 liveness probe |
| `GET /readyz` | **就绪**：DB `SELECT 1` + ES 集群健康 + 知识库索引对账；任一不过 → **503** | 容器 healthcheck、是否该接流量 |
| `GET /health` | **诊断视图，恒 200**，含 `status` / `ready` / `version` / `services` / `uptime_seconds` | 人工排障；不要拿它做编排探针 |
| `GET /metrics` | Prometheus 文本指标 | 抓取；设置 `METRICS_TOKEN` 后需 `Authorization: Bearer <token>` |

就绪判定的精确口径（`backend/app/main.py` 的 `_is_ready`）：

- **参与判定**：`database == healthy`、`elasticsearch == healthy`、`knowledge_base != unhealthy`。
- **不参与判定**：Redis、MinIO、Kafka——它们故障只会让 `/health` 显示 `degraded`，不会让 `/readyz` 返回 503。
- ES 处于 `disabled` 或 `degraded` 都算不就绪；知识库 `degraded`（覆盖率 95%–100% 或瞬时超时）不摘流，`unhealthy` 才 503。
- `/health` 的总体 `status`：全部健康 → `healthy`；任一项 `unhealthy` → `unhealthy`；其余（含 `disabled` / `degraded`）→ `degraded`。

`backend/docker-compose.yml` 中 backend 的 healthcheck 用的就是 `/readyz`，注释写明了原因：`/health` 恒 200，等于永远健康（issue #223）。

```bash
curl -s http://localhost:8000/livez
curl -s http://localhost:8000/readyz
curl -s http://localhost:8000/health | python -m json.tool
```

**怎么读 `/readyz` 的 503**：响应体会指出失败项。常见原因是 MySQL 未起、ES 还在启动（首次 30 秒左右）、或文档表声称 `INDEXED` 但 ES 里查不到（索引对账失败）。第三类用第 4.1 节的 `reconcile_knowledge_index.py` 处理。

### 1.9 首次登录

**演示账号（默认关闭）**：`ENABLE_DEMO_ACCOUNT` 默认 `false`。显式设为 `true` 后，启动流程会创建 `guest` 普通用户
（`guest@example.com`，非超级管理员）；密码在**首次创建时**随机生成并以 `WARNING` 级别打印一次，之后不会每次启动重置
（`backend/app/core/ensure_demo_user.py`、`deploy/README.md`「Demo account」）。首次创建还会顺带创建/复用一个名为
`Default` 的组织并回填 `guest` 的组织归属。

> `DEMO_PASSWORD` 由进程直接读 `os.environ`，宿主机直跑时 `.env` 文件**不会**把它注入环境变量；需要
> `export DEMO_PASSWORD=...`，或由 `.env.docker` / Compose 注入。空值与 `123456` 会被忽略并改用随机密码。
> 同理，`ENABLE_TRACING` / `OTLP_ENDPOINT` / 飞书凭据 / `agent_api` 的配置也是 `os.getenv` 直读（见 `backend/.env.example` 注释）。

**管理员账号**：在后端目录执行

```bash
cd backend
ADMIN_PASSWORD='<至少 8 位>' python reset_admin.py     # Windows PowerShell: $env:ADMIN_PASSWORD='...'; python reset_admin.py
```

`reset_admin.py` 会在用户不存在时创建 `admin`（`admin@example.com`），存在时重置密码；两种情况都会置
`is_active=True`、`role="user"`、`is_superuser=True`。平台级管理员只认显式 `is_superuser`（ADR-001 D3）。

> 不要用仓库里的 `backend/seed_user.py` 引导任何真实环境——它使用硬编码弱口令，仅属早期开发遗留。

**自助注册**：`POST /api/v1/auth/register`（JSON：`username` / `email` / `password`，可选 `full_name`）。注册不能自选组织；服务端会**自动创建个人组织**（名为 `{username} 的空间`，并处理重名兜底）并设为当前组织，不会并入任何既有组织（`backend/app/api/v1/endpoints/auth.py`）。

---

## 2. 配置

### 2.1 两份环境文件，各管一段

| 文件 | 谁读 | 用途 |
|------|------|------|
| `backend/.env` | 宿主机上运行的 backend / worker / 运维脚本 | 本地开发配置 |
| `backend/.env.docker` | `docker compose --env-file .env.docker`（插值）+ 容器 `env_file` | Docker 部署配置 |
| `backend/.env.example` / `.env.docker.example` | 人 | 模板；仓库不含真实密钥 |

注意两类读取方式并存：

- **Pydantic Settings**（`app/core/config/`）读取 `.env` / 环境变量，大小写敏感，`extra="ignore"`；
- **`os.getenv` 直读**的键（上节列出的那些）不会从 `.env` 自动注入，宿主机直跑必须 `export`。

### 2.2 必改项与安全默认

| 变量 | 默认 | 说明 |
|------|------|------|
| `SECRET_KEY` / `JWT_SECRET_KEY` | 空（启动失败） | 必须自行生成；`SECRET_KEY` 目前只承担「非空启动校验」的部署契约，JWT 实际使用 `JWT_SECRET_KEY`（`backend/app/core/config/security.py`） |
| `DEEPSEEK_API_KEY` | 空 | Chat 模型凭据（必填） |
| `EMBEDDING_API_KEY` | 空 | 留空时复用 `DEEPSEEK_API_KEY`（本机 Ollama 不校验 Key） |
| `MYSQL_ROOT_PASSWORD` / `MYSQL_PASSWORD` | 空（compose 失败） | 数据库口令 |
| `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` | 空（compose 失败） | 对象存储凭据；Secret 至少 8 位 |
| `GRAFANA_PASSWORD` | 空（compose 失败） | 监控面板管理员口令 |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | `1440`（24 小时） | access token 有效期 |
| `REFRESH_TOKEN_EXPIRE_MINUTES` | `10080`（7 天） | refresh token 有效期 |
| `ENABLE_DEMO_ACCOUNT` | `false` | 演示账号开关 |
| `ENABLE_WORKFLOW_CODE_NODES` | `false` | 工作流代码节点开关（存在安全风险） |
| `SANDBOX_MODE` | `auto` | `auto` / `docker`：Docker 不可用时**拒绝执行**（fail-closed）；`ast` 才是显式选择进程内 AST 沙箱 |
| `ENABLE_PII_MASKING` | `true` | PII 掩码（上下文 + 流式） |
| `EXPOSE_EXCEPTION_DETAIL` | `false` | 不向客户端暴露异常细节 |
| `ENABLE_RATE_LIMIT` | `true` | 限流；宿主机模板 10000 次/分，compose 覆盖为 300 次/分（Redis 实现为滑动窗口，内存兜底为固定窗口） |
| `ALLOWED_ORIGINS` | 仅 `localhost:3000/5173/5174/5175` 等 | 生产部署必须加入你的前端域名，否则浏览器跨域失败 |

### 2.3 模型与 Embedding：一致性契约

默认值（代码真源 `backend/app/core/config/ai.py`）：

| 配置 | 默认 | 说明 |
|------|------|------|
| `DEEPSEEK_API_URL` | `https://api.deepseek.com` | OpenAI 兼容 |
| `DEEPSEEK_MODEL` | `deepseek-v4-flash` | Chat 模型 |
| `EMBEDDING_API_URL` | `http://localhost:11434/v1` | 本机 Ollama |
| `EMBEDDING_MODEL` | `nomic-embed-text` | 768 维 |
| `VECTOR_DIMENSION` | `768` | 必须与模型输出一致 |
| `EMBEDDING_STARTUP_PROBE` | `true` | 启动时真实调用一次 embedding 校验维度 |
| `EMBEDDING_SEND_DIMENSIONS` | `false` | 默认不在请求里传 `dimensions`：服务端截断会掩盖「维度配置已过期」的漂移 |

**硬件档位自动选择**：如果 `EMBEDDING_MODEL` / `VECTOR_DIMENSION` **完全未显式设置**（任何来源都没有），会按硬件自动选档：CPU `bge-m3` / 1024，8G 显存 `Qwen3-Embedding-4B` / 2048，16G 显存 `Qwen3-Embedding-8B` / 4096（`README.md`「配置说明」；实现见 `ai.py::_auto_tier_defaults`）。显式配置优先于自动档位。**模板里已显式写死 `nomic-embed-text` / 768，照抄模板就不会触发自动档位。**

更换 Embedding 提供方的正确姿势：

1. 同时修改 `EMBEDDING_API_URL`、`EMBEDDING_MODEL`、`VECTOR_DIMENSION`；
2. 清空并重建知识库索引（维度变了，旧向量不能复用）；
3. 重启后端，确认启动日志没有维度不一致告警。

### 2.4 重排（Reranker）

| 变量 | 默认 | 说明 |
|------|------|------|
| `RAG_ENABLE_RERANKER` | `true` | 总开关 |
| `RERANK_USE_LOCAL` | `true` | 本地 cross-encoder（`BAAI/bge-reranker-base`），无需 API 凭据 |
| `RERANK_MODEL` / `RERANK_API_KEY` / `RERANK_API_URL` | 空 | 云端重排；需同时 `RERANK_USE_LOCAL=false` |
| `RAG_RERANK_TOP_N` | `20` | 重排候选数 |
| `RAG_RERANK_TIMEOUT_SECONDS` | `8.0` | 超时后走降级（见 3.2） |
| `RAG_QUERY_REWRITE_TIMEOUT_SECONDS` | `1.2` | 查询改写读阶段预算 |
| `LLM_CONNECT_TIMEOUT_SECONDS` | `1.0` | 连接级快速失败：连接阶段独立短超时（读阶段不受影响；高延迟链路可上调，但会同比抬高不可达暴露时间） |
| `LLM_TRANSIENT_MAX_RETRIES` | `1` | 瞬时故障（408/409/429/5xx、连接建立后的传输中断）重试次数；连接类不可达与读超时零重试 |
| `LLM_UNREACHABLE_COOLDOWN_SECONDS` | `5.0` | 不可达负缓存窗口（按 LLM 端点区分，任一调用成功即清除）；`0` 关闭 |

### 2.5 存储与基础设施

| 变量 | 默认（模板/代码） | 说明 |
|------|------------------|------|
| `DATABASE_URL` | `mysql+aiomysql://...@localhost:3306/docmind_db`（宿主机模板；Docker 基础设施请改 3308） | MySQL |
| `DATABASE_POOL_SIZE` / `DATABASE_MAX_OVERFLOW` | `20` / `10`（compose：pool 20 / max_overflow 30） | 连接池 |
| `REDIS_HOST` / `REDIS_PORT` | 模板 `localhost` / `6379`；代码默认端口 `6390`（与 Docker 映射一致） | 缓存、限流、Token 黑名单 |
| `REDIS_PASSWORD` | 空 | 可选；启用时服务端与应用侧都要设（compose 注释有坑位说明） |
| `ELASTICSEARCH_HOSTS` | `["http://localhost:9200"]` | ES 8 |
| `ELASTICSEARCH_INDEX_NAME` | `docmind_knowledge`（模板值；代码内保留的旧默认值仅为兼容旧数据） | 知识库索引名 |
| `MINIO_ENDPOINT` / `MINIO_BUCKET_NAME` | `localhost:9000` / `docmind` | 对象存储；bucket 在**首次读写时**自动创建 |
| `KAFKA_BOOTSTRAP_SERVERS` | 宿主机 `localhost:9092` | 容器内：backend 用 `docmind-kafka:9092`，worker 用 `docmind-kafka:29092` |
| `KAFKA_FILE_PROCESSING_TOPIC` | `file-processing` | 文档处理主题 |
| `MAX_FILE_SIZE` | `104857600`（100 MB） | 单文件上限 |
| `USER_STORAGE_LIMIT_BYTES` | `10737418240`（10 GiB） | 单用户存储配额（**仅统计展示，上传路径未强制执行**） |

### 2.6 可观测性与日志

| 变量 | 默认 | 说明 |
|------|------|------|
| `PROMETHEUS_ENABLED` | `false`（代码）/ `true`（compose） | 打开 RAG/Agent 业务指标；单进程部署安全，多 worker 需先配置 multiprocess |
| `METRICS_TOKEN` | 空 | 非空时 `/metrics` 需要 Bearer |
| `ENABLE_MONITORING` | `true` | 只控制后台指标快照任务，不影响 `/metrics` 端点 |
| `LOG_LEVEL` / `LOG_JSON` | `INFO` / `true`（代码；模板写 `false`） | 日志级别与 JSON 格式 |
| `LOG_FILE` | `logs/docmind.log` | 日志文件 |
| `ENABLE_TRACING` / `OTLP_ENDPOINT` | `false` / 空 | OpenTelemetry（`os.getenv` 直读） |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_HOST` | 空 / 空 / `https://cloud.langfuse.com` | LLM 全链路追踪（可选） |
| `ENABLE_SCHEDULER` | `true` | 进程内定时任务调度；**单实例语义**，多 worker 部署需外部调度 |

---

## 3. 核心概念

### 3.1 PER Agent：规划 → 执行 → 反思

DocMind 的 Agent 不是「LLM 自由循环」，而是一个有明确阶段的循环（`backend/app/agent/`）：

```
用户指令
  → Planner    把任务拆成 DAG / Serial 步骤，标注依赖与工具提示（tool_hint）
  → Executor   按拓扑序执行；独立步骤可并行；每步最多 3 次重试（指数退避 0.5s × 2^n）
  → Reflector  评估目标达成度（0–100%），决定 pass / retry / replan
  → RunReport  汇总整次运行：做了什么、为什么、结果如何
```

- 规划失败（LLM 输出不是合法 JSON）会退回单步 Plan，直接执行原始问题，而不是整次失败。
- 反思有三种以上结果：`pass` / `retry` / `replan`，以及 `unavailable` / `unparsed`——后两者表示「评估缺席」，既不算通过也不算失败（`backend/app/agent/events.py` 的类型注释）。
- 记忆分四层：短期（会话内）、长期（跨会话）、工作（步骤中间结果）、反思（失败教训）；长期/反思记忆可持久化到 Redis，短期/工作记忆在进程内（`README.md`「已知限制」）。
- 实际默认配置以 `backend/app/agent/config.py` 为准：`max_plan_steps=10`、`max_iterations=20`、`max_tool_calls_per_turn=5`、`max_retries_per_step=3`、单步超时 120s、`max_context_tokens=8000`；`approval_timeout_seconds=0`（一直等）、`plan_approval_mode="never"`。
- `execute_python` / `execute_sql` / `mcp_call` 属于 `DEFAULT_DISABLED_TOOLS`，服务端会强制并入 `disabled_tools`，即使用户配置里没写（`backend/app/agent/config.py`、`endpoints/agent.py`）。
- 反思的 `retry` 会在原地重跑该步骤；`replan` 会重新进入 Planning，每个 run 最多 1 次（`MAX_REPLANS_PER_RUN=1`）。Reviewer 的 `revise` / `reject` 只作为建议记录并展示，不会阻断最终回答。
- Agent 的流式协议有 **23 种 SSE 事件类型**（`docs/facts.json` 的 `sse_event_types`，由 `backend/app/agent/events.py` 的 `EventType` 闭集派生），前端据此渲染计划树、思考流、工具卡片与审批卡片。

> `AGENT_ARCHITECTURE.md` 的「配置体系」示例写的是较早的默认值（如 `max_iterations=10`、`max_plan_steps=8`）；与代码冲突时以 `backend/app/agent/config.py` 为准。

### 3.2 RAG 链路与降级语义

一次 RAG 问答的实际路径（`backend/app/rag/`）：

```
查询
  → 意图 / 复杂度分类
  → 查询改写（1.2s 超时）/ HyDE（1.5s 超时，可选）
  → HybridRetriever：关键词（BM25 风格）+ 向量（cosineSimilarity）+ RRF 融合（k=60）
  → MMR 去重（默认开启；条款定位类查询跳过）
  → Reranker：本地 cross-encoder → 云端 API → LLM 重排 → 不重排
  → 上下文压缩：按查询关键词做句子抽取（规则式，不调用 LLM）
  → 精确缓存（默认开启，TTL 600s）；语义缓存默认关闭（见 3.6）
  → LLM 流式生成（PII 掩码）
```

**检索的三态语义**（`backend/app/rag/errors.py`）：

| 状态 | 表现 |
|------|------|
| 正常但为空 | 返回空结果；`/knowledge/search` 附带 `index_status` 与 `index_doc_count` 帮助判断「库里有没有东西」 |
| 服务失败 | `RetrievalUnavailableError(kind="failed")`；REST 映射为 **502** `error_code=RETRIEVAL_UNAVAILABLE`，`detail.kind` 说明原因 |
| 超时 | `RetrievalUnavailableError(kind="timeout")`，同样 502；不会伪装成「没有相关文档」 |

- 聊天严格模式收到检索失败时发 SSE `error` 事件；非严格模式注入「检索不可用」提示，让模型诚实回答而不是编造。
- **Rerank 是 fail-open 的**：本地模型不可用、API 失败、LLM 重排失败都会降级为「保持融合后的原顺序」，不会让整次检索失败；降级结果不会被写入缓存（`backend/app/rag/reranker.py`、`pipeline.py`）。
- **关键词臂失败会中止该次混合检索尝试**（向量臂成功也不够），流水线会按子查询重试（`RAG_RETRIEVAL_MAX_RETRIES` 默认 2，即最多 3 次）。
- **GraphRAG 不在 RAG 检索链路里**：图谱抽取在索引成功后非阻塞执行，失败只告警；检索仍由 HybridRetriever 完成（`README.md`、`backend/app/worker/doc_processor.py`）。图谱可在知识库页面查看，或通过 `/api/v1/knowledge/graph*` 查询。

### 3.3 组织与多租户：组织 = 知识库

ADR-001 已把这条语义冻结（`docs/decisions/ADR-001-路线冻结.md` D5）：

- **不建 `knowledge_bases` 表**。一个组织就是一个知识库单例：文档、chunk、索引、检索都按 `organization_id` 隔离。
- 用户属于某个组织（`users.organization_id`）；业务端点通过 `require_organization` 取组织，非超管缺失组织时 fail-closed（403）；超管无组织时会回源到库中真实存在的组织。客户端不能用请求体自选组织。
- **切换组织**走 `POST /api/v1/users/me/organizations/{organization_id}/switch`：写入 `users.organization_id`，非成员 403，超管旁路，并写审计日志 `switch_organization`。后续列表/检索都以该字段解析组织上下文。
- **管理员语义（D3）**：取消「`role=admin` 隐式等于全局管理员」；平台级操作只认显式 `is_superuser`，组织管理员由组织成员角色判定（`user_organization_role_association` 的 OrgAdmin），只能管理本组织。
- 知识检索接口 `POST /api/v1/knowledge/search` 的 `organization_id` 是必填字段；非超管传入值会被服务端以有效组织覆盖，跨组织数据不可见。
- Agent 任务的组织校验更严：`AgentTask.organization_id` 必须与当前有效组织完全一致（包括 NULL），否则 404。
- 语义缓存、RAG 缓存、图谱分区都按组织隔离（`README.md`「已修复」与 `backend/app/services/graph_rag_service.py`）。
- 边界：D5 的「清理误导性命名」尚未全部落地——`/knowledge` 列表与前端 `KnowledgeBase` 类型仍沿用旧命名，实际语义以「组织=知识库」为准，不要据此假设支持多知识库。

> 组织切换与历史会话的可见性取决于会话记录的 `organization_id`；详见 7.4。

### 3.4 工具与 HITL 审批

**工具**：所有工具通过 `@register_tool(name=...)` 自注册，共 **33 个内置工具**（`docs/facts.json` 的 `registered_tools`；14 个在 `core_tools.py`，19 个在 `tools/`），另有 MCP 动态接入。工具参数用 JSON Schema 描述，自动转成 OpenAI function-calling 格式；`tags` 用于配置面板筛选，敏感工具在 `DEFAULT_DISABLED_TOOLS` 中默认禁用（`AGENT_ARCHITECTURE.md`）。

**HITL（人在环）** 让 Agent 在「有风险的下一步」停下来等人拍板：

| 配置 | 取值 | 含义 |
|------|------|------|
| `approval_mode` | `never` / `risky` / `always`，默认 `risky` | 何时对**工具调用**停等审批。默认 `risky` 配合「高危工具默认禁用」，默认配置下审批量为 0；启用高危工具后即真实生效 |
| `plan_approval_mode` | `never`（默认）/ `always` | 是否在**计划生成后**先审批再执行（目前仅 API 可设，界面未暴露） |
| `approval_timeout_seconds` | 整数，默认 `0` | `0` = 一直等；`>0` 时到期把任务置 `FAILED(approval_timeout)`，**不会**伪装成「被拒绝」（目前仅 API 可设） |

- 停等时 SSE 发出 `approval_required`（或 `plan_approval_required`），随后 `done` 事件带 `task_state=waiting_approval`（或 `waiting_plan_approval`）。
- 审批接口：`POST /api/v1/agent/tasks/{task_id}/approval`（`decision=approve|deny`，可选 `modified_instruction`（≤2000 字）与 `note`（≤500 字））；计划审批：`POST /api/v1/agent/tasks/{task_id}/plan-approval`（`decision=approve|edit|reject`，`edit` 时带 `edited_steps`；第一次 `reject` 触发一次 replan，第二次直接终态 `rejected`）。
- `approve` 的粘滞范围是 **(tool_name, args) 指纹**：同一 run 内同一调用不再重复打扰；`deny` 让该调用不执行，并以结构化失败 `approval_denied` 交给反思器继续/收敛。
- `approve + modified_instruction` 是「改指令续跑」：人工指导会注入后续步骤提示词。
- 续跑可能停在下一个审批点；并发应答返回 409（`TASK_NOT_WAITING_APPROVAL` / `TASK_NOT_RESUMABLE` / `TASK_BUSY`）。

### 3.5 提示词三层：system / organization / personal

提示词模板（`prompt_templates`）用 `scope` 区分三层可见性（`backend/app/api/v1/endpoints/prompts.py`、迁移 `011_organization_settings.py`）：

| 层 | 存储判据 | 谁能看 | 谁维护 |
|----|----------|--------|--------|
| **system** | `is_system=true AND organization_id IS NULL` | 所有组织只读 | 平台 |
| **organization** | `organization_id = 本组织 AND scope='organization'` | 本组织成员 | 组织管理员 |
| **personal** | `organization_id = 本组织 AND scope='personal' AND creator_id = 当前用户` | 仅创建者 | 用户自己 |

- ADR 约定每个 `(org, category, scope)` 单 active、由管理员事务维护；但**当前代码未强制唯一**：可以同时存在多个 active 模板，RAG 取 `updated_at` 最新的一个（`backend/app/services/chat_service.py`）。启用新版本前建议手动停用旧版本。模板有版本历史（`PromptTemplateVersion`），`/api/v1/prompts` 提供 CRUD 与版本回滚。
- 写权限：system 模板只读；personal 模板仅创建者可写；组织模板的**激活**只有组织管理员能做，非管理员创建的组织模板默认 inactive；客户端不能伪造 `system` 层。激活动作写审计。
- 迁移 011 之前的存量非系统模板若没有组织归属，只对超管可见——不会被所有租户当成系统模板读取。
- 回滚注意：删除 `scope` 列会让 personal 模板退化为组织可见；回滚前先停用个人模板（迁移文件头注释）。
- 三种 scope 是**模板管理层**的概念；Agent 运行时并不会自动叠加三层提示词——Agent 使用内置 system prompt，可选 `system_prompt_override`，模板只通过 `list_prompt_templates` / `get_prompt_template` 工具被 Agent 读取。

### 3.6 语义缓存为什么默认关闭

语义缓存（`SEMANTIC_CACHE_RETRIEVAL_ENABLED` / `SEMANTIC_CACHE_ANSWER_ENABLED`）默认都是 `false`（issue #195）：

- 余弦阈值 0.92 只能做**粗筛**。相邻条款（「第七条」vs「第十三条」）或同句式换实体（「北京」vs「上海」）的向量相似度也可能超过阈值，命中后会**跨问题返回他人的 sources 或完整答案**。
- 因此检索 sources 与问答 answer 两个语义缓存命名空间默认关闭；即使显式开启，命中仍必须通过条号/数字/实体等价判定（`rag/cache.py::queries_equivalent`），阈值不是唯一判据。
- **精确缓存不受影响**：`RAG_ENABLE_CACHE=true` 默认开启，键为 `query + org + top_k`，TTL 600 秒，足以覆盖重复提问。
- 依据：`backend/app/core/config/ai.py` 的注释、`backend/app/rag/cache.py`、`backend/docs/retrieval_routing.md`。

---

## 4. 功能实战

### 4.1 上传文档并让它可检索

**支持的格式**（以 `backend/app/services/document_parser.py` 的 `supported_extensions` 为准）：

| 类型 | 扩展名 | 解析方式 |
|------|--------|----------|
| PDF | `.pdf` | `pdfplumber` 抽文本；页面文本少于 50 字符时尝试 `pytesseract` + `pdf2image` OCR |
| Word | `.docx` | `python-docx` 为主，`docx2txt` 兜底（不支持旧版 `.doc`） |
| Excel | `.xlsx` | `openpyxl`。`.xls` 虽在扩展名映射里，但 `openpyxl` 不支持旧版 `.xls`，实际会失败 |
| 文本 | `.txt` / `.md` / `.csv` | 直接读取 |
| 图片 | `.png` / `.jpg` / `.jpeg` / `.webp` / `.bmp` / `.gif` | RapidOCR（ONNX）；GIF 仅识别首帧 |

注意：

- **不支持** `.doc` / `.ppt` / `.pptx`；上传这类文件会在解析阶段报「不支持的文件格式」。前端上传选择器的 `accept` 也不含 `.gif`（后端解析器支持 `.gif`，可通过 API 上传）。
- 图片 OCR 依赖 `rapidocr-onnxruntime`（在 `requirements.txt` 中）；**扫描版 PDF** 的 OCR 依赖 `pytesseract` / `pdf2image` 与系统级 Tesseract/Poppler，这两项不在默认依赖里——纯扫描 PDF 没装它们会解析失败。
- 单文件上限 `MAX_FILE_SIZE=100 MB`；`USER_STORAGE_LIMIT_BYTES=10 GiB` 目前只用于统计展示，上传路径并未强制执行。
- 分块是**语义分块**（`backend/app/services/semantic_chunker.py`）：按嵌入距离的 90 分位找断点，`chunk_max_chars=1200`；短文本（<500 字符）整体成块；文本足够长且摘要生成成功时，会为每块加一段 50–100 字的文档摘要前缀（contextual retrieval）。
- 知识库页面会在存在待处理文档时每 5 秒轮询状态；失败行提供「重新解析」，顶部提供「最近失败文档重建」，两者都走 `POST /knowledge/rebuild/{document_id}`。

**上传后发生了什么**：

```
POST /api/v1/documents/upload（multipart: file / title / description / tags）
  → 文件写入 MinIO（{organization_id}/{md5}.{ext}）
  → documents 表创建记录：status=PENDING
  → knowledge_processing_jobs 创建任务：QUEUED / trigger_type=upload
  → Kafka file-processing 主题 → Worker 解析 → 分块 → 嵌入 → ES 索引
  → status: PARSING → INDEXED（失败则 FAILED + parse_error）
```

文档状态枚举：`PENDING` / `UPLOADED` / `PARSING` / `PARSED` / `INDEXED` / `FAILED`（`backend/app/models/document.py`）。主处理链路实际使用 `PARSING → INDEXED/FAILED`；`PARSED` 仅存在于旧解析路径。

**Kafka 不可用时**：上传接口检测到生产者未初始化或发送失败，会直接用 `asyncio.create_task` 在进程内跑同一个 `DocumentProcessor`（`backend/app/api/v1/endpoints/documents.py`），本地开发不会卡在 pending。若连进程内调度都失败，文档与任务会被标为 `FAILED`，响应 `success:false`（HTTP 仍是 201，因为文件已接收）。

**验证「能被检索」**：

1. 打开知识库页面搜索，或调用 `POST /api/v1/knowledge/search`；
2. 看文档状态是否为 `INDEXED`；
3. 看 `/readyz` 的 `knowledge_base` 检查是否 `healthy`；
4. 若「状态是 INDEXED 但搜不到」，先对账：

```bash
cd backend
python scripts/reconcile_knowledge_index.py               # 只读对账，不写任何数据
python scripts/reconcile_knowledge_index.py --reindex     # 修复 missing / partial
python scripts/reconcile_knowledge_index.py --org 3       # 限定组织
python scripts/reconcile_knowledge_index.py --reindex --limit 10
```

对账口径：以 `documents.status=INDEXED` 与 `chunk_count` 为基准，对比 ES 按 `document_id` 聚合的 chunk 数，分为 `missing` / `partial` / `extra` / `ok`；`extra` 与「ES 有、DB 未标 INDEXED」的孤儿只报告、**不自动删除**（需人工确认）。`--reindex` 只为 `missing`/`partial` 建 `reprocess` 任务并走正式处理链，不会绕过解析/校验直写 ES。退出码：`0` 一致、`1` 仍有未恢复项、`2` 致命错误。

**单篇重建**：`POST /api/v1/knowledge/rebuild/{document_id}`（会先删该文档的 ES chunk，再走 Kafka / 进程内处理）。

**图谱**：索引成功后会非阻塞地抽取知识图谱，按组织存 Redis（30 天 TTL）。它不参与检索排序，失败也不会把文档标成 FAILED。历史文档可用 `backend/scripts/backfill_graph.py` 回填；图谱页按 `max_nodes`（后端 5–200，默认 50）截断展示。

### 4.2 对话与深度分析

对话页（`/chat`）有两个模式，入口是输入区的「快速问答 / 深度分析」二选一开关（`frontend/src/views/chat/components/ChatInput.vue`）：

| 模式 | 实际路径 | 适用 |
|------|----------|------|
| **快速问答** | RAG 管线（`POST /api/v1/chat/stream`） | 基于知识库的检索问答，带来源引用，延迟低 |
| **深度分析（Agent）** | 同一入口，`useAgent=true`，复用 `/agent` 的 PER 能力 | 多步规划、工具调用、反思校正；**只走 SSE**（前端会强制 `useSSE=true`） |

请求字段就是 `content` / `conversationId` / `fileIds` / `useAgent` / `debug` 与 `payload`（其中 `strictMode` / `privacyMode`）——没有 `mode` 或 `deep_analysis` 字段，「深度分析」在协议层就是 `useAgent=true`。

实战要点：

- **带文件提问**：请求里的 `fileIds` 指向已上传文件；`POST /api/v1/chat/parse-upload` 只做解析、不做索引，适合「临时读一份文档」而不是入库。
- **严格模式 / 隐私模式**：通过请求体 `payload` 传递（`strictMode` / `privacyMode`）。严格模式下检索不可用会直接发 SSE `error`，不会让模型「自由发挥」；隐私模式配合全链路 PII 掩码。
- **停止生成**：前端用 AbortController 中断 SSE；服务端按连接断开收尾。
- **历史会话**：`GET /api/v1/chat/conversations` 支持 `page` / `page_size` / `search`（标题模糊过滤），按更新时间倒序；会话按「本人 + 当前组织」可见（见 7.4）。
- **深度分析会展示过程**：计划树、思考流、工具调用卡片、反思结果都由 20 种 Agent SSE 事件驱动；快速问答只产生 `chunk` / `message` / `error` / `debug` 等少量事件。
- **需要中途拍板时**：深度分析继承了 HITL。`approval_mode` 默认 `risky`，但高危工具默认禁用，所以默认配置下不会触发审批；一旦启用 `execute_python` / `execute_sql` / `mcp_call` 等风险工具，遇到风险调用就会停在审批点。复杂任务建议去 Agent 工作台（4.3）处理，那里有完整的审批与回放视图。
- **边界**：独立搜索页的文件类型筛选目前是**前端展示项**——`file_types` 不在后端 `KnowledgeSearchRequest` 里，过滤不会生效；请用查询词与 `search_mode` 控制检索。

### 4.3 Agent 工作台：审批、断点续跑、回放

Agent 工作台（`/agent`）是「可干预 Agent」的主界面：左侧计划/会话，中间对话与工具卡片，右侧配置面板（`AGENT_ARCHITECTURE.md`「前端架构」，组件在 `frontend/src/components/agent/`）。

**配置**：模型与温度、Planning / Reflection / Memory / Thinking 开关、人格、审批模式、工具启用列表、系统提示词覆盖（`AgentConfigPanel.vue`）；`GET/PUT /api/v1/agent/config`，按用户持久化。Agent 会话：`GET/POST /api/v1/agent/sessions`、`GET/DELETE /api/v1/agent/sessions/{id}`；工具清单：`GET /api/v1/agent/tools`（含参数 Schema、tags、`disabled_by_default` 与总数）。

**服务端命令**（`GET /api/v1/commands`、`POST /api/v1/commands/execute`）：输入 `/help`（命令清单）、`/status`（会话与最近任务）、`/export [markdown|json]`（导出当前会话）、`/tools`（工具与默认禁用状态）。命令在进入模型之前执行，不写用户消息、不产生模型轮次；未知命令返回明确错误而不会当作提示词发给模型。关闭 `COMMANDS_ENABLED` 可整体回退。

**审批与断点续跑**（HITL，语义见 3.4）：

1. 运行到风险点时流内出现 `approval_required` / `plan_approval_required`，任务进入 `waiting_approval` / `waiting_plan_approval`；
2. 在审批卡片（`ApprovalCard.vue` / `PlanApprovalCard.vue`）上批准 / 拒绝 / 改指令，对应 `POST /api/v1/agent/tasks/{task_id}/approval` 与 `.../plan-approval`；
3. 服务端从 checkpoint 续跑，SSE 事件契约与首次运行一致（没有单独的「resumed」事件，`done` 里带 `decision` / `task_state`）；
4. 刷新或断线后：`GET /api/v1/agent/tasks/{task_id}` 返回待审批详情与是否超时；`GET /api/v1/agent/tasks` 列出任务及 `has_replay`；
5. 协作式取消：`POST /api/v1/agent/tasks/{task_id}/cancel`——等待审批时直接终态 `cancelled`（200），运行中返回 202 `cancel_requested` 并在检查点收敛，终态任务 409，Redis 不可用 503；取消不是硬杀，当前工具/LLM 调用会先跑完；
6. 运行中转向（steering）：`POST /api/v1/agent/tasks/{task_id}/steer`（消息 1–2000 字，队列上限 5），消息在步边界注入后续提示词（SSE 事件 `steer_applied`）；队列满 429、状态不符 409、通道不可用 503。

**回放**：`GET /api/v1/agent/tasks/{task_id}/replay?format=json|text` 返回该次运行的执行快照（步骤、工具调用、结果、决策、失败、token 与耗时时间线）；`format=text` 为 CLI 风格文本。快照正常写入 Redis（7 天 TTL）；本地 `benchmark/replay/{task_id}.json` **只在 Redis 写入失败时**作为兜底落盘，读取时先 Redis 后本地——Redis 正常时 TTL 过期后本地通常没有副本。`GET /agent/tasks/{id}` 与任务列表里的 `has_replay` 表示是否可回放。离线命令需在 `backend/` 目录执行：`python -m benchmark.replay <task_id>`、`python -m benchmark.replay --diff <a> <b>`、`--list`。前端入口是运行记录里的回放按钮与 `ReplayTimeline.vue`，支持 `?id=<task_id>` 深链。

**边界**：

- 暂停/取消不是「完成」——不会落库半成品回答；只有真正完成的 run 才补写会话消息。
- `deny` 的调用会以结构化失败交给反思器，可能继续执行其他步骤，而不是整次失败。
- 审批超时任务会置 `FAILED(approval_timeout)`，不会伪装成被拒绝。

### 4.4 工作流

工作流页（`/workflow`）是可视化 DAG 编辑器（Vue Flow）。节点面板的常见类型（`frontend/src/views/workflow/composables/useWorkflowEditor.ts` 的内置定义）：

| 类别 | 节点 |
|------|------|
| LLM | OpenAI GPT、DeepSeek、通义千问 |
| 工具 | 知识库检索（`tool_search`）、语音合成（`tool_tts`） |
| I/O | 输入（`input`）、输出（`output`） |
| 逻辑 | 条件分支（`condition`）、智能路由（`router`） |
| 数据 | 记忆（`memory`）、代码执行（`code`）、API 调用（`api_call`）、数据转换（`transform`） |

> 后端执行器实际支持的就是上表这些类型（`backend/app/services/workflow_engine.py`）。前端在 API 节点定义加载失败时会展示一个通用 `llm` 兜底节点，但后端没有 `llm` 执行器，运行会报 `Unknown node type: llm`——请使用三个具名 LLM 节点。

API 前缀 `/api/v1/workflows`：工作流 CRUD、执行、调试、节点定义。工作流按组织隔离，归属校验在服务端强制执行。

**代码执行节点**（安全边界）：

- 默认关闭：`ENABLE_WORKFLOW_CODE_NODES=false`。执行时若未开启，会得到明确错误：`代码执行节点未启用，请设置 ENABLE_WORKFLOW_CODE_NODES=true`（`backend/app/services/workflow_engine.py`）。
- 即使开启，工作流代码节点也**只认 Docker 沙箱**：`SANDBOX_MODE=ast` 对工作流不生效；Docker 不可用时抛 `代码执行节点需要 Docker 沙箱，当前未启用`（fail-closed）。
- 全 Docker 姿态默认没有把 `/var/run/docker.sock` 挂进 backend 容器，镜像里也没有 Docker CLI，因此**容器内的代码节点会走「Docker 不可用」拒绝路径**；需要时显式挂载 socket 或改为宿主机运行后端（见 7.6）。

**定时任务**：`/api/v1/scheduled-tasks` 提供 CRUD、暂停/恢复与手动触发；调度器由 `ENABLE_SCHEDULER` 控制，是**进程内 asyncio 循环（单实例语义）**，多 worker 部署需外部调度。

### 4.5 组织设置与组织切换

**组织管理**（`/organizations`，管理员）：组织 CRUD、成员管理、角色分配。平台级操作只认 `is_superuser`；组织管理员只能管理本组织（ADR-001 D3）。

**组织设置**（与组织管理共用 `/api/v1/organizations` 前缀，`backend/app/api/v1/endpoints/organization_settings.py`）：

| 设置项 | 含义 |
|--------|------|
| `default_provider` / `default_model` | 组织默认模型与 Provider |
| `retrieval_top_k` | 检索条数（1–50） |
| `allow_privacy_mode` | 是否允许成员开启隐私模式 |
| `monthly_token_quota` | 月度 token 配额（null = 不限额） |
| `tool_whitelist` | 工具白名单 |

- `GET`（生效值）组织成员可读，返回每项的 `value` / `source`（`organization` 覆盖或 `default` 平台默认）/ `requires_restart`，并附带本月用量 `usage.month_tokens` 与配额。
- `PUT` 与审计读取仅限**本组织管理员**或平台超管；所有写入落 `user_activity_logs`（action=`update_organization_settings`）。
- 覆盖是「部分更新」：省略字段保持不变，显式 `null` 清除覆盖、回退平台默认。
- **边界（诚实口径）**：`default_model`、`retrieval_top_k`、`allow_privacy_mode` 已接入运行时；`default_provider`、`monthly_token_quota`、`tool_whitelist` 目前只做存储与展示，**尚未强制执行**（`backend/app/services/organization_settings_service.py`）。

**组织切换**：桌面端在顶栏 `OrgSwitcher` 切换（单组织时显示为静态文本），移动端在用户菜单里。切换后，知识库（组织 = 知识库）与会话列表都按新组织过滤，当前页面会重新挂载并刷新数据。切换看不到旧会话是预期行为，见 7.4；用同一个组织上下文检索、上传，才能看到对应数据。

### 4.6 监控

监控页（`/monitoring`）**只对平台管理员（显式 `is_superuser`）开放**（前端路由 `requiresPlatformAdmin`；后端各端点鉴权不完全一致，部分仅需登录，具体以 Swagger 为准）。页面展示：通知服务状态、CPU/内存、应用指标（活跃连接、请求数、错误数、平均响应时间）、知识库指标（文档/chunk/索引大小/搜索次数）、告警计数与最近告警、趋势图、审计日志；时间范围 1h/6h/12h/24h/7d，每 30 秒自动刷新。对应 `/api/v1/monitoring` 下的端点：仪表盘数据、趋势、系统统计、告警与告警规则、外部告警回调（Alertmanager webhook）、路由性能、通知服务状态、系统日志、性能指标、LLM 资源消耗、组织概览、存储分析、`health`、`llm-status`，以及 RAG 质量评估（单条 / 批量）。页面本身**不嵌入** Prometheus/Grafana 链接。

指标栈：

- **Prometheus**：抓取后端 `/metrics`（HTTP 请求量/错误/延迟/状态码、WebSocket 连接、RAG/Agent 业务指标）。`PROMETHEUS_ENABLED=true` 才打开业务指标；`METRICS_TOKEN` 可给抓取加 Bearer。
- **Grafana**：全 Docker 姿态下 `http://localhost:3001`，预置面板来自 `backend/config/grafana/`；管理员口令由 `GRAFANA_PASSWORD` 提供。
- **告警**：阈值类配置在 `.env`（`ALERT_ERROR_RATE_PERCENT`、`ALERT_P95_MS`、`ALERT_ACTIVE_CONNECTIONS` 等）；后端与 `deploy/monitoring` 的 `ALERT_WEBHOOK_TOKEN` 必须一致。
- **探针**：`/livez`、`/readyz` 的语义与就绪条件见 1.8；worker 用心跳文件而非 HTTP 探针。
- **追踪（可选）**：Langfuse 记录 LLM 调用；OpenTelemetry 需 `ENABLE_TRACING=true` 且配置 `OTLP_ENDPOINT`（`os.getenv` 直读）。

---

## 5. API 与集成

### 5.1 鉴权

所有业务端点统一前缀 `/api/v1`（`backend/app/main.py` 挂载），鉴权方式为 **JWT Bearer**：

| 动作 | 端点 | 请求 |
|------|------|------|
| 表单登录 | `POST /api/v1/auth/login` | `application/x-www-form-urlencoded`：`username`、`password` |
| JSON 登录 | `POST /api/v1/auth/login/json` | JSON：`{ "username": "...", "password": "..." }` |
| 注册 | `POST /api/v1/auth/register` | JSON：`username`、`email`、`password`、`full_name?` |
| 刷新 | `POST /api/v1/auth/refresh` | JSON：`{ "refresh_token": "..." }` |
| 登出 | `POST /api/v1/auth/logout` | `Authorization: Bearer <access>`，可选 `{ "refresh_token": "..." }` |

登录成功返回统一信封：

```json
{
  "success": true,
  "message": "...",
  "data": {
    "access_token": "...",
    "token_type": "bearer",
    "expires_in": 86400,
    "refresh_token": "...",
    "user_info": { "...": "..." }
  }
}
```

`expires_in` 的单位是**秒**（`ACCESS_TOKEN_EXPIRE_MINUTES * 60`），不是分钟。

- HTTP 请求携带 `Authorization: Bearer <access_token>`。
- **Refresh 轮换**：刷新时旧 refresh token 会被加入黑名单并签发新的 access + refresh；旧 access token 不会被立即吊销（自然过期）。Redis 不可用时登出/黑名单 fail-closed 返回 503。
- WebSocket 优先用子协议 `Sec-WebSocket-Protocol: auth.<token>`，兼容旧式 `?token=<token>`；WS 端点：`/api/v1/chat/ws`、`/api/v1/notifications/ws`。
- 主 API **没有** API Key 鉴权；`X-API-Key` 只出现在 dev-only 的独立 Agent 测试服务 `backend/app/agent_api.py`（端口 8010，不随生产部署）。

### 5.2 端点模块清单

当前共 **25 个端点模块**（`docs/facts.json` 的 `endpoint_modules`，由 `backend/app/api/v1/router.py` 的 `include_router` 调用数派生）：

| 模块 | 前缀 | 说明 |
|------|------|------|
| auth | `/api/v1/auth` | 登录、注册、刷新、登出、改密 |
| users | `/api/v1/users` | 用户 CRUD、个人资料、会话、审计日志 |
| files | `/api/v1/files` | 文件上传/头像/列表/删除（MinIO）；文档下载走 `/api/v1/documents/{id}/download` |
| documents | `/api/v1/documents` | 文档上传、解析、向量化、状态 |
| chat | `/api/v1/chat` | 会话 CRUD、SSE 流式问答 |
| knowledge | `/api/v1/knowledge` | 知识检索、统计、重建、图谱 |
| organizations | `/api/v1/organizations` | 组织 CRUD、成员与角色 |
| organization_settings | `/api/v1/organizations` | 组织级设置（与组织管理共用前缀） |
| monitoring | `/api/v1/monitoring` | 性能指标、健康、告警、RAG 质量 |
| notifications | `/api/v1/notifications` | 通知 CRUD 与 WebSocket |
| prompts | `/api/v1/prompts` | 提示词模板与版本；三层 scope |
| token_usage | `/api/v1/token-usage` | Token 用量与报告 |
| manuals | `/api/v1/manuals` | 操作手册 |
| workflow | `/api/v1/workflows` | 工作流 CRUD、执行、调试 |
| llm_config | `/api/v1/llm-config` | 已弃用入口（端点标 deprecated） |
| curated_skills | `/api/v1/curated-skills` | 技能库 |
| memory | `/api/v1/memory` | Agent 记忆（短期/长期/工作/反思） |
| agent | `/api/v1/agent` | PER Agent SSE、工具、会话、配置 |
| commands | `/api/v1/commands` | 服务端命令清单与无模型轮次执行（`/help`、`/status`、`/export`、`/tools`） |
| mcp | `/api/v1/mcp` | MCP 服务器状态（管理员只读） |
| scheduled_tasks | `/api/v1/scheduled-tasks` | 定时任务 CRUD / 暂停 / 手动触发 |
| sessions | `/api/v1/sessions` | 会话 fork / seed / 谱系（issue #476） |
| demo | `/api/v1/demo` | 演示数据填充 |
| user_settings | `/api/v1/user` | 个人 UI 偏好 |

完整交互文档以运行中的 Swagger 为准：`http://localhost:8000/docs`（OpenAPI：`/api/v1/openapi.json`）。

### 5.2.1 会话分叉 API（issue #476）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/v1/sessions/{id}/fork` | 从已结算边界分叉子会话 |
| GET | `/api/v1/sessions/{id}/lineage` | 祖先链（根→父）+ 直接子级 + 谱系元数据 |

`fork` 请求体（全部可选）：`message_id`（边界消息；ASSISTANT=复制到该消息为止，
USER=在该消息之前分叉；默认最后一条已结算消息）、`title`、`include_checkpoint`
（深拷最近一个已结算 checkpoint 为新 PAUSED 任务，可 resume）、`seed`（写入子会话
独立记忆命名空间的种子内容）、`reason`。幂等：请求头 `Idempotency-Key`，顺序重试
返回同一子会话（并发同键不保证）。

错误码：`404`（会话/边界消息不存在、他人或跨组织，统一不泄露归属）、
`409`（`FORK_BOUNDARY_INVALID` / `FORK_QUOTA_EXCEEDED` / `FORK_CHECKPOINT_REQUIRED` /
`FORK_CHECKPOINT_UNAVAILABLE`）、`400`（seed 超长）。

谱系字段：`parent_session_id` / `root_session_id` / `fork_point_message_id` /
`fork_reason` / `is_fork` / `is_seeded`（会话列表/详情与 `lineage` 均返回）。
fork 只能落在已结算轮次（复制前缀以 assistant 结尾或为空；assistant 中间消息与
开放轮次 409）；复制消息在子会话内重新编号 `seq`；默认不复制母记忆。

### 5.3 常见请求示例

**登录并取出 token**

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/api/v1/auth/login/json \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"<你的密码>"}' \
  | python -c "import sys,json;print(json.load(sys.stdin)['data']['access_token'])")
```

**上传文档**（组织取自当前用户上下文，不接受客户端指定 `organization_id`）

```bash
curl -X POST http://localhost:8000/api/v1/documents/upload \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@./manual.pdf" \
  -F "title=产品手册" \
  -F "description=内部资料" \
  -F 'tags=["manual","rag"]'
# 201 → {"success": true, "data": {"id": ..., "filename": ..., "status": ...}}
```

**知识库检索**（`organization_id` 为必填）

```bash
curl -X POST http://localhost:8000/api/v1/knowledge/search \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"如何配置备份？","organization_id":1,"top_k":5,"search_mode":"hybrid"}'
```

响应含 `results[]`、`total_count`、`search_mode`、`search_time`、`index_status`、`index_doc_count`（`backend/app/schemas/knowledge.py`）。

**RAG 对话（SSE）**

```bash
curl -N -X POST http://localhost:8000/api/v1/chat/stream \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"content":"这份手册讲了什么？","conversationId":null}'
```

- RAG 路径的 SSE 线格式为 `event: <type>` + `data: <json>`，事件包括 `chunk`、`message`、`error`、`debug`。
- `useAgent=true` 时走 Agent 路径，事件包括 `thinking`、`plan_start`、`plan_step`、`plan_complete`、`tool_call`、`tool_result`、`tool_error`、`reflection`、`execution_context`、`warning`、`message`、`error`。
- **Agent 端点**（`POST /api/v1/agent/chat`）的帧格式不同：只有 `data: {"type": ..., "content": ...}`，没有 `event:` 行。

**Agent 对话**

```bash
curl -N -X POST http://localhost:8000/api/v1/agent/chat \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"知识库中有哪些文档？","enable_planning":true}'
```

请求体还支持 `session_id`、`enable_tools`、`enable_reflection`、`enable_memory`、`enable_thinking`、`model`、`temperature`、`disabled_tools`、`system_prompt_override`、`approval_mode`、`plan_approval_mode`（`backend/app/api/v1/endpoints/agent.py`）。

**健康与指标**

```bash
curl -s http://localhost:8000/readyz
curl -s -H "Authorization: Bearer $METRICS_TOKEN" http://localhost:8000/metrics
```

---

## 6. 运维

### 6.1 数据库迁移（Alembic）

```bash
cd backend
source venv/bin/activate
alembic upgrade head                  # 应用到最新
alembic revision --autogenerate -m "描述"   # 新增迁移（开发）
```

- 配置：`backend/alembic.ini`；运行时的连接串由 `backend/alembic/env.py` 从 `settings.DATABASE_URL` 覆盖。
- 当前 head：`011_organization_settings`（`backend/alembic/versions/011_organization_settings.py`）。
- 应用启动时的 `init_db()` 只在**空库**执行 `create_all`；已有库不会自动补表。`USE_ALEMBIC=true` 时完全跳过 `create_all`（`backend/app/core/database.py`）。
- **发布顺序**：新代码可能读取新表/新列（例如 011 引入的 `organization_settings` 与 `prompt_templates.scope`）。升级既有部署时，**先跑迁移，再放流量到新实例**；滚动窗口内不要创建个人提示词模板（旧实例会把它当组织模板可见）。
- 011 的 `downgrade` 是**有损**的：删除 `organization_settings` 会丢失组织覆盖值；删除 `scope` 会让个人模板退化为组织可见。回滚前先导出/停用相关数据（迁移文件头注释）。

### 6.2 备份与回滚要点

仓库**没有**内置的备份/恢复脚本，`deploy/README.md` 只把「Set up database backups」列为上线检查项。实际部署请自行建立以下备份：

| 数据 | 位置 | 建议 |
|------|------|------|
| MySQL | Docker volume `mysql-data` | `mysqldump --single-transaction` 定期导出 |
| MinIO 对象 | volume `minio-data` | 使用 MinIO 自带的版本/复制，或对象存储侧备份 |
| Elasticsearch | volume `elasticsearch-data` | 可用 snapshot API；也可在事故后用 `reconcile_knowledge_index.py --reindex` 从 MySQL+MinIO 重建 |
| Prometheus / Grafana | `prometheus-data` / `grafana-data` | 视需要备份仪表盘与历史 |

回滚注意：

- 代码回滚不等于数据回滚。迁移 011 的 `downgrade` 有损（见 6.1），回滚前先备份。
- per-org RBAC 数据迁移有配套的 SQL 回滚脚本：`backend/scripts/migrations/w0_1_rbac_admin_scope_down.sql`，依据备份表 `w0_1_rbac_admin_backup` 精确恢复（`backend/README.md`）。
- 索引问题的首选修复不是「删库重建」，而是 `python scripts/reconcile_knowledge_index.py --reindex`（见 4.1）。

### 6.3 日志与 request_id

- 日志默认写入 `backend/logs/docmind.log`（`LOG_FILE`），`LOG_JSON` 控制是否 JSON 格式（代码默认 `true`，`.env.example` 模板为 `false`）。JSON 日志会自动带上 `request_id` / `trace_id` / `user_id`。
- **`X-Request-ID` 契约**：后端 `RequestIDMiddleware` 接受入站 `X-Request-ID`（仅当完整匹配 `[A-Za-z0-9._-]{1,64}`），否则生成 UUID；响应头回写同一值；错误响应体也包含 `request_id`。前端 axios 拦截器会自动生成/透传该头。
- 排障顺序：先在响应头拿 `X-Request-ID` → 在 JSON 日志中检索同值 → 定位同一请求的上下游日志。注意 CORS 预检与限流拒绝发生在该中间件外层，可能没有 request_id（`backend/app/core/request_id.py` 注释）。
- Agent 执行追踪是 **dev-only**：生产不写 JSONL trace；需要离线评测时用独立的 `backend/app/agent_api.py`（端口 8010）。

### 6.4 指标、告警与探针

- `/metrics`：Prometheus 文本格式；`METRICS_TOKEN` 非空时需 Bearer，否则 401。`PROMETHEUS_ENABLED` 默认 `false`，compose 中显式置 `true`。多 worker 部署前需先确认 `prometheus_client` multiprocess 配置（当前部署均为单进程）。
- 抓取与告警配置：`backend/config/prometheus.yml`、`backend/config/alerts.yml`；Grafana 预置面板在 `backend/config/grafana/`（全 Docker 姿态映射到 9090 / 3001）。**启用 `METRICS_TOKEN` 后要同步给 Prometheus 抓取配置加 `authorization` 头**，否则抓取会 401（仓库自带抓取配置未带该头）。
- 告警 webhook：`ALERT_WEBHOOK_TOKEN` 必须与 `deploy/monitoring` 侧一致，否则告警会被后端拒绝。
- Worker 存活：消费循环刷新心跳文件，`worker_healthcheck.py` 检查其新鲜度；进程冻结/卡死会判 unhealthy（compose 注释，issue #156）。
- 定时任务：`ENABLE_SCHEDULER=true` 时由进程内 asyncio 循环驱动，**单实例语义**；多 worker 部署需外部调度器。

---

## 7. 排错 FAQ

### 7.1 登录过期 / refresh 失败

- Access token 默认 24 小时（`ACCESS_TOKEN_EXPIRE_MINUTES=1440`），refresh token 默认 7 天（`REFRESH_TOKEN_EXPIRE_MINUTES=10080`）。
- 接口返回 401 时，用 refresh token 调 `POST /api/v1/auth/refresh` 换新的一对 token。**刷新会轮换**：旧 refresh token 立即进入黑名单，重复使用旧值会失败。
- 旧 access token 不会因刷新而被立即吊销，它会在自然过期前继续有效；登出会同时拉黑 access 与（若提供）refresh。
- 如果 refresh 也失败，或登出/刷新返回 **503**：多半是 Redis 不可用——Token 黑名单是 fail-closed 的，宁可拒绝也不放行。先查 Redis（`/health` 的 `services.redis`）。
- WebSocket 鉴权失败会以关闭码 `4001/4002/4003` 结束；确认使用的是 `Sec-WebSocket-Protocol: auth.<token>`（或旧式 `?token=`）。

### 7.2 文档显示已上传，但检索不到内容

按顺序排查：

1. **看状态**：文档必须是 `INDEXED`。`PENDING` / `PARSING` 说明 Worker 没消费；`FAILED` 看 `parse_error`（格式不支持、扫描版 PDF 缺 OCR 依赖、嵌入维度不一致等）。
2. **看组织**：组织 = 知识库。确认文档与检索请求属于同一组织；`/knowledge/search` 的 `organization_id` 必填，非超管传入值会被服务端以有效组织覆盖。
3. **看索引对账**：`/readyz` 的 `knowledge_base` 项会对比「DB 声称 INDEXED 的文档/chunk」与「ES 实际 chunk」。chunk 覆盖率 **<95% 为 `unhealthy`**（摘流 503）；**95%–100% 为 `degraded`**（不摘流）；0 chunk 或文档缺失同样为 `unhealthy`。
4. **跑对账脚本**（只读先看）：
   ```bash
   cd backend
   python scripts/reconcile_knowledge_index.py
   python scripts/reconcile_knowledge_index.py --reindex --limit 10
   ```
   `missing` / `partial` 可自动修复；`extra` 与孤儿只报告，需人工确认。
5. **看空结果的附加信息**：`/knowledge/search` 在结果为空时会返回 `index_status` 与 `index_doc_count`——先确认索引里到底有没有内容。
6. **Embedding 漂移**：如果检索直接 502 且 `error_code=RETRIEVAL_UNAVAILABLE`、`detail.kind` 指向 `vector_arm_config_error`，见 7.5。漂移是 fail-closed 的：即使关键词臂有命中也不会返回。
7. **Kafka 与 Worker**：Kafka 在跑但没有 Worker，文档会一直停在 `PENDING`——这是最常见的一类「假成功」。启动 `python start_worker.py`，或只用基础设施时确认没有半套 Kafka。
8. **Rerank 不是原因**：重排失败会 fail-open 保持原顺序，不会把结果清零；它只会让排序变差并记录 degraded 日志。

### 7.3 Agent 停在「等待审批」

这是 HITL 的正常状态，不是卡死：

- 流内收到 `approval_required`（或 `plan_approval_required`），`done` 事件带 `task_state=waiting_approval`（或 `waiting_plan_approval`）。
- 处理方式：
  - 工具审批：`POST /api/v1/agent/tasks/{task_id}/approval`，body `{"decision":"approve"}` 或 `{"decision":"deny","note":"..."}`；也可带 `modified_instruction` 改指令续跑。
  - 计划审批：`POST /api/v1/agent/tasks/{task_id}/plan-approval`（approve / edit / reject）。
  - 取消：`POST /api/v1/agent/tasks/{task_id}/cancel`。
- 刷新页面/重连后：`GET /api/v1/agent/tasks/{task_id}` 会带回待审批详情与 `approval_expired` 判定；`GET /api/v1/agent/tasks/{task_id}/replay` 可看完整回放。
- 审批超时：任务置 `FAILED(approval_timeout)`，API **不会**把它伪装成「被拒绝」；重新发起任务即可。
- 应答时若返回 409：`TASK_NOT_WAITING_APPROVAL`（状态不符/已超时）、`TASK_NOT_RESUMABLE`（已终态）、`TASK_BUSY`（并发应答）。按错误码处理，不要重试轰炸。
- 续跑后可能停在**下一个**审批点，这是预期行为（审批是按 `(tool, args)` 指纹粘滞的，不是一次性放行所有工具）。

### 7.4 切换组织后看不到旧会话

会话是**个人数据 + 组织作用域**：列表条件为「本人 + 未结束 +（当前组织 或 历史 `organization_id IS NULL`）」。切到 B 组织后，创建于 A 组织的会话不会出现在列表里；切回 A 组织即可看到。超管不受组织过滤影响（仍只看到自己的会话）。

另外，系统明确禁止把消息写入其他组织的会话（`save_session_interaction` 会抛 `AuthorizationError`），所以不会出现「切组织后消息写进旧会话」的静默串写。组织归属为 NULL 的历史会话属于迁移前遗留，只对本人可见。

### 7.5 Embedding 维度漂移（fail-closed）

症状与含义：

| 阶段 | 行为 |
|------|------|
| 启动 | 启动探测真实调用 embedding；返回维度 ≠ `VECTOR_DIMENSION` → **拒绝启动**（日志 `[EMBEDDING] 维度漂移，拒绝启动`）；服务不可达只告警 |
| 索引 | Worker 写入前校验每个向量；不一致抛 `EmbeddingDimensionMismatchError`，文档标 `FAILED` 并记录 `parse_error`（上传请求早已返回 201，失败看文档状态） |
| 检索 | 向量臂返回 `vector_arm_config_error`；流水线 **fail-closed** → `RetrievalUnavailableError`；REST 502 `RETRIEVAL_UNAVAILABLE`，聊天严格模式发 SSE `error` |
| ES 索引 | mapping 的 `dims` 与配置不一致抛 `IndexConfigError`，**不会自动重建索引**；`/health` 的知识库项会变 `unhealthy` |

修复步骤：

1. 确认 `EMBEDDING_API_URL` / `EMBEDDING_MODEL` / `VECTOR_DIMENSION` 三者配套（例如 `nomic-embed-text`=768、`bge-m3`=1024、OpenAI `text-embedding-3-small`=1536）；
2. 维度变了必须**重建 ES 索引并重新处理全部文档**（旧向量不能复用）；`reconcile_knowledge_index.py` 只修 missing/partial，不改 mapping；
3. 重启后端，确认启动日志无维度告警；
4. 不要用 `EMBEDDING_SEND_DIMENSIONS=true` 掩盖漂移——服务端截断会让「模型已经换了」看起来像正常。

### 7.6 沙箱无 Docker，代码执行被拒

- **Agent 代码工具**（`execute_python` / `execute_sql`）：`SANDBOX_MODE=auto`（默认）或 `docker` 时，找不到可用的 Docker daemon 就会**拒绝执行**并返回结构化失败，例如 `Docker 沙箱不可用（SANDBOX_MODE=auto），已拒绝执行代码。`——这是 issue #206 的 fail-closed 设计，不会静默回落到进程内执行。
- **工作流代码节点**：另有独立开关 `ENABLE_WORKFLOW_CODE_NODES=false`；开启后**只认 Docker 沙箱**，`SANDBOX_MODE=ast` 对它不生效，Docker 不可用时抛 `代码执行节点需要 Docker 沙箱，当前未启用`。
- 全 Docker 姿态下，compose **默认没有把 `/var/run/docker.sock` 挂进 backend 容器**，镜像里也没有 Docker CLI，所以容器内的代码执行/代码节点会走「Docker 不可用」拒绝路径。需要时：显式挂载 socket（了解容器逃逸风险），或让后端跑在宿主机上。
- `SANDBOX_MODE=ast` 是显式选择进程内 AST 白名单沙箱（隔离性弱于容器），只适用于 Agent 代码工具，不适用于工作流代码节点；生产环境优先修 Docker。

### 7.7 常见 5xx 与限流

| 现象 | 最可能原因 | 处理 |
|------|------------|------|
| **502** `RETRIEVAL_UNAVAILABLE` | 检索服务失败/超时，或 embedding 漂移 fail-closed | 看 `detail.kind`；查 ES 与 embedding；见 7.5 |
| **503** | `/readyz` 不就绪（DB / ES / 知识库对账失败）；或 Redis 不可用导致 Token 黑名单 fail-closed | 先看 `/readyz` 与 `/health` 的 `services`；再查 Redis |
| **500** | 未预期异常；细节只进服务端日志 | 记录响应头 `X-Request-ID`，在 JSON 日志中检索同值；`EXPOSE_EXCEPTION_DETAIL=false` 时客户端只看到通用文案 |
| **409** | Agent 任务状态冲突（`TASK_BUSY` / `TASK_NOT_RESUMABLE` 等） | 按错误码处理，不要并发重试 |
| **429** | 触发限流（宿主机模板 10000 次/分，compose 300 次/分） | 降频或调整 `RATE_LIMIT_REQUESTS_PER_MINUTE`；`/health`、`/livez`、`/readyz`、`/metrics` 免限流 |

排障通用顺序：`/readyz` → `/health` 的 `services` → 文档/任务状态 → 带 `X-Request-ID` 的服务端日志。

## 8. 开发与贡献

### 8.1 测试怎么跑

**后端**

```bash
cd backend
python -m pytest tests/ -v --tb=short                     # 全量
python -m pytest tests/unit/ tests/api/ tests/behavior/ -v  # 快测（CI PR 门禁跑这一组）
python -m pytest tests/ -v --cov=app --cov-report=html    # 覆盖率
```

测试分层用 marker 标注：`unit` / `api` / `integration` / `slow`（`backend/pyproject.toml`）。`pytest.ini` 已删除，唯一配置源是 `pyproject.toml`。

**前端**

```bash
cd frontend
npm test                  # = vitest run（没有 test:run 脚本）
npm run test:watch
npm run test:coverage
npm run type-check        # vue-tsc --noEmit
npm run lint              # eslint . --fix
npm run build             # vue-tsc && vite build
npm run test:e2e          # playwright test（需要已安装浏览器）
```

**全量**：`make test`（后端 + 前端），或分别执行上面的命令。

### 8.2 CI 门禁清单

按 `.github/workflows/ci-fast.yml` 与 `ci-hygiene.yml`，一个 PR 至少要过：

| 门禁 | 内容 |
|------|------|
| **Ruff** | `ruff check app/ tests/ scripts/ evals/` + `ruff format --check`（`backend/pyproject.toml` 配置） |
| **Bandit** | `bandit -r app/ -ll`（安全扫描） |
| **迁移冒烟** | `alembic upgrade head` → `check_schema.py`；MySQL job 还做 downgrade/upgrade 往返 |
| **后端测试** | `pytest tests/unit/ tests/api/ tests/behavior/`；Python 3.12 腿带 `--cov-fail-under=42` |
| **集成测试** | 对 `develop` 的 PR/push 跑 `pytest tests/`（MySQL + Redis + ES） |
| **评测门禁（Eval Gate）** | `make eval-ci`：重建评估索引（伪嵌入）→ 跑 28 条 golden set → 与 `backend/evals/baselines/ci_baseline.json` 零容差比对；任何下跌即红 |
| **前端** | `eslint .`、`vue-tsc --noEmit`、`vitest run --coverage`、`vite build` |
| **前端棘轮** | 硬编码中文基线、i18n 完备性、命名一致、用键定义（只允许缩小，不允许增长） |
| **E2E** | `playwright test` |
| **文档事实** | `python scripts/check_doc_facts.py` + 其判别力测试：源码 ↔ `docs/facts.json` ↔ 文档数字三方一致 |
| **提交规范** | commitlint；PR 还检查冲突标记、内部报告不入境、release 版本同步等 |

本地预检建议：后端 `ruff check` + `ruff format --check` + 相关 pytest；前端 `npm run type-check` + `npm test`。改了文档中的数字（工具数、端点模块数、路由数、SSE 事件数）时，先跑：

```bash
cd backend && python scripts/check_doc_facts.py --print-derived   # 看源码派生值
python scripts/check_doc_facts.py                                  # 校验三方一致
```

### 8.3 ADR（架构决策记录）流程

- 目录：`docs/decisions/`；它回答「为什么是这样」，与 git commit「改了什么」分工（`docs/decisions/README.md`）。
- 一份合格的决策记录必须包含：**结论放最前**；**被否决的方案及否决理由**；**依据**（规范原文 / 实测数据 / 代码证据，注明出处）；**明确区分实测与推导**；**已知不确定项**。
- 正式 ADR 命名参考 `ADR-001-路线冻结.md`；重要路线变更先落 ADR，再拆执行波次。
- 分支约定（`CONTRIBUTING.md`）：`main` 生产 / `develop` 集成分支 / `feat/*`、`fix/*`、`docs/*`；commit 使用 `<type>(<scope>): <description>`。

---

## 9. 事实来源索引

| 主题 | 真源 |
|------|------|
| 端口、服务、healthcheck、volume | `backend/docker-compose.yml` |
| 环境变量模板与必填项 | `backend/.env.example`、`backend/.env.docker.example` |
| 配置默认值 | `backend/app/core/config/{base,ai,database,security}.py` |
| 健康探针语义 | `backend/app/main.py`（`/livez`、`/readyz`、`/health`） |
| 端点模块清单 | `backend/app/api/v1/router.py`；数字口径 `docs/facts.json` |
| 文档格式与解析 | `backend/app/services/document_parser.py` |
| 索引对账/重索引 | `backend/scripts/reconcile_knowledge_index.py` |
| 迁移 | `backend/alembic/versions/`（head：`011_organization_settings`） |
| Agent 架构与事件 | `AGENT_ARCHITECTURE.md`、`backend/app/agent/events.py` |
| 组织=知识库、管理员语义 | `docs/decisions/ADR-001-路线冻结.md`（D3 / D5） |
| 部署与生产检查 | `deploy/README.md`、`SETUP.md` |
| CI 门禁 | `.github/workflows/ci-fast.yml`、`ci-hygiene.yml`、`docs/ci-charter.md` |
| 文档数字校验 | `backend/scripts/check_doc_facts.py`、`docs/facts.json` |
