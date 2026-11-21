"""
Agent 工作流 Pydantic Schemas
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


# --- 工作流节点定义 ---
class WorkflowNode(BaseModel):
    """工作流节点"""

    id: str = Field(..., description="节点唯一ID")
    type: str = Field(
        ...,
        description="节点类型: llm_openai, llm_deepseek, llm_qwen, input, output, condition, tool_tts, tool_search",
    )
    position: dict[str, float] = Field(
        default_factory=lambda: {"x": 0, "y": 0}, description="节点位置"
    )
    data: dict[str, Any] = Field(default_factory=dict, description="节点配置数据")

    model_config = ConfigDict(extra="allow")


class WorkflowEdge(BaseModel):
    """工作流边（连接线）"""

    id: str = Field(..., description="边唯一ID")
    source: str = Field(..., description="源节点ID")
    target: str = Field(..., description="目标节点ID")
    sourceHandle: str | None = Field(None, description="源节点输出端口")
    targetHandle: str | None = Field(None, description="目标节点输入端口")
    label: str | None = Field(None, description="边标签（条件分支时使用）")


class WorkflowConfig(BaseModel):
    """完整工作流配置

    工具白名单与运行预算均为可选：缺省时引擎沿用旧行为，存量 flow_data 无需迁移。
    """

    nodes: list[WorkflowNode] = Field(default_factory=list, description="节点列表")
    edges: list[WorkflowEdge] = Field(default_factory=list, description="边列表")
    # None = 不限制；空列表 = 不允许任何工具节点（两种语义必须区分）
    allowed_tools: list[str] | None = Field(
        None, description="允许执行的工具节点类型；None 表示不限制"
    )
    max_cost_usd: float | None = Field(None, ge=0, description="单次运行最大美元成本；None 不限")
    max_tokens: int | None = Field(None, ge=0, description="单次运行最大 token 数；None 不限")

    @field_validator("allowed_tools")
    @classmethod
    def _validate_allowed_tools(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        # 去重且不接受空白项：避免 "" 造成语义歧义或凭据被塞进 allowlist
        cleaned = [tool.strip() for tool in value if isinstance(tool, str) and tool.strip()]
        return list(dict.fromkeys(cleaned))


# --- API 请求/响应 ---
class WorkflowCreate(BaseModel):
    """创建工作流"""

    name: str = Field(..., min_length=1, max_length=100)
    description: str | None = None
    flow_data: WorkflowConfig | None = None


class WorkflowUpdate(BaseModel):
    """更新工作流"""

    name: str | None = Field(None, min_length=1, max_length=100)
    description: str | None = None
    flow_data: WorkflowConfig | None = None
    is_active: bool | None = None


class WorkflowResponse(BaseModel):
    """工作流响应"""

    id: int
    name: str
    description: str | None
    flow_data: dict[str, Any] | None
    is_active: bool
    created_by: int | None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class WorkflowExecuteRequest(BaseModel):
    """执行工作流请求"""

    workflow_id: int
    input_data: dict[str, Any] | None = None
    stream: bool = Field(default=True, description="是否流式输出")


class NodeResult(BaseModel):
    """节点执行结果"""

    node_id: str
    node_type: str
    status: str  # pending, running, completed, failed
    input: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    # 节点耗时（毫秒）。引擎在完成/失败时写入（workflow_engine.py node_func），
    # 未完成或历史记录为 None；前端按 `${duration}ms` 渲染。
    duration: int | None = None
    # 节点观测（竞品对标 P0）：只记录调用方实际给出的元数据；拿不到一律 None，
    # 不用字符数估算 token、不猜价格。字段随 NodeResult.model_dump(mode="json")
    # 自动进入实时 SSE / 结束回放 / 执行历史 JSON 列。
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None
    model: str | None = None


class ExecutionResponse(BaseModel):
    """执行响应"""

    execution_id: int
    workflow_id: int
    status: str
    input_data: dict[str, Any] | None
    output_data: dict[str, Any] | None
    node_results: list[NodeResult] | None
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class NodeDefinitionResponse(BaseModel):
    """节点定义响应"""

    id: int
    node_type: str
    name: str
    category: str
    description: str | None
    default_config: dict[str, Any] | None
    input_schema: dict[str, Any] | None
    output_schema: dict[str, Any] | None
    icon: str | None
    is_active: bool

    model_config = ConfigDict(from_attributes=True)
