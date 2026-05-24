# DocMind 系统架构设计

本文档描述了 DocMind 企业级 RAG 知识库系统的整体架构设计。

## 系统分层架构

### 1. 表现层 (Frontend)
- Vue 3 + TypeScript 构建的单页应用
- Naive UI 组件库提供统一界面风格
- Pinia 状态管理 + Vue Router 路由
- Vite 构建工具 + 热更新开发体验

### 2. API 网关层
- FastAPI 框架提供 RESTful API
- JWT Token 认证 + RBAC 权限控制
- 请求/响应中间件统一格式
- SSE 和 WebSocket 支持流式响应

### 3. 业务逻辑层
- 文档管理: 上传、解析、分块、向量化
- RAG 检索: 混合检索 (关键词 + 向量 + RRF)
- Agent 系统: PER (规划-执行-反思) 架构
- 知识库管理: CRUD + 权限隔离

### 4. AI/LLM 层
- DeepSeek API 提供大语言模型能力
- ZhipuAI Embedding 模型用于文本向量化
- 语义缓存减少重复 LLM 调用
- 流式输出提供实时响应体验

### 5. 数据存储层
- SQLite/MySQL: 结构化业务数据
- Redis: 缓存 + 会话管理 + 速率限制
- Elasticsearch: 全文检索 + 向量搜索
- MinIO: 文件存储 (文档原文)

## 核心技术特性

### RAG 管线
1. 用户提问 → 向量化 + 关键词提取
2. 混合检索 (向量相似度 + BM25 关键词)
3. RRF 融合排序
4. 语义缓存命中检查
5. LLM 上下文注入 + 流式生成
6. 引用溯源

### Agent 系统 (PER 架构)
1. Plan: 复杂任务自动拆解为可执行步骤
2. Execute: 按顺序执行工具调用
3. Reflect: 执行结果自我评估与纠正
4. 25+ 内置工具: 搜索、分析、翻译、代码执行
