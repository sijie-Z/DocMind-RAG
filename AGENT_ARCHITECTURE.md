# DocMind Agent — 企业级 PER 架构文档

## 概览

DocMind Agent 是一个 **Planning-Execution-Reflection (PER)** 三阶段自主智能体系统，集成了 25 个生产级工具、四层记忆系统和实时 SSE 流式事件协议。架构设计参考了学术界和工业界的主流 Agent 范式，面向企业 RAG 知识库场景深度定制。

## 架构总览

```
                        ┌──────────────────────────────┐
                        │       Memory System           │
                        │  STM / LTM / Working / Refl.  │
                        └──────────────┬───────────────┘
                                       │
User Query ──► Phase 0: 记忆检索 ──────┘
                  │
                  ▼
              Phase 1: Planning (Planner)
                  │  • LLM 生成结构化 JSON Plan
                  │  • DAG 依赖 + tool_hints
                  │  • 拓扑排序验证无环
                  │  • 流式思考 reasoning tokens
                  ▼
              Phase 2: Execution (Executor)
                  │  • 按依赖顺序执行 PlanStep
                  │  • LLM + 工具调用编排
                  │  • 3 次重试 + 指数退避
                  │  • 结果存入 WorkingMemory
                  ▼
              Phase 3: Reflection (Reflector)
                  │  • 目标达成度评估 (0-100%)
                  │  • 决策: pass / retry / replan
                  │  • 失败经验 → ReflectiveMemory
                  ▼
              Final Answer ──► 存储对话记忆
```

## 核心设计原则

| 原则 | 实现 |
|------|------|
| **结构化规划** | LLM 将复杂查询拆解为 DAG Plan，声明步骤、依赖关系和工具提示 |
| **可靠执行** | 依赖拓扑排序执行，每步自带指数退避重试（最多 3 次） |
| **自我反思** | 执行后 LLM 自评目标达成度和信息完整性，自主决定重试或重新规划 |
| **持久记忆** | 四层记忆系统（短期/长期/工作/反思），嵌入向量语义检索 |
| **流式可见** | 11 种 SSE 事件类型，前端实时展示规划树、思考流、工具调用 |
| **安全沙箱** | Python/SQL 执行默认禁用，DuckDuckGo 搜索限流，子代理工具隔离 |
| **优雅降级** | LLM 调用失败退回默认 Plan，Redis 不可用时记忆系统跳过，工具异常不阻断流程 |

## 模块详解

### Phase 0: 记忆检索 (`memory_bridge.py`)

```
AgentMemoryBridge
  ├── get_context_for_query(query)
  │     • 语义相似度检索 (embedding cos-sim)
  │     • 关键词匹配检索
  │     • 混合排序 → 注入 system prompt
  ├── store_step_result(step_id, result)
  │     • 写入 WorkingMemory (临时)
  ├── record_experience(success, action, result)
  │     • 成功 → ShortTermMemory
  │     • 失败 → ReflectiveMemory (教训)
  └── record_interaction(query, answer)
        • ShortTermMemory (会话上下文)
        • LongTermMemory (持久化, 带衰减)
```

底层对接 `AgentMemorySystem`（`app/services/memory_service.py`），支持 embedding 驱动的语义搜索和基于关键词的精确匹配。

### Phase 1: Planner (`planner.py`)

**输入**: 用户 query + 对话历史 + 记忆上下文 + 可用工具列表  
**输出**: 结构化 Plan（goal + steps + dependencies + tool_hints）

```
Planner.plan(query, history, memory_context)
  │
  ├─► LLM 调用 (temperature=0.1)
  │     System: 任务规划专家提示 + 25 个工具描述
  │     User: 用户 query
  │
  ├─► 流式 reasoning tokens (thinking events)
  │
  ├─► JSON 解析 + Schema 验证
  │     {
  │       "goal": "...",
  │       "reasoning": "...",
  │       "steps": [
  │         {"id": "s1", "description": "...", "dependencies": [], "tool_hint": "list_documents"},
  │         {"id": "s2", "description": "...", "dependencies": ["s1"], "tool_hint": "summarize_document"}
  │       ]
  │     }
  │
  ├─► DAG 验证 (Kahn 拓扑排序, 环检测)
  │
  └─► 生成 Plan 对象 ──► 持久化 Redis (1h TTL)
```

**降级策略**：JSON 解析失败 → 自动退回单步 Plan（直接执行原始 query）。

### Phase 2: Executor (`executor.py`)

按依赖拓扑顺序执行 PlanStep：

```
for each ready_step (dependencies satisfied):
  │
  ├─► yield thinking event (reasoning)
  │
  ├─► _execute_step_with_retry (最多 3 次)
  │     │
  │     ├─► 构建 step system prompt
  │     │     • step_description
  │     │     • tool_hint (建议工具)
  │     │     • 前置步骤结果
  │     │     • 记忆上下文
  │     │     • 上次错误上下文 (重试时)
  │     │
  │     ├─► LLM 调用 + 工具定义
  │     │     如果 tool_hint 非空 → 优先排列该工具
  │     │
  │     ├─► 处理 tool_calls:
  │     │     yield tool_call event
  │     │     → tool_registry.execute(name, args)
  │     │     yield tool_result event (含耗时 ms)
  │     │     结果 = "Error:..." → yield tool_error event
  │     │
  │     └─► 失败重试: 指数退避 0.5s * 2^n
  │
  ├─► 存储结果 → WorkingMemory
  │
  ├─► record_experience(success, action, result)
  │
  └─► yield 完成/失败 event
```

### Phase 3: Reflector (`reflector.py`)

```
Reflector.reflect(plan, results)
  │
  ├─► 快速通行检查:
  │     所有步骤 completed/skipped + 有结果 + 无失败 + ≤3 步
  │     → achievement=100%, decision="pass" (跳过 LLM)
  │
  └─► LLM 评估:
        System: 质量评估专家 + 原始目标 + 执行计划 + 结果摘要
        → {
            "achievement": 85,        // 0-100%
            "gaps": [...],            // 缺失信息
            "quality_issues": [...],  // 质量问题
            "decision": "pass",       // pass | retry | replan
            "retry_step_id": null,    // retry 时指定步骤
            "reasoning": "..."        // 1-2 句话
          }
```

## 工具生态（25 个）

### 原始工具 (11) — `core_tools.py`

| 工具 | 描述 | 标签 |
|------|------|------|
| `search_knowledge_base` | 混合检索（关键词 + 向量 + RRF 融合 + 重排序） | search, retrieval |
| `vector_search` | 纯语义向量搜索 | search |
| `summarize_document` | LLM 文档摘要 | analysis |
| `extract_keywords` | 关键术语和实体提取 | analysis |
| `list_documents` | 列出知识库文档（ID/文件名/状态/日期） | management |
| `get_document_info` | 文档详情（状态/分块数/大小/元数据） | management |
| `list_conversations` | 列出用户的聊天会话 | conversation, management |
| `get_conversation_history` | 获取会话消息历史 | conversation |
| `list_prompt_templates` | 列出提示词模板 | prompts |
| `get_prompt_template` | 获取指定模板内容 | prompts |
| `get_current_time` | 当前日期时间 | utility |

### 新增工具 (14) — `tools/` 包

| 工具 | 描述 | 标签 | 安全 |
|------|------|------|------|
| `web_search` | DuckDuckGo 网页搜索（标题+URL+摘要） | web, external, search | 限流 10次/分钟 |
| `fetch_webpage` | 网页正文提取（≤5000 字符） | web, external | — |
| `execute_python` | 沙箱化 Python（RestrictedPython + subprocess, 10s 超时） | code, compute | **默认禁用** |
| `execute_sql` | 组织数据库只读查询（LIMIT 100, 5s 超时） | code, data | **默认禁用** |
| `analyze_data` | 数值统计分析（均值/方差/中位数/四分位数/趋势） | analysis, data | — |
| `compare_documents` | 2-5 篇文档对比（相似点/差异/关键主题） | analysis, documents | — |
| `translate_text` | 30+ 语言翻译 | language, translation | — |
| `detect_language` | 语言检测（名称/ISO 代码/置信度） | language | — |
| `calculate` | 安全数学表达式求值 | utility, compute | — |
| `format_converter` | JSON ↔ YAML ↔ CSV ↔ Markdown 格式互转 | utility, format | — |
| `get_system_status` | ES 索引统计 + Redis 内存 + DB 表统计 | utility, system | — |

### 工具注册模式

```python
@register_tool(
    name="web_search",
    description="Search the web using DuckDuckGo...",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "max_results": {"type": "integer", "default": 10}
        },
        "required": ["query"]
    },
    tags=["web", "external", "search"],
)
async def web_search(query: str, max_results: int = 10, **ctx) -> str:
    ...
```

- 每个工具通过 `@register_tool` 装饰器自注册到全局 `tool_registry`
- 参数使用 JSON Schema 定义，自动转换为 OpenAI function-calling 格式
- `tags` 用于配置面板中的分类筛选和启用/禁用
- `requires_auth` 标记需要认证的工具
- `disabled_by_default` 列表 (`DEFAULT_DISABLED_TOOLS`) 控制安全敏感工具

## 记忆系统

```
AgentMemorySystem
  ├── ShortTermMemory    ← 会话内上下文（滑动窗口）
  ├── LongTermMemory     ← 跨会话知识（重要性衰减 + embedding）
  ├── WorkingMemory      ← 当前任务中间结果（步骤级别）
  └── ReflectiveMemory   ← 失败教训 & 成功模式

MemoryBridge (app/agent/memory_bridge.py)
  ├── get_context_for_query() → 语义/关键词混合召回
  ├── store_step_result()     → WorkingMemory
  ├── record_experience()     → ShortTerm + Reflective
  └── record_interaction()    → ShortTerm + LongTerm
```

**特性**：
- Embedding 驱动的语义相似度搜索（`MEMORY_SIMILARITY_THRESHOLD`）
- 关键词精确匹配作为补充
- 重要性评分（`importance_score`）影响记忆衰减
- `access_count` 和 `last_accessed` 追踪记忆热度

## SSE 事件协议

前端通过 SSE 接收 11 种事件类型：

```json
// 思考流
{"type":"thinking","content":"...","thinking_type":"planning|reasoning|evaluation|correction","plan_id":"p1"}

// 计划生命周期
{"type":"plan_start","plan_id":"p1","content":"正在制定计划..."}
{"type":"plan_step","plan_id":"p1","plan_step_id":"s1","content":"...","dependencies":[],"tool_hint":"..."}
{"type":"plan_complete","plan_id":"p1","content":"计划已生成: N 步","plan_progress":0.0}

// 工具调用
{"type":"tool_call","tool_name":"...","tool_args":{...},"tool_call_id":"...","plan_step_id":"s1"}
{"type":"tool_result","tool_name":"...","content":"...","tool_duration_ms":345,"plan_step_id":"s1"}
{"type":"tool_error","tool_name":"...","content":"...","retry_attempt":1}

// 反思
{"type":"reflection","reflection_result":"pass|retry|replan","content":"...","plan_progress":1.0}

// 内容 & 终止
{"type":"chunk","content":"..."}
{"type":"done","plan_progress":1.0}
{"type":"error","content":"..."}
```

## 前端架构

```
Three-Column Layout (views/agent/index.vue)
┌──────────────┬────────────────────┬──────────────┐
│  PlanTree    │   Chat (center)     │ ConfigPanel  │
│  (DAG树)     │   + ToolCallCard    │  (可折叠)     │
│              │   + ThinkingStream  │              │
│              │   + ReflectionBanner│              │
│              │   + AgentInput      │              │
└──────────────┴────────────────────┴──────────────┘

状态管理: Pinia (stores/agent.ts)
  • SSE 事件解析 → 更新 plan / messages / tools / config
  • AbortController 管理 (停止生成)

SSE 客户端 (api/agent.ts)
  • fetch + ReadableStream reader
  • TextDecoder 流式解码
  • 按 \n 分割行, 解析 "data: {json}" 格式
  • AbortError 抑制 (用户停止)
```

**关键组件**：
- `PlanTree.vue` — DAG 计划可视化（步骤状态图标 + 进度条）
- `ThinkingStream.vue` — 实时 reasoning tokens（按 thinking_type 分色）
- `ToolCallCard.vue` — 工具调用卡片（图标 + 参数摘要 + 耗时 + 展开结果）
- `ReflectionBanner.vue` — 反思结果横幅（绿色 PASS / 黄色 RETRY / 红色 REPLAN）
- `AgentConfigPanel.vue` — 模型/温度/Planning/Reflection/Memory/Thinking 开关 + 工具启用列表
- `SessionSelector.vue` — Agent 会话切换/创建/删除

## 参考框架对比

| 特性 | ReAct (Yao 2022) | Plan-and-Solve (Wang 2023) | Reflexion (Shinn 2023) | **DocMind PER** |
|------|------------------|---------------------------|----------------------|----------------|
| 规划 | 无显式规划，逐轮推理 | 先生成完整计划再执行 | 无显式规划 | **结构化 DAG Plan + 拓扑排序** |
| 执行 | Thought→Action→Observe 循环 | 按计划步骤顺序执行 | ReAct 循环 | **步骤级重试 (3x) + 指数退避** |
| 反思 | 无（Observation 即反馈） | 无 | LLM 自我反思 + 长期记忆 | **目标达成度评估 + pass/retry/replan** |
| 记忆 | 无 | 无 | 向量数据库长期记忆 | **四层：STM/LTM/Work/Reflective** |
| 工具 | 预定义工具集 | 预定义工具集 | 预定义工具集 | **25 个自注册工具 + tags 分类 + 安全分级** |
| 流式 | 取决于实现 | 取决于实现 | 取决于实现 | **11 种 SSE 事件类型, 前端可视化** |
| 多步依赖 | 不支持 | 线性依赖 | 不支持 | **完整 DAG 依赖管理** |
| 失败恢复 | 无 | 无 | 基本反思 | **步骤级重试 + 错误上下文传递** |

### 核心参考

| 参考 | 借鉴点 |
|------|--------|
| **ReAct** (Yao et al., 2022) | Thought-Action-Observation 循环理念 → 演化为 PER 三阶段 |
| **Plan-and-Solve** (Wang et al., 2023) | 先规划后执行 → Planner 生成 DAG Plan + tool_hints |
| **Reflexion** (Shinn et al., 2023) | LLM 自我反思 → Reflector 评估 + ReflectiveMemory |
| **Generative Agents** (Park et al., 2023) | 记忆流与检索 → 四层记忆系统 + 语义检索 |
| **Mem0** (mem0.ai, 2024) | 记忆 embedding 与衰减 → embedding 驱动的记忆召回 |
| **hermes-agent** (NousResearch) | 自注册工具 + 子代理 → 装饰器工具注册 + subagent.py |
| **LangChain** | Agent 工具抽象 → 独立的 ToolRegistry + JSON Schema 参数 |
| **Dify** | 可视化工作流编排 → 前端 PlanTree + ToolCallCard 可视化 |

## 安全模型

| 层级 | 措施 |
|------|------|
| **工具分级** | `tags: ["code"]` 的工具默认禁用，需显式开启 |
| **Python 沙箱** | RestrictedPython + subprocess + `__builtins__` 白名单 + 10s 超时 |
| **SQL 安全** | 独立只读会话 + `statement_timeout=5s` + 强制 `LIMIT 100` |
| **网络限流** | `web_search` Redis 计数器：10 次/分钟/用户 |
| **子代理隔离** | 子代理仅授权 search + analysis 工具，更低迭代预算 |
| **认证** | 所有 Agent 端点需 JWT Bearer Token |
| **多租户** | 所有工具调用注入 `organization_id`，数据隔离 |

## 配置体系

```python
AgentConfig(
    model="deepseek-chat",
    temperature=0.1,
    enable_planning=True,       # Phase 1
    enable_reflection=True,     # Phase 3
    enable_memory=True,         # Phase 0
    enable_thinking=True,       # 流式 reasoning tokens
    enable_tools=True,
    max_iterations=10,
    max_plan_steps=8,
    max_retries_per_step=3,
    disabled_tools=["execute_python", "execute_sql"],
    tool_tags=["search", "analysis", "management", "web", "utility", "conversation", "prompts", "language"],
)
```

配置通过 Redis 持久化，按 session_id 隔离，跨请求保持。

## API 端点

```
POST   /api/v1/agent/chat           PER agent 聊天 (SSE 流式)
GET    /api/v1/agent/tools          列出所有工具
GET    /api/v1/agent/skills         列出已学技能
GET    /api/v1/agent/sessions       列出 Agent 会话
POST   /api/v1/agent/sessions       创建 Agent 会话
GET    /api/v1/agent/sessions/{id}  获取会话详情
DELETE /api/v1/agent/sessions/{id}  删除 Agent 会话
GET    /api/v1/agent/config         获取用户 Agent 配置
PUT    /api/v1/agent/config         更新用户 Agent 配置
```

## 测试

```bash
# 健康检查
curl http://localhost:8000/health

# 登录获取 Token
curl -X POST http://localhost:8000/api/v1/auth/login/json \
  -H "Content-Type: application/json" \
  -d '{"username":"guest","password":"123456"}'

# Agent 聊天 (SSE)
curl -N -X POST http://localhost:8000/api/v1/agent/chat \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"query":"知识库中有哪些文档？","enable_planning":true}'

# 列出工具
curl http://localhost:8000/api/v1/agent/tools \
  -H "Authorization: Bearer <token>"
```

## 文件索引

```
backend/app/agent/
  ├── events.py          # 11 种 AgentEvent 类型
  ├── config.py          # AgentConfig 模型 + Redis 持久化
  ├── registry.py        # 自注册工具注册表
  ├── planner.py         # Phase 1: DAG 计划生成
  ├── executor.py        # Phase 2: 步骤执行 + 重试
  ├── reflector.py       # Phase 3: 质量评估 + 决策
  ├── loop.py            # PERAgentLoop 主循环
  ├── memory_bridge.py   # 记忆系统桥接
  ├── context.py         # 上下文压缩
  ├── skills.py          # 技能学习
  ├── subagent.py        # 子代理委托
  ├── service.py         # AgentService 入口
  ├── core_tools.py      # 原始 11 个工具
  └── tools/             # 新增 14 个工具
      ├── web_search.py
      ├── code_execution.py
      ├── data_analysis.py
      ├── translation.py
      └── utility.py

frontend/src/
  ├── views/agent/index.vue         # 三栏 Agent 页面
  ├── api/agent.ts                  # SSE 客户端 + API
  ├── stores/agent.ts               # Pinia 状态管理
  ├── types/agent.ts                # TypeScript 类型
  └── components/agent/
      ├── PlanTree.vue              # 计划树
      ├── ThinkingStream.vue        # 思考流
      ├── ToolCallCard.vue          # 工具卡片
      ├── ReflectionBanner.vue      # 反思横幅
      ├── AgentConfigPanel.vue      # 配置面板
      ├── AgentInput.vue            # 输入区
      └── SessionSelector.vue       # 会话选择器
```
