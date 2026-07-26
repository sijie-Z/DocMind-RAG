# DocMind 测试策略

> 目标:把测试从"测代码有没有执行"扭到"测系统有没有完成任务"。

## 1. 一句话

**行为测试金字塔 + Agent 项目铁律:不测函数被调用,测用户意图被满足。**

```
                 E2E
          5%   用户真实流程(3 条 journey)

        Integration
       25%   Agent / RAG 链路(fixture LLM,真检索)

      Behavior
     60%   核心业务行为(RAG 答案、Agent 选 tool、Memory 跨轮召回)

    Unit
   10%   纯函数(token 估算、chunk 切分、metadata、权限、配置)
```

## 2. 必须保证(测试锁死)

- 用户上传 PDF 后,该文档能被检索召回
- Agent 在合理 prompt 下能从可用 tools 中选出 analysis / search 类 tool
- Streaming 协议的输出顺序(event types、chunk 形状)
- Memory 跨轮召回(短期:同会话下一轮答出上一轮说过的事实;长期:跨会话仍能召回)
- 鉴权:无 token 拒绝;坏 token 拒绝;好 token 通过
- 配置解析:`ENABLE_*` 切换、模型选择、Redis key 形状

## 3. 不保证(测试不要锁)

- **exact answer 文本**(LLM 温度会让它漂,锁死会让 CI 永远红)
- **token 数量精确值**(会随 prompt 微调变化)
- **prompt 字面字符串**(业务功能对、prompt 措辞不重要)
- **调用了哪个内部函数**(Agent 可能今天走 tool A、明天走 tool B)
- **embedding 模型维度**(本地模型切来切去,见 §7)

## 4. 不允许 mock / 必须走真实路径

- **RAG 检索路径**(Qdrant / 向量检索 / rerank):用 fake embedding + 真向量库,或 in-memory fake
- **Memory 召回逻辑**(4 层 + bridge):走真 `AgentMemorySystem`,只 mock 外部 LLM
- **Agent planner → tool selection**:走真 ToolRegistry,只 mock LLM 决策
- **Streaming 输出组装**:走真 loop,不 mock event 构造

## 5. 允许 mock(外部不可控)

- LLM API(智谱 / OpenAI / Ollama 远端)
- Redis(用 `fakeredis` 或真 redis container)
- MySQL(用 sqlite 或真 mysql container)
- Qdrant(用 in-memory fake 或真 qdrant container)
- Prometheus(用 `prometheus_client.REGISTRY` 重置)
- HTTP 外部 API

## 6. 反模式清单(Code Review 直接拒)

| 反模式 | 替代写法 |
|---|---|
| `assert vector_store.search.called_with(...)` | `assert "k8s" in rag.ask("公司技术栈").answer` |
| `mock.patch("app.agent.tool_registry")` 然后 verify 调用次数 | 给 registry 真传 fake handler,跑一个端到端 ask,验证选 tool 的**结果** |
| `assert "我叫张三" in llm_response.text`(锁字面) | `assert result.long_term_recall_contains("张三")` 或 `assert next_turn_memory_returned_previous_fact()` |
| mock 整个 `AgentMemorySystem` 后 verify `recall()` 被调一次 | 用真 AgentMemorySystem,只把 embedding provider 替成 fake |
| 测 `LLM 选了 search tool`(温度不稳) | 测 `Agent 在分析 query 下,tools_used 集合与 analysis 类有交集` |

## 7. 测试分层定义

### Unit (~10%,`backend/tests/unit/`)

纯函数 + 无副作用。可在 PR 阶段跑。

- `estimate_tokens` fallback 边界(`text=2500 chars` → `1000 tokens`,不是 `833`)
- `semantic_chunker` 切分稳定性
- `masking_service` 各种 PII 模式
- `config` 解析(`ENABLE_LOCAL_EMBEDDING` 切换、embedding model 选择)
- `token 配额 / 权限判断`

### Behavior (~60%,`backend/tests/behavior/`)

**新增**。核心业务路径,只 mock 外部。

必含:
- **Memory**:跨轮召回、短期缓冲容量、LongTerm scoring 公式双路径
- **RAG**:上传 → 检索 → 答案包含 source
- **Agent**:分析类 query 触发 analysis tool;失败写入 experience
- **Streaming**:event 顺序、chunk 形状、token 累加

### Integration (~25%,`backend/tests/integration/`)

已有 9 个文件,需要审视。多数是 mock-heavy 的实现测试,**多数需重写或删**。
目标:留下真链路集成测试,需要 Qdrant/Redis 真容器或 docker-compose。

### E2E (~5%,`e2e/`)

3 条 journey:
1. **Chat 流式**:打开页面 → 登录 → 输入 → 看 stream → 引用显示
2. **RAG 流程**:上传 PDF → 等待 embedding → 提问 → 答案含 source
3. **Agent tool**:输入分析任务 → agent 调工具 → 结果展示

技术选型:Playwright。**不超过 5 个文件**。

## 8. CI 架构

```
PR 阶段(<10min):
  backend-unit         pytest backend/tests/unit/         无 LLM,纯函数
  backend-behavior     pytest backend/tests/behavior/     fixture LLM,真业务路径
  frontend-test        vitest run                        行为测试
  lint                 ruff + eslint                     必须 0 错
  typecheck            vue-tsc + mypy(可选)              必须 0 错

Nightly:
  backend-integration  pytest backend/tests/integration/  docker compose up
  e2e                  playwright                        3 条 journey
  smoke                docker build + health endpoint
```

**关键修改**:
- 拆 `.github/workflows/ci.yml` 为 `ci-fast.yml` + `ci-nightly.yml`
- 砍掉 Python 3.12 matrix(3.12 job 历史 100% CANCELLED,且项目实际只用 3.11)
- pytest 路径分层,`tests/integration/` 不再 `--ignore`
- frontend 不再强制 `--coverage`(vitest 0 测试时 coverage 会让 job 退出非 0)

## 9. Fixture 设计

`backend/tests/conftest.py` 中央 fixture:

```python
@pytest.fixture
def fake_llm():
    """替 LLM API,根据 prompt 模板返回固定结构。"""

@pytest.fixture
def in_memory_qdrant():
    """替代 Qdrant,纯 dict 实现余弦检索。"""

@pytest.fixture
def fakeredis_client():
    """替代 Redis,支持 TTL 与 hset/hgetall/to_dict 全套。"""

@pytest.fixture
def memory_system(fake_llm, fakeredis_client):
    """真 AgentMemorySystem + 假 LLM/Redis。"""
```

**铁律**:fixture 不允许调用真 LLM。所有"LLM 决定"在 fixture 里写成可断言的返回值。

## 10. 跟当前状态的差距

| 现状 | 目标 | 行动 |
|---|---|---|
| 24 个测试,80% 是 mock-heavy 实现测试 | 24 个 behavior + 单元测试 | 重写为主,少量删 |
| integration 测试 `--ignore`,不在 PR 跑 | 拆 ci-fast + ci-nightly,integration 走 nightly | 改 CI |
| frontend 0 测试,vitest run 必失败 | 至少 5 个核心组件行为测试 | 新增 |
| Python 3.12 矩阵历史 100% 取消 | 只跑 3.11 | 砍 matrix |
| pytest 路径无分层 | `unit/` + `behavior/` + `integration/` 三层 | 重构 conftest + pytest.ini |

## 11. 跟现有 doc 的关系

- `backend/docs/memory_system.md` 里的具体行为定义(MemoryItem 字段、recall 默认层、LongTerm scoring 公式)就是 behavior 测试的**真值来源**。doc 改了,测试必须跟着改;反之亦然。
- `backend/docs/retrieval_routing.md` 是 RAG behavior 测试的真值来源。
- 测试策略文档本身改了,必须有人 review(同其他 doc)。

---

**为什么这套规则**:Agent / RAG 项目最贵的失败模式是"LangChain 内部调对了,但用户问题没回答"。mock 越多,这种失败越看不见。本策略的本质:**测试看着像用户在测,而不是开发者在测**。