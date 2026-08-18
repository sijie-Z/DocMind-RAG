# DocMind 本地启动指南

## 前置依赖

- Python 3.10+
- Node.js 18+
- MySQL 8
- Redis
- Elasticsearch 8
- MinIO
- Kafka + Zookeeper

---

## 方式一：Docker 启动基础设施（推荐）

```bash
cd backend

# 只启动基础设施（MySQL、Redis、ES、MinIO、Kafka）
docker-compose up -d mysql redis elasticsearch minio kafka zookeeper

# 等待所有服务健康（约 1-2 分钟）
docker-compose ps
```

### Docker 服务端口映射

| 服务 | 端口 | 说明 |
|------|------|------|
| MySQL | 3308 | 宿主机 3308 → 容器 3306 |
| Redis | 6379 | 默认端口 |
| Elasticsearch | 9200 | 默认端口 |
| MinIO API | 9000 | 对象存储 |
| MinIO Console | 9001 | Web 管理界面 |
| Kafka | 9092 | 消息队列 |

### 创建数据库

```sql
-- 连接 MySQL（端口 3308）
mysql -h 127.0.0.1 -P 3308 -u root -pchangeme_root_pw

-- 创建数据库
CREATE DATABASE docmind_db CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
```

---

## 方式二：本地服务

如果本地已安装这些服务，确保以下配置对应：

| 服务 | 默认连接 |
|------|----------|
| MySQL | `root:root@localhost:3306/docmind_db` |
| Redis | `localhost:6379` |
| Elasticsearch | `http://localhost:9200` |
| MinIO | `localhost:9000` (minioadmin/minioadmin) |
| Kafka | `localhost:9092` |

---

## 启动后端

```bash
cd backend

# 激活虚拟环境
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
| `backend/.env` | 本地开发环境配置 |
| `backend/.env.docker` | Docker 部署配置 |
| `backend/.env.example` | 配置模板（参考） |

### AI 模型配置

当前使用**智谱 AI 全家桶**：

| 模型 | 用途 | 配置项 |
|------|------|--------|
| glm-4-flash | Chat 对话 | `DEEPSEEK_MODEL` |
| embedding-3 | 向量化 | `EMBEDDING_MODEL`，维度 2048 |
| rerank | 重排序 | `RERANK_MODEL` |

三个模型共用同一个 API Key，已配置在 `.env` 中。

---

## 常见问题

### 1. MySQL 连接失败
确认 MySQL 已启动，数据库已创建。Docker 模式下端口是 3308 不是 3306。

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

会自动启动后端、Worker 和前端，打开三个命令行窗口。
