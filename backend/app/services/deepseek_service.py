"""
DeepSeek API服务 - 集成DeepSeek大模型实现知识问答
"""
import asyncio
import logging
from typing import List, Dict, Any, Optional, AsyncGenerator
from datetime import datetime
import json
import uuid

import httpx
from openai import AsyncOpenAI

from app.core.config import settings
from app.services.knowledge_service import knowledge_service
from app.services.rag_service import rag_service
from app.core.database import AsyncSessionLocal
from app.models.chat import ChatSession, ChatMessage, MessageType
from app.core.circuit_breaker import ai_service_breaker

logger = logging.getLogger(__name__)


class DeepSeekService:
    """DeepSeek API服务 - 处理知识问答和对话"""
    
    def __init__(self):
        # 初始化客户端 (优先使用本地模型)
        if settings.ENABLE_LOCAL_LLM:
            self.client = AsyncOpenAI(
                api_key="ollama",
                base_url=settings.LOCAL_LLM_URL
            )
            self.model = settings.LOCAL_LLM_MODEL
            logger.info(f"DeepSeekService 已初始化，使用本地模型: {self.model}")
        else:
            self.client = AsyncOpenAI(
                api_key=settings.DEEPSEEK_API_KEY,
                base_url=settings.DEEPSEEK_API_URL
            )
            self.model = settings.DEEPSEEK_MODEL
            logger.info(f"DeepSeekService 已初始化，使用 DeepSeek 模型: {self.model}")
        
        # 系统提示词模板
        self.system_prompts = {
            "knowledge_qa": """你是一个专业的AI知识助手，基于提供的知识库内容回答问题。

要求：
1. 只使用提供的知识库内容回答问题，不要编造信息
2. 如果知识库中没有相关信息，请明确说明
3. 回答要准确、简洁、有用
4. 可以引用多个来源的信息进行综合回答
5. 对于不确定的信息，请明确标注

知识库内容：
{context}

请基于以上知识库内容回答用户的问题。""",
            
            "text_to_sql": """你是一个SQL专家，能够将自然语言问题转换为SQL查询。

要求：
1. 根据提供的表结构信息生成准确的SQL查询
2. 只使用标准SQL语法
3. 考虑数据安全和查询效率
4. 对于复杂查询，提供解释说明

表结构信息：
{table_schema}

请根据用户需求生成SQL查询。""",
            
            "general_chat": """你是一个友好的AI助手，能够进行自然对话。

要求：
1. 回答要友好、有帮助
2. 保持对话的连贯性
3. 对于专业问题，建议寻求专业帮助
4. 承认自己的局限性"""
        }
    
    async def generate_knowledge_answer(
        self,
        query: str,
        user,  # 当前用户对象
        chat_session_id: Optional[str] = None,
        top_k: int = 5,
        stream: bool = False
    ) -> AsyncGenerator[str, None]:
        """
        基于知识库生成回答 - 转发给更强大的 rag_service 处理
        """
        try:
            logger.info(f"开始生成知识问答，查询: {query}, 用户: {user.username}")
            
            # 1. 检索知识 (使用整合后的知识库检索)
            search_results = await knowledge_service.search_knowledge(
                query=query,
                organization_id=str(user.organization_id) if hasattr(user, 'organization_id') else None,
                top_k=top_k
            )
            
            if not search_results:
                yield "抱歉，我在知识库中没有找到相关信息。请尝试用不同的方式提问，或者上传更多相关文档。"
                return

            # 2. 获取聊天历史
            history = []
            if chat_session_id:
                history = await self._get_chat_history(chat_session_id, limit=6)

            # 3. 调用 rag_service 的流式生成
            # 注意：rag_service.chat_stream 返回的是 AsyncGenerator
            full_answer = ""
            async for chunk in rag_service.chat_stream(
                query=query,
                context=search_results,
                history=history
            ):
                full_answer += chunk
                yield chunk

            # 4. 保存对话记录 (如果是流式，在结束时保存)
            if chat_session_id:
                # 异步保存，不阻塞流输出
                asyncio.create_task(self.save_chat_message(
                    session_id=chat_session_id,
                    content=query,
                    message_type=MessageType.USER
                ))
                asyncio.create_task(self.save_chat_message(
                    session_id=chat_session_id,
                    content=full_answer,
                    message_type=MessageType.ASSISTANT,
                    metadata={"sources": [res.get("source") for res in search_results]}
                ))
                
        except Exception as e:
            logger.error(f"生成知识问答失败: {str(e)}")
            yield f"抱歉，生成回答时出现了错误：{str(e)}"
    
    async def generate_text_to_sql(
        self,
        query: str,
        table_schema: str,
        organization_id: str,
        stream: bool = False
    ) -> AsyncGenerator[str, None]:
        """
        生成TextToSQL查询
        
        Args:
            query: 自然语言查询
            table_schema: 表结构信息
            organization_id: 组织ID
            stream: 是否流式输出
            
        Yields:
            SQL查询和解释
        """
        try:
            logger.info(f"开始生成TextToSQL，查询: {query}")
            
            # 构建系统提示词
            system_prompt = self.system_prompts["text_to_sql"].format(table_schema=table_schema)
            
            # 构建用户消息
            user_message = f"用户需求：{query}"
            
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ]
            
            # 调用DeepSeek API
            if stream:
                async for chunk in self._stream_chat_completion(messages):
                    yield chunk
            else:
                answer = await self._chat_completion(messages)
                yield answer
                
        except Exception as e:
            logger.error(f"生成TextToSQL失败: {str(e)}")
            yield f"生成SQL查询失败：{str(e)}"
    
    async def generate_general_response(
        self,
        query: str,
        chat_session_id: Optional[str] = None,
        stream: bool = False
    ) -> AsyncGenerator[str, None]:
        """
        生成通用对话回答
        
        Args:
            query: 用户查询
            chat_session_id: 聊天会话ID（可选）
            stream: 是否流式输出
            
        Yields:
            回答文本
        """
        try:
            logger.info(f"开始生成通用回答，查询: {query}")
            
            # 构建消息
            messages = [{"role": "system", "content": self.system_prompts["general_chat"]}]
            
            if chat_session_id:
                # 获取会话历史
                chat_history = await self._get_chat_history(chat_session_id, limit=5)
                messages.extend(chat_history)
            
            messages.append({"role": "user", "content": query})
            
            # 调用DeepSeek API
            if stream:
                async for chunk in self._stream_chat_completion(messages):
                    yield chunk
            else:
                answer = await self._chat_completion(messages)
                yield answer
                
        except Exception as e:
            logger.error(f"生成通用回答失败: {str(e)}")
            yield f"抱歉，生成回答时出现了错误：{str(e)}"
    
    def _build_context_from_search_results(self, search_results: List[Dict[str, Any]]) -> str:
        """
        从搜索结果构建上下文
        
        Args:
            search_results: 搜索结果列表
            
        Returns:
            格式化的上下文文本
        """
        if not search_results:
            return ""
        
        context_parts = []
        
        for i, result in enumerate(search_results, 1):
            text = result.get("text", "")
            metadata = result.get("metadata", {})
            score = result.get("score", 0)
            
            # 添加来源信息
            filename = metadata.get("filename", "未知文件")
            page_number = metadata.get("page_number")
            chunk_index = metadata.get("chunk_index", 0)
            
            source_info = f"来源: {filename}"
            if page_number:
                source_info += f", 第{page_number}页"
            source_info += f", 相似度: {score:.3f}"
            
            context_parts.append(f"[{i}] {source_info}:\n{text}\n")
        
        return "\n".join(context_parts)
    
    async def _get_chat_history(self, chat_session_id: str, limit: int = 5) -> List[Dict[str, str]]:
        """
        获取聊天历史
        
        Args:
            chat_session_id: 聊天会话ID
            limit: 历史消息数量限制
            
        Returns:
            消息历史列表
        """
        try:
            async with AsyncSessionLocal() as session:
                # 获取最近的聊天记录
                from sqlalchemy import select
                result = await session.execute(
                    select(ChatMessage)
                    .where(ChatMessage.session_id == chat_session_id)
                    .order_by(ChatMessage.created_at.desc())
                    .limit(limit)
                )
                messages = result.scalars().all()
                
                # 反转顺序（从旧到新）
                messages.reverse()  # pyright: ignore[reportAttributeAccessIssue]
                
                # 转换为OpenAI格式
                chat_history = []
                for message in messages:
                    if message.message_type == MessageType.USER:  # pyright: ignore[reportGeneralTypeIssues]
                        chat_history.append({
                            "role": "user",
                            "content": message.content
                        })
                    elif message.message_type == MessageType.ASSISTANT:  # pyright: ignore[reportGeneralTypeIssues]
                        chat_history.append({
                            "role": "assistant",
                            "content": message.content
                        })
                
                return chat_history
                
        except Exception as e:
            logger.error(f"获取聊天历史失败: {str(e)}")
            return []
    
    @ai_service_breaker
    async def _chat_completion(self, messages: List[Dict[str, str]]) -> str:
        """
        调用DeepSeek API进行对话完成
        
        Args:
            messages: 消息列表
            
        Returns:
            回答文本
        """
        try:
            # 调用DeepSeek API
            response = await self.client.chat.completions.create(  # pyright: ignore[reportCallIssue]
                model=self.model,
                messages=messages,  # pyright: ignore[reportArgumentType]
                temperature=0.1,
                max_tokens=settings.AI_MAX_TOKENS,
                stream=False
            )
            
            return response.choices[0].message.content
            
        except Exception as e:
            logger.error(f"DeepSeek API调用失败: {str(e)}")
            raise e
    
    async def _stream_chat_completion(self, messages: List[Dict[str, str]]) -> AsyncGenerator[str, None]:
        """
        流式调用DeepSeek API进行对话完成
        
        Args:
            messages: 消息列表
            
        Yields:
            回答文本块
        """
        try:
            # 如果API密钥是演示密钥，返回模拟流式回答
            if settings.DEEPSEEK_API_KEY == "demo-key":
                async for chunk in self._generate_mock_stream_response(messages):
                    yield chunk
                return
            
            # 流式调用DeepSeek API
            response = await self.client.chat.completions.create(  # pyright: ignore[reportCallIssue]
                model=self.model,
                messages=messages,  # pyright: ignore[reportArgumentType]
                temperature=0.1,
                max_tokens=settings.AI_MAX_TOKENS,
                stream=True
            )
            
            async for chunk in response:
                if chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
                    
        except Exception as e:
            logger.error(f"DeepSeek API流式调用失败: {str(e)}")
            # 返回模拟流式回答作为备选
            async for chunk in self._generate_mock_stream_response(messages):
                yield chunk
    
    def _generate_mock_response(self, messages: List[Dict[str, str]]) -> str:
        """
        Mock 方法已废弃
        """
        raise NotImplementedError("Mock responses are disabled in production mode.")
    
    async def _generate_mock_stream_response(self, messages: List[Dict[str, str]]) -> AsyncGenerator[str, None]:
        """
        Mock 流式方法已废弃
        """
        raise NotImplementedError("Mock responses are disabled in production mode.")
        yield ""
    
    async def create_chat_session(self, user_id: str, organization_id: str, title: str = None) -> str:  # pyright: ignore[reportArgumentType]
        """
        创建聊天会话
        
        Args:
            user_id: 用户ID
            organization_id: 组织ID
            title: 会话标题
            
        Returns:
            会话ID
        """
        try:
            async with AsyncSessionLocal() as session:
                session_id = str(uuid.uuid4())
                
                chat_session = ChatSession(
                    id=session_id,
                    user_id=user_id,
                    organization_id=organization_id,
                    title=title or f"新会话 {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                    status="active"
                )
                
                session.add(chat_session)
                await session.commit()
                
                logger.info(f"创建聊天会话: {session_id}")
                return session_id
                
        except Exception as e:
            logger.error(f"创建聊天会话失败: {str(e)}")
            raise
    
    async def save_chat_message(
        self,
        session_id: str,
        content: str,
        message_type: MessageType,
        metadata: Dict[str, Any] = None  # pyright: ignore[reportArgumentType]
    ) -> bool:
        """
        保存聊天消息
        
        Args:
            session_id: 会话ID
            content: 消息内容
            message_type: 消息类型
            metadata: 元数据
            
        Returns:
            是否保存成功
        """
        try:
            async with AsyncSessionLocal() as session:
                # 1. 保存消息
                message = ChatMessage(
                    id=str(uuid.uuid4()),
                    session_id=session_id,
                    content=content,
                    message_type=message_type,
                    meta_data=metadata or {}
                )
                
                session.add(message)
                
                # 2. 更新会话统计
                chat_session = await session.get(ChatSession, session_id)
                if chat_session:
                    chat_session.message_count += 1  # pyright: ignore[reportAttributeAccessIssue]
                    chat_session.last_message_at = datetime.now()  # pyright: ignore[reportAttributeAccessIssue]
                    chat_session.updated_at = datetime.now()  # pyright: ignore[reportAttributeAccessIssue]
                
                await session.commit()
                
                logger.debug(f"保存聊天消息: {message.id}")
                return True
                
        except Exception as e:
            logger.error(f"保存聊天消息失败: {str(e)}")
            return False


# 创建服务实例
deepseek_service = DeepSeekService()