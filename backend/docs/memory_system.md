# DocMind 记忆系统(Memory System)

> 面试向系统设计文档。覆盖架构、四层记忆、检索链路、Embedding 与 Token 设计、Experience 单独系统、当前断点。

---

## 一句话定位

DocMind 的记忆系统是一个**双层架构**:底层是按用途切分的四层记忆(短期/长期/工作/反思),上层是 **MemoryBridge** 适配器把记忆接进 PER 环路。整套系统最终统一落到 Redis 持久化,Embedding 来自 RAG pipeline 注入,跨用户/Agent 命名空间隔离。

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

所有层(除 Experience)都用同一个 `MemoryItem` 结构:

```python
@dataclass
class MemoryItem:
    content: str                    # 内容文本
    memory_type: str                # short_term / long_term / working / reflective
    importance: float = 0.5         # 重要性 0-1
    metadata: dict | None           # 任意元数据
    embedding: list[float] | None   # 2048 维向量
    created_at: datetime
    last_accessed: datetime
    access_count: int = 0
    item_id: str | None
```

设计要点:
- **embedding 字段是可选的**:只有 long_term / reflective 实际使用
- **importance 影响衰减速度**:值越高,衰减越慢
- **access_count 追踪热度**:被检索命中的次数

---

## 3. 四层记忆详解

### 3.1 ShortTermMemory — 会话内上下文

| 维度 | 设计 |
|---|---|
| **存储** | 进程内 list |
| **容量** | 滑动窗口,默认 50 条 |
| **检索** | 无,按时间序遍历 |
| **持久化** | 否,会话结束清空 |
| **用途** | 当前会话的对话历史,塞进 LLM system prompt |
| **代码位置** | `memory_service.py:85` |

**特点**:最轻量,纯 FIFO。不检索不排序,只是 LLM 调用前的"上下文缓存"。

### 3.2 LongTermMemory — 跨会话持久化

| 维度 | 设计 |
|---|---|
| **存储** | Redis(主) + 进程内 cache |
| **检索方式** | 关键词打分 + Embedding cosine 相似度 |
| **衰减** | 时间衰减,half-life = 24 小时 |
| **重要性加权** | `importance` 越高,衰减越慢 |
| **持久化 key** | `{namespace}:long_term` |
| **代码位置** | `memory_service.py:123` |

**检索打分公式**:
```
score = 0.6 × embedding相似度 + 0.2 × 时间衰减得分 + 0.2 × importance
```

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
| **存储** | Redis |
| **数据结构** | `insights: list` + `lessons: list` |
| **检索** | 关键词 + embedding |
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
    def __init__(self, namespace: str):
        self.namespace = namespace          # "org_id:user_id:agent_id"
        self.short_term = ShortTermMemory()
        self.long_term = LongTermMemory()
        self.working = WorkingMemory()
        self.reflective = ReflectiveMemory()
```

**核心方法**:

```python
# 跨四层混合检索
async def recall(query, top_k=5, memory_types=None) -> list[dict]:
    results = []
    for mem_type in (memory_types or ["long_term", "reflective"]):
        layer = self._get_layer(mem_type)
        hits = layer.search(query, top_k=top_k)
        results.extend(hits)
    return sorted(results, key=lambda x: x["score"], reverse=True)[:top_k]

# 写入
async def remember(content, memory_type, importance, metadata)
async def store_experience(success, action, result, context)
async def store_interaction(user_input, assistant_response)
async def record_lesson(lesson, trigger, solution)

# Embedding 注入
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
| Phase 2 失败 | `record_experience(success, action, result)` | episodic(非 ExperienceStore) |
| Phase 3 reflector | `record_insight()` / `record_lesson()` | ReflectiveMemory |
| Phase 4 完成 | `record_interaction(query, answer)` | ShortTerm + LongTerm |

### Embedding 注入

```python
async def ensure_embedding(self):
    if self._embedding_ready:
        return
    from app.dependencies import get_rag_pipeline
    pipeline = get_rag_pipeline()
    self.system.set_embedding_provider(pipeline.get_embedding)
    self._embedding_ready = True
```

启动时一次性 wire,后续 recall 走语义搜索就有 embedding 可用。

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

- **模型**:`embedding-3`(智谱 AI,OpenAI 兼容 API)
- **维度**:2048
- **批处理**:支持批量,带 retry
- **本地 fallback**:支持 Ollama(`ENABLE_LOCAL_EMBEDDING`)

### Token 估算

```python
def estimate_tokens(text: str) -> int:
    try:
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except:
        return len(text) // 3  # 中英文混合经验值
```

**fallback 逻辑**:`tiktoken` 失败时退化为字符数 / 3。

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