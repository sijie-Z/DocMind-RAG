# -*- coding: utf-8 -*-
"""
Agent 工作流数据库模型
"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, JSON, Float, ForeignKey
from sqlalchemy.orm import relationship
from app.core.database import Base


class Workflow(Base):
    """工作流定义"""
    __tablename__ = "workflows"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, comment="工作流名称")
    description = Column(Text, nullable=True, comment="工作流描述")
    flow_data = Column(JSON, nullable=True, comment="工作流节点和边配置")
    is_active = Column(Boolean, default=True, comment="是否启用")
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    # 关系
    executions = relationship("WorkflowExecution", back_populates="workflow", lazy="dynamic")


class WorkflowExecution(Base):
    """工作流执行记录"""
    __tablename__ = "workflow_executions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workflow_id = Column(Integer, ForeignKey("workflows.id"), nullable=False)
    status = Column(String(20), default="pending", comment="执行状态: pending, running, completed, failed")
    input_data = Column(JSON, nullable=True, comment="输入数据")
    output_data = Column(JSON, nullable=True, comment="输出结果")
    node_results = Column(JSON, nullable=True, comment="各节点执行结果")
    error_message = Column(Text, nullable=True, comment="错误信息")
    started_at = Column(DateTime, nullable=True, comment="开始时间")
    completed_at = Column(DateTime, nullable=True, comment="完成时间")
    created_at = Column(DateTime, default=datetime.now)

    # 关系
    workflow = relationship("Workflow", back_populates="executions")


class NodeDefinition(Base):
    """节点定义"""
    __tablename__ = "node_definitions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    node_type = Column(String(50), nullable=False, unique=True, comment="节点类型标识")
    name = Column(String(100), nullable=False, comment="节点显示名称")
    category = Column(String(50), default="llm", comment="节点分类: llm, tool, io, logic")
    description = Column(Text, nullable=True, comment="节点描述")
    default_config = Column(JSON, nullable=True, comment="默认配置")
    input_schema = Column(JSON, nullable=True, comment="输入参数定义")
    output_schema = Column(JSON, nullable=True, comment="输出参数定义")
    icon = Column(String(50), nullable=True, comment="图标名称")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)
