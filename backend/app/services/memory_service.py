# -*- coding: utf-8 -*-
"""
Agent 记忆系统服务
支持短期记忆、长期记忆、工作记忆和反思记忆
"""
import asyncio
import json
import math
import hashlib
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional, Callable, Awaitable
from collections import defaultdict
import logging

logger = logging.getLogger(__name__)


class MemoryItem:
    """记忆项"""
    def __init__(
        self,
        content: str,
        memory_type: str = "short_term",
        importance: float = 0.5,
        metadata: Dict[str, Any] = None,
        embedding: Optional[List[float]] = None,
        created_at: Optional[str] = None,
        last_accessed: Optional[str] = None,
        access_count: int = 0,
        item_id: Optional[str] = None,
    ):
        self.content = content
        self.memory_type = memory_type
        self.importance = importance
        self.metadata = metadata or {}
        self.embedding = embedding
        if created_at:
            try:
                self.created_at = datetime.fromisoformat(created_at)
            except (ValueError, TypeError):
                self.created_at = datetime.now()
        else:
            self.created_at = datetime.now()
        if last_accessed:
            try:
                self.last_accessed = datetime.fromisoformat(last_accessed)
            except (ValueError, TypeError):
                self.last_accessed = datetime.now()
        else:
            self.last_accessed = datetime.now()
        self.access_count = access_count
        self.id = item_id or self._generate_id()

    def _generate_id(self) -> str:
        """生成唯一ID"""
        import uuid
        return uuid.uuid4().hex[:12]

    def access(self) -> None:
        """访问记忆"""
        self.last_accessed = datetime.now()
        self.access_count += 1

    def get_decay_score(self, half_life_hours: float = 24.0) -> float:
        """计算衰减分数"""
        age_hours = (datetime.now() - self.created_at).total_seconds() / 3600
        decay = 0.5 ** (age_hours / half_life_hours)
        return decay * self.importance

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        d = {
            "id": self.id,
            "content": self.content,
            "memory_type": self.memory_type,
            "importance": self.importance,
            "metadata": self.metadata,
            "created_at": self.created_at.isoformat(),
            "last_accessed": self.last_accessed.isoformat(),
            "access_count": self.access_count,
        }
        if self.embedding is not None:
            d["embedding"] = self.embedding
        return d


class ShortTermMemory:
    """短期记忆 - 滑动窗口缓冲区"""

    def __init__(self, max_size: int = 20):
        self.max_size = max_size
        self.buffer: List[MemoryItem] = []

    def add(self, item: MemoryItem) -> None:
        """添加记忆"""
        self.buffer.append(item)
        if len(self.buffer) > self.max_size:
            self.buffer.pop(0)

    def get_recent(self, n: int = 10) -> List[MemoryItem]:
        """获取最近的记忆"""
        return self.buffer[-n:]

    def search(self, query: str, top_k: int = 5) -> List[MemoryItem]:
        """简单关键词搜索"""
        results = []
        query_lower = query.lower()
        for item in reversed(self.buffer):
            if query_lower in item.content.lower():
                item.access()
                results.append(item)
            if len(results) >= top_k:
                break
        return results

    def clear(self) -> None:
        """清空记忆"""
        self.buffer.clear()

    def to_list(self) -> List[Dict]:
        """转换为列表"""
        return [item.to_dict() for item in self.buffer]


class LongTermMemory:
    """长期记忆 - 持久化存储"""

    def __init__(self, storage_backend: str = "memory"):
        self.storage_backend = storage_backend
        self.memories: Dict[str, List[MemoryItem]] = defaultdict(list)
        self.index: Dict[str, List[str]] = defaultdict(list)  # 简单倒排索引

    @staticmethod
    def _tokenize(text: str) -> set:
        """分词：空格分词 + CJK bigram，覆盖中英文混合场景"""
        tokens = set()
        lower = text.lower().strip()
        # Space-separated tokens
        for word in lower.split():
            if len(word) > 2:
                tokens.add(word)
        # CJK bigrams for Chinese text without spaces
        cjk_run = ""
        for ch in lower:
            if '一' <= ch <= '鿿' or '㐀' <= ch <= '䶿':
                cjk_run += ch
            else:
                if len(cjk_run) >= 2:
                    for i in range(len(cjk_run) - 1):
                        tokens.add(cjk_run[i:i + 2])
                cjk_run = ""
        if len(cjk_run) >= 2:
            for i in range(len(cjk_run) - 1):
                tokens.add(cjk_run[i:i + 2])
        return tokens

    def add(self, item: MemoryItem) -> None:
        """添加记忆"""
        memory_type = item.memory_type
        self.memories[memory_type].append(item)

        # 更新索引
        for token in self._tokenize(item.content):
            self.index[token].append(item.id)

    def search(
        self,
        query: str,
        memory_type: str = None,
        top_k: int = 10,
        min_importance: float = 0.0,
        use_decay: bool = True
    ) -> List[MemoryItem]:
        """搜索记忆"""
        candidates = []

        # 获取候选记忆
        if memory_type:
            candidates = self.memories.get(memory_type, [])
        else:
            for mem_list in self.memories.values():
                candidates.extend(mem_list)

        # 过滤和评分
        results = []
        query_tokens = self._tokenize(query)

        for item in candidates:
            # 重要性过滤
            if item.importance < min_importance:
                continue

            # 计算相关性分数
            item_tokens = self._tokenize(item.content)
            overlap = len(query_tokens & item_tokens)
            if overlap == 0:
                continue

            # 综合分数
            relevance = overlap / max(len(query_tokens), 1)
            decay_score = item.get_decay_score() if use_decay else item.importance
            final_score = 0.5 * relevance + 0.3 * decay_score + 0.2 * min(item.access_count / 10, 1.0)

            results.append((final_score, item))

        # 排序并返回
        results.sort(key=lambda x: x[0], reverse=True)

        # 标记访问
        for _, item in results[:top_k]:
            item.access()

        return [item for _, item in results[:top_k]]

    def get_by_time_range(
        self,
        start_time: datetime,
        end_time: datetime,
        memory_type: str = None
    ) -> List[MemoryItem]:
        """按时间范围获取记忆"""
        results = []
        candidates = []

        if memory_type:
            candidates = self.memories.get(memory_type, [])
        else:
            for mem_list in self.memories.values():
                candidates.extend(mem_list)

        for item in candidates:
            if start_time <= item.created_at <= end_time:
                results.append(item)

        return sorted(results, key=lambda x: x.created_at, reverse=True)

    def get_important_memories(self, top_k: int = 10) -> List[MemoryItem]:
        """获取重要记忆"""
        all_memories = []
        for mem_list in self.memories.values():
            all_memories.extend(mem_list)

        all_memories.sort(key=lambda x: x.importance, reverse=True)
        return all_memories[:top_k]

    @staticmethod
    def _cosine_similarity(a: List[float], b: List[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        na = math.sqrt(sum(x * x for x in a))
        nb = math.sqrt(sum(y * y for y in b))
        if na == 0.0 or nb == 0.0:
            return 0.0
        return dot / (na * nb)

    def search_semantic(
        self,
        query_embedding: List[float],
        memory_type: str = None,
        top_k: int = 10,
        min_importance: float = 0.0,
    ) -> List[MemoryItem]:
        """基于向量相似度的语义搜索"""
        candidates = []
        if memory_type:
            candidates = self.memories.get(memory_type, [])
        else:
            for mem_list in self.memories.values():
                candidates.extend(mem_list)

        if not candidates:
            return []

        scored = []
        for item in candidates:
            if item.importance < min_importance or item.embedding is None:
                continue
            sim = self._cosine_similarity(query_embedding, item.embedding)
            if sim > 0.3:  # minimum relevance threshold
                decay = item.get_decay_score()
                final_score = 0.6 * sim + 0.4 * decay
                scored.append((final_score, item))

        scored.sort(key=lambda x: x[0], reverse=True)

        for _, item in scored[:top_k]:
            item.access()

        return [item for _, item in scored[:top_k]]

    def forget(self, memory_id: str) -> bool:
        """遗忘记忆"""
        for memory_type, mem_list in self.memories.items():
            for i, item in enumerate(mem_list):
                if item.id == memory_id:
                    mem_list.pop(i)
                    # 从索引中移除
                    for token in self._tokenize(item.content):
                        if token in self.index and memory_id in self.index[token]:
                            self.index[token].remove(memory_id)
                    return True
        return False

    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            mem_type: [item.to_dict() for item in items]
            for mem_type, items in self.memories.items()
        }


class WorkingMemory:
    """工作记忆 - 当前任务状态"""

    def __init__(self):
        self.state: Dict[str, Any] = {}
        self.task_stack: List[Dict[str, Any]] = []
        self.intermediate_results: Dict[str, Any] = {}
        self.variables: Dict[str, Any] = {}

    def set_state(self, key: str, value: Any) -> None:
        """设置状态"""
        self.state[key] = value

    def get_state(self, key: str, default: Any = None) -> Any:
        """获取状态"""
        return self.state.get(key, default)

    def push_task(self, task: Dict[str, Any]) -> None:
        """推入任务"""
        self.task_stack.append({
            "task": task,
            "started_at": datetime.now().isoformat()
        })

    def pop_task(self) -> Optional[Dict[str, Any]]:
        """弹出任务"""
        if self.task_stack:
            return self.task_stack.pop()
        return None

    def set_result(self, key: str, value: Any) -> None:
        """设置中间结果"""
        self.intermediate_results[key] = value

    def get_result(self, key: str) -> Any:
        """获取中间结果"""
        return self.intermediate_results.get(key)

    def set_variable(self, key: str, value: Any) -> None:
        """设置变量"""
        self.variables[key] = value

    def get_variable(self, key: str) -> Any:
        """获取变量"""
        return self.variables.get(key)

    def resolve_template(self, template: str) -> str:
        """解析模板变量"""
        result = template
        for key, value in self.variables.items():
            result = result.replace(f"{{{{{key}}}}}", str(value))
        return result

    def clear(self) -> None:
        """清空工作记忆"""
        self.state.clear()
        self.task_stack.clear()
        self.intermediate_results.clear()
        self.variables.clear()

    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            "state": self.state,
            "task_stack": self.task_stack,
            "intermediate_results": self.intermediate_results,
            "variables": self.variables
        }


class ReflectiveMemory:
    """反思记忆 - 高层洞察和经验"""

    def __init__(self):
        self.insights: List[Dict[str, Any]] = []
        self.patterns: List[Dict[str, Any]] = []
        self.lessons: List[Dict[str, Any]] = []

    def add_insight(self, insight: str, context: Optional[Dict[str, Any]] = None) -> None:
        """添加洞察"""
        self.insights.append({
            "content": insight,
            "context": context or {},
            "created_at": datetime.now().isoformat()
        })

    def add_pattern(self, pattern: str, examples: Optional[List[str]] = None) -> None:
        """添加模式"""
        self.patterns.append({
            "pattern": pattern,
            "examples": examples or [],
            "created_at": datetime.now().isoformat()
        })

    def add_lesson(self, lesson: str, trigger: Optional[str] = None, solution: Optional[str] = None) -> None:
        """添加经验教训"""
        self.lessons.append({
            "lesson": lesson,
            "trigger": trigger,
            "solution": solution,
            "created_at": datetime.now().isoformat()
        })

    def get_relevant_insights(self, context: str, top_k: int = 5) -> List[str]:
        """获取相关洞察"""
        results = []
        context_lower = context.lower()

        for insight in self.insights:
            insight_content = insight["content"].lower()
            context_data = insight.get("context", {})

            # 简单匹配
            if any(word in insight_content for word in context_lower.split()):
                results.append(insight["content"])

            if len(results) >= top_k:
                break

        return results

    def get_lessons_for_situation(self, situation: str) -> List[Dict[str, Any]]:
        """获取相关的经验教训"""
        results = []
        situation_lower = situation.lower()

        for lesson in self.lessons:
            trigger = lesson.get("trigger", "")
            if trigger and trigger.lower() in situation_lower:
                results.append(lesson)

        return results

    def to_dict(self) -> Dict:
        """转换为字典"""
        return {
            "insights": self.insights,
            "patterns": self.patterns,
            "lessons": self.lessons
        }


class AgentMemorySystem:
    """
    Agent 记忆系统 - 完整的记忆管理

    参考：
    - 斯坦福 Generative Agents 记忆流
    - Mem0 记忆架构
    - Letta 持久记忆
    """

    def __init__(self, agent_id: str = "default"):
        self.agent_id = agent_id
        self.short_term = ShortTermMemory()
        self.long_term = LongTermMemory()
        self.working = WorkingMemory()
        self.reflective = ReflectiveMemory()

        # 配置
        self.config = {
            "auto_reflect": True,
            "reflect_interval": 10,  # 每10次交互后反思
            "importance_threshold": 0.3,
            "max_short_term": 20,
        }

        self.interaction_count = 0
        self._loaded = False
        self._embedding_provider: Optional[Callable[[str], Awaitable[List[float]]]] = None

    def set_embedding_provider(self, provider: Callable[[str], Awaitable[List[float]]]) -> None:
        """设置 embedding 提供者，启用语义搜索"""
        self._embedding_provider = provider

    @property
    def _persist_key(self) -> str:
        return f"agent:memory:{self.agent_id}"

    async def _get_redis(self):
        """获取 Redis 客户端（可能为 None）"""
        try:
            from app.core.redis import redis_client
            return redis_client
        except Exception:
            return None

    async def _load_from_redis(self):
        """从 Redis 加载长期记忆和反思记忆"""
        if self._loaded:
            return
        try:
            r = await self._get_redis()
            if r is None:
                return
            data_raw = await r.get(self._persist_key)
            if not data_raw:
                return
            data = json.loads(data_raw)
            self.import_data(data)
            logger.info(f"Agent '{self.agent_id}' 记忆已从 Redis 恢复")
        except Exception as e:
            logger.warning(f"从 Redis 加载记忆失败: {e}")
        finally:
            self._loaded = True

    async def _save_to_redis(self):
        """保存长期记忆和反思记忆到 Redis"""
        try:
            r = await self._get_redis()
            if r is None:
                return
            persist_data = {
                "long_term": self.long_term.to_dict(),
                "reflective": self.reflective.to_dict(),
                "interaction_count": self.interaction_count,
            }
            await r.setex(
                self._persist_key,
                86400 * 7,  # 7 天过期
                json.dumps(persist_data, ensure_ascii=False, default=str)
            )
        except Exception as e:
            logger.warning(f"保存记忆到 Redis 失败: {e}")

    async def remember(
        self,
        content: str,
        memory_type: str = "short_term",
        importance: float = 0.5,
        metadata: Dict[str, Any] = None
    ) -> MemoryItem:
        """存储记忆"""
        if not self._loaded:
            await self._load_from_redis()

        # Compute embedding for long-term memories if provider is set
        embedding = None
        if memory_type != "short_term" and self._embedding_provider:
            try:
                embedding = await self._embedding_provider(content)
            except Exception as e:
                logger.warning(f"Embedding computation failed for memory: {e}")

        item = MemoryItem(
            content=content,
            memory_type=memory_type,
            importance=importance,
            metadata=metadata,
            embedding=embedding,
        )

        if memory_type == "short_term":
            self.short_term.add(item)
        else:
            self.long_term.add(item)
            await self._save_to_redis()

        # 检查是否需要反思
        self.interaction_count += 1
        if self.config["auto_reflect"] and self.interaction_count % self.config["reflect_interval"] == 0:
            await self._auto_reflect()

        return item

    async def recall(
        self,
        query: str,
        memory_types: List[str] = None,
        top_k: int = 10
    ) -> List[Dict[str, Any]]:
        """检索记忆。如果配置了 embedding provider，优先使用语义搜索。"""
        if not self._loaded:
            await self._load_from_redis()

        results = []

        if memory_types is None:
            memory_types = ["short_term", "long_term", "reflective"]

        if "short_term" in memory_types:
            results.extend([item.to_dict() for item in self.short_term.search(query, top_k)])

        if "long_term" in memory_types:
            # Prefer semantic search if embedding provider is available
            if self._embedding_provider:
                try:
                    query_emb = await self._embedding_provider(query)
                    semantic_results = self.long_term.search_semantic(query_emb, top_k=top_k)
                    if semantic_results:
                        results.extend([item.to_dict() for item in semantic_results])
                    else:
                        # Fall back to keyword search
                        results.extend([item.to_dict() for item in self.long_term.search(query, top_k=top_k)])
                except Exception as e:
                    logger.warning(f"Semantic search failed, falling back to keyword: {e}")
                    results.extend([item.to_dict() for item in self.long_term.search(query, top_k=top_k)])
            else:
                results.extend([item.to_dict() for item in self.long_term.search(query, top_k=top_k)])

        if "reflective" in memory_types:
            results.extend([{"type": "insight", "content": i} for i in self.reflective.get_relevant_insights(query)])
            results.extend([{"type": "lesson", **l} for l in self.reflective.get_lessons_for_situation(query)])

        return results[:top_k]

    async def get_context(self, query: str) -> str:
        """获取上下文用于LLM"""
        memories = await self.recall(query, top_k=5)

        if not memories:
            return ""

        context_parts = ["相关记忆："]
        for i, mem in enumerate(memories, 1):
            content = mem.get("content", mem.get("lesson", ""))
            context_parts.append(f"{i}. {content}")

        return "\n".join(context_parts)

    async def _auto_reflect(self):
        """自动反思 - 从短期记忆提取洞察"""
        recent_memories = self.short_term.get_recent(10)

        if len(recent_memories) < 3:
            return

        # 分析最近记忆，提取模式
        contents = [m.content for m in recent_memories]
        combined = " ".join(contents)

        # 简单模式检测
        words = combined.lower().split()
        word_freq = {}
        for word in words:
            if len(word) > 3:
                word_freq[word] = word_freq.get(word, 0) + 1

        # 高频词作为潜在模式
        patterns = [(w, c) for w, c in word_freq.items() if c >= 2]
        patterns.sort(key=lambda x: x[1], reverse=True)

        for word, count in patterns[:3]:
            self.reflective.add_pattern(f"频繁提及: {word}")

        if patterns:
            await self._save_to_redis()

    async def store_interaction(self, user_input: str, assistant_response: str) -> None:
        """存储交互"""
        await self.remember(
            f"用户: {user_input}",
            memory_type="short_term",
            importance=0.6
        )
        await self.remember(
            f"助手: {assistant_response}",
            memory_type="short_term",
            importance=0.5
        )

    async def store_experience(self, success: bool, action: str, result: str, context: str = "") -> None:
        """存储经验"""
        importance = 0.8 if not success else 0.6
        await self.remember(
            f"{'成功' if success else '失败'}: {action} -> {result}",
            memory_type="long_term",
            importance=importance,
            metadata={"context": context}
        )

        if not success:
            self.reflective.add_lesson(
                lesson=result,
                trigger=action
            )

    def export(self) -> Dict[str, Any]:
        """导出所有记忆"""
        return {
            "agent_id": self.agent_id,
            "short_term": self.short_term.to_list(),
            "long_term": self.long_term.to_dict(),
            "working": self.working.to_dict(),
            "reflective": self.reflective.to_dict(),
            "interaction_count": self.interaction_count
        }

    def import_data(self, data: Dict[str, Any]) -> None:
        """导入记忆数据"""
        self.agent_id = data.get("agent_id", self.agent_id)
        self.interaction_count = data.get("interaction_count", 0)

        # 恢复短期记忆
        for item_data in data.get("short_term", []):
            item = MemoryItem(
                content=item_data["content"],
                memory_type=item_data.get("memory_type", "short_term"),
                importance=item_data.get("importance", 0.5),
                metadata=item_data.get("metadata"),
                embedding=item_data.get("embedding"),
                created_at=item_data.get("created_at"),
                last_accessed=item_data.get("last_accessed"),
                access_count=item_data.get("access_count", 0),
                item_id=item_data.get("id"),
            )
            self.short_term.add(item)

        # 恢复长期记忆
        for mem_type, items in data.get("long_term", {}).items():
            for item_data in items:
                item = MemoryItem(
                    content=item_data["content"],
                    memory_type=mem_type,
                    importance=item_data.get("importance", 0.5),
                    metadata=item_data.get("metadata"),
                    embedding=item_data.get("embedding"),
                    created_at=item_data.get("created_at"),
                    last_accessed=item_data.get("last_accessed"),
                    access_count=item_data.get("access_count", 0),
                    item_id=item_data.get("id"),
                )
                self.long_term.add(item)


# 全局记忆系统实例
memory_systems: Dict[str, AgentMemorySystem] = {}


def get_memory_system(agent_id: str = "default") -> AgentMemorySystem:
    """获取或创建记忆系统"""
    if agent_id not in memory_systems:
        memory_systems[agent_id] = AgentMemorySystem(agent_id)
    return memory_systems[agent_id]
