# -*- coding: utf-8 -*-
"""
Agent 工作流引擎
基于 LangGraph 实现的状态图工作流引擎
支持：多LLM、工具调用、条件分支、循环、记忆系统、错误重试
"""
import asyncio
import json
import re
from datetime import datetime
from typing import Dict, Any, List, Optional, Callable, TypedDict, Annotated
from enum import Enum
import logging
import traceback

from langgraph.graph import StateGraph, END
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, BaseMessage
from langchain_openai import ChatOpenAI

from app.core.config import settings
from app.schemas.workflow import WorkflowConfig, WorkflowNode, WorkflowEdge, NodeResult

logger = logging.getLogger(__name__)


class ExecutionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class WorkflowState(TypedDict):
    """工作流状态 - 完整版"""
    input: Dict[str, Any]           # 用户输入
    messages: List[Any]             # 对话消息历史
    current_node: Optional[str]     # 当前执行节点
    node_outputs: Dict[str, Any]    # 各节点输出结果
    context: Dict[str, Any]         # 共享上下文（用于节点间传递数据）
    errors: List[str]               # 错误列表
    memory: Dict[str, Any]          # Agent 短期记忆
    iteration_count: int            # 迭代计数（用于循环控制）
    tool_results: List[Dict]        # 工具调用结果历史


def merge_dicts(left: Dict, right: Dict) -> Dict:
    """合并字典的 reducer 函数"""
    return {**left, **right}


def merge_lists(left: List, right: List) -> List:
    """合并列表的 reducer 函数"""
    return left + right


class NodeExecutor:
    """节点执行器基类"""

    def __init__(self, node: WorkflowNode, config: Dict[str, Any] = None):
        self.node = node
        self.config = config or {}
        self.llm_config = config.get("llm", {})
        self.max_retries = config.get("max_retries", 3)
        self.retry_delay = config.get("retry_delay", 1.0)

    async def execute(self, state: WorkflowState) -> Dict[str, Any]:
        """执行节点逻辑"""
        raise NotImplementedError

    async def execute_with_retry(self, state: WorkflowState) -> Dict[str, Any]:
        """带重试的执行"""
        last_error = None
        for attempt in range(self.max_retries):
            try:
                return await self.execute(state)
            except Exception as e:
                last_error = e
                logger.warning(f"节点 {self.node.id} 执行失败 (尝试 {attempt + 1}/{self.max_retries}): {str(e)}")
                if attempt < self.max_retries - 1:
                    await asyncio.sleep(self.retry_delay * (2 ** attempt))  # 指数退避

        raise last_error

    def get_llm(self, model_type: str = "openai") -> ChatOpenAI:
        """获取 LLM 实例"""
        llm_settings = self.llm_config.get(model_type, {})
        node_data = self.node.data or {}

        if model_type in ["openai", "deepseek", "aiping"]:
            api_key = llm_settings.get("api_key") or getattr(settings, 'OPENAI_API_KEY', '')
            base_url = llm_settings.get("base_url")

            if model_type == "deepseek":
                base_url = base_url or "https://api.deepseek.com/v1"
                model = node_data.get("model") or llm_settings.get("model", "deepseek-chat")
            elif model_type == "aiping":
                base_url = base_url or getattr(settings, 'AIPING_BASE_URL', '')
                model = node_data.get("model") or llm_settings.get("model", "gpt-4")
            else:
                model = node_data.get("model") or llm_settings.get("model", "gpt-4o-mini")

            temperature = node_data.get("temperature", llm_settings.get("temperature", 0.7))
            max_tokens = node_data.get("maxTokens", node_data.get("max_tokens", 2048))

            return ChatOpenAI(
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                api_key=api_key,
                base_url=base_url
            )
        elif model_type == "qwen":
            # 通义千问
            from langchain_community.chat_models import ChatTongyi
            return ChatTongyi(
                model=node_data.get("model") or llm_settings.get("model", "qwen-plus"),
                dashscope_api_key=llm_settings.get("api_key") or getattr(settings, 'DASHSCOPE_API_KEY', '')
            )

        return None


class InputNodeExecutor(NodeExecutor):
    """输入节点执行器"""

    async def execute(self, state: WorkflowState) -> Dict[str, Any]:
        prompt_template = self.node.data.get("prompt", "")
        user_input = state.get("input", {}).get("text", "")

        # 替换模板变量
        if "{{input}}" in prompt_template:
            prompt = prompt_template.replace("{{input}}", user_input)
        else:
            prompt = f"{prompt_template}\n\n用户输入: {user_input}" if prompt_template else user_input

        return {
            "messages": [HumanMessage(content=prompt)],
            "context": {"input_prompt": prompt, "original_input": user_input}
        }


class LLMNodeExecutor(NodeExecutor):
    """LLM 节点执行器 - 支持流式输出"""

    async def execute(self, state: WorkflowState) -> Dict[str, Any]:
        node_type = self.node.type
        model_type = node_type.replace("llm_", "")

        llm = self.get_llm(model_type)
        if not llm:
            raise ValueError(f"Unsupported LLM type: {model_type}")

        # 获取系统提示
        system_prompt = self.node.data.get("systemPrompt", "你是一个有用的AI助手。")

        # 构建消息
        messages = [SystemMessage(content=system_prompt)]

        # 添加历史消息
        history_messages = state.get("messages", [])
        for msg in history_messages[-10:]:  # 限制历史消息数量
            messages.append(msg)

        # 添加知识库上下文（如果有）
        knowledge_context = state.get("context", {}).get("knowledge_context", "")
        if knowledge_context:
            context_message = HumanMessage(content=f"参考资料：\n{knowledge_context}")
            messages.insert(1, context_message)

        # 调用 LLM
        response = await llm.ainvoke(messages)

        return {
            "messages": [response],
            "node_outputs": {self.node.id: {"content": response.content, "model": model_type}},
            "context": {"last_response": response.content}
        }


class KnowledgeSearchNodeExecutor(NodeExecutor):
    """知识库搜索节点执行器"""

    async def execute(self, state: WorkflowState) -> Dict[str, Any]:
        from app.services.rag_service import RAGService

        # 获取查询
        query = state.get("input", {}).get("text", "")
        if not query:
            # 尝试从消息中获取
            messages = state.get("messages", [])
            if messages:
                last_msg = messages[-1]
                query = last_msg.content if hasattr(last_msg, 'content') else str(last_msg)

        # 获取配置
        top_k = self.node.data.get("topK", self.node.data.get("top_k", 5))
        score_threshold = self.node.data.get("scoreThreshold", self.node.data.get("score_threshold", 0.5))

        # 搜索知识库
        rag_service = RAGService()
        organization_id = state.get("input", {}).get("organization_id", 1)
        docs = await rag_service.search(query, organization_id=organization_id, top_k=top_k)

        # 过滤低相关性结果
        filtered_docs = [d for d in docs if d.get("score", 0) >= score_threshold]

        # 构建上下文
        context = "\n\n".join([doc.get("content", "") for doc in filtered_docs[:3]])

        # 构建来源信息
        sources = [{"title": d.get("title", "未知"), "score": d.get("score", 0)} for d in filtered_docs]

        return {
            "context": {"knowledge_context": context, "knowledge_sources": sources},
            "messages": [HumanMessage(content=f"找到 {len(filtered_docs)} 条相关参考资料")]
        }


class ConditionNodeExecutor(NodeExecutor):
    """条件分支节点执行器"""

    async def execute(self, state: WorkflowState) -> Dict[str, Any]:
        condition = self.node.data.get("condition", "")
        context = state.get("context", {})
        messages = state.get("messages", [])
        input_text = state.get("input", {}).get("text", "")

        # 获取最后一条消息
        last_message = ""
        for msg in reversed(messages):
            if hasattr(msg, 'content'):
                last_message = msg.content
                break

        # 条件判断逻辑
        result = self._evaluate_condition(condition, {
            "text": input_text,
            "last_message": last_message,
            "context": context
        })

        return {
            "context": {"condition_result": result},
            "node_outputs": {self.node.id: {"condition_result": result}}
        }

    def _evaluate_condition(self, condition: str, variables: Dict[str, Any]) -> str:
        """评估条件表达式"""
        if not condition:
            return "default"

        text = variables.get("text", "")
        last_message = variables.get("last_message", "")
        combined_text = f"{text} {last_message}".lower()

        # 支持的条件函数
        condition_lower = condition.lower()

        if "翻译" in combined_text or "translate" in combined_text:
            return "translate"
        elif "总结" in combined_text or "摘要" in combined_text or "summarize" in combined_text:
            return "summarize"
        elif "代码" in combined_text or "编程" in combined_text or "code" in combined_text:
            return "code"
        elif "分析" in combined_text or "analyze" in combined_text:
            return "analyze"
        else:
            return "default"


class OutputNodeExecutor(NodeExecutor):
    """输出节点执行器"""

    async def execute(self, state: WorkflowState) -> Dict[str, Any]:
        messages = state.get("messages", [])
        context = state.get("context", {})
        output = ""

        # 优先从上下文获取最终输出
        if context.get("final_output"):
            output = context["final_output"]
        else:
            # 从消息中获取最后的 AI 回复
            for msg in reversed(messages):
                if isinstance(msg, AIMessage):
                    output = msg.content
                    break

        return {
            "node_outputs": {self.node.id: {"output": output}},
            "context": {"final_output": output}
        }


class TTSNodeExecutor(NodeExecutor):
    """TTS 语音合成节点执行器"""

    async def execute(self, state: WorkflowState) -> Dict[str, Any]:
        messages = state.get("messages", [])
        text = ""

        for msg in reversed(messages):
            if isinstance(msg, AIMessage):
                text = msg.content
                break

        if not text:
            text = state.get("context", {}).get("final_output", "")

        # TODO: 实现实际的 TTS 调用
        audio_url = f"/api/v1/tts/generate?text={text[:100]}"

        return {
            "node_outputs": {self.node.id: {"audio_url": audio_url, "text": text[:500]}}
        }


class MemoryNodeExecutor(NodeExecutor):
    """记忆节点执行器 - 集成 Agent 记忆系统"""

    async def execute(self, state: WorkflowState) -> Dict[str, Any]:
        from app.services.memory_service import get_memory_system

        memory_type = self.node.data.get("memoryType", "short_term")
        action = self.node.data.get("action", "store")
        agent_id = self.node.data.get("agentId", "default")

        memory_system = get_memory_system(agent_id)
        current_memory = state.get("memory", {})

        if action == "store":
            # 存储记忆
            messages = state.get("messages", [])
            importance = self.node.data.get("importance", 0.6)

            # 提取最后的交互
            user_msg = ""
            assistant_msg = ""
            for i in range(len(messages) - 1, -1, -1):
                if isinstance(messages[i], AIMessage) and not assistant_msg:
                    assistant_msg = messages[i].content
                elif isinstance(messages[i], HumanMessage) and not user_msg:
                    user_msg = messages[i].content
                    break

            if user_msg or assistant_msg:
                if user_msg:
                    await memory_system.remember(
                        f"用户: {user_msg}",
                        memory_type="short_term",
                        importance=importance
                    )
                if assistant_msg:
                    await memory_system.remember(
                        f"助手: {assistant_msg}",
                        memory_type="short_term",
                        importance=importance * 0.8
                    )

            # 更新工作记忆
            if memory_type not in current_memory:
                current_memory[memory_type] = []
            current_memory[memory_type].append({
                "user": user_msg,
                "assistant": assistant_msg,
                "timestamp": datetime.now().isoformat()
            })
            # 保留最近20条
            current_memory[memory_type] = current_memory[memory_type][-20:]

        elif action == "retrieve":
            # 检索记忆
            query = state.get("input", {}).get("text", "")
            top_k = self.node.data.get("topK", 5)

            memories = await memory_system.recall(
                query,
                memory_types=[memory_type] if memory_type != "all" else None,
                top_k=top_k
            )

            # 构建记忆上下文
            if memories:
                context_text = "相关历史记忆：\n" + "\n".join([
                    f"- {m.get('content', m.get('lesson', str(m)))}"
                    for m in memories[:5]
                ])
            else:
                context_text = ""

            return {
                "context": {"retrieved_memories": memories, "memory_context": context_text},
                "memory": current_memory,
                "node_outputs": {self.node.id: {"memories": memories, "context": context_text}}
            }

        elif action == "reflect":
            # 反思操作 - 提取洞察
            await memory_system._auto_reflect()
            insights = memory_system.reflective.get_relevant_insights(
                state.get("input", {}).get("text", ""),
                top_k=5
            )
            lessons = memory_system.reflective.lessons[-5:] if memory_system.reflective.lessons else []

            return {
                "context": {"insights": insights, "lessons": lessons},
                "memory": current_memory,
                "node_outputs": {self.node.id: {"insights": insights, "lessons": lessons}}
            }

        elif action == "clear":
            # 清空指定类型记忆
            if memory_type in current_memory:
                current_memory[memory_type] = []

        return {"memory": current_memory}


class CodeExecuteNodeExecutor(NodeExecutor):
    """代码执行节点执行器"""

    async def execute(self, state: WorkflowState) -> Dict[str, Any]:
        code = self.node.data.get("code", "")
        language = self.node.data.get("language", "python")

        if not code:
            raise ValueError("代码执行节点需要提供代码")

        # 安全模式：限制危险操作
        dangerous_keywords = ["import os", "import sys", "subprocess", "eval", "exec", "__import__"]
        for kw in dangerous_keywords:
            if kw in code:
                raise ValueError(f"安全限制：代码包含不允许的关键字 '{kw}'")

        # 准备执行上下文
        context_vars = {
            "input": state.get("input", {}),
            "context": state.get("context", {}),
            "result": None
        }

        try:
            # 执行代码（受限环境）
            exec_globals = {"__builtins__": __builtins__}
            exec_locals = context_vars.copy()
            exec(code, exec_globals, exec_locals)
            result = exec_locals.get("result", "执行完成，无返回结果")
        except Exception as e:
            result = f"执行错误: {str(e)}"

        return {
            "node_outputs": {self.node.id: {"result": str(result), "language": language}},
            "context": {"code_result": result}
        }


class APICallNodeExecutor(NodeExecutor):
    """API 调用节点执行器"""

    async def execute(self, state: WorkflowState) -> Dict[str, Any]:
        import httpx

        url = self.node.data.get("url", "")
        method = self.node.data.get("method", "GET").upper()
        headers = self.node.data.get("headers", {})
        body_template = self.node.data.get("body", "{}")
        timeout = self.node.data.get("timeout", 30)

        if not url:
            raise ValueError("API 调用节点需要提供 URL")

        # 替换模板变量
        context = state.get("context", {})
        input_data = state.get("input", {})

        for key, value in {**context, **input_data}.items():
            if isinstance(value, (str, int, float)):
                body_template = body_template.replace(f"{{{{{key}}}}}", str(value))
                url = url.replace(f"{{{{{key}}}}}", str(value))

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                if method == "GET":
                    response = await client.get(url, headers=headers)
                elif method == "POST":
                    response = await client.post(url, headers=headers, content=body_template)
                elif method == "PUT":
                    response = await client.put(url, headers=headers, content=body_template)
                elif method == "DELETE":
                    response = await client.delete(url, headers=headers)
                else:
                    raise ValueError(f"不支持的方法: {method}")

                result = response.json() if response.headers.get("content-type", "").startswith("application/json") else response.text

                return {
                    "node_outputs": {self.node.id: {
                        "status_code": response.status_code,
                        "response": result
                    }},
                    "context": {"api_result": result}
                }
        except Exception as e:
            raise ValueError(f"API 调用失败: {str(e)}")


class TransformNodeExecutor(NodeExecutor):
    """数据转换节点执行器"""

    async def execute(self, state: WorkflowState) -> Dict[str, Any]:
        transform_type = self.node.data.get("transformType", "json_extract")

        context = state.get("context", {})
        input_data = state.get("node_outputs", {})
        last_output = self.node.data.get("inputSource", "last")

        # 获取输入数据
        if last_output == "last":
            source_data = context.get("last_response", "")

            # JSON 提取
            if transform_type == "json_extract":
                json_path = self.node.data.get("jsonPath", "$")
                try:
                    import json
                    data = json.loads(source_data) if isinstance(source_data, str) else source_data
                    # 简单路径解析
                    for key in json_path.split(".")[1:]:
                        if key.isdigit():
                            data = data[int(key)]
                        elif key in data:
                            data = data[key]
                        else:
                            break
                    result = data
                except Exception as e:
                    result = {"error": str(e)}

            # 文本截取
            elif transform_type == "text_slice":
                start = self.node.data.get("startIndex", 0)
                end = self.node.data.get("endIndex", len(str(source_data)))
                result = str(source_data)[start:end]

            # 正则提取
            elif transform_type == "regex_extract":
                pattern = self.node.data.get("pattern", "")
                matches = re.findall(pattern, str(source_data))
                result = matches[0] if matches else ""

            else:
                result = source_data

        else:
            result = input_data.get(last_output, {})

        return {
            "context": {"transformed_data": result},
            "node_outputs": {self.node.id: {"result": result}}
        }


class RouterNodeExecutor(NodeExecutor):
    """路由节点执行器 - 根据内容智能路由"""

    async def execute(self, state: WorkflowState) -> Dict[str, Any]:
        routes = self.node.data.get("routes", [])
        input_text = state.get("input", {}).get("text", "").lower()

        matched_route = "default"
        for route in routes:
            keywords = route.get("keywords", [])
            if any(kw.lower() in input_text for kw in keywords):
                matched_route = route.get("target", "default")
                break

        return {
            "context": {"route_result": matched_route},
            "node_outputs": {self.node.id: {"matched_route": matched_route}}
        }


class WorkflowEngine:
    """工作流引擎 - 基于 LangGraph"""

    def __init__(self, workflow_config: WorkflowConfig, llm_config: Dict[str, Any] = None):
        self.config = workflow_config
        self.llm_config = llm_config or {}
        self.nodes = {node.id: node for node in workflow_config.nodes}
        self.edges = workflow_config.edges
        self.node_results: Dict[str, NodeResult] = {}
        self.event_callback: Optional[Callable] = None

    def set_event_callback(self, callback: Callable):
        """设置事件回调函数（用于 SSE 推送）"""
        self.event_callback = callback

    async def emit_event(self, event_type: str, data: Dict[str, Any]):
        """发送事件"""
        if self.event_callback:
            await self.event_callback(event_type, data)

    def get_executor(self, node: WorkflowNode) -> NodeExecutor:
        """获取节点执行器"""
        executors = {
            "input": InputNodeExecutor,
            "output": OutputNodeExecutor,
            "llm_openai": LLMNodeExecutor,
            "llm_deepseek": LLMNodeExecutor,
            "llm_qwen": LLMNodeExecutor,
            "llm_aiping": LLMNodeExecutor,
            "tool_search": KnowledgeSearchNodeExecutor,
            "tool_tts": TTSNodeExecutor,
            "condition": ConditionNodeExecutor,
            "memory": MemoryNodeExecutor,
            "code": CodeExecuteNodeExecutor,
            "api_call": APICallNodeExecutor,
            "transform": TransformNodeExecutor,
            "router": RouterNodeExecutor,
        }

        executor_class = executors.get(node.type)
        if not executor_class:
            raise ValueError(f"Unknown node type: {node.type}")

        return executor_class(node, self.llm_config)

    def build_graph(self) -> StateGraph:
        """构建 LangGraph 状态图"""
        workflow = StateGraph(WorkflowState)

        # 添加节点
        for node_id, node in self.nodes.items():
            async def node_func(state: WorkflowState, _node=node) -> Dict[str, Any]:
                executor = self.get_executor(_node)

                # 记录开始
                started_at = datetime.now()
                self.node_results[_node.id] = NodeResult(
                    node_id=_node.id,
                    node_type=_node.type,
                    status="running",
                    started_at=started_at
                )
                await self.emit_event("node_start", {
                    "node_id": _node.id,
                    "node_type": _node.type,
                    "timestamp": started_at.isoformat()
                })

                try:
                    result = await executor.execute_with_retry(state)
                    completed_at = datetime.now()
                    duration = int((completed_at - started_at).total_seconds() * 1000)

                    # 更新状态
                    state_updates = {"current_node": _node.id}
                    if "messages" in result:
                        state_updates["messages"] = result["messages"]
                    if "node_outputs" in result:
                        state_updates["node_outputs"] = {**state.get("node_outputs", {}), **result["node_outputs"]}
                    if "context" in result:
                        state_updates["context"] = {**state.get("context", {}), **result["context"]}
                    if "memory" in result:
                        state_updates["memory"] = result["memory"]
                    if "errors" in result:
                        state_updates["errors"] = result.get("errors", [])

                    # 记录完成
                    self.node_results[_node.id].status = "completed"
                    self.node_results[_node.id].output = result
                    self.node_results[_node.id].completed_at = completed_at
                    self.node_results[_node.id].duration = duration

                    await self.emit_event("node_complete", {
                        "node_id": _node.id,
                        "node_type": _node.type,
                        "output": result,
                        "duration": duration,
                        "timestamp": completed_at.isoformat()
                    })

                    return state_updates

                except Exception as e:
                    completed_at = datetime.now()
                    duration = int((completed_at - started_at).total_seconds() * 1000)

                    self.node_results[_node.id].status = "failed"
                    self.node_results[_node.id].error = str(e)
                    self.node_results[_node.id].completed_at = completed_at
                    self.node_results[_node.id].duration = duration

                    await self.emit_event("node_error", {
                        "node_id": _node.id,
                        "error": str(e),
                        "traceback": traceback.format_exc(),
                        "timestamp": completed_at.isoformat()
                    })

                    return {"errors": [str(e)]}

            workflow.add_node(node_id, node_func)

        # 构建边映射
        edge_map: Dict[str, List[Dict]] = {}
        for edge in self.edges:
            if edge.source not in edge_map:
                edge_map[edge.source] = []
            edge_map[edge.source].append({
                "target": edge.target,
                "label": edge.label,
                "sourceHandle": edge.sourceHandle
            })

        # 设置入口点（input 节点或第一个节点）
        entry_node = None
        for node_id, node in self.nodes.items():
            if node.type == "input":
                entry_node = node_id
                break

        if not entry_node and self.nodes:
            entry_node = list(self.nodes.keys())[0]

        if entry_node:
            workflow.set_entry_point(entry_node)

        # 添加边
        for source, targets in edge_map.items():
            if len(targets) == 1:
                # 单一目标
                workflow.add_edge(source, targets[0]["target"])
            elif len(targets) > 1:
                # 多目标：条件分支
                def make_router(src: str, tgts: List[Dict]):
                    async def router(state: WorkflowState) -> str:
                        condition_result = state.get("context", {}).get("condition_result", "default")
                        route_result = state.get("context", {}).get("route_result", "default")

                        # 优先使用路由结果
                        if route_result != "default":
                            for edge in tgts:
                                if edge.get("label", "").lower() == route_result.lower():
                                    return edge["target"]

                        # 使用条件结果
                        for edge in tgts:
                            if edge.get("label") and edge["label"].lower() == condition_result.lower():
                                return edge["target"]

                        # 默认返回第一个目标
                        return tgts[0]["target"]

                    return router

                workflow.add_conditional_edges(source, make_router(source, targets))

        # 添加到 END
        for node_id, node in self.nodes.items():
            if node.type == "output":
                workflow.add_edge(node_id, END)

        return workflow

    async def execute(self, input_data: Dict[str, Any] = None) -> Dict[str, Any]:
        """执行工作流"""
        # 初始化状态
        initial_state: WorkflowState = {
            "input": input_data or {},
            "messages": [],
            "current_node": None,
            "node_outputs": {},
            "context": {},
            "errors": [],
            "memory": {},
            "iteration_count": 0,
            "tool_results": []
        }

        # 构建图
        graph = self.build_graph()

        # 编译并执行
        app = graph.compile()

        await self.emit_event("workflow_start", {
            "workflow_id": self.config.id if hasattr(self.config, 'id') else None,
            "timestamp": datetime.now().isoformat(),
            "input": input_data
        })

        try:
            result = await app.ainvoke(initial_state)

            final_output = result.get("context", {}).get("final_output", "")

            await self.emit_event("workflow_complete", {
                "output": final_output,
                "node_count": len(self.node_results),
                "success_count": sum(1 for r in self.node_results.values() if r.status == "completed"),
                "timestamp": datetime.now().isoformat()
            })

            return {
                "success": True,
                "output": final_output,
                "node_outputs": result.get("node_outputs", {}),
                "context": result.get("context", {}),
                "memory": result.get("memory", {})
            }

        except Exception as e:
            await self.emit_event("workflow_error", {
                "error": str(e),
                "traceback": traceback.format_exc(),
                "timestamp": datetime.now().isoformat()
            })

            return {
                "success": False,
                "error": str(e),
                "node_outputs": {}
            }

    def get_node_results(self) -> List[NodeResult]:
        """获取所有节点执行结果"""
        return list(self.node_results.values())
