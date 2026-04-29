# 派聪明AI知识库系统 - 开发环境配置

## 系统要求

### 必需软件
- Python 3.8+
- Node.js 16+
- MySQL 8.0
- Redis 6.0+
- Elasticsearch 8.10.0
- Apache Kafka
- MinIO

### 可选软件
- Docker & Docker Compose
- Git
- IDE (VS Code, PyCharm等)

## 环境安装

### 1. Python环境
```bash
# 安装Python 3.8+
# Ubuntu/Debian
sudo apt update
sudo apt install python3.8 python3.8-venv python3.8-dev

# macOS
brew install python@3.8

# Windows
# 从python.org下载安装包
```

### 2. Node.js环境
```bash
# 安装Node.js 16+
# Ubuntu/Debian
curl -fsSL https://deb.nodesource.com/setup_16.x | sudo -E bash -
sudo apt-get install -y nodejs

# macOS
brew install node@16

# Windows
# 从nodejs.org下载安装包
```

### 3. MySQL数据库
```bash
# Ubuntu/Debian
sudo apt update
sudo apt install mysql-server
sudo mysql_secure_installation

# macOS
brew install mysql
brew services start mysql

# Windows
# 从mysql.com下载安装包
```

### 4. Redis缓存
```bash
# Ubuntu/Debian
sudo apt update
sudo apt install redis-server

# macOS
brew install redis
brew services start redis

# Windows
# 使用WSL或下载Windows版本
```

### 5. Elasticsearch
```bash
# 下载Elasticsearch 8.10.0
wget https://artifacts.elastic.co/downloads/elasticsearch/elasticsearch-8.10.0-linux-x86_64.tar.gz
tar -xzf elasticsearch-8.10.0-linux-x86_64.tar.gz
cd elasticsearch-8.10.0/
./bin/elasticsearch
```

### 6. Apache Kafka
```bash
# 下载Kafka
wget https://downloads.apache.org/kafka/3.5.0/kafka_2.13-3.5.0.tgz
tar -xzf kafka_2.13-3.5.0.tgz
cd kafka_2.13-3.5.0/

# 启动Zookeeper
bin/zookeeper-server-start.sh config/zookeeper.properties

# 启动Kafka（新终端）
bin/kafka-server-start.sh config/server.properties
```

### 7. MinIO对象存储
```bash
# 下载MinIO
wget https://dl.min.io/server/minio/release/linux-amd64/minio
chmod +x minio

# 启动MinIO
./minio server /data
```

## 项目配置

### 1. 克隆项目
```bash
git clone <repository-url>
cd paicongming-ai-knowledge-base
```

### 2. 后端配置
```bash
cd paicongming_backend

# 创建虚拟环境
python3 -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate  # Windows

# 安装依赖
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env
# 编辑 .env 文件，配置数据库等连接信息
```

### 3. 前端配置
```bash
cd paicongming_frontend

# 安装依赖
npm install

# 配置环境变量
cp .env.example .env.local
# 编辑 .env.local 文件，配置API地址等
```

### 4. 数据库初始化
```sql
-- 创建数据库
CREATE DATABASE paicongming CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

-- 创建用户（可选）
CREATE USER 'paicongming'@'localhost' IDENTIFIED BY 'your_password';
GRANT ALL PRIVILEGES ON paicongming.* TO 'paicongming'@'localhost';
FLUSH PRIVILEGES;
```

## 开发启动

### 启动所有服务
```bash
# 在项目根目录
./start_system.sh
```

### 单独启动服务
```bash
# 后端API
cd paicongming_backend
python main.py

# 文件处理服务
python file_processor.py

# 监控服务
python monitoring_service.py

# 前端开发服务器
cd paicongming_frontend
npm run dev
```

## 开发工具配置

### VS Code推荐插件
- Python
- Vue Language Features (Volar)
- TypeScript Vue Plugin (Volar)
- ESLint
- Prettier
- Thunder Client (API测试)

### 代码格式化
```bash
# Python代码格式化
black .
isort .

# TypeScript代码格式化
npm run lint
npm run format
```

## 测试

### 后端测试
```bash
cd paicongming_backend
pytest tests/ -v
```

### 前端测试
```bash
cd paicongming_frontend
npm run test
```

### API测试
```bash
# 健康检查
curl http://localhost:8000/api/health

# 用户注册
curl -X POST http://localhost:8000/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username":"testuser","email":"test@example.com","password":"123456"}'

# 用户登录
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"testuser","password":"123456"}'
```

## 部署

### Docker部署
```bash
# 构建镜像
docker-compose build

# 启动服务
docker-compose up -d

# 查看日志
docker-compose logs -f
```

### 生产环境部署
1. 配置环境变量
2. 设置反向代理(Nginx)
3. 配置HTTPS证书
4. 设置数据库备份
5. 配置监控告警

## 故障排除

### 常见问题

#### 1. 端口被占用
```bash
# 查找占用端口的进程
lsof -i :8000
netstat -tulpn | grep 8000

# 终止进程
kill -9 <PID>
```

#### 2. 数据库连接失败
- 检查MySQL服务是否启动
- 检查数据库配置是否正确
- 检查用户权限

#### 3. Redis连接失败
- 检查Redis服务是否启动
- 检查Redis配置是否正确
- 检查防火墙设置

#### 4. Elasticsearch启动失败
- 检查内存设置（需要至少2GB）
- 检查文件描述符限制
- 检查端口是否被占用

### 日志查看
```bash
# 系统日志
tail -f logs/backend.log
tail -f logs/frontend.log
tail -f logs/file_processor.log
tail -f logs/monitoring.log

# 服务状态
./status_system.sh
```

## 性能优化

### 数据库优化
- 添加索引
- 配置连接池
- 定期清理数据

### Redis优化
- 配置内存策略
- 使用连接池
- 设置合理的过期时间

### Elasticsearch优化
- 配置分片和副本
- 优化查询性能
- 定期重建索引

## 安全建议

### 生产环境
- 使用强密码
- 配置HTTPS
- 设置防火墙
- 定期更新依赖
- 启用访问日志

### 环境变量
- 不要将敏感信息提交到代码仓库
- 使用环境变量管理配置
- 定期轮换密钥

## 更新日志

### v1.0.0 (2024-01-01)
- 初始版本发布
- 基础功能实现
- 文档完善

## 贡献指南

1. Fork项目
2. 创建特性分支
3. 提交代码
4. 创建Pull Request

## 许可证

MIT License