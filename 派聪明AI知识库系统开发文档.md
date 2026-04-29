# 派聪明AI知识库系统开发文档

## 项目概述

派聪明（PaiCongMing）是一个企业级AI智能知识库系统，基于RAG（Retrieval-Augmented Generation）架构实现。系统支持多用户、多组织、多文档类型的知识管理，提供智能问答、文档检索、知识图谱等功能。

### 核心特性

- **智能问答**：基于RAG架构，结合知识库内容生成准确回答
- **多模态支持**：支持文本、PDF、Word、Excel、图片等20+种文件格式
- **混合检索**：语义搜索 + 关键词搜索 + 智能重排序
- **实时通信**：WebSocket支持实时对话和消息推送
- **企业级架构**：微服务架构，支持水平扩展
- **权限管理**：基于RBAC的细粒度权限控制
- **性能优化**：多级缓存、异步处理、流式响应

## 技术架构

### 后端技术栈

- **框架**：Python 3.11 + FastAPI 0.104.1
- **数据库**：MySQL 8.0 + SQLAlchemy 2.0
- **缓存**：Redis 7.2
- **搜索引擎**：Elasticsearch 8.10.0
- **消息队列**：Apache Kafka 3.6
- **文件存储**：MinIO + Apache Tika 2.9
- **AI模型**：DeepSeek API + 自研Embedding服务
- **监控**：Prometheus + Grafana

### 前端技术栈

- **框架**：Vue 3.4 + TypeScript 5.3
- **UI库**：Naive UI 2.38
- **构建工具**：Vite 5.0
- **状态管理**：Pinia 2.1
- **路由**：Vue Router 4.2
- **样式**：UnoCSS + SCSS
- **图标**：Iconify

### 系统架构图

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   前端 Vue3     │    │   API Gateway   │    │   负载均衡器     │
│  + TypeScript   │◄──►│   + 限流熔断     │◄──►│   + 健康检查     │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │                       │
         ▼                       ▼                       ▼
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   WebSocket     │    │   FastAPI       │    │   监控告警       │
│   实时通信        │◄──►│   微服务        │◄──►│   Prometheus    │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │
         ▼                       ▼
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   文件处理        │    │   AI服务        │    │   知识检索       │
│   Kafka队列       │◄──►│   DeepSeek      │◄──►│   Elasticsearch  │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │                       │
         ▼                       ▼                       ▼
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   MySQL数据库     │    │   Redis缓存     │    │   MinIO存储     │
│   关系型数据      │◄──►│   会话+缓存      │◄──►│   对象存储       │
└─────────────────┘    └─────────────────┘    └─────────────────┘
```

## 核心模块设计

### 1. 用户管理模块

#### 功能特性
- 用户注册、登录、找回密码
- 多组织支持，用户可加入多个组织
- 基于RBAC的权限控制（角色：超级管理员、组织管理员、普通用户、访客）
- JWT令牌认证，支持刷新令牌
- 用户行为审计日志

#### 数据库设计

```sql
-- 用户表
CREATE TABLE users (
    id INT PRIMARY KEY AUTO_INCREMENT,
    username VARCHAR(50) UNIQUE NOT NULL,
    email VARCHAR(100) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    full_name VARCHAR(100),
    avatar_url VARCHAR(500),
    is_active BOOLEAN DEFAULT TRUE,
    is_superuser BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- 组织表
CREATE TABLE organizations (
    id INT PRIMARY KEY AUTO_INCREMENT,
    name VARCHAR(100) NOT NULL,
    description TEXT,
    max_users INT DEFAULT 100,
    max_storage_gb INT DEFAULT 10,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
);

-- 用户组织关联表
CREATE TABLE user_organizations (
    id INT PRIMARY KEY AUTO_INCREMENT,
    user_id INT NOT NULL,
    organization_id INT NOT NULL,
    role VARCHAR(20) NOT NULL, -- owner, admin, member, guest
    joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (organization_id) REFERENCES organizations(id),
    UNIQUE KEY unique_user_org (user_id, organization_id)
);
```

#### 关键代码实现

```python
# 用户认证服务
class AuthService:
    def __init__(self):
        self.pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
        self.oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/auth/login")
    
    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """验证密码"""
        return self.pwd_context.verify(plain_password, hashed_password)
    
    def get_password_hash(self, password: str) -> str:
        """生成密码哈希"""
        return self.pwd_context.hash(password)
    
    def create_access_token(self, data: dict, expires_delta: Optional[timedelta] = None) -> str:
        """创建访问令牌"""
        to_encode = data.copy()
        if expires_delta:
            expire = datetime.utcnow() + expires_delta
        else:
            expire = datetime.utcnow() + timedelta(minutes=15)
        to_encode.update({"exp": expire})
        encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
        return encoded_jwt
```

### 2. 文件处理模块

#### 功能特性
- 支持20+种文件格式：txt, pdf, docx, xlsx, pptx, md, html, json, csv, jpg, png等
- 大文件分片上传（5MB分片），支持断点续传
- 异步文档处理，使用Kafka消息队列
- 自动文本提取和OCR识别
- 文档向量化处理，生成768维向量
- 智能文档分块，支持重叠分块策略

#### 文件处理流程

```
文件上传 → 格式验证 → 分片存储 → 消息队列 → 异步处理 → 文本提取 → 向量化 → 索引存储
```

#### 关键代码实现

```python
# 文档处理器
class DocumentProcessor:
    def __init__(self):
        self.supported_formats = {
            'txt': self.process_text_file,
            'pdf': self.process_pdf_file,
            'docx': self.process_word_file,
            'xlsx': self.process_excel_file,
            'jpg': self.process_image_file,
            'png': self.process_image_file,
            # ... 20+ formats
        }
    
    async def process_file(self, file_path: str, file_type: str) -> List[DocumentChunk]:
        """处理文档文件"""
        processor = self.supported_formats.get(file_type)
        if not processor:
            raise ValueError(f"不支持的文件格式: {file_type}")
        
        # 提取文本内容
        content = await processor(file_path)
        
        # 文档分块
        chunks = self.chunk_text(content)
        
        # 生成向量
        for chunk in chunks:
            chunk.embedding = await self.generate_embedding(chunk.content)
        
        return chunks
    
    def chunk_text(self, text: str, chunk_size: int = 1000, overlap: int = 200) -> List[str]:
        """智能文档分块"""
        chunks = []
        start = 0
        while start < len(text):
            end = start + chunk_size
            if end >= len(text):
                chunks.append(text[start:])
                break
            
            # 在句子边界处切分
            while end > start and text[end] not in '.。!！?？':
                end -= 1
            
            if end == start:  # 没找到句子边界
                end = start + chunk_size
            
            chunks.append(text[start:end])
            start = end - overlap
        
        return chunks
```

### 3. 知识检索模块

#### 功能特性
- 混合检索算法：语义搜索 + BM25关键词搜索 + 重排序
- 多维度过滤：组织权限、文档类型、时间范围、标签
- 智能问答：基于RAG架构，结合检索和生成
- 知识图谱：构建实体关系图，支持图查询
- 实时索引更新：文档处理后立即更新搜索索引

#### 检索算法

```python
# 混合检索服务
class HybridSearchService:
    def __init__(self):
        self.elasticsearch_client = Elasticsearch(ELASTICSEARCH_URL)
        self.embedding_service = EmbeddingService()
    
    async def hybrid_search(
        self,
        query: str,
        organization_id: int,
        top_k: int = 10,
        filters: Optional[Dict] = None
    ) -> List[SearchResult]:
        """混合检索"""
        # 1. 语义搜索（向量相似度）
        semantic_results = await self.semantic_search(query, organization_id, top_k)
        
        # 2. 关键词搜索（BM25）
        keyword_results = await self.keyword_search(query, organization_id, top_k)
        
        # 3. 结果融合与重排序
        combined_results = self.rerank_results(semantic_results, keyword_results, query)
        
        return combined_results[:top_k]
    
    async def semantic_search(
        self,
        query: str,
        organization_id: int,
        top_k: int
    ) -> List[SearchResult]:
        """语义搜索"""
        # 生成查询向量
        query_embedding = await self.embedding_service.generate_embedding(query)
        
        # Elasticsearch向量搜索
        search_body = {
            "size": top_k,
            "query": {
                "script_score": {
                    "query": {
                        "bool": {
                            "filter": [
                                {"term": {"organization_id": organization_id}}
                            ]
                        }
                    },
                    "script": {
                        "source": "cosineSimilarity(params.query_vector, 'embedding') + 1.0",
                        "params": {"query_vector": query_embedding}
                    }
                }
            }
        }
        
        response = self.elasticsearch_client.search(
            index="knowledge_chunks",
            body=search_body
        )
        
        return self.parse_search_results(response)
```

### 4. 对话交互模块

#### 功能特性
- WebSocket实时通信，支持流式响应
- 多轮对话上下文管理
- 对话历史持久化
- 知识源引用和可追溯性
- 对话分享和协作
- 智能提示和自动补全

#### 对话流程

```
用户提问 → 权限验证 → 知识检索 → 提示构建 → AI生成 → 流式响应 → 结果存储
```

#### 关键代码实现

```python
# 对话服务
class ChatService:
    def __init__(self):
        self.ai_service = AIService()
        self.search_service = HybridSearchService()
        self.conversation_manager = ConversationManager()
    
    async def process_message(
        self,
        conversation_id: int,
        message: str,
        user_id: int,
        organization_id: int
    ) -> AsyncIterator[str]:
        """处理用户消息"""
        # 1. 检索相关知识
        search_results = await self.search_service.hybrid_search(
            query=message,
            organization_id=organization_id,
            top_k=5
        )
        
        # 2. 构建提示词
        context = self.build_context(search_results)
        prompt = self.build_prompt(message, context)
        
        # 3. 生成AI回复
        full_response = ""
        async for chunk in self.ai_service.generate_streaming_response(prompt):
            full_response += chunk
            yield chunk
        
        # 4. 保存消息记录
        await self.conversation_manager.add_message(
            conversation_id=conversation_id,
            user_message=message,
            assistant_message=full_response,
            knowledge_sources=search_results
        )
    
    def build_prompt(self, user_message: str, context: str) -> List[Dict[str, str]]:
        """构建提示词"""
        system_prompt = """你是一个专业的AI助手，基于提供的知识库内容回答用户问题。
        
规则：
1. 只使用提供的知识库内容回答问题
2. 如果知识库中没有相关信息，请明确说明
3. 回答要准确、简洁、专业
4. 在回答末尾引用相关的知识源

知识库内容：
{context}
"""
        
        return [
            {"role": "system", "content": system_prompt.format(context=context)},
            {"role": "user", "content": user_message}
        ]
```

### 5. 系统监控模块

#### 功能特性
- 实时系统指标监控（CPU、内存、磁盘、网络）
- 应用性能监控（请求响应时间、错误率、吞吐量）
- 业务指标监控（用户数、文档数、对话数）
- 告警机制（邮件、短信、Webhook）
- 健康检查和故障自愈
- 日志收集和分析

#### 监控指标

```python
# 系统监控服务
class MonitoringService:
    def __init__(self):
        self.metrics = {
            'system_cpu_percent': Gauge('system_cpu_percent', 'CPU使用率'),
            'system_memory_percent': Gauge('system_memory_percent', '内存使用率'),
            'request_duration_seconds': Histogram('request_duration_seconds', '请求响应时间'),
            'request_total': Counter('request_total', '请求总数', ['method', 'endpoint', 'status']),
            'active_users': Gauge('active_users', '活跃用户数量'),
            'document_count': Gauge('document_count', '文档总数'),
            'conversation_count': Gauge('conversation_count', '对话总数')
        }
    
    async def collect_system_metrics(self) -> SystemMetrics:
        """收集系统指标"""
        # CPU使用率
        cpu_percent = psutil.cpu_percent(interval=1)
        
        # 内存使用率
        memory = psutil.virtual_memory()
        memory_percent = memory.percent
        
        # 磁盘使用率
        disk = psutil.disk_usage('/')
        disk_percent = (disk.used / disk.total) * 100
        
        # 网络IO
        network = psutil.net_io_counters()
        
        return SystemMetrics(
            cpu_percent=cpu_percent,
            memory_percent=memory_percent,
            disk_percent=disk_percent,
            network_bytes_sent=network.bytes_sent,
            network_bytes_recv=network.bytes_recv
        )
    
    async def check_health(self) -> HealthStatus:
        """健康检查"""
        checks = {}
        
        # 数据库连接检查
        try:
            db_status = await self.check_database_connection()
            checks['database'] = db_status
        except Exception as e:
            checks['database'] = HealthCheckResult(status="unhealthy", error=str(e))
        
        # Redis连接检查
        try:
            redis_status = await self.check_redis_connection()
            checks['redis'] = redis_status
        except Exception as e:
            checks['redis'] = HealthCheckResult(status="unhealthy", error=str(e))
        
        # Elasticsearch检查
        try:
            es_status = await self.check_elasticsearch_connection()
            checks['elasticsearch'] = es_status
        except Exception as e:
            checks['elasticsearch'] = HealthCheckResult(status="unhealthy", error=str(e))
        
        # 整体健康状态
        overall_status = "healthy" if all(
            check.status == "healthy" for check in checks.values()
        ) else "unhealthy"
        
        return HealthStatus(status=overall_status, checks=checks)
```

## 部署指南

### 环境要求

- **操作系统**：Ubuntu 20.04+ / CentOS 8+ / Windows 10+
- **Python**：3.11+
- **Node.js**：18.0+
- **MySQL**：8.0+
- **Redis**：7.0+
- **Elasticsearch**：8.10.0+
- **Kafka**：3.6+
- **MinIO**：最新版本

### 快速部署

#### 1. 克隆项目
```bash
git clone https://github.com/your-org/paicongming.git
cd paicongming
```

#### 2. 启动基础设施服务
```bash
# 使用Docker Compose启动依赖服务
docker-compose up -d mysql redis elasticsearch kafka minio
```

#### 3. 配置环境变量
```bash
# 后端配置
cp paicongming_backend/.env.example paicongming_backend/.env
# 编辑配置文件，设置数据库、Redis等连接信息

# 前端配置
cp paicongming_frontend/.env.example paicongming_frontend/.env
# 编辑配置文件，设置API地址等
```

#### 4. 安装依赖
```bash
# 安装后端依赖
cd paicongming_backend
pip install -r requirements.txt

# 安装前端依赖
cd ../paicongming_frontend
npm install
```

#### 5. 初始化数据库
```bash
cd paicongming_backend
python -m alembic upgrade head
```

#### 6. 启动系统
```bash
# 使用系统管理脚本
./start_system.sh

# 或者手动启动
cd paicongming_backend && python main.py &
cd paicongming_frontend && npm run dev &
```

### 生产部署

#### Docker部署
```bash
# 构建镜像
docker build -t paicongming-backend ./paicongming_backend
docker build -t paicongming-frontend ./paicongming_frontend

# 启动服务
docker-compose -f docker-compose.prod.yml up -d
```

#### Kubernetes部署
```bash
# 应用配置
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/configmap.yaml
kubectl apply -f k8s/secret.yaml

# 部署服务
kubectl apply -f k8s/backend-deployment.yaml
kubectl apply -f k8s/frontend-deployment.yaml
kubectl apply -f k8s/ingress.yaml
```

## API文档

### 认证相关API

#### 用户注册
```http
POST /api/auth/register
Content-Type: application/json

{
  "username": "testuser",
  "email": "test@example.com",
  "password": "password123",
  "full_name": "测试用户"
}
```

**响应示例**:
```json
{
  "success": true,
  "data": {
    "id": 1,
    "username": "testuser",
    "email": "test@example.com",
    "full_name": "测试用户",
    "created_at": "2024-01-01T00:00:00Z"
  }
}
```

#### 用户登录
```http
POST /api/auth/login
Content-Type: application/json

{
  "username": "testuser",
  "password": "password123"
}
```

**响应示例**:
```json
{
  "success": true,
  "data": {
    "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "token_type": "bearer",
    "expires_in": 1800
  }
}
```

### 文件相关API

#### 初始化大文件上传
```http
POST /api/files/upload/init
Content-Type: multipart/form-data

filename: document.pdf
file_size: 10485760
file_type: pdf
organization_id: 1
```

**响应示例**:
```json
{
  "success": true,
  "data": {
    "upload_id": "550e8400-e29b-41d4-a716-446655440000",
    "chunk_size": 5242880,
    "total_chunks": 2,
    "upload_url": "/api/files/upload/chunk"
  }
}
```

#### 上传文件分片
```http
POST /api/files/upload/chunk
Content-Type: multipart/form-data

upload_id: 550e8400-e29b-41d4-a716-446655440000
chunk_index: 0
total_chunks: 2
file: [二进制数据]
```

**响应示例**:
```json
{
  "success": true,
  "data": {
    "chunk_index": 0,
    "uploaded": true,
    "progress": 50
  }
}
```

### 对话相关API

#### 创建对话
```http
POST /api/conversations
Content-Type: application/json
Authorization: Bearer {token}

{
  "title": "新产品功能讨论",
  "organization_id": 1
}
```

**响应示例**:
```json
{
  "success": true,
  "data": {
    "id": 1,
    "title": "新产品功能讨论",
    "organization_id": 1,
    "created_at": "2024-01-01T00:00:00Z"
  }
}
```

#### WebSocket连接
```javascript
// WebSocket连接示例
const ws = new WebSocket('ws://localhost:8000/api/ws/chat?token={access_token}&conversation_id=1');

ws.onopen = function(event) {
    console.log('WebSocket连接已建立');
};

ws.onmessage = function(event) {
    const data = JSON.parse(event.data);
    if (data.type === 'message') {
        console.log('收到消息:', data.content);
    }
};

// 发送消息
ws.send(JSON.stringify({
    type: 'message',
    content: '你好，请介绍一下产品功能'
}));
```

## 性能优化

### 1. 缓存策略

#### 多级缓存架构
```
浏览器缓存 ► CDN缓存 ► Nginx缓存 ► Redis缓存 ► 应用内存缓存 ► 数据库
```

#### 缓存实现
```python
# 缓存装饰器
def cache_result(expire_time: int = 300):
    def decorator(func):
        async def wrapper(*args, **kwargs):
            # 生成缓存键
            cache_key = f"cache:{func.__name__}:{hash(str(args) + str(kwargs))}"
            
            # 尝试从缓存获取
            cached_result = await redis.get(cache_key)
            if cached_result:
                return json.loads(cached_result)
            
            # 执行函数
            result = await func(*args, **kwargs)
            
            # 存入缓存
            await redis.setex(cache_key, expire_time, json.dumps(result))
            
            return result
        return wrapper
    return decorator

# 使用示例
@cache_result(expire_time=600)
async def get_user_profile(user_id: int) -> UserProfile:
    return await db.query(User).filter(User.id == user_id).first()
```

### 2. 数据库优化

#### 索引优化
```sql
-- 用户表索引
CREATE INDEX idx_users_username ON users(username);
CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_users_created_at ON users(created_at);

-- 文件表索引
CREATE INDEX idx_files_organization_id ON files(organization_id);
CREATE INDEX idx_files_created_at ON files(created_at);
CREATE INDEX idx_files_file_type ON files(file_type);

-- 对话表索引
CREATE INDEX idx_conversations_user_id ON conversations(user_id);
CREATE INDEX idx_conversations_organization_id ON conversations(organization_id);
CREATE INDEX idx_conversations_updated_at ON conversations(updated_at);
```

#### 查询优化
```python
# 使用连接池
engine = create_async_engine(
    DATABASE_URL,
    pool_size=20,
    max_overflow=40,
    pool_pre_ping=True,
    pool_recycle=3600
)

# 批量操作
async def batch_insert_chunks(chunks: List[DocumentChunk]):
    async with db.begin():
        db.add_all(chunks)
        await db.commit()

# 延迟加载优化
class File(Base):
    __tablename__ = "files"
    
    id = Column(Integer, primary_key=True)
    organization_id = Column(Integer, ForeignKey("organizations.id"))
    
    # 使用selectin加载避免N+1问题
    chunks = relationship("FileChunk", lazy="selectin")
```

### 3. 异步处理

#### 异步文件处理
```python
# Kafka生产者
class KafkaProducer:
    def __init__(self):
        self.producer = AIOKafkaProducer(
            bootstrap_servers=KAFKA_SERVERS,
            value_serializer=lambda v: json.dumps(v).encode()
        )
    
    async def send_file_processing_task(self, file_id: int, organization_id: int):
        message = {
            "file_id": file_id,
            "organization_id": organization_id,
            "timestamp": datetime.utcnow().isoformat()
        }
        await self.producer.send("file-processing", message)

# Kafka消费者
class FileProcessingConsumer:
    def __init__(self):
        self.consumer = AIOKafkaConsumer(
            "file-processing",
            bootstrap_servers=KAFKA_SERVERS,
            value_deserializer=lambda v: json.loads(v.decode())
        )
        self.document_processor = DocumentProcessor()
    
    async def consume(self):
        async for message in self.consumer:
            try:
                file_data = message.value
                await self.process_file(file_data["file_id"], file_data["organization_id"])
            except Exception as e:
                logger.error(f"文件处理失败: {e}")
                # 发送到死信队列
                await self.send_to_dlq(message.value)
```

### 4. 前端优化

#### 组件懒加载
```javascript
// 路由懒加载
const routes = [
  {
    path: '/dashboard',
    component: () => import('@/views/Dashboard.vue')
  },
  {
    path: '/chat',
    component: () => import('@/views/Chat.vue')
  },
  {
    path: '/files',
    component: () => import('@/views/Files.vue')
  }
];

// 组件懒加载
const FileUpload = defineAsyncComponent(() => 
  import('@/components/FileUpload.vue')
);
```

#### 虚拟滚动
```vue
<template>
  <n-virtual-list
    :item-size="60"
    :items="messages"
    :height="600"
  >
    <template #default="{ item }">
      <MessageItem :message="item" />
    </template>
  </n-virtual-list>
</template>
```

#### 图片懒加载
```vue
<template>
  <img
    v-lazy="image.src"
    :alt="image.alt"
    class="lazy-image"
  />
</template>

<script>
// 自定义懒加载指令
const lazyDirective = {
  mounted(el, binding) {
    const observer = new IntersectionObserver((entries) => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          el.src = binding.value;
          observer.unobserve(el);
        }
      });
    });
    observer.observe(el);
  }
};
</script>
```

## 安全设计

### 1. 认证安全

#### JWT令牌安全
```python
# 使用强密钥和算法
SECRET_KEY = os.environ.get("SECRET_KEY")  # 256位随机密钥
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30
REFRESH_TOKEN_EXPIRE_DAYS = 7

# 令牌验证
def verify_token(token: str) -> TokenData:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
        return TokenData(username=username)
    except JWTError:
        raise credentials_exception

# 刷新令牌
async def refresh_token(refresh_token: str) -> TokenPair:
    token_data = verify_token(refresh_token)
    user = await get_user(username=token_data.username)
    if not user:
        raise credentials_exception
    
    # 创建新的访问令牌
    access_token = create_access_token(data={"sub": user.username})
    return TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
        token_type="bearer"
    )
```

#### 密码安全
```python
# 密码复杂度验证
def validate_password(password: str) -> bool:
    if len(password) < 8:
        return False
    if not re.search(r"[A-Z]", password):  # 大写字母
        return False
    if not re.search(r"[a-z]", password):  # 小写字母
        return False
    if not re.search(r"\d", password):  # 数字
        return False
    if not re.search(r"[!@#$%^&*(),.?\":{}|<>]", password):  # 特殊字符
        return False
    return True

# 密码历史记录
class PasswordHistory(Base):
    __tablename__ = "password_history"
    
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    password_hash = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)

# 防止密码重用
async def check_password_history(user_id: int, new_password: str) -> bool:
    histories = await db.query(PasswordHistory).filter(
        PasswordHistory.user_id == user_id
    ).order_by(PasswordHistory.created_at.desc()).limit(5).all()
    
    for history in histories:
        if verify_password(new_password, history.password_hash):
            return False  # 密码最近使用过
    return True
```

### 2. 数据安全

#### 数据加密
```python
# 敏感数据加密
from cryptography.fernet import Fernet

class EncryptionService:
    def __init__(self):
        self.key = os.environ.get("ENCRYPTION_KEY")
        self.cipher = Fernet(self.key.encode())
    
    def encrypt(self, data: str) -> str:
        return self.cipher.encrypt(data.encode()).decode()
    
    def decrypt(self, encrypted_data: str) -> str:
        return self.cipher.decrypt(encrypted_data.encode()).decode()

# 使用示例
encryption_service = EncryptionService()
encrypted_email = encryption_service.encrypt("user@example.com")
decrypted_email = encryption_service.decrypt(encrypted_email)
```

#### 数据脱敏
```python
# 敏感信息脱敏
def mask_sensitive_data(data: str, data_type: str) -> str:
    if data_type == "email":
        # 邮箱脱敏：t***r@example.com
        parts = data.split("@")
        if len(parts) == 2:
            username = parts[0]
            if len(username) > 2:
                masked = username[0] + "*" * (len(username) - 2) + username[-1]
            else:
                masked = "*" * len(username)
            return f"{masked}@{parts[1]}"
    
    elif data_type == "phone":
        # 手机号脱敏：138****8888
        if len(data) == 11:
            return data[:3] + "****" + data[7:]
    
    elif data_type == "id_card":
        # 身份证号脱敏：1101****1234
        if len(data) == 18:
            return data[:4] + "****" + data[-4:]
    
    return data
```

### 3. API安全

#### 限流防护
```python
# 基于Redis的限流
class RateLimiter:
    def __init__(self):
        self.redis = redis_client
    
    async def is_allowed(
        self,
        key: str,
        limit: int = 100,
        window: int = 60
    ) -> bool:
        current = await self.redis.incr(key)
        if current == 1:
            await self.redis.expire(key, window)
        return current <= limit

# 限流装饰器
def rate_limit(limit: int = 100, window: int = 60):
    def decorator(func):
        async def wrapper(*args, **kwargs):
            request = kwargs.get("request")
            if request:
                client_ip = request.client.host
                key = f"rate_limit:{func.__name__}:{client_ip}"
                
                if not await rate_limiter.is_allowed(key, limit, window):
                    raise HTTPException(
                        status_code=429,
                        detail="请求过于频繁，请稍后再试"
                    )
            
            return await func(*args, **kwargs)
        return wrapper
    return decorator

# 使用示例
@rate_limit(limit=10, window=60)  # 每分钟最多10次请求
async def send_verification_code(request: Request):
    # 发送验证码逻辑
    pass
```

#### SQL注入防护
```python
# 使用ORM防止SQL注入
class UserService:
    async def get_user_by_username(self, username: str) -> Optional[User]:
        # 安全的查询方式
        result = await db.execute(
            select(User).where(User.username == username)
        )
        return result.scalar_one_or_none()
    
    async def search_users(self, search_term: str) -> List[User]:
        # 使用参数化查询
        result = await db.execute(
            select(User).where(User.username.like(f"%{search_term}%"))
        )
        return result.scalars().all()

# 输入验证
class UserValidator:
    def validate_username(self, username: str) -> bool:
        # 只允许字母、数字、下划线
        return re.match(r"^[a-zA-Z0-9_]{3,20}$", username) is not None
    
    def validate_email(self, email: str) -> bool:
        # 邮箱格式验证
        pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
        return re.match(pattern, email) is not None
```

### 4. 文件上传安全

#### 文件类型验证
```python
# 文件类型检查
class FileValidator:
    ALLOWED_EXTENSIONS = {
        'txt', 'pdf', 'docx', 'xlsx', 'pptx', 'md', 'html', 'json', 'csv',
        'jpg', 'jpeg', 'png', 'gif', 'bmp', 'svg'
    }
    
    MAX_FILE_SIZE = 100 * 1024 * 1024  # 100MB
    
    def validate_file(self, filename: str, file_size: int) -> bool:
        # 检查文件扩展名
        ext = filename.rsplit('.', 1)[-1].lower()
        if ext not in self.ALLOWED_EXTENSIONS:
            return False
        
        # 检查文件大小
        if file_size > self.MAX_FILE_SIZE:
            return False
        
        return True
    
    def check_file_content(self, file_path: str) -> bool:
        # 检查文件内容是否为恶意代码
        with open(file_path, 'rb') as f:
            content = f.read(1024)  # 读取前1KB
            
            # 检查PHP代码
            if b'<?php' in content:
                return False
            
            # 检查JavaScript代码
            if b'<script' in content:
                return False
            
            # 检查可执行文件头
            if content.startswith(b'MZ'):  # PE文件
                return False
        
        return True
```

#### 文件存储安全
```python
# 安全的文件存储
class FileStorageService:
    def __init__(self):
        self.storage_path = "/app/uploads"
        self.encryption_service = EncryptionService()
    
    async def save_file(self, file: UploadFile, organization_id: int) -> str:
        # 生成安全的文件名
        file_extension = file.filename.rsplit('.', 1)[-1]
        safe_filename = f"{uuid.uuid4()}.{file_extension}"
        
        # 创建组织目录
        org_path = os.path.join(self.storage_path, str(organization_id))
        os.makedirs(org_path, exist_ok=True)
        
        # 保存文件
        file_path = os.path.join(org_path, safe_filename)
        
        with open(file_path, "wb") as buffer:
            content = await file.read()
            
            # 可选：加密存储
            encrypted_content = self.encryption_service.encrypt(content)
            buffer.write(encrypted_content.encode())
        
        return safe_filename
    
    async def get_file(self, filename: str, organization_id: int) -> bytes:
        file_path = os.path.join(self.storage_path, str(organization_id), filename)
        
        if not os.path.exists(file_path):
            raise FileNotFoundError("文件不存在")
        
        with open(file_path, "rb") as f:
            encrypted_content = f.read()
            return self.encryption_service.decrypt(encrypted_content.decode())
```

## 测试策略

### 1. 单元测试

#### 后端单元测试
```python
# 用户服务测试
class TestUserService:
    @pytest.fixture
    def user_service(self):
        return UserService()
    
    @pytest.fixture
    def test_user(self):
        return User(
            username="testuser",
            email="test@example.com",
            password_hash="hashed_password"
        )
    
    @pytest.mark.asyncio
    async def test_create_user(self, user_service, test_user):
        # 测试用户创建
        user = await user_service.create_user(test_user)
        assert user.username == "testuser"
        assert user.email == "test@example.com"
    
    @pytest.mark.asyncio
    async def test_authenticate_user(self, user_service, test_user):
        # 测试用户认证
        authenticated_user = await user_service.authenticate_user(
            "testuser", "password123"
        )
        assert authenticated_user is not None
        assert authenticated_user.username == "testuser"

# 文件处理测试
class TestDocumentProcessor:
    @pytest.fixture
    def processor(self):
        return DocumentProcessor()
    
    @pytest.mark.asyncio
    async def test_process_text_file(self, processor):
        # 测试文本文件处理
        test_content = "这是一段测试文本内容。"
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
            f.write(test_content)
            temp_path = f.name
        
        try:
            chunks = await processor.process_file(temp_path, 'txt')
            assert len(chunks) > 0
            assert test_content in chunks[0].content
        finally:
            os.unlink(temp_path)
```

#### 前端单元测试
```javascript
// Vue组件测试
describe('UserLogin.vue', () => {
  let wrapper;
  
  beforeEach(() => {
    wrapper = mount(UserLogin, {
      global: {
        plugins: [createTestingPinia()]
      }
    });
  });
  
  it('renders login form', () => {
    expect(wrapper.find('form')).toBeTruthy();
    expect(wrapper.find('input[type="text"]')).toBeTruthy();
    expect(wrapper.find('input[type="password"]')).toBeTruthy();
  });
  
  it('validates required fields', async () => {
    const form = wrapper.find('form');
    await form.trigger('submit');
    
    expect(wrapper.text()).toContain('用户名不能为空');
    expect(wrapper.text()).toContain('密码不能为空');
  });
  
  it('submits form with valid data', async () => {
    const usernameInput = wrapper.find('input[type="text"]');
    const passwordInput = wrapper.find('input[type="password"]');
    
    await usernameInput.setValue('testuser');
    await passwordInput.setValue('password123');
    
    // 模拟API调用
    const mockPost = vi.spyOn(axios, 'post');
    mockPost.mockResolvedValue({
      data: { success: true, data: { token: 'test-token' } }
    });
    
    await wrapper.find('form').trigger('submit');
    
    expect(mockPost).toHaveBeenCalledWith('/api/auth/login', {
      username: 'testuser',
      password: 'password123'
    });
  });
});
```

### 2. 集成测试

#### API集成测试
```python
# FastAPI集成测试
class TestAPIIntegration:
    @pytest.fixture
    async def client(self):
        async with AsyncClient(app=app, base_url="http://test") as ac:
            yield ac
    
    @pytest.fixture
    async def auth_token(self, client):
        # 创建测试用户并获取令牌
        response = await client.post("/api/auth/register", json={
            "username": "testuser",
            "email": "test@example.com",
            "password": "password123"
        })
        
        login_response = await client.post("/api/auth/login", json={
            "username": "testuser",
            "password": "password123"
        })
        
        return login_response.json()["data"]["access_token"]
    
    @pytest.mark.asyncio
    async def test_file_upload_flow(self, client, auth_token):
        # 测试完整的文件上传流程
        headers = {"Authorization": f"Bearer {auth_token}"}
        
        # 1. 初始化上传
        init_response = await client.post(
            "/api/files/upload/init",
            data={
                "filename": "test.pdf",
                "file_size": 1024,
                "file_type": "pdf",
                "organization_id": 1
            },
            headers=headers
        )
        
        assert init_response.status_code == 200
        upload_data = init_response.json()["data"]
        upload_id = upload_data["upload_id"]
        
        # 2. 上传文件
        upload_response = await client.post(
            "/api/files/upload/chunk",
            data={
                "upload_id": upload_id,
                "chunk_index": 0,
                "total_chunks": 1
            },
            files={"file": ("test.pdf", b"test content", "application/pdf")},
            headers=headers
        )
        
        assert upload_response.status_code == 200
```

### 3. 性能测试

#### 负载测试脚本
```python
# 使用locust进行性能测试
from locust import HttpUser, task, between

class WebsiteUser(HttpUser):
    wait_time = between(1, 3)
    
    def on_start(self):
        # 用户登录
        response = self.client.post("/api/auth/login", json={
            "username": "testuser",
            "password": "password123"
        })
        self.token = response.json()["data"]["access_token"]
    
    @task(3)
    def search_knowledge(self):
        # 知识搜索测试
        self.client.get(
            "/api/search",
            params={"q": "产品功能", "organization_id": 1},
            headers={"Authorization": f"Bearer {self.token}"}
        )
    
    @task(2)
    def get_conversations(self):
        # 获取对话列表
        self.client.get(
            "/api/conversations",
            headers={"Authorization": f"Bearer {self.token}"}
        )
    
    @task(1)
    def upload_file(self):
        # 文件上传测试
        self.client.post(
            "/api/files/upload",
            files={"file": ("test.pdf", b"test content", "application/pdf")},
            headers={"Authorization": f"Bearer {self.token}"}
        )

# 运行测试
# locust -f performance_test.py --host=http://localhost:8000
```

## 监控与运维

### 1. 系统监控

#### 监控指标
```python
# 自定义监控指标
from prometheus_client import Counter, Histogram, Gauge

# 业务指标
user_registrations = Counter('user_registrations_total', '用户注册总数')
file_uploads = Counter('file_uploads_total', '文件上传总数', ['file_type'])
search_queries = Counter('search_queries_total', '搜索查询总数')

# 性能指标
request_duration = Histogram('request_duration_seconds', '请求响应时间')
database_queries = Histogram('database_query_duration_seconds', '数据库查询时间')

# 系统指标
active_users = Gauge('active_users_current', '当前活跃用户数量')
system_load = Gauge('system_load_average', '系统负载')
```

#### 健康检查
```python
# 健康检查端点
@app.get("/health")
async def health_check():
    health_status = {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "services": {}
    }
    
    # 检查数据库连接
    try:
        await db.execute("SELECT 1")
        health_status["services"]["database"] = "healthy"
    except Exception:
        health_status["services"]["database"] = "unhealthy"
        health_status["status"] = "unhealthy"
    
    # 检查Redis连接
    try:
        await redis.ping()
        health_status["services"]["redis"] = "healthy"
    except Exception:
        health_status["services"]["redis"] = "unhealthy"
        health_status["status"] = "unhealthy"
    
    # 检查Elasticsearch
    try:
        await elasticsearch.ping()
        health_status["services"]["elasticsearch"] = "healthy"
    except Exception:
        health_status["services"]["elasticsearch"] = "unhealthy"
        health_status["status"] = "unhealthy"
    
    return health_status
```

### 2. 日志管理

#### 结构化日志
```python
# 日志配置
import structlog
import logging.config

logging.config.dictConfig({
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {
            "()": structlog.stdlib.ProcessorFormatter,
            "processor": structlog.dev.ConsoleRenderer(),
        },
        "plain": {
            "()": structlog.stdlib.ProcessorFormatter,
            "processor": structlog.dev.ConsoleRenderer(colors=False),
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
        },
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": "logs/app.log",
            "maxBytes": 10485760,  # 10MB
            "backupCount": 5,
            "formatter": "json",
        },
    },
    "loggers": {
        "": {
            "handlers": ["console", "file"],
            "level": "INFO",
        },
    },
})

# 使用结构化日志
logger = structlog.get_logger()

# 记录业务事件
logger.info(
    "用户注册成功",
    user_id=user.id,
    username=user.username,
    email=user.email,
    ip_address=request.client.host,
    user_agent=request.headers.get("user-agent"),
)

# 记录性能指标
logger.info(
    "API请求完成",
    method=request.method,
    path=request.url.path,
    status_code=response.status_code,
    duration=duration,
    user_id=getattr(request.state, "user_id", None),
)
```

### 3. 备份策略

#### 数据备份
```python
# 自动备份脚本
import asyncio
import aiomysql
import aiofiles
from datetime import datetime

class BackupService:
    def __init__(self):
        self.backup_dir = "/app/backups"
        self.retention_days = 7
    
    async def backup_database(self):
        """备份数据库"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = f"{self.backup_dir}/database_backup_{timestamp}.sql"
        
        # 使用mysqldump备份
        command = f"""
        mysqldump -h{DB_HOST} -u{DB_USER} -p{DB_PASSWORD} \\
        --single-transaction \\
        --routines \\
        --triggers \\
        {DB_NAME} > {backup_file}
        """
        
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        
        stdout, stderr = await process.communicate()
        
        if process.returncode == 0:
            logger.info(f"数据库备份成功: {backup_file}")
            
            # 压缩备份文件
            compressed_file = f"{backup_file}.gz"
            await self.compress_file(backup_file, compressed_file)
            
            # 上传到对象存储
            await self.upload_to_s3(compressed_file)
            
            # 清理本地备份文件
            os.remove(backup_file)
            os.remove(compressed_file)
        else:
            logger.error(f"数据库备份失败: {stderr.decode()}")
            raise Exception("数据库备份失败")
    
    async def backup_files(self):
        """备份文件"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = f"{self.backup_dir}/files_backup_{timestamp}.tar.gz"
        
        # 打包文件目录
        command = f"tar -czf {backup_file} /app/uploads"
        
        process = await asyncio.create_subprocess_shell(command)
        await process.communicate()
        
        if process.returncode == 0:
            logger.info(f"文件备份成功: {backup_file}")
            
            # 上传到对象存储
            await self.upload_to_s3(backup_file)
            
            # 清理本地备份文件
            os.remove(backup_file)
        else:
            logger.error("文件备份失败")
            raise Exception("文件备份失败")
    
    async def cleanup_old_backups(self):
        """清理旧备份"""
        # 清理本地备份
        for filename in os.listdir(self.backup_dir):
            file_path = os.path.join(self.backup_dir, filename)
            if os.path.isfile(file_path):
                file_age = (datetime.now() - datetime.fromtimestamp(
                    os.path.getctime(file_path)
                )).days
                
                if file_age > self.retention_days:
                    os.remove(file_path)
                    logger.info(f"删除旧备份: {file_path}")
        
        # 清理云端备份
        await self.cleanup_s3_backups()
```

## 故障排查

### 常见问题

#### 1. 数据库连接问题
```python
# 连接池监控
async def check_database_connections():
    """检查数据库连接池状态"""
    try:
        # 获取连接池统计信息
        pool = engine.pool
        stats = {
            "size": pool.size(),
            "checked_in": pool.checkedin(),
            "checked_out": pool.checkedout(),
            "overflow": pool.overflow()
        }
        
        logger.info(f"数据库连接池状态: {stats}")
        
        # 检查连接是否可用
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            await result.scalar()
        
        return {"status": "healthy", "stats": stats}
        
    except Exception as e:
        logger.error(f"数据库连接检查失败: {e}")
        return {"status": "unhealthy", "error": str(e)}
```

#### 2. 内存泄漏排查
```python
# 内存使用监控
import tracemalloc
import gc

def monitor_memory():
    """监控内存使用情况"""
    # 开始跟踪内存分配
    tracemalloc.start()
    
    # 获取当前内存使用
    current, peak = tracemalloc.get_traced_memory()
    
    # 获取内存使用详情
    snapshot = tracemalloc.take_snapshot()
    top_stats = snapshot.statistics('lineno')
    
    logger.info(f"当前内存使用: {current / 1024 / 1024:.2f} MB")
    logger.info(f"峰值内存使用: {peak / 1024 / 1024:.2f} MB")
    
    # 显示内存使用最多的代码
    for stat in top_stats[:10]:
        logger.info(f"内存使用: {stat}")
    
    # 强制垃圾回收
    gc.collect()
    
    return {
        "current_mb": current / 1024 / 1024,
        "peak_mb": peak / 1024 / 1024,
        "gc_objects": len(gc.get_objects())
    }
```

#### 3. 性能瓶颈分析
```python
# 性能分析装饰器
def profile_performance(func):
    async def wrapper(*args, **kwargs):
        start_time = time.time()
        start_memory = psutil.Process().memory_info().rss
        
        try:
            result = await func(*args, **kwargs)
            
            end_time = time.time()
            end_memory = psutil.Process().memory_info().rss
            
            duration = end_time - start_time
            memory_used = (end_memory - start_memory) / 1024 / 1024  # MB
            
            logger.info(
                f"性能分析 - {func.__name__}",
                duration=duration,
                memory_used_mb=memory_used,
                timestamp=datetime.utcnow().isoformat()
            )
            
            return result
            
        except Exception as e:
            logger.error(f"函数执行失败 - {func.__name__}: {e}")
            raise
    
    return wrapper

# 使用示例
@profile_performance
async def process_large_file(file_path: str):
    # 大文件处理逻辑
    pass
```

### 日志分析

#### 错误日志分析
```python
# 日志分析工具
class LogAnalyzer:
    def __init__(self, log_file: str):
        self.log_file = log_file
    
    def analyze_errors(self, time_window: int = 3600) -> Dict:
        """分析错误日志"""
        errors = {
            "total_errors": 0,
            "error_types": {},
            "endpoints": {},
            "users": {},
            "timeline": []
        }
        
        cutoff_time = datetime.now() - timedelta(seconds=time_window)
        
        with open(self.log_file, 'r') as f:
            for line in f:
                try:
                    log_entry = json.loads(line)
                    
                    # 检查时间范围
                    log_time = datetime.fromisoformat(log_entry.get("timestamp", ""))
                    if log_time < cutoff_time:
                        continue
                    
                    # 检查错误级别
                    if log_entry.get("level") in ["ERROR", "CRITICAL"]:
                        errors["total_errors"] += 1
                        
                        # 统计错误类型
                        error_type = log_entry.get("event", "unknown")
                        errors["error_types"][error_type] = errors["error_types"].get(error_type, 0) + 1
                        
                        # 统计错误端点
                        if "path" in log_entry:
                            endpoint = log_entry["path"]
                            errors["endpoints"][endpoint] = errors["endpoints"].get(endpoint, 0) + 1
                        
                        # 统计用户相关错误
                        if "user_id" in log_entry:
                            user_id = log_entry["user_id"]
                            errors["users"][user_id] = errors["users"].get(user_id, 0) + 1
                        
                        # 时间线
                        errors["timeline"].append({
                            "timestamp": log_time.isoformat(),
                            "error_type": error_type,
                            "message": log_entry.get("message", ""),
                            "user_id": log_entry.get("user_id"),
                            "path": log_entry.get("path")
                        })
                        
                except json.JSONDecodeError:
                    continue
        
        return errors
    
    def generate_report(self, analysis_result: Dict) -> str:
        """生成分析报告"""
        report = f"""
错误日志分析报告
================

总错误数: {analysis_result['total_errors']}
分析时间窗口: 最近1小时

错误类型分布:
"""
        
        for error_type, count in sorted(
            analysis_result['error_types'].items(),
            key=lambda x: x[1],
            reverse=True
        ):
            report += f"- {error_type}: {count}次\n"
        
        report += "\n错误端点分布:\n"
        for endpoint, count in sorted(
            analysis_result['endpoints'].items(),
            key=lambda x: x[1],
            reverse=True
        ):
            report += f"- {endpoint}: {count}次\n"
        
        report += "\n用户相关错误:\n"
        for user_id, count in sorted(
            analysis_result['users'].items(),
            key=lambda x: x[1],
            reverse=True
        )[:10]:  # 只显示前10个
            report += f"- 用户ID {user_id}: {count}次\n"
        
        return report
```

## 扩展功能

### 1. 插件系统

#### 插件架构
```python
# 插件基类
class PluginBase(ABC):
    @abstractmethod
    def get_name(self) -> str:
        pass
    
    @abstractmethod
    def get_version(self) -> str:
        pass
    
    @abstractmethod
    async def process(self, content: str, context: Dict) -> str:
        pass

# 插件管理器
class PluginManager:
    def __init__(self):
        self.plugins: Dict[str, PluginBase] = {}
    
    def register_plugin(self, plugin: PluginBase):
        """注册插件"""
        name = plugin.get_name()
        self.plugins[name] = plugin
        logger.info(f"插件注册成功: {name} v{plugin.get_version()}")
    
    def unregister_plugin(self, name: str):
        """卸载插件"""
        if name in self.plugins:
            del self.plugins[name]
            logger.info(f"插件卸载成功: {name}")
    
    async def execute_plugins(self, content: str, context: Dict) -> str:
        """执行所有插件"""
        result = content
        for name, plugin in self.plugins.items():
            try:
                result = await plugin.process(result, context)
                logger.debug(f"插件 {name} 处理完成")
            except Exception as e:
                logger.error(f"插件 {name} 执行失败: {e}")
        
        return result

# 示例插件
class TranslationPlugin(PluginBase):
    def get_name(self) -> str:
        return "translation"
    
    def get_version(self) -> str:
        return "1.0.0"
    
    async def process(self, content: str, context: Dict) -> str:
        # 翻译逻辑
        target_language = context.get("target_language", "en")
        # ... 翻译实现
        return translated_content

class SummaryPlugin(PluginBase):
    def get_name(self) -> str:
        return "summary"
    
    def get_version(self) -> str:
        return "1.0.0"
    
    async def process(self, content: str, context: Dict) -> str:
        # 摘要生成逻辑
        max_length = context.get("max_length", 200)
        # ... 摘要生成实现
        return summary
```

### 2. 多语言支持

#### 国际化实现
```python
# 国际化服务
class I18nService:
    def __init__(self):
        self.translations = {}
        self.load_translations()
    
    def load_translations(self):
        """加载翻译文件"""
        locales_dir = Path("locales")
        for locale_file in locales_dir.glob("*.json"):
            locale = locale_file.stem
            with open(locale_file, 'r', encoding='utf-8') as f:
                self.translations[locale] = json.load(f)
    
    def translate(self, key: str, locale: str = "zh_CN", **kwargs) -> str:
        """翻译文本"""
        translation = self.translations.get(locale, {})
        text = translation.get(key, key)
        
        # 格式化文本
        if kwargs:
            text = text.format(**kwargs)
        
        return text
    
    def get_available_locales(self) -> List[str]:
        """获取可用的语言环境"""
        return list(self.translations.keys())

# 翻译装饰器
def translate_response(locale_key: str = "locale"):
    def decorator(func):
        async def wrapper(*args, **kwargs):
            result = await func(*args, **kwargs)
            
            # 获取语言环境
            locale = kwargs.get(locale_key, "zh_CN")
            i18n_service = I18nService()
            
            # 翻译响应消息
            if isinstance(result, dict) and "message" in result:
                result["message"] = i18n_service.translate(
                    result["message"], locale
                )
            
            return result
        return wrapper
    return decorator

# 使用示例
@translate_response()
async def create_user(user_data: dict, locale: str = "zh_CN"):
    # 用户创建逻辑
    return {
        "success": True,
        "message": "user_created_successfully",
        "data": user
    }
```

### 3. 移动端支持

#### 响应式设计
```vue
<!-- 响应式聊天界面 -->
<template>
  <div class="chat-container" :class="{ 'mobile': isMobile }">
    <!-- 侧边栏 -->
    <div class="sidebar" v-show="!isMobile || showSidebar">
      <ConversationList @select="selectConversation" />
    </div>
    
    <!-- 主聊天区域 -->
    <div class="chat-main">
      <div class="chat-header">
        <n-button
          v-if="isMobile"
          quaternary
          circle
          @click="toggleSidebar"
        >
          <template #icon>
            <n-icon><menu-icon /></n-icon>
          </template>
        </n-button>
        
        <h3>{{ currentConversation?.title }}</h3>
      </div>
      
      <div class="chat-messages" ref="messageContainer">
        <MessageList :messages="messages" />
      </div>
      
      <div class="chat-input">
        <MessageInput @send="sendMessage" />
      </div>
    </div>
  </div>
</template>

<script setup>
import { useMediaQuery } from '@vueuse/core'

// 检测设备类型
const isMobile = useMediaQuery('(max-width: 768px)')
const showSidebar = ref(!isMobile.value)

// 监听设备变化
watch(isMobile, (mobile) => {
  showSidebar.value = !mobile
})

function toggleSidebar() {
  showSidebar.value = !showSidebar.value
}
</script>

<style scoped>
/* 移动端样式 */
@media (max-width: 768px) {
  .chat-container {
    flex-direction: column;
  }
  
  .sidebar {
    position: fixed;
    top: 0;
    left: 0;
    width: 80%;
    height: 100%;
    background: white;
    z-index: 1000;
    transform: translateX(-100%);
    transition: transform 0.3s;
  }
  
  .sidebar.show {
    transform: translateX(0);
  }
  
  .chat-main {
    width: 100%;
  }
  
  .chat-input {
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    background: white;
    padding: 10px;
    box-shadow: 0 -2px 10px rgba(0,0,0,0.1);
  }
}
</style>
```

#### PWA支持
```javascript
// service-worker.js
const CACHE_NAME = 'paicongming-v1'
const urlsToCache = [
  '/',
  '/css/app.css',
  '/js/app.js',
  '/offline.html'
]

// 安装Service Worker
self.addEventListener('install', event => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then(cache => cache.addAll(urlsToCache))
  )
})

// 拦截网络请求
self.addEventListener('fetch', event => {
  event.respondWith(
    caches.match(event.request)
      .then(response => {
        //