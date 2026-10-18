"""RAG 检索链路的故障语义（issue #292）。

检索结果必须三态可分，而不是塌缩成一个空列表：

- **正常空结果**：各检索臂都已正常执行、确实没有命中 —— 不抛异常（既有语义）；
- **服务故障（异常）**：检索后端（ES / embedding / 兜底臂）调用失败 ——
  `RetrievalUnavailableError(kind="failed")`；
- **超时**：检索调用超时 —— `RetrievalUnavailableError(kind="timeout")`。

故障与超时都**不得**回落成「知识库无相关内容」的用户语义（那是把「服务坏了」
说成「没有找到」），也不得写入任何检索/问答缓存（否则一次抖动会被固化成
缓存 TTL 窗口内的持续假无结果）。
"""


class RetrievalUnavailableError(RuntimeError):
    """检索后端不可用（服务故障或超时）——结果不可知，绝不等于「无内容」。

    调用方契约：
    - 上层聊天链：如实降级（"知识库暂不可用"）或显式拒答，不得伪装成「暂无相关文档」；
    - REST 搜索：映射为 502 语义，而不是 200 + 空数组；
    - Agent 工具：返回结构化 ToolResult.fail，走 tool_error / run_report 失败通道。
    """

    def __init__(
        self,
        message: str,
        *,
        kind: str = "failed",
        attempts: int = 0,
        sub_queries: list[str] | None = None,
    ):
        super().__init__(message)
        self.kind = kind  # "failed" | "timeout"
        self.attempts = attempts
        self.sub_queries = list(sub_queries or [])

    @property
    def is_timeout(self) -> bool:
        return self.kind == "timeout"


class EmbeddingUnavailableError(RuntimeError):
    """embedding 服务调用失败（retriever 内部信号）。

    此前 `get_embedding` 把一切失败吞成 `[]`，向量臂被静默关闭、整体呈
    「无结果」——本异常让 retriever 能区分「按设计未配置向量臂」与「服务故障」，
    再由 pipeline 聚合为面向调用方的 `RetrievalUnavailableError`。
    """


class LLMNotConfiguredError(RuntimeError):
    """LLM 客户端未配置 —— 生成能力不可用，绝不伪装成回答内容。

    此前 `RAGPipeline.chat_stream` 在无 client 时 `yield "LLM未配置"`：这句配置
    诊断被上层当作正文 chunk 逐字累加、作为 ASSISTANT 消息落库并写入长期记忆，
    会话历史里沉淀下「LLM未配置」这样的垃圾回答。现在改为抛出本异常，由聊天
    管线在入口预检并以 error 事件语义返回（与 agent 通道的 type="error" 对齐）。
    """


class EvalJudgeUnavailableError(RuntimeError):
    """RAG 评估评委（LLM-as-judge）不可用 —— 分数不可知，绝不等于「0 分」（issue #301）。

    此前 `RAGEvaluator._score` 把一切评委调用失败吞成 `(0.0, err)`：
    - 单条评估：0/0/0 的 200 与「真实评了 0 分」不可区分（details.error 是唯一线索）；
    - 批量评估：故障条目的 0 分还被计入均值，污染整批统计。

    调用方契约：端点映射为 5xx（评委是外部 LLM 服务 → 502 语义），
    批量评估对单条失败条目标注 `failures` 并从均值剔除，全部失败则上抛。
    """


def is_timeout_error(exc: BaseException) -> bool:
    """判定异常是否属于「超时」态（三态之一）。

    asyncio.TimeoutError 在 3.11+ 即内建 TimeoutError；httpx / openai 等
    客户端有各自的超时类，为避免为一次判定引入新依赖，按类名兜底识别。
    """
    if isinstance(exc, TimeoutError):
        return True
    return "timeout" in type(exc).__name__.lower()
