# -*- coding: utf-8 -*-
"""
Agent 工作流 Pydantic Schemas
"""
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, ConfigDict, Field
from datetime import datetime


# --- 工作流节点定义 ---
class WorkflowNode(BaseModel):
    """工作流节点"""
    id: str = Field(..., description="节点唯一ID")
    type: str = Field(..., description="节点类型: llm_openai, llm_deepseek, llm_qwen, input, output, condition, tool_tts, tool_search")
    position: Dict[str, float] = Field(default_factory=lambda: {"x": 0, "y": 0}, description="节点位置")
    data: Dict[str, Any] = Field(default_factory=dict, description="节点配置数据")

    model_config = ConfigDict(extra="allow")


class WorkflowEdge(BaseModel):
    """工作流边（连接线）"""
    id: str = Field(..., description="边唯一ID")
    source: str = Field(..., description="源节点ID")
    target: str = Field(..., description="目标节点ID")
    sourceHandle: Optional[str] = Field(None, description="源节点输出端口")
    targetHandle: Optional[str] = Field(None, description="目标节点输入端口")
    label: Optional[str] = Field(None, description="边标签（条件分支时使用）")


class WorkflowConfig(BaseModel):
    """完整工作流配置"""
    nodes: List[WorkflowNode] = Field(default_factory=list, description="节点列表")
    edges: List[WorkflowEdge] = Field(default_factory=list, description="边列表")


# --- API 请求/响应 ---
class WorkflowCreate(BaseModel):
    """创建工作流"""
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    flow_data: Optional[WorkflowConfig] = None


class WorkflowUpdate(BaseModel):
    """更新工作流"""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    flow_data: Optional[WorkflowConfig] = None
    is_active: Optional[bool] = None


class WorkflowResponse(BaseModel):
    """工作流响应"""
    id: int
    name: str
    description: Optional[str]
    flow_data: Optional[Dict[str, Any]]
    is_active: bool
    created_by: Optional[int]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class WorkflowExecuteRequest(BaseModel):
    """执行工作流请求"""
    workflow_id: int
    input_data: Optional[Dict[str, Any]] = None
    stream: bool = Field(default=True, description="是否流式输出")


class NodeResult(BaseModel):
    """节点执行结果"""
    node_id: str
    node_type: str
    status: str  # pending, running, completed, failed
    input: Optional[Dict[str, Any]] = None
    output: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class ExecutionResponse(BaseModel):
    """执行响应"""
    execution_id: int
    workflow_id: int
    status: str
    input_data: Optional[Dict[str, Any]]
    output_data: Optional[Dict[str, Any]]
    node_results: Optional[List[NodeResult]]
    error_message: Optional[str]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class NodeDefinitionResponse(BaseModel):
    """节点定义响应"""
    id: int
    node_type: str
    name: str
    category: str
    description: Optional[str]
    default_config: Optional[Dict[str, Any]]
    input_schema: Optional[Dict[str, Any]]
    output_schema: Optional[Dict[str, Any]]
    icon: Optional[str]
    is_active: bool

    model_config = ConfigDict(from_attributes=True)