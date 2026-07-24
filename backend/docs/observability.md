# DocMind 可观测性体系(面试级回答)

> 一份覆盖"指标怎么采、LLM 怎么追、Agent 怎么回放、缺口在哪"的面试向讲解。三套指标体系(Prometheus / Langfuse / OpenTelemetry)+ Agent 自建 TraceStore + ExecutionContext 飞行记录器 + 用户反馈闭环断点。

---

## 0. 一句话定位

DocMind 的可观测性是**三层并联 + 两层串联**:**Prometheus 抓业务指标、Langfuse 抓 LLM 调用、OpenTelemetry 抓分布式 trace**;**TraceStore 串起单次 Agent run 的步骤、ExecutionContext 串起单次 run 的决策快照**。但**用户反馈闭环(feedback → 自动调参)目前是断的**,这是最大缺口。

---

## 1. 完整可观测性体系全景

```
用户 / API 调用
       │
       ▼
┌──────────────────────────────────────────────────────────┐
│              FastAPI Middleware                            │
│  - RequestIDMiddleware(注入 X-Request-ID)                │
│  - MetricsCollector(内存 + Prometheus)                    │
└────────────────────┬─────────────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────────────────┐
│              Agent / RAG Pipeline                          │
│                                                              │
│  Langfuse ──► LLM Span(5 处埋点)                          │
│  OTel ──────► 跨服务 trace                                 │
│  Prometheus ► Counter/Histogram/RAG/Agent 指标           │
│  TraceStore ► Planner/Executor/Reflector 步骤             │
│  ExecutionContext ► 单 run 全量(Redis 7 天)             │
└────────────────────┬─────────────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────────────────┐
│              存储层                                          │
│                                                              │
│  Redis: 指标缓存 / ExecutionContext / Memory                │
│  MySQL: chat_messages.feedback / token_usage_records        │
│  Prometheus: 时序数据                                       │
│  Langfuse: LLM 调用数据                                     │
│  Grafana: 可视化                                           │
└──────────────────────────────────────────────────────────┘
```

---

## 2. Prometheus — 业务指标(最丰富)

**位置**:`backend/app/core/prometheus.py`(170 行)

### 2.1 设计原则:lazy no-op 默认

```python
class _LazyMetric:
    """默认是 no-op,避免 prometheus_client Windows multiprocessing deadlock"""
    def __init__(self, name, doc, labelnames=()):
        self._metric = None
        if PROMETHEUS_ENABLED:
            self._metric = Counter(name, doc, labelnames)

    def inc(self, **labels):
        if self._metric:
            self._metric.labels(**labels).inc()
```

**关键工程权衡**:Prometheus **默认关闭**。`prometheus_client` 在 Windows 多进程下会死锁,开发/测试环境跑也会出问题。生产部署时显式开启。

### 2.2 RAG 指标清单

| 指标 | 类型 | Labels | 用途 |
|---|---|---|---|
| `RAG_RETRIEVAL_TOTAL` | Counter | `strategy` (keyword_only/hybrid/hybrid_hyde) | 各策略调用次数,看用户问题分布 |
| `RAG_RETRIEVAL_ERRORS` | Counter | `error_type` | 检索失败原因分布 |
| `RAG_RETRIEVAL_HITS` | Counter | `doc_count` | 召回文档数分布 |
| `RAG_RETRIEVAL_LATENCY` | Histogram | `strategy` | 检索延迟 P50/P95/P99 |
| `RAG_CACHE_HITS` | Counter | `layer` (exact/semantic) | 缓存命中率 |
| `RAG_CACHE_MISSES` | Counter | — | 缓存 miss 数 |
| `RAG_RERANK_TOTAL` | Counter | `model` | 各 rerank 模型调用次数 |
| `RAG_RERANK_LATENCY` | Histogram | — | 重排延迟 |
| `RAG_GROUNDED_TOTAL` | Counter | `grounded` (true/false) | **答案是否引证,防幻觉指标** |
| `RAG_INTENT_TOTAL` | Counter | `intent` | 意图分类分布 |
| `RAG_ADAPTIVE_TOTAL` | Counter | `strategy` | Adaptive RAG 路由分布 |
| `RAG_EVAL_SCORE` | Histogram | `metric` | 离线评估分数 |

### 2.3 Agent 指标清单

| 指标 | Labels | 用途 |
|---|---|---|
| `AGENT_PLANNING_LATENCY` | — | 规划阶段延迟 |
| `AGENT_EXECUTION_STEPS` | — | 平均执行步数 |
| `AGENT_TOOL_CALLS` | `tool_name, status` | 工具调用分布 + 失败率 |
| `AGENT_REFLECTION_LATENCY` | — | 反思阶段延迟 |
| `AGENT_MEMORY_RECALLS` | `result` (hit/miss) | 记忆召回命中率 |
| `AGENT_FEEDBACK` | `rating` | 用户反馈分布 |

### 2.4 暴露端点

**位置**:`main.py:448-482` `/metrics`

```python
@app.get("/metrics", response_class=PlainTextResponse)
async def metrics():
    return get_prometheus_metrics()
```

输出格式:`text/plain; version=0.0.4`,Prometheus / Grafana 直接抓。

### 2.5 与 Grafana 集成

**位置**:
- `monitoring/grafana-dashboard.json`(**18 个面板**)
- `deploy/monitoring/grafana/provisioning/dashboards/rag_dashboard.json`

**Grafana 部署**(`deploy/monitoring/docker-compose.monitoring.yml`):

```yaml
prometheus:
  image: prom/prometheus
  volumes:
    - ./prometheus/prometheus.yml:/etc/prometheus/prometheus.yml

grafana:
  image: grafana/grafana
  provision:
    - datasources/datasource.yml
    - dashboards/dashboards.yml
```

**prometheus.yml 抓取配置**:
```yaml
scrape_configs:
  - job_name: 'docmind-backend'
    static_configs:
      - targets: ['backend:8000']
    metrics_path: '/metrics'
    scrape_interval: 15s
```

---

## 3. Langfuse — LLM 全链路追踪

**位置**:`backend/app/agent/observability.py`(135 行)

### 3.1 初始化(懒加载)

```python
def get_langfuse():
    global _langfuse
    if _langfuse is None:
        if not settings.LANGFUSE_PUBLIC_KEY:
            return None  # 没配 key 就关掉
        _langfuse = Langfuse(public_key=..., secret_key=..., host=...)
    return _langfuse
```

### 3.2 埋点 5 处

| 位置 | as_type | 文件 |
|---|---|---|
| `planner.plan()` | `"planning"` span | `planner.py` |
| `executor._execute_step_once()` | `"execute_step"` span | `executor.py:326` |
| `tool_registry.execute()` | `"tool_call"` tool | `registry.py:95` |
| `reflector.reflect()` | `"reflection"` span | `reflector.py` |
| `memory_bridge.get_context_for_query()` | `"memory_recall"` tool | `memory_bridge.py:78` |

### 3.3 上下文管理器

```python
@contextmanager
def trace_span(name, **kwargs):
    lf = get_langfuse()
    if lf:
        with lf.span(name=name, **kwargs) as span:
            yield span
    else:
        yield None  # 没配 Langfuse 就 no-op
```

### 3.4 看什么?

Langfuse Dashboard 上能看:
- 每次 LLM 调用的 **prompt 原文 + completion 原文**
- **Token 消耗**(input/output)
- **延迟**
- **嵌套 span 树**:plan → execute_step → tool_call → LLM synthesis
- **用户 feedback**(可绑定到 trace)

**这是调优 LLM 行为的金矿**——比日志好读,比 OpenTelemetry 聚焦 LLM 维度。

### 3.5 缺点

- **外部依赖**:需要 Langfuse 服务(或自托管),开发时不开
- **数据出域**:prompt 里可能含敏感信息,企业部署要慎用
- **配额**:Langfuse Cloud 有免费额度,超出收费

---

## 4. OpenTelemetry — 分布式追踪

**位置**:`backend/app/core/tracing.py`(47 行)

### 4.1 初始化

```python
def setup_opentelemetry(app):
    if not settings.ENABLE_TRACING:
        return
    exporter = OTLPSpanExporter(endpoint="localhost:4317", insecure=True)
    provider = TracerProvider()
    provider.add_span_processor(BatchSpanProcessor(exporter))
    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider)
```

### 4.2 服务名 + 端点

- 服务名:`rag_backend`
- OTLP 端点:`OTLP_ENDPOINT`(默认 `localhost:4317`)
- 传输:OTLP gRPC

### 4.3 vs Langfuse

| | Langfuse | OpenTelemetry |
|---|---|---|
| **粒度** | LLM 维度 | 通用服务维度 |
| **埋点成本** | 5 处精心埋 | 自动 instrument |
| **查询能力** | LLM 友好(看 prompt) | 跨服务链路 |
| **数据出域** | 是 | 看 OTLP 配置 |

**两套可以共存**:OpenTelemetry 抓基础设施 span,Langfuse 抓 LLM span。

---

## 5. Agent TraceStore(自建)

**位置**:`backend/app/agent/tracing.py`(382 行)

### 5.1 数据模型

```python
@dataclass
class StepExecutionRecord:
    step_id: str
    status: str  # success/failure/skipped
    duration_ms: float
    semantic_type: str  # read_only/mutating
    retry_strategy: str
    risk_level: str
    parallel_group: int
    retry_count: int
    fallback_used: bool
    tool_calls: list[ToolCallRecord]

@dataclass
class ExecutionTrace:
    query: str
    plan_id: str
    planner_tool_hints: list[str]  # Planner 推荐的工具
    executor_tool_calls: list[str]  # Executor 实际调用的
    steps: list[StepExecutionRecord]
    created_at: datetime
```

**亮点**:`planner_tool_hints` vs `executor_tool_calls` 的 diff,这是评估"Planner 推荐的工具准不准"的关键数据。

### 5.2 TraceStore(进程内 ring buffer)

```python
class TraceStore:
    def __init__(self, max_size=2000):
        self._traces = deque(maxlen=max_size)
    
    def record(self, trace: ExecutionTrace):
        self._traces.append(trace)
    
    def query(self, success=None, tool=None, category=None):
        # 多维度过滤查询
        ...
    
    def get_failure_stats(self) -> dict:
        """统计每个工具的失败率"""
        ...
    
    def get_semantic_effectiveness(self) -> dict:
        """重试成功率、fallback 成功率"""
        ...
```

### 5.3 局限

- **进程内**——重启就丢,多实例不共享
- **无持久化**——没有写 Redis 或 MySQL
- **未来方向**:能持久化到 ClickHouse / TimescaleDB,做时间序列分析

---

## 6. ExecutionContext(单次 run 全量快照)

**位置**:`backend/app/agent/exec_context.py:191-253`

### 6.1 持久化

```python
async def save(self, ttl=86400*7):
    """存 Redis,7 天 TTL,本地 JSON fallback"""
    key = f"agent:replay:{self.task_id}"
    await redis_client.setex(key, ttl, json.dumps(self.to_dict()))
```

### 6.2 包含什么

```python
{
    "task_id": "abc123",
    "query": "对比 A 和 B",
    "plan_summary": "...",
    "steps": [{step_id, description, tool, status, duration_ms, error}],
    "findings": [{source_step, content, tool, confidence}],
    "decisions": [{phase, action, reasoning}],  # ← 决策轨迹
    "failures": ["step 3 timeout"],
    "duration_ms": 45230,
    "total_tokens": 8721,
}
```

### 6.3 Replay(`backend/app/agent/replay/engine.py`)

```bash
python -m app.agent.replay <task_id>
python -m app.agent.replay --diff <task_id_a> <task_id_b>
```

**作用**:飞行记录器,能回放任意一次 run 的完整决策 + 执行轨迹。**对比功能支持 A/B diff**。

---

## 7. RAG 评估指标(离线)

**位置**:`backend/app/rag/evaluator.py`(227 行)

### 7.1 三个核心指标

```python
class RAGEvaluator:
    async def evaluate(self, query, response, contexts):
        return {
            "faithfulness": 0.85,    # 答案是否被上下文支撑
            "relevancy": 0.92,       # 答案对问题的相关度
            "context_precision": 0.78  # 检索到的文档里相关的比例
        }
```

### 7.2 Faithfulness(防幻觉核心)

**判定方法**:LLM-as-judge,问"答案里的每句话,是否能从上下文里找到支撑"。

```python
prompt = """
给定上下文:
{contexts}

答案:
{response}

判断:答案的每句话是否都被上下文支撑?
输出 0-1 分。
"""
```

### 7.3 API 端点

```
GET  /api/v1/monitoring/rag-eval          # 评估最新一批
POST /api/v1/monitoring/rag-eval          # 提交评估
GET  /api/v1/monitoring/rag-eval/{id}     # 单次评估详情
```

---

## 8. 用户反馈闭环(断点)

### 8.1 数据已经采集了

`chat_messages.feedback` 字段(0/1,带 `feedback_note`),用户每次打分都写 MySQL。

### 8.2 但没回灌

**问题**:
- 反馈数据在 MySQL,没人读它
- 没有"差评 query 二次检索"机制
- 没有"反馈 → 调参"自动化

### 8.3 改进方向(待 RunReport 验证后再讨论)

```
用户反馈差评
  ↓
查询哪些 chunk 被引用?
  ↓
这些 chunk 是否真的相关?→ 调 reranker 阈值
  ↓
query 改写是否启用?→ 调 query_processor 路由
```

**这是 RunReport 验证完成后的下一刀方向**——但当前不碰。

---

## 9. 实时查询的指标清单

面试时如果被问"你们怎么监控 RAG 质量",可以这么说:

### 9.1 实时业务指标

```promql
# 检索平均延迟
histogram_quantile(0.95, RAG_RETRIEVAL_LATENCY)

# 缓存命中率
RAG_CACHE_HITS / (RAG_CACHE_HITS + RAG_CACHE_MISSES)

# 各策略使用比例
sum by(strategy)(RAG_RETRIEVAL_TOTAL)

# 工具失败率(按工具)
sum by(tool_name)(AGENT_TOOL_CALLS{status="error"}) 
  / sum by(tool_name)(AGENT_TOOL_CALLS)

# 答案引证率(grounded)
RAG_GROUNDED_TOTAL{grounded="true"} / RAG_GROUNDED_TOTAL
```

### 9.2 离线质量指标

```python
rag_evaluator.evaluate(
    query, llm_response, retrieved_contexts
) → {
    "faithfulness": 0.85,
    "relevancy": 0.92,
    "context_precision": 0.78
}
```

### 9.3 LLM 调用追踪(Langfuse)

每次调 LLM 都记:
- 输入 prompt 完整文本
- 输出 completion 完整文本
- Token 数(input + output)
- 延迟
- 嵌套关系(plan → tool_call → synthesis)

### 9.4 Agent 决策回放(ExecutionContext)

任意一次 run 可回放:
- 每个 step 用了什么工具、为什么
- 失败原因
- reflector 怎么评

---

## 10. 关键文件索引

| 关注点 | 文件 |
|---|---|
| Prometheus 指标 | `backend/app/core/prometheus.py` |
| Langfuse 集成 | `backend/app/agent/observability.py` |
| OpenTelemetry | `backend/app/core/tracing.py` |
| Agent TraceStore | `backend/app/agent/tracing.py` |
| ExecutionContext | `backend/app/agent/exec_context.py` |
| RAG Evaluator | `backend/app/rag/evaluator.py` |
| Grafana 面板 | `monitoring/grafana-dashboard.json` |
| Prometheus 配置 | `backend/config/prometheus.yml` |
| 监控 API 端点 | `backend/app/api/v1/endpoints/monitoring.py` |
| Replay 引擎 | `backend/app/agent/replay/engine.py` |

---

## 11. 运行监控的工程权衡

### 11.1 Prometheus 默认关闭

```python
if PROMETHEUS_ENABLED:
    self._metric = Counter(...)
else:
    self._metric = None  # no-op
```

**为什么**:`prometheus_client` 在 Windows 多进程下死锁。开发环境跑会卡,所以默认关。生产部署时开。

### 11.2 Langfuse 默认关闭

```python
def get_langfuse():
    if not settings.LANGFUSE_PUBLIC_KEY:
        return None
```

**为什么**:没 API key 时不能强依赖,必须能优雅退化。

### 11.3 TraceStore 进程内

```python
self._traces = deque(maxlen=2000)
```

**为什么**:多实例不共享,但单机 2000 条够分析趋势。**未来需要 ClickHouse / TimescaleDB**。

### 11.4 ExecutionContext 7 天 TTL

```python
async def save(self, ttl=86400*7):  # 7 天
```

**为什么**:够回放历史 run,但不无限堆积。Redis 是按 key 自动过期,这个设计很干净。

---

## 12. 监控设计模式提炼

### 12.1 三套体系的分工

```
Prometheus  → "这件事发生了多少次?有多快?"(业务指标)
Langfuse    → "LLM 调用的内容是什么?花了多少钱?"(LLM 维度)
OpenTelemetry → "跨服务调用链路是怎样的?"(基础设施)
```

**互不重叠,各有侧重**。同时启用会获得完整观测能力。

### 12.2 四层采集粒度

```
指标(数字)        ← Prometheus / Grafana
  ↓
Span(调用链)      ← OpenTelemetry / Langfuse
  ↓
Trace(单次 Agent) ← TraceStore
  ↓
Context(单 run 全量) ← ExecutionContext(可回放)
```

**从粗到细,从数字到细节**。日常看 Grafana,排查看 Langfuse,深度回放看 ExecutionContext。

### 12.3 反馈闭环设计模式

```
事件发生 → 埋点 → 持久化 → 仪表盘 → 告警
  ↑                                         │
  └────────────── 自动调参(若接入) ←────────┘
```

**DocMind 当前状态**:前四步都做了,**最后一步没接**。这是最大缺口。

---

## 13. 面试题预设回答(8 题)

### Q1:"RAG 怎么评估效果?"

> "三层评估:**业务指标(Prometheus)看吞吐和延迟、质量指标(Langfuse)看 LLM 调用细节、离线评估(RAGEvaluator)看 faithfulness/relevancy/context_precision**。最终用户反馈通过 chat_messages.feedback 字段采集,但目前没有自动回灌到检索参数。"

### Q2:"监控上有什么指标你重点看?"

> "P95 检索延迟、缓存命中率、答案 grounded 率、工具失败率。前两个是性能指标,后两个是质量指标。**grounded 率是最关键的**——它直接反映 LLM 编造答案的频率。"

### Q3:"Langfuse 和 OpenTelemetry 有什么区别?"

> "Langfuse 专注 LLM 维度,能看 prompt/completion/token/cost;OpenTelemetry 是通用分布式 tracing,覆盖所有服务调用。两者能共存:OTel 抓基础设施,Langfuse 抓 LLM。"

### Q4:"怎么做 A/B 测试?"

> "诚实说:**没做过完整线上 A/B**。我们有离线 benchmark(30 题)对比 5 轮实验,但线上对照实验没跑过。这是行业普遍状态,不是 DocMind 独有。RunReport 验证完应该先做用户研究,再做 A/B。"

### Q5:"怎么发现检索质量下降?"

> "看 Grafana 三个面板:1) RAG_GROUNDED_TOTAL 下降 → 答案开始幻觉;2) RAG_CACHE_HITS 下降 → 缓存失效;3) RAG_RETRIEVAL_LATENCY P95 上升 → ES 索引或 LLM 慢。任一指标突变都该告警。"

### Q6:"Token 消耗怎么监控?"

> "Prometheus 里有 token_usage 表,每条 LLM 调用都记 input/output tokens 和 cost。Langfuse 也统计。**token 成本是 RAG 系统最容易被忽略的 OPEX**,监控必须做。"

### Q7:"用户反馈怎么用?"

> "当前只在 MySQL 里存,没有自动回灌。这是断点。**理想流程**:差评 → 分析该 query 检索了什么 chunk → 这些 chunk 真的相关吗? → 调 reranker 阈值或 query 改写权重。RunReport 验证完成后下一步会做这个。"

### Q8:"Grafana 上有什么面板?"

> "18 个面板覆盖:RAG 检索延迟分桶、缓存命中率、token 消耗、LLM 调用频次、工具失败率、用户活跃度、token 成本趋势。具体面板在 `monitoring/grafana-dashboard.json`。"

---

## 14. 改进优先级(诚实版)

| 优先级 | 缺口 | 影响 | 工作量 |
|---|---|---|---|
| **P0** | 用户反馈闭环(差评→调参) | 调优依据 | 中 |
| **P0** | RunReport MVP(已做,待验证) | 调试能力 | 已完成 |
| **P1** | 线上 A/B 测试框架 | 调优验证 | 高 |
| **P1** | TraceStore 持久化(ClickHouse) | 长期分析 | 中 |
| **P2** | 自动告警(P95 超阈值) | 稳定性 | 低 |
| **P2** | Langfuse 集成完善(嵌套 span) | 调试 LLM | 中 |
| **P3** | OpenTelemetry 完善(每个端点 span) | 跨服务追踪 | 低 |

**关键诚实点**:**用户反馈闭环是最大的监控缺口**——数据采了不用,等于没监控。

讲这套的时候,面试官会看出你**知道监控不只是 dashboard,更是"能反过来驱动优化的闭环"**。

---

## 15. 配套文档

- `backend/docs/memory_system.md` — 记忆系统 + MySQL 存什么
- `backend/docs/retrieval_routing.md` — Adaptive RAG 检索路由
- `backend/docs/observability.md` — 本文档(可观测性体系)

三份合起来覆盖面试向 RAG 系统设计的三大块:**数据/检索/监控**。