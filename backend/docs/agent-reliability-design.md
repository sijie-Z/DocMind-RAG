# Agent 可靠性设计(Agent Reliability Design)

> DocMind Agent 的运行时工程契约。定义 P0 三大块:**工具调用失败处理 / 任务中断恢复 / 人工兜底**,以及测试策略。
>
> 这份文档解决的不是"缺一个功能",而是 **AI 生成的代码缺工程运行时设计**。后续 Agent 加能力都按这份契约对齐。

---

## 0. 现状与缺口

### 0.1 当前 Agent 循环结构

```
user → FastAPI → AgentService → PERAgentLoop → Planner/Executor/Reflector
                                → MemoryBridge
                                → ToolRegistry
                                → ExecutionContext(单 run 快照)
```

### 0.2 三处真实缺口

| 缺口 | 现象 | 根因 |
|---|---|---|
| **工具失败处理粗糙** | Agent 工具失败 → 不知道怎么办 → 直接结束 | 错误没分类,retry/fallback/ask_user 三选一决策没有 |
| **任务中断不可恢复** | 服务挂了/网页关了 → 任务没了 | 没有 checkpoint,没有 task lifecycle 持久化 |
| **人工兜底缺失** | 反复失败 → 胡说 → 答案不可信 | 没有 escalation 阈值,没有 wait_for_human 路径 |

### 0.3 已经有的资产(不能丢)

- `ExecutionContext`(单 run 全量快照,Redis 7 天 TTL)—— checkpoint 的胚
- `ToolResult.success/error/code`(统一结果)—— error classification 的基础
- `step.retry_strategy`(auto_retry/ask_user/skip)—— 字段在,但 ask_user 是 stub
- `step.fallback_tool`(备用工具)—— 字段在,但 fallback 链没设计
- `MAX_CONSECUTIVE_FAILURES`(Plan 级熔断)—— 有,但只 3 次

---

## 1. 设计目标与不目标

### 1.1 目标

- **可恢复**:任何 run 失败都能从 checkpoint 继续,不丢已做的工作
- **可降级**:工具/服务失败时自动降级,不直接告诉用户"我挂了"
- **可追问**:Agent 真的不知道时主动问人,而不是沉默或胡编
- **可观测**:每一步失败/降级/追问都有记录,可回放可分析
- **可测试**:每个失败路径都有测试,不靠手动跑 demo 验证

### 1.2 不目标(本轮不做)

- ❌ Python sandbox(seccomp / subprocess 隔离)—— P0 之外
- ❌ Policy engine / 完整 permission system —— P0 之外
- ❌ RL-based retry policy —— 复杂度爆炸,规则 + 配置够用
- ❌ 多用户隔离下的资源配额 —— 单用户场景先不碰
- ❌ 跨进程 / 跨机器的任务调度 —— 单进程先做

---

## 2. P0-A: 工具调用失败处理

### 2.1 错误分类(统一在 `ToolResult.error_type`)

```
ToolErrorType = Literal[
    # 可重试(retry with backoff)
    "timeout",
    "rate_limited",
    "api_error",            # 5xx 类的服务侧错误
    "connection_error",
    # 不可重试,需要 fallback
    "not_found",
    "permission_denied",
    "validation_error",     # 参数错,retry 也没用
    # 不可重试,需要 human
    "auth_expired",         # token 失效,需要重新登录
    "budget_exceeded",      # 用户配额/费用上限
    "ambiguous_input",      # 参数看起来不对,但不知道哪个对
]
```

### 2.2 决策表(错误 → 动作)

| error_type | retryable | fallback? | ask_user? |
|---|---|---|---|
| timeout | ✅ exponential backoff | ✅ 切 fallback_tool | ❌ |
| rate_limited | ✅ 长退避(60s+) | ✅ | ❌ |
| api_error | ✅ | ✅ | ❌ |
| connection_error | ✅ | ✅ | ❌ |
| not_found | ❌ | ✅ 切 fallback_tool | ✅ 若都找不到 |
| permission_denied | ❌ | ❌(无权用其他) | ✅ 必须 |
| validation_error | ❌ | ❌(参数问题) | ❌(通常错在 agent 选错工具) |
| auth_expired | ❌ | ❌ | ✅ 必须 |
| budget_exceeded | ❌ | ❌ | ✅ 必须 |
| ambiguous_input | ❌ | ❌ | ✅ 必须 |

### 2.3 单步重试引擎(替换 `_execute_step_with_retry`)

**当前实现问题**:
- 所有错误都 retry,不知道哪些该 retry 哪些不该
- retry 失败后直接 fallback_tool,不问"用户能不能手动给个参数"

**新设计**(`executor.py:retry_with_policy`):

```python
async def retry_with_policy(
    step: PlanStep,
    error_type: str,
    attempt: int,
) -> RetryDecision:
    policy = RETRY_POLICIES[error_type]

    # 1. 不可重试
    if not policy.retryable:
        return RetryDecision(action="fallback_or_ask")

    # 2. 已超过最大次数
    if attempt >= policy.max_retries:
        return RetryDecision(action="fallback_or_ask")

    # 3. 计算退避
    delay = policy.backoff(attempt)
    return RetryDecision(action="retry_after", delay_seconds=delay)
```

**配置驱动**(`agent/config.py`):

```python
@dataclass
class RetryPolicy:
    retryable: bool
    max_retries: int          # 不同错误不同上限
    backoff: Callable[[int], float]  # 返回秒数

RETRY_POLICIES: dict[str, RetryPolicy] = {
    "timeout":          RetryPolicy(retryable=True,  max_retries=3, backoff=lambda n: 0.5 * (2 ** n)),
    "rate_limited":     RetryPolicy(retryable=True,  max_retries=2, backoff=lambda n: 60 + 30 * n),
    "api_error":        RetryPolicy(retryable=True,  max_retries=3, backoff=lambda n: 1 * (2 ** n)),
    "connection_error": RetryPolicy(retryable=True,  max_retries=5, backoff=lambda n: 2 * (2 ** n)),
    "not_found":        RetryPolicy(retryable=False, max_retries=0, backoff=lambda n: 0),
    "permission_denied":RetryPolicy(retryable=False, max_retries=0, backoff=lambda n: 0),
    "validation_error": RetryPolicy(retryable=False, max_retries=0, backoff=lambda n: 0),
    "auth_expired":     RetryPolicy(retryable=False, max_retries=0, backoff=lambda n: 0),
    "budget_exceeded":  RetryPolicy(retryable=False, max_retries=0, backoff=lambda n: 0),
    "ambiguous_input":  RetryPolicy(retryable=False, max_retries=0, backoff=lambda n: 0),
}
```

### 2.4 Fallback 链(替换单层 fallback_tool)

**当前问题**:`step.fallback_tool` 只支持一个备用,没备选就崩。

**新设计**:`fallback_chain: list[str]`,按顺序尝试,失败后尝试下一个,所有失败再 ask_user。

```python
@dataclass
class PlanStep:
    tool_hint: str
    fallback_chain: list[str] = field(default_factory=list)  # 备选工具链

async def try_tool_chain(step, context) -> ToolResult:
    """按顺序尝试 primary + fallback chain"""
    tools_to_try = [step.tool_hint] + step.fallback_chain
    last_error = None
    for tool_name in tools_to_try:
        result = await tool_registry.execute(tool_name, step.args, context)
        if result.success:
            return result
        last_error = result
        if not RETRY_POLICIES[result.error_type].retryable:
            # 这个工具不行,试下一个
            continue
        # 可重试的错误,在工具内部重试
        ...
    # 链全失败
    return last_error
```

### 2.5 错误传播路径

```
ToolRegistry.execute
  ↓ 错误
ToolResult(success=False, error_type="timeout", ...)
  ↓
Executor._execute_step_once 识别 error_type
  ↓
retry_with_policy(step, error_type, attempt)
  ├─ retry_after → asyncio.sleep(delay) → 重跑
  ├─ fallback_or_ask → try_tool_chain → 全失败则 ask_user
  └─ ask_user → wait_for_human(...) → 收到选择后继续/放弃
  ↓
继续 / 标记 step failed / escalate 到 plan level
```

---

## 3. P0-B: 任务中断恢复

### 3.1 数据模型(新增 task_lifecycle 表)

**位置**:`backend/app/models/agent_task.py`

```python
class AgentTask(Base):
    """跨 run 的任务生命周期。checkpoint 的承载。"""
    __tablename__ = "agent_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), index=True)
    organization_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("organizations.id"))

    # 任务状态
    status: Mapped[str] = mapped_column(Enum(TaskStatus), index=True)
    #   TaskStatus: pending / running / waiting_tool / waiting_human / failed / completed / abandoned
    #   waiting_tool: 工具调用中,可被中断恢复
    #   waiting_human: 等用户回答,可能等很久

    # 任务元数据
    query: Mapped[str] = mapped_column(Text)
    plan_id: Mapped[str | None] = mapped_column(String(36), index=True)
    current_step_id: Mapped[str | None] = mapped_column(String(36))

    # checkpoint 数据
    # 完整 ExecutionContext.to_dict() 序列化到这里
    # 包括 step results / decisions / findings / failures
    context_snapshot: Mapped[Any] = mapped_column(JSON)

    # 进度追踪
    completed_steps: Mapped[int] = mapped_column(Integer, default=0)
    total_steps: Mapped[int | None] = mapped_column(Integer)
    progress: Mapped[float] = mapped_column(Float, default=0.0)

    # 错误上下文
    last_error: Mapped[str | None] = mapped_column(Text)
    last_error_type: Mapped[str | None] = mapped_column(String(50))

    # 重试计数
    retry_count: Mapped[int] = mapped_column(Integer, default=0)

    # 时间戳
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), onupdate=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # SLA
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True,
                                                     comment="任务过期时间,waiting_human 时清理")
```

### 3.2 Checkpoint 策略

**当前 `ExecutionContext.save()`** 只在 run 结束时调一次,**断电就丢**。

**新设计**:`save_checkpoint()` 在每个关键节点调:

```python
CHECKPOINT_TRIGGERS = [
    "after_planning",        # Planner 完成,Plan 已生成
    "after_each_step",       # 每步完成后
    "before_long_tool_call", # 长工具调用开始前(timeout > 5s)
    "before_waiting_human",  # 暂停等人介入前
    "before_retry",          # 重试前(让重试可恢复)
    "on_error",              # 任何 error 后
]

async def save_checkpoint(ctx: ExecutionContext, trigger: str, task_id: str):
    """把当前 ctx 完整快照到 AgentTask.context_snapshot"""
    agent_task = await db.get(AgentTask, task_id)
    agent_task.context_snapshot = ctx.to_dict()
    agent_task.current_step_id = ctx.current_step_id
    agent_task.status = derive_status(trigger, ctx)
    agent_task.completed_steps = len(ctx.completed_steps)
    agent_task.progress = ctx.progress
    agent_task.last_error = ctx.failures[-1] if ctx.failures else None
    await db.commit()
```

### 3.3 恢复路径

**用户回来后**(`GET /api/v1/agent/tasks/{task_id}`):

```python
async def get_task_status(task_id: str) -> AgentTaskStatus:
    task = await db.get(AgentTask, task_id)

    if task.status == "completed":
        return {state: "completed", result: task.context_snapshot["final_output"]}

    if task.status == "waiting_human":
        # 等用户回答,前端弹个 prompt
        return {state: "waiting_human",
                question: task.context_snapshot["pending_question"]}

    if task.status in ("running", "waiting_tool"):
        # 还在跑/工具卡住,前端可触发 cancel 或 wait
        return {state: "running", progress: task.progress}

    if task.status == "failed":
        # 失败,可恢复
        return {state: "failed", last_error: task.last_error,
                recoverable: task.retry_count < MAX_RETRIES}


async def resume_task(task_id: str) -> AsyncGenerator[AgentEvent]:
    """从 checkpoint 继续"""
    task = await db.get(AgentTask, task_id)

    # 1. 重建 ExecutionContext
    ctx = ExecutionContext.from_dict(task.context_snapshot)

    # 2. 决定从哪一步继续
    if task.status == "failed":
        # 从失败的步骤重试(不是从头跑)
        resume_step_id = task.current_step_id
    elif task.status == "waiting_human":
        # 等用户回答,resume 是从这一步继续
        resume_step_id = task.current_step_id
    else:
        raise ValueError(f"Cannot resume task in state {task.status}")

    # 3. 重建 Plan
    plan = await load_plan(task.plan_id)

    # 4. 从指定 step 继续
    async for event in loop.continue_from(plan, ctx, resume_step_id):
        yield event
```

### 3.4 中断 vs 崩溃的区分

| 场景 | 状态变化 | 处理 |
|---|---|---|
| **用户主动 cancel**(关网页) | 客户端断开,SSE close | server 端 coroutine 收到 cancel 信号 → 保存 checkpoint → 退出 |
| **服务崩溃**(OOM / kill) | 进程死了,coroutine 没机会 save | 依赖最新 checkpoint 恢复(可能在几十秒前) |
| **长工具调用卡住**(timeout 不触发) | run 还在 running | 心跳监控,30s 没活动 → 自动 cancel + checkpoint |

**心跳检测**(新组件 `agent_heartbeat`):

```python
class AgentHeartbeat:
    """每个 run 在 Redis 写心跳,看门狗定期检查"""
    async def heartbeat(self, task_id: str):
        await redis.setex(f"heartbeat:{task_id}", 30, "alive")

    async def check_stuck(self):
        """返回心跳超时的 task_ids"""
        # 30s 内没心跳的 → 标记为 stuck → 触发 cancel
        ...
```

---

## 4. P0-C: 人工兜底

### 4.1 何时升级到人

**升级阈值**(配置驱动):

```python
@dataclass
class EscalationConfig:
    # 单步错误连续触发升级
    consecutive_tool_failures: int = 3     # 同一工具 3 次失败
    # 整 task 错误累计
    total_task_failures: int = 5          # 整个任务累计 5 次失败
    # 时间相关
    max_waiting_time_seconds: int = 600   # 等工具超过 10 分钟
    # 业务相关
    requires_user_decision: list[str] = field(default_factory=list)
    #   哪些 error_type 必须问人
    #   例: ["permission_denied", "auth_expired", "budget_exceeded", "ambiguous_input"]
```

### 4.2 升级流程

```
executor 检测到升级条件
  ↓
yield AgentEvent(
    type="escalation_required",
    task_id=task_id,
    question="需要您的选择",
    options=[
        {"id": "retry", "label": "重新尝试", "description": "..."},
        {"id": "skip", "label": "跳过这步", "description": "..."},
        {"id": "modify", "label": "修改问题", "description": "..."},
        {"id": "abort", "label": "放弃任务", "description": "..."},
    ],
    context_summary={...},  # 当前状态摘要
)
  ↓
AgentTask.status = "waiting_human"
save_checkpoint()
  ↓
前端弹 prompt 等用户选
  ↓
POST /api/v1/agent/tasks/{task_id}/respond {choice: "retry"|"skip"|"modify"|"abort", payload: {...}}
  ↓
backend 恢复 run:
  - retry: 从失败步骤重试,retry_count++
  - skip: 标记 step skipped,继续下一步
  - modify: 修改 plan/step 后继续
  - abort: 标记 task abandoned,返回部分结果
```

### 4.3 等人的实现(关键)

**当前 ask_user 是 stub**(`executor.py:330`):发出 event 就直接 return,不等人回答。

**新设计 `wait_for_human`**:

```python
async def wait_for_human(
    task_id: str,
    question: str,
    options: list[dict],
    timeout_seconds: int = 600,
) -> HumanResponse:
    """等用户回答,带超时"""
    # 1. 保存到 Redis: agent:human_response:{task_id} (待响应)
    response_key = f"agent:human_response:{task_id}"
    await redis.setex(response_key, timeout_seconds, json.dumps({
        "state": "waiting",
        "question": question,
        "options": options,
    }))

    # 2. AgentTask.status = waiting_human,save_checkpoint

    # 3. 阻塞等待(可被 cancel)
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        resp = await redis.get(response_key)
        if resp and json.loads(resp).get("state") == "responded":
            return HumanResponse.from_dict(json.loads(resp))
        await asyncio.sleep(2)  # 每 2s 轮询

    # 4. 超时:默认 abort 或 escalate(可配)
    return HumanResponse(action="timeout_abort")
```

**响应端**(`POST /api/v1/agent/tasks/{task_id}/respond`):

```python
@router.post("/tasks/{task_id}/respond")
async def respond_to_agent(task_id: str, body: HumanResponseBody):
    response_key = f"agent:human_response:{task_id}"
    await redis.setex(response_key, 600, json.dumps({
        "state": "responded",
        "choice": body.choice,
        "payload": body.payload,
        "responded_at": datetime.now().isoformat(),
    }))
    return {"status": "recorded"}
```

### 4.4 降级回答策略

**重试 + fallback 失败 + 等人超时 → 怎么办?**

不允许沉默返回空字符串。允许的降级回答:

1. **诚实失败**:`"我尝试调用 X 但连续失败,问题可能在于 Y,建议您检查 Z。"`
2. **部分结果**:已成功的步骤结果拼起来,标注哪些步骤缺失
3. **建议转移**:推荐改问别的问题或换工具

**禁止的降级**:
- ❌ 编造一个看似合理的答案
- ❌ 返回空字符串假装"没找到"
- ❌ 反复 retry 后悄悄放弃

---

## 5. 配置体系

**`AgentConfig` 增强**(`agent/config.py`):

```python
@dataclass
class AgentConfig:
    # ... 现有字段 ...

    # P0-A 工具失败处理
    retry_policies: dict[str, RetryPolicy] = field(default_factory=lambda: RETRY_POLICIES)
    enable_fallback_chain: bool = True
    max_fallback_chain_length: int = 3

    # P0-B 任务恢复
    enable_checkpoint: bool = True
    checkpoint_triggers: list[str] = field(default_factory=lambda: CHECKPOINT_TRIGGERS)
    task_ttl_seconds: int = 86400 * 3   # 任务保留 3 天

    # P0-C 人工兜底
    escalation_config: EscalationConfig = field(default_factory=EscalationConfig)
    enable_human_escalation: bool = True
    waiting_timeout_seconds: int = 600

    # 心跳
    heartbeat_interval_seconds: int = 10
    heartbeat_timeout_seconds: int = 30
```

---

## 6. 测试策略

> **Agent 改造最怕"代码感觉更好了"但没有证明。每个能力必须对应测试。**

### 6.1 单元测试(快,CI 必跑)

**文件**:`backend/tests/unit/test_retry_policies.py`

```python
@pytest.mark.parametrize("error_type,expected_retryable", [
    ("timeout", True),
    ("rate_limited", True),
    ("not_found", False),
    ("permission_denied", False),
    ("auth_expired", False),
])
def test_retryable_classification(error_type, expected_retryable):
    policy = RETRY_POLICIES[error_type]
    assert policy.retryable == expected_retryable

def test_backoff_exponential():
    policy = RETRY_POLICIES["timeout"]
    assert policy.backoff(0) == 0.5
    assert policy.backoff(1) == 1.0
    assert policy.backoff(2) == 2.0

def test_rate_limited_longer_backoff():
    policy = RETRY_POLICIES["rate_limited"]
    assert policy.backoff(0) >= 60  # 至少 60s
```

### 6.2 集成测试(mock LLM,真 loop)

**文件**:`backend/tests/integration/test_agent_retry.py`

```python
class MockTimeoutTool(BaseTool):
    def __init__(self, fail_times=3):
        self.fail_times = fail_times
        self.calls = 0

    async def execute(self, **kwargs):
        self.calls += 1
        if self.calls <= self.fail_times:
            return ToolResult.fail("timeout", "simulated timeout")
        return ToolResult.ok({"data": "success"})


async def test_tool_timeout_triggers_retry():
    """工具连续 timeout,前 N 次重试,然后成功"""
    tool = MockTimeoutTool(fail_times=2)
    tool_registry.register("mock_timeout", tool)

    config = AgentConfig(max_retries_per_step=5)
    loop = PERAgentLoop(client=mock_llm, config=config)

    events = []
    async for event in loop.run("调用 mock_timeout"):
        events.append(event)

    assert tool.calls == 3  # 失败 2 次 + 成功 1 次
    # 最终事件是 done,不是 error
    assert any(e.type == "done" for e in events)


async def test_unrecoverable_error_does_not_retry():
    """permission_denied 不应该 retry,直接 fallback 或 ask_user"""
    tool = MockAlwaysFail("permission_denied")
    config = AgentConfig(max_retries_per_step=5)
    loop = PERAgentLoop(client=mock_llm, config=config)

    events = []
    async for event in loop.run("调用 tool"):
        events.append(event)

    assert tool.calls == 1  # 只调一次,不 retry
    # 应该触发 escalation 或 fallback
    assert any(e.type in ("escalation_required", "fallback_attempted") for e in events)
```

### 6.3 Checkpoint / 恢复测试

**文件**:`backend/tests/integration/test_agent_recovery.py`

```python
async def test_checkpoint_after_each_step():
    """每步后都有 checkpoint,中间崩溃能从这一步恢复"""
    db_session = await get_test_db()

    # 跑一半,然后强制中断
    config = AgentConfig(enable_checkpoint=True)
    loop = PERAgentLoop(client=mock_llm, config=config)
    task = None
    step_count = 0
    async for event in loop.run("test query"):
        if event.type == "step_complete":
            step_count += 1
            if step_count == 3:
                # 模拟崩溃(抛异常)
                task = await db_session.get(AgentTask, event.task_id)
                raise SimulatedCrashError()

    # 验证 checkpoint 存在
    assert task.context_snapshot is not None
    assert len(task.context_snapshot["steps"]) == 3

    # 重新启动,从 step 3 继续
    loop2 = PERAgentLoop(client=mock_llm, config=config)
    resumed_task = await resume_task(task.id)

    assert resumed_task.context_snapshot["steps"][-1]["step_id"] == "s3"


async def test_task_recovery_after_service_restart():
    """整个服务重启后,任务能从 checkpoint 恢复"""
    task_id = "task-123"

    # 服务 1 跑一半
    loop1 = PERAgentLoop(client=mock_llm, config=AgentConfig())
    async for event in loop1.run("test"):
        if event.type == "checkpoint_saved":
            break

    # 模拟服务重启(新的 loop 实例)
    loop2 = PERAgentLoop(client=mock_llm_continued, config=AgentConfig())
    resumed = await loop2.continue_task(task_id)

    assert resumed.status != "fresh_start"
```

### 6.4 人工兜底测试

**文件**:`backend/tests/integration/test_human_escalation.py`

```python
async def test_escalation_after_consecutive_failures():
    """连续失败超过阈值,触发 escalation"""
    tool = MockAlwaysFail("api_error")
    config = AgentConfig(
        max_retries_per_step=5,
        escalation_config=EscalationConfig(consecutive_tool_failures=3),
    )
    loop = PERAgentLoop(client=mock_llm, config=config)

    events = []
    async for event in loop.run("test"):
        events.append(event)

    # 期望:前 3 次失败后,触发 escalation_required
    escalation = [e for e in events if e.type == "escalation_required"]
    assert len(escalation) == 1
    assert "重新尝试" in escalation[0].options[0]["label"]


async def test_human_response_unblocks_run():
    """用户选 retry 后,run 从失败步骤继续"""
    # 启动一个 run,触发 escalation
    # mock 用户响应:retry
    # 验证 run 继续并最终 done

    config = AgentConfig(enable_human_escalation=True)
    loop = PERAgentLoop(client=mock_llm, config=config)
    task_id = None

    async def run_until_escalation():
        async for event in loop.run("test"):
            if event.type == "escalation_required":
                task_id = event.task_id
                return
            yield event

    await run_until_escalation()

    # 模拟用户选择 retry
    response_key = f"agent:human_response:{task_id}"
    await redis.setex(response_key, 600, json.dumps({
        "state": "responded",
        "choice": "retry",
    }))

    # 继续 run
    final_events = []
    async for event in loop.continue_after_response(task_id):
        final_events.append(event)

    # 应该最终 done
    assert any(e.type == "done" for e in final_events)
```

### 6.5 测试覆盖率目标

| 模块 | 目标覆盖率 | 必测场景 |
|---|---|---|
| `retry_policies.py` | 100% | 每个 error_type 都测 retryable/backoff |
| `executor.py:_execute_step_*` | 80% | retry / fallback / escalation 三条路径 |
| `agent_task.py` 模型 | 100% | status 转换 / checkpoint 序列化 |
| `checkpoint.py` | 90% | 每个 trigger 都触发 save |
| `wait_for_human` | 90% | 正常响应 / 超时 / cancel |
| `escalation_config.py` | 100% | 阈值触发 / 边界值 |

### 6.6 不测什么

- ❌ **不测 LLM 输出质量**(那是 benchmark 的事,不是单元测试)
- ❌ **不测真实网络/真实 ES**(用 mock)
- ❌ **不测前端**(前端有自己的测试)
- ❌ **不测 sandbox 真实 syscall 拦截**(那是基础设施测试,要 Linux 环境)

---

## 7. 迁移路径(从当前实现到新设计)

### 7.1 阶段 1:数据模型 + 配置(无破坏性)

**PR 1: 加表 + 加配置**
- 新增 `AgentTask` 模型 + Alembic migration
- 新增 `EscalationConfig` / `RetryPolicy` dataclass
- 新增 `RETRY_POLICIES` 字典 + `CHECKPOINT_TRIGGERS` 列表

**验收**:`pytest backend/tests/unit/test_retry_policies.py` 全绿,所有现有测试不破。

### 7.2 阶段 2:工具错误分类(向后兼容)

**PR 2: 错误分类**
- `ToolError` 增加 `error_type` 字段(默认 unknown)
- 现有工具调用包装 `_classify_error()` 函数
- `executor` 用 `RETRY_POLICIES[error_type].retryable` 代替硬编码 `attempt < max_retries`

**验收**:新增 `test_tool_error_classification.py`,模拟各种 error type,验证 retry 决策。

### 7.3 阶段 3:Checkpoint(增量)

**PR 3: Checkpoint 集成**
- `ExecutionContext` 增加 `task_id` 字段
- 新增 `save_checkpoint()` 函数,在 CHECKPOINT_TRIGGERS 处调用
- 新增 `AgentService.resume_task()` API

**验收**:`test_agent_recovery.py` 全绿。

### 7.4 阶段 4:人工兜底(增量)

**PR 4: 人工兜底**
- 实现 `wait_for_human()` 异步阻塞
- 新增 `POST /api/v1/agent/tasks/{task_id}/respond` API
- 实现 `escalation_required` 事件

**验收**:`test_human_escalation.py` 全绿。

### 7.5 阶段 5:回填测试(回归)

**PR 5: 测试补全**
- 已有 executor.py 的测试覆盖率补到 80%
- 集成测试覆盖 retry + checkpoint + escalation 三条主路径
- 跑全量 `pytest backend/tests/` 确认无回归

---

## 8. 风险与边界

### 8.1 已知风险

| 风险 | 缓解 |
|---|---|
| **Checkpoint 写入太频繁,影响性能** | 异步批量写,失败重试 |
| **`wait_for_human` 阻塞占资源** | 单 run 占一个 coroutine,可被 cancel |
| **escalation 阈值不通用** | 业务级默认 vs 用户级 override |
| **任务 TTL 过期清理** | 后台 cron 清理 expired tasks |
| **状态机太复杂导致 bug** | 状态转换表 + 集成测试全覆盖 |

### 8.2 不在本文档处理

- Python sandbox 安全(seccomp/subprocess)—— P1,后续设计
- RAG 召回失败处理—— RAG 层独立设计
- 前端 ask_user 弹窗 UI—— 前端独立设计
- 多 Agent 协同—— 单 Agent 完成后再说

---

## 9. 与现有架构的对齐

### 9.1 复用,不重写

| 已有资产 | 怎么复用 |
|---|---|
| `ExecutionContext` | 直接当 checkpoint 的内容载体 |
| `ToolResult.success/error/code` | 加 `error_type` 字段,不破坏现有调用 |
| `ToolRegistry` | 加 error_classifier hook |
| `ToolError` 枚举 | 扩展为 `ToolErrorType` Literal |
| `step.retry_strategy` | 替换为 RetryPolicy lookup,不破坏 dataclass |
| `step.fallback_tool` | 替换为 `fallback_chain: list[str]`,单元素链向后兼容 |
| `Redis client` | 复用存 checkpoint 和 human response |
| `Langfuse` | 加 wait_for_human / escalation span |

### 9.2 新增组件

| 新组件 | 位置 |
|---|---|
| `AgentTask` 模型 | `backend/app/models/agent_task.py` |
| Alembic migration | `backend/alembic/versions/00X_agent_task.py` |
| `RetryPolicy` / `RETRY_POLICIES` | `backend/app/agent/retry_policy.py`(新) |
| `CheckpointManager` | `backend/app/agent/checkpoint.py`(新) |
| `WaitForHuman` | `backend/app/agent/wait_for_human.py`(新) |
| `EscalationConfig` | `backend/app/agent/escalation.py`(新) |
| API 端点 | `backend/app/api/v1/endpoints/agent.py`(扩) |

---

## 10. 开放问题(待 RunReport 验证后决定)

下面这几个我刻意没写死,因为 RunReport 还没跑过真实失败:

1. **是否给 wait_for_human 设硬超时?**
   - 选项 A: 超时后 abort,简单但用户得重新发起
   - 选项 B: 超时后问系统,让系统决定(可能给个部分答案)
   - 倾向 A,但等真实使用反馈再定

2. **fallback_chain 是否自动发现?**
   - 选项 A: 手动配(planner 决策时指定)
   - 选项 B: 运行时自动找相似工具(embedding 检索)
   - 倾向 A,简单可控

3. **escalation 事件要不要发 Langfuse?**
   - 倾向要,但要看 Langfuse 的 trace 结构能不能容纳

4. **要不要支持"回滚到 step N"而不是"从 N 继续"?**
   - "回滚"= 撤销 N+1 已做的工作,从 N 重做
   - "继续"= 跳过错误,从 N+1 开始
   - 当前设计是"继续",先够用,加"回滚"会增加复杂度

这些问题在跑过真实失败后,通过 RunReport 数据决定优先级。

---

## 11. 文件索引

| 关注点 | 位置 |
|---|---|
| 当前 PERAgentLoop | `backend/app/agent/loop.py:99` |
| 当前 retry 实现 | `backend/app/agent/executor.py:309-442` |
| 当前 ToolResult | `backend/app/agent/registry.py:76-125` |
| 当前 ExecutionContext | `backend/app/agent/exec_context.py:57-188` |
| 当前 PlanStep | `backend/app/agent/planner.py:92-115` |
| 当前 ask_user stub | `backend/app/agent/executor.py:330` |
| 当前 Plan 持久化 | `backend/app/agent/planner.py:763-832` |
| `Testing Strategy`(已有) | `backend/docs/testing-strategy.md` |

---

## 12. 一句话总结

> **AI 生成的代码缺的不是 feature,是工程运行时设计。**
> DocMind Agent 的 P0 三大缺口(工具失败粗糙 / 中断不可恢复 / 人工兜底缺失)不是孤立的 bug,而是**没有工程契约**。
>
> 这份文档定义工程契约:**错误有分类,任务有生命周期,失败有升级路径,每条路径都有测试**。
>
> 后面任何 Agent 加新能力的 PR,都按这份契约对齐:
> 1. 它可能调用什么工具?失败时按 RETRY_POLICIES 怎么处理?
> 2. 它会写什么 checkpoint?在哪个 trigger 触发?
> 3. 它什么时候会 escalation?阈值是多少?
> 4. 它的每条路径都有测试吗?
>
> 没有这四个回答的 PR 不合并。