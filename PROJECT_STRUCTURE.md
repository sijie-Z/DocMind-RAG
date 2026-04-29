# 派聪明AI知识库系统 - 项目结构说明

## 📁 项目根目录
```
1_demo/
├── backend/                    # 后端服务 (Python + FastAPI)
├── frontend/                   # 前端应用 (Vue.js + TypeScript)
├── *.md                        # 项目文档文件
├── *.sh                        # 启动脚本文件
└── requirements.txt            # Python依赖
```

---

## 🔧 Backend 结构
```
backend/
├── app/                        # 主要应用代码
│   ├── api/v1/                 # API路由
│   │   ├── endpoints/          # 各个端点
│   │   │   ├── auth.py        # 认证接口
│   │   │   ├── chat.py        # 聊天接口
│   │   │   ├── files.py       # 文件管理接口
│   │   │   ├── knowledge.py   # 知识库接口
│   │   │   ├── monitoring.py  # 监控接口
│   │   │   ├── organizations.py # 组织管理接口
│   │   │   └── users.py       # 用户管理接口
│   │   └── router.py          # 路由汇总
│   ├── core/                   # 核心配置
│   │   ├── config.py          # 应用配置
│   │   ├── database.py        # 数据库连接
│   │   ├── elasticsearch.py   # Elasticsearch配置
│   │   ├── logging.py         # 日志配置
│   │   ├── minio.py           # 对象存储配置
│   │   └── redis.py           # Redis配置
│   ├── extra_services/         # 额外服务
│   │   ├── ai_service.py      # AI服务
│   │   ├── chunked_upload_service.py # 分块上传
│   │   ├── enhanced_document_parser.py # 文档解析
│   │   ├── file_processor.py  # 文件处理
│   │   ├── monitoring_service.py # 监控服务
│   │   └── notification_service.py # 通知服务
│   ├── models/                 # 数据模型
│   │   ├── chat.py            # 聊天模型
│   │   ├── document.py        # 文档模型
│   │   ├── organization.py    # 组织模型
│   │   └── user.py            # 用户模型
│   ├── schemas/                # 数据验证模式
│   │   ├── chat.py            # 聊天模式
│   │   ├── file.py            # 文件模式
│   │   └── knowledge.py       # 知识库模式
│   └── services/               # 业务逻辑服务
│       ├── auth_service.py    # 认证服务
│       ├── chat_service.py    # 聊天服务
│       ├── deepseek_service.py # DeepSeek AI服务
│       ├── document_parser.py # 文档解析服务
│       ├── embedding_service.py # 向量嵌入服务
│       ├── file_service.py    # 文件服务
│       ├── knowledge_service.py # 知识库服务
│       └── organization_service.py # 组织服务
├── lib/                        # 工具库
│   ├── auth/                   # 认证工具
│   ├── fastapi_compat/         # FastAPI兼容层
│   ├── rag/                    # RAG相关工具
│   ├── rbac/                   # 权限控制
│   ├── scripts/                # 脚本工具
│   └── upload/                 # 上传管理
├── config/                     # 配置文件
├── data/                       # 数据文件
├── vector_store/               # 向量存储
├── backup/                     # 备份文件
└── *.py, *.txt               # 主入口和依赖文件
```

---

## 🎨 Frontend 结构
```
frontend/
├── src/                        # 源代码
│   ├── api/                    # API接口
│   ├── layouts/                # 布局组件
│   ├── router/                 # 路由配置
│   ├── stores/                 # 状态管理
│   ├── styles/                 # 样式文件
│   ├── types/                  # TypeScript类型定义
│   ├── utils/                  # 工具函数
│   ├── views/                  # 页面组件
│   │   ├── chat/              # 聊天页面
│   │   ├── conversations/     # 对话历史
│   │   ├── error/             # 错误页面
│   │   ├── knowledge/         # 知识库管理
│   │   ├── login/             # 登录页面
│   │   ├── monitoring/        # 系统监控
│   │   ├── organizations/     # 组织管理
│   │   ├── profile/           # 个人资料
│   │   ├── users/             # 用户管理
│   │   └── search.vue         # 搜索页面
│   ├── App.vue                # 根组件
│   └── main.ts                # 入口文件
├── dist/                       # 构建输出
└── 配置文件 (*.json, *.ts)   # 项目配置
```

---

## 🎯 核心功能模块

### 1. 用户认证系统
- **位置**: `backend/app/services/auth_service.py`
- **功能**: JWT认证、用户注册登录、权限管理

### 2. 知识库管理
- **位置**: `backend/app/services/knowledge_service.py`
- **功能**: 文档上传、向量化、智能检索

### 3. AI聊天服务
- **位置**: `backend/app/services/deepseek_service.py`
- **功能**: DeepSeek API集成、智能问答、流式响应

### 4. 文件处理
- **位置**: `backend/app/services/file_service.py`
- **功能**: 多格式文档解析、分块上传、对象存储

### 5. 向量嵌入
- **位置**: `backend/app/services/embedding_service.py`
- **功能**: 文本向量化、相似度搜索、混合检索

### 6. 前端界面
- **位置**: `frontend/src/views/`
- **功能**: 现代化UI、响应式设计、实时交互

---

## 🚀 技术栈

### 后端
- **框架**: FastAPI (Python)
- **数据库**: MySQL + SQLAlchemy
- **缓存**: Redis
- **搜索**: Elasticsearch
- **存储**: MinIO (对象存储)
- **AI模型**: DeepSeek API + OpenAI Embeddings

### 前端
- **框架**: Vue.js 3 + TypeScript
- **UI库**: Element Plus
- **状态管理**: Pinia
- **路由**: Vue Router
- **构建工具**: Vite

---

## 📋 环境要求

### Python环境
- Python 3.8+
- 依赖管理: pip

### Node.js环境
- Node.js 16+
- 包管理器: npm

### 数据库
- MySQL 8.0+
- Redis 6.0+
- Elasticsearch 8.0+

### 其他服务
- MinIO (对象存储)

---

## 🔧 开发指南

1. **后端开发**: 在 `backend/app/` 目录下进行
2. **前端开发**: 在 `frontend/src/` 目录下进行
3. **工具库**: 通用工具放在 `backend/lib/` 目录
4. **配置文件**: 环境配置放在 `backend/` 根目录
5. **文档**: 项目文档放在根目录

这个结构确保了代码的模块化、可维护性和可扩展性，符合企业级项目的标准。