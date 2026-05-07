# DocMind 后端

基于 FastAPI 的企业级 RAG 知识库系统后端，支持文档管理、混合检索与流式智能问答。

## 🚀 功能特性

### 📄 文档管理
- **多格式支持**: PDF、Word、Excel、PPT、TXT、Markdown、CSV
- **大文件上传**: 支持分块上传和断点续传，最大支持100MB文件
- **智能解析**: 自动提取文本内容，支持表格和结构化数据
- **文档分块**: 智能文本分块，支持上下文重叠

### 🔍 知识检索
- **混合搜索**: 结合关键词搜索和向量语义搜索
- **向量嵌入**: 使用OpenAI嵌入模型将文本转换为向量
- **相似度匹配**: 基于余弦相似度的语义匹配
- **权限控制**: 基于组织的知识库访问控制

### 💬 智能问答
- **DeepSeek集成**: 基于DeepSeek大模型的知识问答
- **多轮对话**: 支持上下文保持的多轮对话
- **流式输出**: 支持WebSocket和SSE流式响应
- **TextToSQL**: 自然语言到SQL查询转换

### 🏗️ 系统架构
- **FastAPI**: 高性能异步Web框架
- **MySQL**: 关系型数据存储
- **Redis**: 缓存和会话管理
- **Elasticsearch**: 向量存储和搜索
- **MinIO**: 对象存储
- **Docker**: 容器化部署

## 🛠️ 技术栈

- **后端框架**: FastAPI + Python 3.11
- **数据库**: MySQL 8.0 + SQLAlchemy
- **缓存**: Redis 7.0
- **搜索引擎**: Elasticsearch 8.11
- **对象存储**: MinIO
- **AI模型**: DeepSeek + OpenAI Embeddings
- **消息队列**: Kafka (可选)
- **部署**: Docker + Docker Compose

## 📦 快速开始

### 环境要求
- Python 3.11+
- MySQL 8.0+
- Redis 7.0+
- Elasticsearch 8.11+
- MinIO

### 1. 克隆项目
```bash
git clone <repository-url>
cd paicongming/backend
```

### 2. 安装依赖
```bash
pip install -r requirements.txt
```

### 3. 配置环境变量
```bash
cp .env.example .env
# 编辑 .env 文件，配置数据库、Redis、Elasticsearch等连接信息
```

### 4. 数据库迁移
```bash
alembic upgrade head
```

### 5. 启动服务
```bash
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 6. 管理员账号（登录 401 时必做）
若登录提示「用户名或密码错误」，说明库里没有 `admin` 或密码不对。在**后端目录**执行：
```bash
python reset_admin.py
```
会将 `admin` 用户密码重置为 `123456`（若不存在则创建）。然后使用 **admin / 123456** 登录。

### 7. Docker部署（推荐）
```bash
docker-compose up -d
```

## 🔧 配置说明

### 核心配置
- `DATABASE_URL`: MySQL连接字符串
- `REDIS_HOST`: Redis服务器地址
- `ELASTICSEARCH_HOSTS`: Elasticsearch节点地址
- `MINIO_ENDPOINT`: MinIO服务器地址
- `DEEPSEEK_API_KEY`: DeepSeek API密钥
- `OPENAI_API_KEY`: OpenAI API密钥（用于嵌入）

### 文件上传配置
- `MAX_FILE_SIZE`: 最大文件大小（默认100MB）
- `CHUNK_SIZE`: 分块大小（默认5MB）

### AI配置
- `VECTOR_DIMENSION`: 向量维度（默认768）
- `SIMILARITY_THRESHOLD`: 相似度阈值（默认0.7）
- `TOP_K_RESULTS`: 搜索结果数量（默认10）

## 📚 API文档

启动服务后，访问以下地址查看API文档：
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

### 主要API端点

#### 认证相关
- `POST /api/v1/auth/login` - 用户登录
- `POST /api/v1/auth/register` - 用户注册
- `POST /api/v1/auth/refresh` - 刷新令牌

#### 文件管理
- `POST /api/v1/files/upload` - 文件上传
- `POST /api/v1/files/upload-chunk` - 分块上传
- `GET /api/v1/files/list` - 文件列表
- `DELETE /api/v1/files/{id}` - 删除文件

#### 知识库
- `POST /api/v1/knowledge/search` - 知识搜索
- `GET /api/v1/knowledge/suggestions` - 搜索建议
- `GET /api/v1/knowledge/stats/{org_id}` - 统计信息
- `POST /api/v1/knowledge/build/{doc_id}` - 构建知识库

#### 聊天对话
- `POST /api/v1/chat/chat` - 发送消息
- `POST /api/v1/chat/chat/stream` - 流式对话
- `WebSocket /api/v1/chat/ws/chat` - WebSocket聊天
- `GET /api/v1/chat/sessions` - 会话列表

## 🧪 测试

### 运行测试（需在 backend 目录下执行）
```bash
python -m pytest tests/ -v
```

### 测试用例（132 个）

| 文件 | 覆盖范围 | 用例数 |
|------|----------|--------|
| `test_auth_service.py` | JWT 创建/验证、密码哈希、Token 黑名单、RBAC | 22 |
| `test_masking_service.py` | PII 脱敏（手机/邮箱/身份证/IP）、还原 | 11 |
| `test_circuit_breaker.py` | 熔断器状态机、降级返回值、异步支持 | 10 |
| `test_semantic_cache.py` | 余弦相似度边界情况 | 7 |
| `test_rag_service.py` | RRF 融合、查询意图、上下文压缩、长查询优化 | 7 |
| `test_config.py` | 弱密钥拒绝、连接池、限流配置 | 5 |
| `test_document_parser.py` | TXT/DOCX 解析、元数据 | 5 |
| `test_auth_api.py` | 注册密码校验、缺少字段、无效邮箱 | 7 |
| `test_memory_service.py` | 记忆系统（短期/长期/工作/反思） | 52 |
| `test_health.py` | 健康检查端点 | 2 |

### 测试覆盖率（可选）
```bash
pytest tests/ --cov=app --cov-report=html
```

## 🚀 性能优化

### 数据库优化
- 使用连接池管理数据库连接
- 添加适当的索引提高查询性能
- 使用异步操作避免阻塞

### 缓存策略
- Redis缓存热点数据
- 向量搜索结果缓存
- 用户会话缓存

### 文件处理优化
- 异步文件解析
- 分块处理大文件
- 并发处理多个文档

### 搜索优化
- Elasticsearch集群部署
- 向量索引优化
- 搜索结果的智能缓存

## 🔒 安全考虑

### 认证授权
- JWT令牌认证
- 基于角色的权限控制
- 组织级别的数据隔离

### 数据安全
- 敏感数据加密存储
- API访问限流
- SQL注入防护

### 文件安全
- 文件类型验证
- 文件大小限制
- 恶意文件检测

## 📊 监控和日志

### 日志系统
- 结构化日志记录
- 日志轮转和归档
- 错误日志告警

### 性能监控
- API响应时间监控
- 数据库性能监控
- 系统资源使用监控

### 业务监控
- 用户活跃度统计
- 知识库使用统计
- 搜索质量评估

## 🤝 贡献指南

1. Fork 项目
2. 创建特性分支 (`git checkout -b feature/amazing-feature`)
3. 提交更改 (`git commit -m 'Add some amazing feature'`)
4. 推送到分支 (`git push origin feature/amazing-feature`)
5. 创建 Pull Request

## 📝 许可证

本项目采用 MIT 许可证 - 查看 [LICENSE](LICENSE) 文件了解详情。

## 🆘 支持

如遇到问题，请：
1. 查看文档和常见问题
2. 在 Issues 中搜索类似问题
3. 创建新的 Issue 描述问题

## 📞 联系方式

- 项目维护者: [Your Name]
- 邮箱: [your.email@example.com]
- 项目主页: [项目URL]