"""
聊天服务 - 管理聊天会话和消息
"""
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

from sqlalchemy import select, and_, func
from sqlalchemy.orm import selectinload

from app.core.database import AsyncSessionLocal
from app.models.chat import ChatSession, ChatMessage, ChatSessionStatus, MessageType

logger = logging.getLogger(__name__)


class ChatService:
    """聊天服务 - 管理聊天会话和消息"""
    
    async def get_user_chat_sessions(
        self, 
        user_id: str, 
        organization_id: Optional[str] = None,
        limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        获取用户的聊天会话列表
        
        Args:
            user_id: 用户ID
            organization_id: 组织ID（可选，用于筛选）
            limit: 返回数量限制
            
        Returns:
            会话列表
        """
        try:
            async with AsyncSessionLocal() as session:
                # 构建查询
                query = select(ChatSession).where(ChatSession.user_id == user_id)
                
                if organization_id:
                    query = query.where(ChatSession.organization_id == organization_id)
                
                query = query.order_by(ChatSession.last_message_at.desc()).limit(limit)
                
                result = await session.execute(query)
                sessions = result.scalars().all()
                
                # 转换为字典格式
                session_list = []
                for session in sessions:
                    session_list.append({
                        "session_id": session.id,
                        "title": session.title,
                        "user_id": session.user_id,
                        "organization_id": session.organization_id,
                        "status": session.status.value,
                        "message_count": session.message_count,
                        "last_message_at": session.last_message_at.isoformat() if session.last_message_at else None,  # pyright: ignore[reportGeneralTypeIssues]
                        "created_at": session.created_at.isoformat(),
                        "updated_at": session.updated_at.isoformat() if session.updated_at else None  # pyright: ignore[reportGeneralTypeIssues]
                    })
                
                return session_list
                
        except Exception as e:
            logger.error(f"获取用户聊天会话列表失败: {str(e)}")
            return []
    
    async def get_chat_history(
        self, 
        session_id: str, 
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        获取聊天会话历史
        
        Args:
            session_id: 会话ID
            limit: 返回数量限制
            offset: 偏移量
            
        Returns:
            消息列表
        """
        try:
            async with AsyncSessionLocal() as session:
                # 获取消息列表
                result = await session.execute(
                    select(ChatMessage)
                    .where(ChatMessage.session_id == session_id)
                    .order_by(ChatMessage.created_at.asc())
                    .offset(offset)
                    .limit(limit)
                )
                messages = result.scalars().all()
                
                # 转换为字典格式
                message_list = []
                for message in messages:
                    message_list.append({
                        "id": message.id,
                        "session_id": message.session_id,
                        "content": message.content,
                        "message_type": message.message_type.value,
                        "metadata": message.metadata or {},
                        "created_at": message.created_at.isoformat()
                    })
                
                return message_list
                
        except Exception as e:
            logger.error(f"获取聊天历史失败: {str(e)}")
            return []
    
    async def save_chat_message(
        self,
        session_id: str,
        content: str,
        message_type: MessageType,
        metadata: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        保存聊天消息
        
        Args:
            session_id: 会话ID
            content: 消息内容
            message_type: 消息类型
            metadata: 元数据（可选）
            
        Returns:
            是否保存成功
        """
        try:
            async with AsyncSessionLocal() as session:
                # 创建消息
                message = ChatMessage(
                    session_id=session_id,
                    content=content,
                    message_type=message_type,
                    metadata=metadata or {}
                )
                
                session.add(message)
                
                # 更新会话统计信息
                session_result = await session.execute(
                    select(ChatSession).where(ChatSession.id == session_id)
                )
                chat_session = session_result.scalar_one_or_none()
                
                if chat_session:
                    chat_session.message_count += 1  # pyright: ignore[reportAttributeAccessIssue]
                    chat_session.last_message_at = datetime.now()  # pyright: ignore[reportAttributeAccessIssue]
                
                await session.commit()
                
                logger.debug(f"保存聊天消息成功: {message.id}")
                return True
                
        except Exception as e:
            logger.error(f"保存聊天消息失败: {str(e)}")
            return False
    
    async def delete_chat_session(self, session_id: str) -> bool:
        """
        删除聊天会话（级联删除所有消息）
        
        Args:
            session_id: 会话ID
            
        Returns:
            是否删除成功
        """
        try:
            async with AsyncSessionLocal() as session:
                # 获取会话
                result = await session.execute(
                    select(ChatSession).where(ChatSession.id == session_id)
                )
                chat_session = result.scalar_one_or_none()
                
                if not chat_session:
                    logger.warning(f"聊天会话不存在: {session_id}")
                    return False
                
                # 删除会话（级联删除消息）
                await session.delete(chat_session)
                await session.commit()
                
                logger.info(f"删除聊天会话成功: {session_id}")
                return True
                
        except Exception as e:
            logger.error(f"删除聊天会话失败: {str(e)}")
            return False
    
    async def clear_chat_history(self, session_id: str) -> bool:
        """
        清空聊天会话历史（只删除消息，保留会话）

        Args:
            session_id: 会话ID

        Returns:
            是否清空成功
        """
        try:
            async with AsyncSessionLocal() as session:
                # 删除所有消息
                await session.execute(
                    delete(ChatMessage).where(ChatMessage.session_id == session_id)
                )

                # 更新会话统计信息
                result = await session.execute(
                    select(ChatSession).where(ChatSession.id == session_id)
                )
                chat_session = result.scalar_one_or_none()

                if chat_session:
                    chat_session.message_count = 0  # pyright: ignore[reportAttributeAccessIssue]
                    chat_session.last_message_at = None  # pyright: ignore[reportAttributeAccessIssue]

                await session.commit()

                logger.info(f"清空聊天历史成功: {session_id}")
                return True

        except Exception as e:
            logger.error(f"清空聊天历史失败: {str(e)}")
            return False
    
    async def check_session_permission(self, session_id: str, user_id: str) -> bool:
        """
        检查用户是否有权限访问会话
        
        Args:
            session_id: 会话ID
            user_id: 用户ID
            
        Returns:
            是否有权限
        """
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(ChatSession).where(
                        and_(
                            ChatSession.id == session_id,
                            ChatSession.user_id == user_id
                        )
                    )
                )
                chat_session = result.scalar_one_or_none()
                
                return chat_session is not None
                
        except Exception as e:
            logger.error(f"检查会话权限失败: {str(e)}")
            return False
    
    async def update_session_title(self, session_id: str, title: str) -> bool:
        """
        更新会话标题
        
        Args:
            session_id: 会话ID
            title: 新标题
            
        Returns:
            是否更新成功
        """
        try:
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(ChatSession).where(ChatSession.id == session_id)
                )
                chat_session = result.scalar_one_or_none()
                
                if not chat_session:
                    logger.warning(f"聊天会话不存在: {session_id}")
                    return False
                
                chat_session.title = title  # pyright: ignore[reportAttributeAccessIssue]
                await session.commit()
                
                logger.info(f"更新会话标题成功: {session_id}")
                return True
                
        except Exception as e:
            logger.error(f"更新会话标题失败: {str(e)}")
            return False
    
    async def get_session_statistics(self, user_id: str, organization_id: Optional[str] = None) -> Dict[str, Any]:
        """
        获取用户聊天统计信息
        
        Args:
            user_id: 用户ID
            organization_id: 组织ID（可选）
            
        Returns:
            统计信息
        """
        try:
            async with AsyncSessionLocal() as session:
                # 基础查询
                query = select(ChatSession).where(ChatSession.user_id == user_id)
                
                if organization_id:
                    query = query.where(ChatSession.organization_id == organization_id)
                
                # 获取会话统计
                result = await session.execute(query)
                sessions = result.scalars().all()
                
                total_sessions = len(sessions)
                active_sessions = len([s for s in sessions if s.status == ChatSessionStatus.ACTIVE])  # pyright: ignore[reportAttributeAccessIssue, reportGeneralTypeIssues]
                total_messages = sum(s.message_count for s in sessions)
                
                # 获取今日消息统计
                from datetime import date
                today = date.today()
                
                today_messages_result = await session.execute(
                    select(func.count(ChatMessage.id))
                    .join(ChatSession)
                    .where(
                        and_(
                            ChatSession.user_id == user_id,
                            func.date(ChatMessage.created_at) == today
                        )
                    )
                )
                today_messages = today_messages_result.scalar() or 0
                
                return {
                    "total_sessions": total_sessions,
                    "active_sessions": active_sessions,
                    "total_messages": total_messages,
                    "today_messages": today_messages,
                    "average_messages_per_session": total_messages / total_sessions if total_sessions > 0 else 0
                }
                
        except Exception as e:
            logger.error(f"获取聊天统计信息失败: {str(e)}")
            return {
                "total_sessions": 0,
                "active_sessions": 0,
                "total_messages": 0,
                "today_messages": 0,
                "average_messages_per_session": 0
            }


# 创建服务实例
chat_service = ChatService()