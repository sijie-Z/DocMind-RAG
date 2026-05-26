"""
Agent 工作流 Pydantic Schemas
"""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# --- 工作流节点定义 ---
class WorkflowNode(BaseModel):
    """工作流节点"""
    id: str = Field(..., description="节点唯一ID")
    type: str = Field(..., description="节点类型: llm_openai, llm_deepseek, llm_qwen, input, output, condition, tool_tts, tool_search")
    position: dict[str, float] = Field(default_factory=lambda: {"x": 0, "y": 0}, description="节点位置")
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
    """完整工作流配置"""
    nodes: list[WorkflowNode] = Field(default_factory=list, description="节点列表")
    edges: list[WorkflowEdge] = Field(default_factory=list, description="边列表")


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
