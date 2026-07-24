# DocMind 记忆系统(Memory System)

> 面试向系统设计文档。覆盖架构、四层记忆、检索链路、Embedding 与 Token 设计、Experience 单独系统、当前断点。

---

## 一句话定位

DocMind 的记忆系统是一个**双层架构**:底层是按用途切分的四层记忆(短期/长期/工作/反思),上层是 **MemoryBridge** 适配器把记忆接进 PER 环路。ShortTerm / Working 仅在进程内;LongTerm / Reflective 被打包进单个 Redis key(`agent:memory:{agent_id}`,只含 agent_id,**不含** org_id / user_id);Experience Loop 有独立存储,不走四层记忆。

---

## 1. 整体架构

```
┌────────────────────────────────────────────────────────┐
│                  PER Agent Loop                        │
│  Phase 0 Recall ─ Phase 1 Plan ─ Phase 2 Execute      │
│  Phase 3 Reflect ─ Phase 4 Store                       │
└──────────────────────┬─────────────────────────────────┘
                       │
                MemoryBridge(适配器)
                命名空间: org:user:agent
                       │
┌──────────────────────▼─────────────────────────────────┐
│              AgentMemorySystem(聚合层)                 │
│                                                        │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐ │
│  │ ShortTerm    │  │ LongTerm     │  │ Working      │ │
│  │ 会话上下文   │  │ 跨会话知识   │  │ 任务中间结果 │ │
│  │ 滑动窗口     │  │ 衰减+向量    │  │ KV+任务栈    │ │
│  └──────────────┘  └──────────────┘  └──────────────┘ │
│                                                        │
│  ┌──────────────┐                                     │
│  │ Reflective   │                                     │
│  │ 失败教训     │                                     │
│  └──────────────┘                                     │
└──────────────────────┬─────────────────────────────────┘
                       │ Redis 持久化
┌──────────────────────▼─────────────────────────────────┐
│                  Experience Loop(独立)                 │
│  抽取 → 存储 → 负迁移保护 → 注入 Planner prompt       │
└────────────────────────────────────────────────────────┘
```

**关键设计**:四层记忆**不共享数据结构**,每层是独立类,通过 `AgentMemorySystem` 聚合。Experience Loop **独立于四层记忆**,有自己的存储、检索、注入路径。

---

## 2. 基础数据单元:MemoryItem

所有 **长期记忆项** 用同一个 `MemoryItem` 结构(普通 class + 显式 `__init__`,**不是** `@dataclass`):

```python
class MemoryItem:
    def __init__(self, content, memory_type, importance=0.5,
                 metadata=None, embedding=None, ...):
        self.id = ...                          # 实际字段名是 id,不是 item_id
        self.content = content
        self.memory_type = memory_type         # short_term / long_term(working/reflective 不用它)
        self.importance = float
        self.metadata = metadata or {}         # 永远不会是 None
        self.embedding = embedding             # 仅 long_term 实际用
        self.created_at = datetime
        self.last_accessed = datetime
        self.access_count = int = 0
```

注意:
- **`WorkingMemory` / `ReflectiveMemory` 都不是 `MemoryItem` 实例**——它们是独立类,字段是普通 `dict`(state / task_stack / intermediate_results / variables)或 `{content, context, created_at}` 这种 `dict` 列表。
- **importance 只在 LongTermMemory 检索打分里作为最终乘子**(`final_score *= importance` 在 decay 之外另算,见 §3.2),**不**改变 half-life。
- `access_count` 在 ShortTermMemory.search / LongTermMemory.search / search_semantic 命中时由 `item.access()` 累加。

---

## 3. 四层记忆详解

### 3.1 ShortTermMemory — 会话内上下文

| 维度 | 设计 |
|---|---|
| **存储** | 进程内 list |
| **容量** | 滑动窗口,默认 20 条 (ShortTermMemory.max_size) / AgentMemorySystem.config["max_short_term"] |
| **检索** | `search(query, top_k)` 关键词扫描(命中后调用 item.access()) |
| **持久化** | 否,会话结束清空 |
| **用途** | 当前会话的对话历史,塞进 LLM system prompt |
| **代码位置** | `memory_service.py:85` |

**特点**:最轻量,纯 FIFO。不检索不排序,只是 LLM 调用前的"上下文缓存"。

### 3.2 LongTermMemory — 跨会话持久化

| 维度 | 设计 |
|---|---|
| **存储** | 进程内 defaultdict(短时不做持久化) + Redis 周期性快照(由 AgentMemorySystem._save_to_redis 打包成单个 key) |
| **检索方式** | 关键词打分(LSTM.search, 公式见下) 或 embedding cosine 相似度(LSTM.search_semantic, 公式见下) |
| **衰减** | 时间衰减,half-life = 24 小时 |
| **重要性加权** | `importance` 仅作为得分乘法因子,**不**改变 half-life |
| **持久化 key** | `agent:memory:{agent_id}` (整个 AgentMemorySystem 打包成一个 JSON;不含 org_id / user_id) |
| **代码位置** | `memory_service.py:123` |

**实际检索打分**:
```
# 关键词路径 (LongTermMemory.search)
score = 0.5 × overlap + 0.3 × decay_score + 0.2 × min(access_count/10, 1.0)

# embedding 路径 (LongTermMemory.search_semantic, 阈值 sim > 0.3)
score = 0.6 × cosine_similarity + 0.4 × decay_score
```

两条路径共用 `LongTermMemory.get_decay_score()`(只算时间衰减,不再乘 importance)。

**触发场景**:用户问了一个跟历史对话相关的问题,系统能"想起"上个月提过的内容。

### 3.3 WorkingMemory — 当前任务中间结果

| 维度 | 设计 |
|---|---|
| **存储** | 进程内 dict |
| **数据结构** | `state` + `task_stack` + `intermediate_results` + `variables` |
| **持久化** | 否,任务结束清空 |
| **代码位置** | `memory_service.py:311` |

**核心数据结构**:

```python
state: dict[str, Any]              # 步骤级 KV
task_stack: list[dict]             # 当前任务栈
intermediate_results: dict[str, Any]  # 中间结果(按 step_id)
variables: dict[str, Any]          # 模板变量
```

**关键方法**:
- `set_result(step_id, result)` — 存一步的产出
- `get_result(step_id)` — 后续步骤读依赖结果
- `push_task({step_id, description})` / `pop_task()`
- `set_variable(key, value)` — 模板解析

**作用**:PER 环路里 planner → executor → reflector 之间**真正传数据的载体**。executor 写,reflector 读。

### 3.4 ReflectiveMemory — 失败教训

| 维度 | 设计 |
|---|---|
| **存储** | 进程内 (随 AgentMemorySystem 一起打包到 Redis) |
| **数据结构** | `insights: list` + `patterns: list` + `lessons: list`(三段独立) |
| **检索** | 关键词子串匹配(`get_relevant_insights` 按 word 子串;`get_lessons_for_situation` 按 trigger 子串) — **不**用 embedding |
| **代码位置** | `memory_service.py:381` |

**核心方法**:
- `add_insight(insight, context)` — 高级洞察
- `add_lesson(lesson, trigger, solution)` — 失败教训

**和 Experience 的区别**(关键设计决策):

| 维度 | ReflectiveMemory | Experience |
|---|---|---|
| 抽取 | LLM 可能自动 | **手写 SCENARIO_RULES** |
| 元数据 | importance | **confidence + verified_count + avoid_for** |
| 负迁移保护 | 无 | 有 |
| 注入路径 | Phase 0 system prompt | Planner prompt |

**断点**:两者**没接起来**。`record_lesson` 写 reflective,但格式跟 Experience 对不上。

---

## 4. 聚合层:AgentMemorySystem

```python
class AgentMemorySystem:
    def __init__(self, agent_id: str = "default"):
        self.agent_id = agent_id           # 实际字段
        self.short_term = ShortTermMemory()
        self.long_term = LongTermMemory()
        self.working = WorkingMemory()
        self.reflective = ReflectiveMemory()
```

注:**`namespace` 不在 AgentMemorySystem 上**。冒号拼接的 `org_id:user_id:agent_id` 在 `AgentMemoryBridge.__init__` 里构造,只用来索引 `get_memory_system(namespace)` 的进程内单例字典,**不**参与 Redis 持久化 key。

**核心方法**:

```python
# 跨三层混合检索(默认是 short_term + long_term + reflective,不分层排序、append 后直接 results[:top_k])
async def recall(query, top_k=5, memory_types=None) -> list[dict]:
    results = []
    if memory_types is None:
        memory_types = ["short_term", "long_term", "reflective"]
    if "short_term" in memory_types:
        results.extend([item.to_dict() for item in self.short_term.search(query, top_k)])
    if "long_term" in memory_types:
        ...   # 优先 search_semantic(若 embedding_provider 已配),降级到 search
    if "reflective" in memory_types:
        results.extend(... reflective.get_relevant_insights(query))
        results.extend(... reflective.get_lessons_for_situation(query))
    return results[:top_k]   # 不做跨层排序

# 写入
async def remember(content, memory_type, importance, metadata)
async def store_experience(success, action, result, context)   # 写 long_term + (失败时) reflective.add_lesson
async def store_interaction(user_input, assistant_response)     # 两段都写 memory_type="short_term",**不**写 long_term
# record_lesson / record_interaction 在 bridge 层(memory_bridge.record_experience / record_lesson) — 不在 AgentMemorySystem API 上

# Embedding 注入(只在有 provider 时启用 search_semantic 路径)
def set_embedding_provider(fn)

# 持久化
async def _save_to_redis()
async def _load_from_redis()
```

---

## 5. MemoryBridge:接进 PER 环路

`backend/app/agent/memory_bridge.py` 是适配器,把 `AgentMemorySystem` 包成 PER 环路用得到的接口。

### 命名空间隔离

```python
namespace = f"{organization_id}:{user_id}:{agent_id}"
# 例: "3:42:default"
```

不同用户/不同 Agent persona 的记忆**完全隔离**,不会跨账号污染。

### Phase 0 记忆召回(loop.py 启动时调)

```python
async def get_context_for_query(self, query, top_k=5) -> str:
    memories = await self.system.recall(query, top_k=top_k)
    # 格式化成 markdown 段:
    # "## 相关记忆\n1. 上次分析财报时发现...\n2. ..."
    return formatted_string
```

这段文本会塞进 LLM 的 system prompt。

### 写入路径(每个 phase 触发)

| Phase | 调用 | 写入层 |
|---|---|---|
| Phase 2 每步完成 | `store_step_result()` | WorkingMemory |
| Phase 2 失败 | `record_experience(success, action, result)` | long_term(成功 / 失败统一),失败时额外 `reflective.add_lesson`(注意:不是 `ExperienceStore`,见 §6) |
| Phase 3 reflector | `record_insight()` / `record_lesson()` | ReflectiveMemory |
| Phase 4 完成 | `record_interaction(query, answer)` | ShortTerm + LongTerm |

### Embedding 注入

Embedding provider 在两个时机注入,**两个机制并存**:

```python
# 机制 A: 应用启动时一次性批量注入(dependencies.wire_memory_embedding_provider)
# 遍历当前 memory_systems 单例字典,对没有 _embedding_provider 的实例一次性 set_embedding_provider
# 见 backend/app/main.py:86 启动钩子 + backend/app/dependencies.py:92 实现
```

```python
# 机制 B: 每次 bridge 调用 get_context_for_query 时 lazy 注入(确保新创建的 AgentMemorySystem 也能用)
async def ensure_embedding(self):
    if self._embedding_ready: return
    pipeline = get_rag_pipeline()
    self.system.set_embedding_provider(pipeline.get_embedding)
    self._embedding_ready = True
# 见 backend/app/agent/memory_bridge.py:63
```

启动时(A)+ 首次 recall 时(B)双轨并存:**两路都会写同一个 `set_embedding_provider`**,幂等,不会重复。

---

## 6. Experience Loop:独立的"自我进化"系统

**位置**:`backend/app/agent/experience/`

跟四层记忆**完全不同**的独立存储系统。

### 数据结构

```python
class Experience:
    scenario: str           # 场景: cross_document / framework / web_search
    symptom: str            # 失败症状: only_one_document_used
    lesson: str             # 中文教训
    confidence: float       # 0.4-0.95
    verified_count: int     # 验证次数
    fail_count: int         # 失败次数
    applicable_to: list[str]  # 适用场景
    avoid_for: list[str]    # **负迁移保护**:禁用场景
    source_benchmark_case: str
    tags: list[str]
```

### 抽取机制

**手工规则映射**(SCENARIO_RULES):
```python
SCENARIO_RULES = {
    "cross_document": [
        {"symptom": "only_one_document_used", "lesson": "跨文档对比必须逐一查询..."},
        {"symptom": "no_comparison_made", "lesson": "..."},
    ],
    "framework": [
        {"symptom": "keywords_missing_swot", "lesson": "使用 SWOT 框架时,必须输出..."},
    ],
    # ...
}
```

`_detect_symptom(case)` 走启发式:扫关键词 + 关键词 missed 列表,匹配 symptom。

### 检索打分

**关键词 overlap + CJK bigram + 负迁移保护**:

```python
score = 0.0
# 1. scenario 命中 (+3.0)
if scenario_normalized in query:
    score += 3.0
# 2. 英文关键词 overlap (×2.0)
score += 2.0 * overlap_ratio
# 3. CJK bigram overlap (×1.5)
score += 1.5 * cjk_bigram_overlap_ratio

# 负迁移保护
if exp.avoid_for and any(s in query_scenarios):
    score *= 0.3   # 大幅降权
if exp.applicable_to and not any(s in query_scenarios):
    score *= 0.5   # 中幅降权
```

### 注入 Planner

`planner.py:236-245`:
```python
if self.config.enable_experience:
    exp_context = await store.format_for_planner(query, top_k=3)
    if exp_context:
        system_prompt += f"\n\n{exp_context}"
```

生成 markdown 段:
```
## 经验教训
以下是从历史失败中总结的经验教训,请在你的规划中参考:
1. [cross_document] 跨文档对比必须逐一查询每个目标文档...(置信度: 80%)
2. [framework] 使用 SWOT 框架时,必须输出优势/劣势/机会/威胁...
```

---

## 7. Embedding 与 Token 设计

### Embedding 模型

- **模型**:`EMBEDDING_MODEL`(默认 `embedding-3`,智谱 AI,OpenAI 兼容 API)
- **维度**:2048
- **批处理**:支持批量,带 retry
- **本地 fallback**:`ENABLE_LOCAL_EMBEDDING=true` 时改用 `LOCAL_EMBEDDING_MODEL`(默认 `nomic-embed-text`,Ollama)
  - **注意**:MemoryBridge 通过 RAG pipeline 间接注入 embedding,所以走的也是 `embedding_service.py` 这条分支——本地 / 在线会自动跟当前 `ENABLE_LOCAL_EMBEDDING` 一致
  - **维度可能不再 2048**——本地模型的输出维度由 `LOCAL_EMBEDDING_MODEL` 决定,需要重新校准 LongTerm 里的存量 embedding(否则 search_semantic 会因为维度不一致抛错)

### Token 估算

**实际代码** (`context.py:estimate_tokens`):
```python
CHARS_PER_TOKEN = 2.5
def estimate_tokens(text: str) -> int:
    if _ENCODER:
        return max(1, len(_ENCODER.encode(text)))
    return max(1, int(len(text) / 2.5))  # fallback 不再是 // 3
```

**fallback 逻辑**:`tiktoken` 失败时退化为字符数 / 2.5(CJK+EN 混排经验值)。

### 上下文窗口管理

```python
class ContextEngine:
    def __init__(self, max_tokens: int = 8000):
        self.max_tokens = max_tokens
        self.tail_window = 6  # 保留最近 6 条原样
```

**压缩策略**:
1. **pin system prompt**(永不裁)
2. **tail_window = 最近 6 条**原样保留
3. **中间消息** → LLM 摘要压缩(调 DeepSeek)
4. **fallback**:句子级截断

按 token 预算从大到小依次丢弃。

---

## 8. 持久化与命名空间

### Redis Key 命名

| 系统 | Key 格式 |
|---|---|
| ShortTermMemory | `{namespace}:short_term` |
| LongTermMemory | `{namespace}:long_term` |
| ReflectiveMemory | `{namespace}:reflective` |
| Experience Store | `agent:experience:store`(全局,不分 ns) |
| Execution Context(replay) | `agent:replay:{task_id}` |

**namespace 格式**:`{org_id}:{user_id}:{agent_id}`

**设计意图**:四层记忆按用户隔离,Experience 全局共享(它是"通用知识",不分用户)。

---

## 9. 当前断点与改进方向

这是 Capability Map 暴露的真实问题:

| 断点 | 影响 |
|---|---|
| **ReflectiveMemory ↔ ExperienceStore 未接通** | `record_lesson` 写的教训进不了 Experience 检索路径 |
| **Embedding 跨系统不共享** | RAG / LongTerm / Experience 三套独立 embedding 调用 |
| **Experience 抽取是手写规则** | 不是 LLM 自动,新场景无法自动积累 |
| **Confidence / Verified 未累积** | `verified_count` 字段存在但 runtime 不调 |
| **WorkingMemory 不持久化** | 任务崩溃后中间结果丢失 |

---

## 10. 面试可能问的问题

**Q1:为什么用四层而不是一层?**
A:每层的访问模式、容量、生命周期完全不同。ShortTerm 滑动窗口不检索、LongTerm 要 embedding、Working 是 KV 不持久化、Reflective 是结构化教训。强行合一会让简单事情复杂化。

**Q2:为什么 Experience 独立于四层记忆?**
A:Experience 是"学到的教训",需要 confidence / verified_count / avoid_for 这些额外字段,跨用户共享,跟个人记忆不同语义。强行塞进 MemoryItem 会污染数据模型。

**Q3:Embedding 注入而不是写死依赖有什么好处?**
A:`set_embedding_provider(fn)` 让 LongTermMemory 不直接依赖 RAG pipeline,测试时可以传 mock embedding 函数,生产时由 `main.py` 启动时一次性 wire,降低耦合。

**Q4:为什么 WorkingMemory 不持久化?**
A:WorkingMemory 是单次任务内的中间结果,如果失败要重跑,持久化反而会保留脏数据。Phase 4 的 final output 才该持久化,这是 ExecutionContext 的职责(Redis 7 天 TTL)。

**Q5:四层记忆的检索是串行还是并行?**
A:`recall()` 默认串行遍历各层,按统一打分公式排序。原因:四层召回量都很小(各 top_k=5),串行开销可控,实现简单。如果未来某层召回量爆炸(比如 long_term 扩到上千条),再考虑每层独立召回 + 并行合并。

**Q6:为什么 LongTerm 用 cosine similarity 而不是 dot product?**
A:2048 维向量,如果没做 L2 归一化,cosine 比 dot 更稳。代价是要多算一次范数,值得。

**Q7:ContextEngine 压缩策略为什么优先 LLM 摘要?**
A:截断会丢失语义连贯性,LLM 摘要能保留核心信息。fallback 到截断是因为 LLM 调用本身有成本和延迟,要做兜底。

**Q8:Experience 的负迁移保护怎么实现?**
A:每个 Experience 维护 `applicable_to`(适用场景)+ `avoid_for`(禁用场景)。检索时如果 query 场景命中 avoid_for,score ×0.3 降权;如果不命中 applicable_to,score ×0.5 降权。这比硬过滤更灵活,允许一个 lesson 在边缘场景模糊应用。

---

## 11. 文件索引

| 关注点 | 文件:行 |
|---|---|
| MemoryItem 定义 | `backend/app/services/memory_service.py:16` |
| ShortTermMemory | `memory_service.py:85` |
| LongTermMemory | `memory_service.py:123` |
| WorkingMemory | `memory_service.py:311` |
| ReflectiveMemory | `memory_service.py:381` |
| AgentMemorySystem | `memory_service.py:453` |
| get_memory_system factory | `memory_service.py:737` |
| MemoryBridge | `backend/app/agent/memory_bridge.py:26` |
| Phase 0 召回 | `memory_bridge.py:78-121` |
| Embedding 注入 | `memory_bridge.py:63-74` |
| Experience 数据模型 | `backend/app/agent/experience/models.py` |
| Experience 存储 | `backend/app/agent/experience/store.py` |
| Experience 抽取 | `backend/app/agent/experience/extractor.py` |
| Experience 注入 | `backend/app/agent/planner.py:236-245` |
| 上下文窗口 | `backend/app/agent/context.py:40` |
| Token 估算 | `backend/app/agent/context.py:24` |
| Embedding 配置 | `backend/app/core/config/ai.py` |

---

## 12. 四层记忆到底存哪 — 一个常被问的问题

**关键事实**:**四层记忆一个都不存 MySQL,全部存 Redis 或进程内**。MySQL 存的是另一个范畴的数据。这一节专门讲清楚这件事。

### 12.1 存储介质总览

| 层级 | 持久化 | Key 命名 | 序列化 |
|---|---|---|---|
| ShortTerm | **进程内**(不持久化) | — | Python object |
| LongTerm | **Redis** | `{namespace}:long_term` | JSON |
| Working | **进程内**(不持久化) | — | Python dict |
| Reflective | **Redis** | `{namespace}:reflective` | JSON |
| Experience | **Redis + 本地 JSON** | `agent:experience:store`(全局) | JSON |
| ExecutionContext | **Redis 7天 TTL** | `agent:replay:{task_id}` | JSON |

**namespace 格式**:`{org_id}:{user_id}:{agent_id}`(四层记忆按用户隔离;Experience 全局共享,因为它是"通用教训"不在账号维度)

### 12.2 为什么四层记忆不上 MySQL?

面试常问。三个原因:

**原因一:访问模式不同**
- Redis 单次读 < 1ms,MySQL 即使有索引也要 5-20ms
- ShortTerm / Working 是热路径,**每轮 LLM 调用前都要读**,延迟敏感
- MySQL 上你会想用连接池,但每个 Agent 请求都过池子,反而慢

**原因二:数据模型不匹配**
- MySQL 是关系型,要建表、字段、关联
- MemoryItem 是**半结构化**(embedding 可选、metadata 是 dict、importance 是浮点)
- 强行建表要预留 JSON 字段,反而不如 Redis 的 JSON 直接

**原因三:生命周期不同**
- ShortTerm / Working **不该跨进程持久化**——会话结束或崩溃就该清
- Redis 序列化是按 key,清就是 `DEL`,MySQL 是按行,要 DELETE WHERE
- Redis 的 TTL 是天然的"自动过期",MySQL 要 cron 清理

### 12.3 那 MySQL 到底存什么?

**MySQL 存的是"业务实体",不是"Agent 运行时记忆"**。看 `backend/app/models/`,22 张表,我按业务域分类:

#### 用户与组织域
- **`users`** — 用户账号、密码哈希(hashed_password)、API Key、avatar、个人偏好
- **`organizations`** — 组织树(parent_id 自引用,支持多层级)
- **`user_organization`** — 用户-组织 M:N 关联
- **`roles` + `permissions`** — RBAC 角色权限系统(23 个 PermissionType 常量)
- **`role_permission_association`** — 角色-权限 M:N
- **`user_organization_role_association`** — 用户-组织-角色 三方关联(支持一个用户在不同组织有不同角色)

#### 知识库域
- **`documents`** — 文档元数据(filename / md5_hash / status / chunk_count / org_id / uploaded_by)
- **`document_chunks`** — 分块文本(chunk_text / chunk_length / page_number / section_title / embedding_id)
  - 这里的 `embedding_id` 是关联到 ES 的,实际向量**在 ES 不在 MySQL**
- **`document_tags` + `tags`** — 标签系统(M:N)
- **`knowledge_processing_jobs`** — 文档处理任务队列(异步摄取的状态机:PENDING → PARSING → INDEXED)

#### 聊天与会话域
- **`chat_sessions`** — 会话元数据(user_id / title / status / settings JSON)
- **`chat_messages`** — 消息记录(content / message_type USER|ASSISTANT|SYSTEM / meta_data / feedback / feedback_note)
  - `feedback` 字段用于**用户对 RAG 回答的反馈打分**,会自动回灌到 RAG 评估
  - **注意**:memory_bridge.record_interaction 写的是 AgentMemorySystem.episodic,**不是**这张表 —— 又是设计上的断点

#### 工作流域
- **`workflows`** — 工作流定义(name / flow_data JSON / is_active)
- **`workflow_executions`** — 执行记录(input_data / output_data / node_results JSON / status / error_message)
- **`node_definitions`** — 节点元数据(node_type / category llm|tool|io|logic / input_schema / output_schema)

#### 配置与审计域
- **`prompt_templates` + `prompt_template_versions`** — 提示词模板及版本历史(content / version / change_note)
- **`token_usage_records`** — Token 消耗记录(user_id / org_id / model / source rag_chat|agent|tool_call / input_tokens / output_tokens / cost_usd) —— **成本控制的核心数据**
- **`user_settings`** — 用户配置(JSON)
- **`system_manuals`** — 系统手册
- **`notifications`** — 站内通知
- **`user_login_sessions`** — 设备登录会话(token / device_info / expires_at)
- **`user_activity_logs`** — 用户行为审计(action / target_type / target_id / ip / user_agent)

### 12.4 一个反直觉的事实

如果你看 `chat_messages` 表和 `memory_bridge.record_interaction()`,会发现:

```python
# memory_bridge.py:184
async def record_interaction(self, user_input, assistant_response):
    await self.system.store_interaction(user_input, assistant_response)
    # ↑ 写 LongTermMemory(Redis)
    # ↑ 而非 ChatMessage(MySQL)
```

也就是说,**用户问过的每一句话 + AI 每一句回答,会同时存在两处**:
- 业务侧:写入 `chat_messages` MySQL(供前端列表展示)
- 记忆侧:写入 `LongTermMemory` Redis(供未来 recall)

**两边数据会发散**——这是设计断点。Recall 时调 LongTermMemory,但前端显示的是 MySQL 的 chat_messages,**两边可能查到的"历史"不一致**。修复要等 RunReport 验证完再讨论。

### 12.5 选型决策树(面试可用)

```
Q: 这数据要跨服务读吗?
  ├─ 是 → MySQL(其他服务也要查)
  │       例: documents / chat_messages / workflows
  └─ 否 → Q: 需要持久化吗?
            ├─ 否 → 进程内 list/dict
            │       例: ShortTerm / WorkingMemory
            └─ 是 → Q: 读延迟敏感吗?
                      ├─ 是(< 5ms)→ Redis
                      │       例: LongTerm / Reflective / Experience / ExecutionContext
                      └─ 否 → MySQL
```

**对照 DocMind**:用户问的"业务实体"(用户/文档/聊天记录)走 MySQL;Agent 运行时的"瞬时记忆"(四层/Experience)走 Redis + 进程内。**两套存储关注的不是同一类问题**。

### 12.6 面试可能被追问的问题

**Q:"Redis 挂了怎么办?"**
> "短期数据(ShortTerm/Working)丢了就丢了,会话结束本来就该清。长期记忆(Redis 不可用)会跳过 retrieval,Agent 退化成无记忆模式,不会崩。Experience Store 有本地 JSON fallback。ExecutionContext 走 Redis 没有本地 fallback,所以 Redis 真挂了,replay 会失败——这是已知的运维断点。"

**Q:"为什么 Experience 不按用户分?"**
> "Experience 是'通用教训',多个用户都能受益。如果按用户隔离,lesson 库不会收敛,永远是个体小池子。代价是失去个性化,但对当前阶段更划算。"

**Q:"如果你重做,会怎么改?"**
> "把 `chat_messages` 的内容反向同步到 LongTermMemory,或干脆让 LongTermMemory 直接读写 MySQL 的 chat_messages,避免双写发散。WorkingMemory 可以考虑加 Redis 持久化用于崩溃恢复,但默认就丢,因为含执行中间态、崩溃时数据未必一致。"

---

## 13. 双写发散问题(架构断点 #2)

`memory_service.py` 的 `store_interaction` 和 endpoints 的 `chat_messages` 写入是**两套独立路径**:

| 路径 | 写什么 | 谁调用 |
|---|---|---|
| `bridge.record_interaction()` | LongTermMemory (Redis) | Agent 链路 |
| `chat.py` endpoint | chat_messages (MySQL) | API 路由 |

**后果**:同一轮对话,Redis 里和 MySQL 里的 content 可能不一致(失败重试、系统时间差异、用户编辑等)。

**修复方向**:RunReport 验证完成后,考虑合二为一——要么全走 MySQL(用 MySQL JSON 列存 embedding),要么全走 Redis(用 Redis Stream 存消息流)。**当前不动**,等真实反馈决定优先级。