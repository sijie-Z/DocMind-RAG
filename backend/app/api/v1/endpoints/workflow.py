# -*- coding: utf-8 -*-
"""
Agent 工作流 API 端点
"""
import asyncio
import json
from datetime import datetime
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc

from app.core.database import get_db
from app.core.security import get_current_user
from app.models.user import User
from app.models.workflow import Workflow, WorkflowExecution, NodeDefinition
from app.schemas.workflow import (
    WorkflowCreate, WorkflowUpdate, WorkflowResponse,
    WorkflowExecuteRequest, ExecutionResponse, NodeDefinitionResponse
)
from app.services.workflow_engine import WorkflowEngine, WorkflowConfig
from app.exceptions import NotFoundError, ValidationError, AppError

router = APIRouter()


@router.get("/", response_model=Dict[str, Any])
async def list_workflows(
    skip: int = 0,
    limit: int = 20,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """获取工作流列表"""
    # 统计总数
    count_stmt = select(func.count(Workflow.id)).where(Workflow.is_active == True)
    total = (await db.execute(count_stmt)).scalar() or 0

    # 查询列表
    stmt = select(Workflow).where(Workflow.is_active == True).order_by(desc(Workflow.updated_at)).offset(skip).limit(limit)
    result = await db.execute(stmt)
    workflows = result.scalars().all()

    return {
        "success": True,
        "data": {
            "items": [
                {
                    "id": w.id,
                    "name": w.name,
                    "description": w.description,
                    "is_active": w.is_active,
                    "created_at": w.created_at.isoformat() if w.created_at else None,
                    "updated_at": w.updated_at.isoformat() if w.updated_at else None
                } for w in workflows
            ],
            "total": total
        }
    }


@router.post("/", response_model=Dict[str, Any])
async def create_workflow(
    workflow_in: WorkflowCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """创建工作流"""
    workflow = Workflow(
        name=workflow_in.name,
        description=workflow_in.description,
        flow_data=workflow_in.flow_data.model_dump() if workflow_in.flow_data else None,
        created_by=current_user.id
    )
    db.add(workflow)
    await db.commit()
    await db.refresh(workflow)

    return {
        "success": True,
        "data": {
            "id": workflow.id,
            "name": workflow.name,
            "description": workflow.description,
            "flow_data": workflow.flow_data,
            "created_at": workflow.created_at.isoformat()
        }
    }


@router.get("/{workflow_id}", response_model=Dict[str, Any])
async def get_workflow(
    workflow_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """获取工作流详情"""
    result = await db.execute(select(Workflow).where(Workflow.id == workflow_id))
    workflow = result.scalar_one_or_none()

    if not workflow:
        raise NotFoundError(detail="工作流不存在")

    return {
        "success": True,
        "data": {
            "id": workflow.id,
            "name": workflow.name,
            "description": workflow.description,
            "flow_data": workflow.flow_data,
            "is_active": workflow.is_active,
            "created_at": workflow.created_at.isoformat(),
            "updated_at": workflow.updated_at.isoformat()
        }
    }


@router.put("/{workflow_id}", response_model=Dict[str, Any])
async def update_workflow(
    workflow_id: int,
    workflow_in: WorkflowUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """更新工作流"""
    result = await db.execute(select(Workflow).where(Workflow.id == workflow_id))
    workflow = result.scalar_one_or_none()

    if not workflow:
        raise NotFoundError(detail="工作流不存在")

    update_data = workflow_in.model_dump(exclude_unset=True)
    if "flow_data" in update_data and update_data["flow_data"]:
        if hasattr(update_data["flow_data"], "model_dump"):
            update_data["flow_data"] = update_data["flow_data"].model_dump()

    for key, value in update_data.items():
        setattr(workflow, key, value)

    workflow.updated_at = datetime.now()
    await db.commit()
    await db.refresh(workflow)

    return {
        "success": True,
        "data": {
            "id": workflow.id,
            "name": workflow.name,
            "description": workflow.description,
            "flow_data": workflow.flow_data
        }
    }


@router.delete("/{workflow_id}", response_model=dict)
async def delete_workflow(
    workflow_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """删除工作流（软删除）"""
    result = await db.execute(select(Workflow).where(Workflow.id == workflow_id))
    workflow = result.scalar_one_or_none()

    if not workflow:
        raise NotFoundError(detail="工作流不存在")

    workflow.is_active = False
    await db.commit()

    return {"success": True, "message": "删除成功"}


@router.post("/{workflow_id}/execute")
async def execute_workflow(
    workflow_id: int,
    request: Request,
    body: Optional[Dict[str, Any]] = None,
    stream: bool = True,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """执行工作流 - 支持流式和非流式两种模式"""
    result = await db.execute(select(Workflow).where(Workflow.id == workflow_id, Workflow.is_active == True))
    workflow = result.scalar_one_or_none()

    if not workflow:
        raise NotFoundError(detail="工作流不存在")

    if not workflow.flow_data:
        raise ValidationError(detail="工作流配置为空")

    # 从请求体获取输入数据
    input_data = body if body else {}

    # 创建执行记录
    execution = WorkflowExecution(
        workflow_id=workflow_id,
        status="pending",
        input_data=input_data
    )
    db.add(execution)
    await db.commit()
    await db.refresh(execution)

    if stream:
        # 流式执行 - 使用 SSE
        async def event_generator():
            try:
                from app.schemas.workflow import WorkflowConfig as WConfig
                config = WConfig(**workflow.flow_data)

                engine = WorkflowEngine(config)

                # 收集所有事件
                events_queue = asyncio.Queue()

                async def callback(event_type: str, data: Dict[str, Any]):
                    await events_queue.put({"event": event_type, "data": data})

                engine.set_event_callback(callback)

                # 发送开始事件
                yield f"event: workflow_start\ndata: {json.dumps({'execution_id': execution.id, 'workflow_name': workflow.name}, ensure_ascii=False)}\n\n"

                # 更新执行状态
                execution.status = "running"
                execution.started_at = datetime.now()
                await db.commit()

                # 在后台执行工作流
                async def run_workflow():
                    try:
                        result = await engine.execute(input_data)
                        await events_queue.put({"event": "workflow_result", "data": result})
                    except Exception as e:
                        await events_queue.put({"event": "workflow_error", "data": {"error": str(e)}})

                # 启动执行任务
                task = asyncio.create_task(run_workflow())

                # 实时发送事件
                while True:
                    try:
                        event = await asyncio.wait_for(events_queue.get(), timeout=30.0)
                        event_type = event["event"]
                        event_data = event["data"]

                        if event_type == "workflow_result":
                            # 执行完成
                            execution.status = "completed"
                            execution.completed_at = datetime.now()
                            execution.output_data = event_data
                            execution.node_results = [r.model_dump() for r in engine.get_node_results()]
                            await db.commit()

                            # 发送节点结果
                            for node_result in engine.get_node_results():
                                yield f"event: node_complete\ndata: {json.dumps(node_result.model_dump(), ensure_ascii=False)}\n\n"

                            # 发送最终结果
                            yield f"event: complete\ndata: {json.dumps(event_data, ensure_ascii=False)}\n\n"
                            break
                        elif event_type == "workflow_error":
                            execution.status = "failed"
                            execution.error_message = event_data.get("error")
                            execution.completed_at = datetime.now()
                            await db.commit()

                            yield f"event: error\ndata: {json.dumps(event_data, ensure_ascii=False)}\n\n"
                            break
                        else:
                            yield f"event: {event_type}\ndata: {json.dumps(event_data, ensure_ascii=False)}\n\n"
                    except asyncio.TimeoutError:
                        # 发送心跳保持连接
                        yield f"event: heartbeat\ndata: {json.dumps({'timestamp': datetime.now().isoformat()})}\n\n"
                    except Exception as e:
                        yield f"event: error\ndata: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"
                        break

                # 等待执行任务完成
                await task

            except Exception as e:
                execution.status = "failed"
                execution.error_message = str(e)
                execution.completed_at = datetime.now()
                await db.commit()

                yield f"event: error\ndata: {json.dumps({'error': str(e)}, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no"
            }
        )
    else:
        # 非流式执行
        try:
            from app.schemas.workflow import WorkflowConfig as WConfig
            config = WConfig(**workflow.flow_data)

            engine = WorkflowEngine(config)

            execution.status = "running"
            execution.started_at = datetime.now()
            await db.commit()

            result = await engine.execute(input_data)

            execution.status = "completed"
            execution.completed_at = datetime.now()
            execution.output_data = result
            execution.node_results = [r.model_dump() for r in engine.get_node_results()]
            await db.commit()

            return {
                "success": True,
                "data": {
                    "execution_id": execution.id,
                    "status": execution.status,
                    "output": result,
                    "node_results": execution.node_results
                }
            }

        except Exception as e:
            execution.status = "failed"
            execution.error_message = str(e)
            execution.completed_at = datetime.now()
            await db.commit()

            raise AppError(detail=str(e))


@router.get("/executions/{execution_id}", response_model=Dict[str, Any])
async def get_execution(
    execution_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """获取执行记录详情"""
    result = await db.execute(select(WorkflowExecution).where(WorkflowExecution.id == execution_id))
    execution = result.scalar_one_or_none()

    if not execution:
        raise NotFoundError(detail="执行记录不存在")

    return {
        "success": True,
        "data": {
            "id": execution.id,
            "workflow_id": execution.workflow_id,
            "status": execution.status,
            "input_data": execution.input_data,
            "output_data": execution.output_data,
            "node_results": execution.node_results,
            "error_message": execution.error_message,
            "started_at": execution.started_at.isoformat() if execution.started_at else None,
            "completed_at": execution.completed_at.isoformat() if execution.completed_at else None,
            "created_at": execution.created_at.isoformat()
        }
    }


@router.get("/nodes/definitions", response_model=Dict[str, Any])
async def get_node_definitions(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """获取节点定义列表"""
    result = await db.execute(select(NodeDefinition).where(NodeDefinition.is_active == True))
    definitions = result.scalars().all()

    # 如果没有定义，初始化默认节点
    if not definitions:
        default_nodes = [
            NodeDefinition(
                node_type="input",
                name="输入节点",
                category="io",
                description="工作流的输入入口，定义用户输入的提示词模板",
                default_config={"prompt": ""},
                input_schema={},
                output_schema={"text": "string"},
                icon="EditOutlined"
            ),
            NodeDefinition(
                node_type="output",
                name="输出节点",
                category="io",
                description="工作流的输出出口，返回最终结果",
                default_config={},
                input_schema={"text": "string"},
                output_schema={"output": "string"},
                icon="ExportOutlined"
            ),
            NodeDefinition(
                node_type="llm_openai",
                name="OpenAI GPT",
                category="llm",
                description="使用 OpenAI GPT 模型进行文本生成",
                default_config={"model": "gpt-4o-mini", "temperature": 0.7, "systemPrompt": ""},
                input_schema={"text": "string"},
                output_schema={"content": "string"},
                icon="RobotOutlined"
            ),
            NodeDefinition(
                node_type="llm_deepseek",
                name="DeepSeek",
                category="llm",
                description="使用 DeepSeek 模型进行文本生成",
                default_config={"model": "deepseek-v4-flash", "temperature": 0.7, "systemPrompt": ""},
                input_schema={"text": "string"},
                output_schema={"content": "string"},
                icon="RobotOutlined"
            ),
            NodeDefinition(
                node_type="llm_qwen",
                name="通义千问",
                category="llm",
                description="使用阿里云通义千问模型",
                default_config={"model": "qwen-plus", "temperature": 0.7, "systemPrompt": ""},
                input_schema={"text": "string"},
                output_schema={"content": "string"},
                icon="RobotOutlined"
            ),
            NodeDefinition(
                node_type="tool_search",
                name="知识库检索",
                category="tool",
                description="从知识库中检索相关文档内容",
                default_config={"top_k": 5},
                input_schema={"query": "string"},
                output_schema={"context": "string", "sources": "array"},
                icon="SearchOutlined"
            ),
            NodeDefinition(
                node_type="tool_tts",
                name="语音合成",
                category="tool",
                description="将文本转换为语音",
                default_config={"voice": "alloy"},
                input_schema={"text": "string"},
                output_schema={"audio_url": "string"},
                icon="SoundOutlined"
            ),
            NodeDefinition(
                node_type="condition",
                name="条件分支",
                category="logic",
                description="根据条件选择不同的执行路径",
                default_config={"condition": ""},
                input_schema={"text": "string"},
                output_schema={"result": "string"},
                icon="BranchesOutlined"
            ),
            NodeDefinition(
                node_type="router",
                name="智能路由",
                category="logic",
                description="使用LLM进行智能内容路由",
                default_config={"routes": []},
                input_schema={"text": "string"},
                output_schema={"route_result": "string"},
                icon="CompassOutlined"
            ),
            NodeDefinition(
                node_type="memory",
                name="记忆节点",
                category="data",
                description="Agent记忆系统，支持存储和检索",
                default_config={"memoryType": "short_term", "action": "store"},
                input_schema={"text": "string"},
                output_schema={"memory": "object"},
                icon="DatabaseOutlined"
            ),
            NodeDefinition(
                node_type="code",
                name="代码执行",
                category="data",
                description="执行Python代码进行数据处理",
                default_config={"language": "python", "code": ""},
                input_schema={"input": "object"},
                output_schema={"result": "any"},
                icon="CodeOutlined"
            ),
            NodeDefinition(
                node_type="api_call",
                name="API调用",
                category="data",
                description="调用外部HTTP API",
                default_config={"method": "GET", "url": "", "timeout": 30},
                input_schema={"params": "object"},
                output_schema={"response": "object"},
                icon="CloudOutlined"
            ),
            NodeDefinition(
                node_type="transform",
                name="数据转换",
                category="data",
                description="JSON提取、文本处理、正则匹配",
                default_config={"transformType": "json_extract", "jsonPath": "$"},
                input_schema={"data": "any"},
                output_schema={"result": "any"},
                icon="FilterOutlined"
            ),
        ]

        for node in default_nodes:
            db.add(node)
        await db.commit()

        result = await db.execute(select(NodeDefinition).where(NodeDefinition.is_active == True))
        definitions = result.scalars().all()

    return {
        "success": True,
        "data": [
            {
                "id": d.id,
                "node_type": d.node_type,
                "name": d.name,
                "category": d.category,
                "description": d.description,
                "default_config": d.default_config,
                "input_schema": d.input_schema,
                "output_schema": d.output_schema,
                "icon": d.icon
            } for d in definitions
        ]
    }
