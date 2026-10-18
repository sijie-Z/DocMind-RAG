import asyncio
import hashlib
import json
import logging
import re
from collections import defaultdict
from typing import Any

logger = logging.getLogger(__name__)

# 图数据与幂等标记共用同一 TTL：两者同生共死，过期后回填可整体重建（见 ingest_document）。
# 取 30 天（对齐仓内先例 app/agent/experience/store.py 的 setex(..., 86400 * 30)）：
# 原 24h 对展示/使用场景太短——无新摄入的组织图会日常蒸发；30 天仍是有界 TTL，
# 防止孤儿组织键无限累积，过期后重跑回填脚本即干净重建。
_TTL_SECONDS = 86400 * 30


def _require_org(organization_id: int | None, method: str) -> int:
    """A2 P0（fail-closed）：图谱按组织分区，缺组织上下文一律拒绝。

    默认值此前为 1：调用方漏传时会把读写静默落到 1 号组织，构成跨租户读写。
    """
    if organization_id is None:
        raise ValueError(
            f"GraphRAGService.{method} 缺少 organization_id，拒绝跨租户访问"
        )
    return organization_id


class GraphRAGService:
    def __init__(self):
        self._redis_key = "rag:graph"
        # 安全加固：图谱按组织分区（内存 dict 与 Redis key 均带 organization_id），
        # 防止跨组织读取实体/关系数据。
        self._graphs: dict[int, dict[str, dict[str, Any]]] = {}
        self.entity_types = {
            "PERSON": "人物",
            "ORGANIZATION": "组织/公司",
            "LOCATION": "地点",
            "EVENT": "事件",
            "CONCEPT": "概念",
            "PRODUCT": "产品",
            "TECHNOLOGY": "技术",
        }

    def _graph_for(self, organization_id: int) -> dict[str, dict[str, Any]]:
        if organization_id not in self._graphs:
            self._graphs[organization_id] = defaultdict(lambda: {
                "entity_type": "UNKNOWN",
                "description": "",
                "relationships": [],
                "occurrences": 0
            })
        return self._graphs[organization_id]

    def _redis_key_for(self, organization_id: int) -> str:
        return f"{self._redis_key}:{organization_id}"

    def _docs_key_for(self, organization_id: int) -> str:
        """已摄入文档的幂等标记集（Set）。"""
        return f"{self._redis_key}:docs:{organization_id}"

    async def load(self, organization_id: int | None = None) -> None:
        """Load the graph for an organization from Redis; keeps in-memory state when Redis is unavailable."""
        organization_id = _require_org(organization_id, "load")
        from app.core.redis import redis_client

        if not redis_client:
            return
        try:
            raw = await redis_client.get(self._redis_key_for(organization_id))
            if raw:
                data = json.loads(raw)
                graph = self._graph_for(organization_id)
                graph.clear()
                graph.update(data)
        except Exception as e:
            logger.warning(f"GraphRAG load from Redis failed: {e}")

    async def save(self, organization_id: int | None = None) -> None:
        """Persist the graph for an organization to Redis when available."""
        organization_id = _require_org(organization_id, "save")
        from app.core.redis import redis_client

        if not redis_client:
            return
        try:
            await redis_client.setex(
                self._redis_key_for(organization_id),
                _TTL_SECONDS,
                json.dumps(dict(self._graph_for(organization_id)), ensure_ascii=False),
            )
        except Exception as e:
            logger.warning(f"GraphRAG save to Redis failed: {e}")

    async def is_document_extracted(self, document_id: str, organization_id: int | None = None) -> bool | None:
        """该文档是否已摄入图谱（幂等判据）。

        - True —— 已摄入（标记在且图数据键仍存在）；
        - False —— 未摄入（含「标记还在但图键已失踪」：/graph/clear 清标记后的
          图键保留态、或 TTL 先于标记到期 —— 此时必须重抽，否则图谱永远重建不起来）；
        - None —— Redis 不可用，判断不了（调用方自行决定，与既有降级风格一致）。
        """
        organization_id = _require_org(organization_id, "is_document_extracted")
        from app.core.redis import redis_client

        if not redis_client:
            return None
        try:
            marked = await redis_client.sismember(
                self._docs_key_for(organization_id), document_id
            )
            if not marked:
                return False
            raw = await redis_client.get(self._redis_key_for(organization_id))
            return raw is not None
        except Exception as e:
            logger.warning(f"GraphRAG doc marker check failed: {e}")
            return None

    async def mark_document_extracted(self, document_id: str, organization_id: int | None = None) -> None:
        """写入幂等标记；TTL 与图数据键一致，两者同生共死。"""
        organization_id = _require_org(organization_id, "mark_document_extracted")
        from app.core.redis import redis_client

        if not redis_client:
            return
        try:
            key = self._docs_key_for(organization_id)
            await redis_client.sadd(key, document_id)
            await redis_client.expire(key, _TTL_SECONDS)
        except Exception as e:
            logger.warning(f"GraphRAG doc marker save failed: {e}")

    async def extract_entities_with_llm(self, text: str, llm_client: Any = None) -> list[dict[str, Any]]:
        if not text or len(text) < 50:
            return []

        prompt = f"""从以下文本中提取实体及其关系。以JSON数组格式返回。

文本内容：
{text[:2000]}

要求：
1. 识别实体类型：PERSON（人物）、ORGANIZATION（组织/公司）、LOCATION（地点）、EVENT（事件）、CONCEPT（概念）、PRODUCT（产品）、TECHNOLOGY（技术）
2. 识别实体间关系：WORK_AT（任职）、OWN（拥有）、LOCATED_IN（位于）、PART_OF（属于）、KNOWS（认识）、CREATE（创造）、BELONG_TO（属于）
3. 返回格式：[{{"entity": "实体名", "type": "类型", "description": "描述", "relations": [{{"target": "目标实体", "relation": "关系类型"}}]}}]

JSON返回："""

        try:
            if llm_client:
                from app.core.config import settings
                # 修复：AsyncOpenAI 的调用必须 await——此前访问 coroutine.choices 必然抛错，实体提取永远静默回退
                response = await llm_client.chat.completions.create(
                    model=settings.DEEPSEEK_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1
                )
                content = response.choices[0].message.content
                content = content.strip().strip('```json').strip('```').strip()
                parsed = json.loads(content)
                # LLM 偶尔以 {"entities": [...]} 包裹或混入非对象元素：直接放行会在
                # build_graph_from_entities 里炸 AttributeError。形状不对 → 抛错走
                # 下方 except 的规则回退（写链不断）。
                if not isinstance(parsed, list) or any(
                    not isinstance(e, dict) for e in parsed
                ):
                    raise ValueError("LLM 提取结果不是实体对象数组")
                return parsed
        except Exception as e:
            logger.warning(f"LLM entity extraction failed: {e}")

        return self._rule_based_extraction(text)

    def _rule_based_extraction(self, text: str) -> list[dict[str, Any]]:
        entities = []

        person_pattern = r'([A-Z\u4e00-\u9fa5][a-z\u4e00-\u9fa5]{1,20}(?:\s+[A-Z\u4e00-\u9fa5][a-z\u4e00-\u9fa5]{1,20})*(?:\s+(?:先生|女士|博士|教授|总监|经理|CEO|CTO|CFO|董事长|总裁|总经理))?)'
        org_pattern = r'([A-Z\u4e00-\u9fa5](?:[A-Za-z\u4e00-\u9fa5·]{1,30}(?:公司|集团|大学|研究所|中心|机构|组织|银行|酒店))?)'
        location_pattern = r'([A-Z\u4e00-\u9fa5](?:[A-Za-z\u4e00-\u9fa5]{1,20}(?:市|省|区|县|镇|街|路|大厦|中心|楼))?)'

        for match in re.finditer(person_pattern, text):
            entity = match.group(1).strip()
            if entity and len(entity) > 1:
                entities.append({
                    "entity": entity,
                    "type": "PERSON",
                    "description": "",
                    "relations": []
                })

        for match in re.finditer(org_pattern, text):
            entity = match.group(1).strip()
            if entity and len(entity) > 2:
                entities.append({
                    "entity": entity,
                    "type": "ORGANIZATION",
                    "description": "",
                    "relations": []
                })

        for match in re.finditer(location_pattern, text):
            entity = match.group(1).strip()
            if entity and len(entity) > 2:
                entities.append({
                    "entity": entity,
                    "type": "LOCATION",
                    "description": "",
                    "relations": []
                })

        return entities[:20]

    async def build_graph_from_entities(self, entities: list[dict[str, Any]], organization_id: int | None = None) -> None:
        organization_id = _require_org(organization_id, "build_graph_from_entities")
        graph = self._graph_for(organization_id)
        for ent in entities:
            entity_name = ent.get("entity", "")
            if not entity_name:
                continue

            entity_key = self._normalize_entity(entity_name)
            graph[entity_key]["entity_name"] = entity_name
            graph[entity_key]["entity_type"] = ent.get("type", "UNKNOWN")
            graph[entity_key]["description"] = ent.get("description", "")
            graph[entity_key]["occurrences"] += 1

            for rel in ent.get("relations", []):
                target = rel.get("target", "")
                if target:
                    target_key = self._normalize_entity(target)
                    graph[entity_key]["relationships"].append({
                        "target": target_key,
                        "relation": rel.get("relation", "RELATED_TO")
                    })
                    graph[target_key]["relationships"].append({
                        "target": entity_key,
                        "relation": rel.get("relation", "RELATED_TO")
                    })
                    # 目标未被单独抽成实体时也补上显示名——否则读 API 只能把
                    # md5 键当节点 id 展示（读侧 entity_name 回退的丑态）。
                    if not graph[target_key].get("entity_name"):
                        graph[target_key]["entity_name"] = target
        await self.save(organization_id)

    async def ingest_document(
        self,
        *,
        document_id: str,
        organization_id: int | None = None,
        text: str,
        llm_client: Any = None,
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        """单文档图谱摄入（worker 索引成功后与回填脚本共用的唯一写入口）。

        幂等：同一文档重复调用 → 直接 skipped，occurrences/关系条目不叠加
        （幂等标记见 mark_document_extracted；标记失效由 is_document_extracted 兜底）。

        非阻塞契约由调用方负责：本方法失败时上抛（含 LLM 挂起被 timeout 切断时的
        TimeoutError）。timeout 默认 60s < worker 心跳最大容忍 90s（heartbeat.py），
        保证抽取挂起不会把 worker 拖成「卡死」。

        返回 ``{"status": "skipped"|"extracted", "entities": int}``。
        """
        organization_id = _require_org(organization_id, "ingest_document")
        already = await self.is_document_extracted(document_id, organization_id)
        if already is True:
            return {"status": "skipped", "entities": 0}

        entities = await asyncio.wait_for(
            self.extract_entities_with_llm(text, llm_client=llm_client), timeout=timeout
        )
        # 先合并存量再写：build_graph_from_entities 改的是进程内存 dict、随后整份
        # 覆盖 Redis —— 不先 load 会把其它文档已贡献的实体整份冲掉。
        await self.load(organization_id)
        await self.build_graph_from_entities(entities, organization_id)
        await self.mark_document_extracted(document_id, organization_id)
        return {"status": "extracted", "entities": len(entities)}

    async def clear(self, organization_id: int | None = None) -> None:
        organization_id = _require_org(organization_id, "clear")
        self._graph_for(organization_id).clear()
        await self.save(organization_id)
        # 幂等标记描述的是「这份图收进了哪些文档」——图清空后必须同步清标记，
        # 否则回填脚本会被旧标记整批跳过，图谱永远重建不起来。
        from app.core.redis import redis_client

        if redis_client:
            try:
                await redis_client.delete(self._docs_key_for(organization_id))
            except Exception as e:
                logger.warning(f"GraphRAG doc markers clear failed: {e}")

    def _normalize_entity(self, entity: str) -> str:
        return hashlib.md5(entity.lower().encode(), usedforsecurity=False).hexdigest()[:16]

    async def search_graph(
        self, query: str, organization_id: int | None = None, max_hops: int = 2, llm_client: Any = None
    ) -> list[dict[str, Any]]:
        organization_id = _require_org(organization_id, "search_graph")
        await self.load(organization_id)
        graph = self._graph_for(organization_id)
        if llm_client is None:
            # 缺口收口：此前唯一调用点不传 llm_client → 查询实体识别恒走规则回退。
            # 拿不到真实客户端时降级为 None（规则回退），不阻断查询。
            try:
                from app.services.rag_service import rag_service

                llm_client = rag_service.openai_client
            except Exception as e:
                logger.warning(f"GraphRAG search: llm client unavailable: {e}")
                llm_client = None
        query_entities = await self.extract_entities_with_llm(query, llm_client=llm_client)
        if not query_entities:
            return []

        results = []
        for ent in query_entities[:5]:
            entity_name = ent.get("entity", "")
            entity_key = self._normalize_entity(entity_name)

            if entity_key in graph:
                node_data = graph[entity_key]
                results.append({
                    "entity": entity_name,
                    "type": node_data.get("entity_type", "UNKNOWN"),
                    "description": node_data.get("description", ""),
                    "occurrences": node_data.get("occurrences", 0),
                    "relationships": node_data.get("relationships", [])[:10]
                })

        return results

    def get_subgraph_context(self, entities: list[str], organization_id: int | None = None, max_hops: int = 1) -> str:
        organization_id = _require_org(organization_id, "get_subgraph_context")
        context_parts = []
        graph = self._graph_for(organization_id)

        for ent in entities:
            entity_key = self._normalize_entity(ent)
            if entity_key not in graph:
                continue

            node = graph[entity_key]
            type_name = self.entity_types.get(node["entity_type"], node["entity_type"])

            rels = []
            for rel in node.get("relationships", [])[:5]:
                target_key = rel.get("target", "")
                for name, _data in graph.items():
                    if name == target_key:
                        rels.append(f"{name[:8]}...({rel.get('relation', 'RELATED')})")
                        break
            rel_str = ", ".join(rels) if rels else "无直接关系"

            context_parts.append(
                f"实体：{node.get('entity_name', ent)}\n"
                f"类型：{type_name}\n"
                f"关系：{rel_str}"
            )

        return "\n---\n".join(context_parts[:10])

    async def get_analytics(self, organization_id: int | None = None) -> dict[str, Any]:
        organization_id = _require_org(organization_id, "get_analytics")
        await self.load(organization_id)
        graph = self._graph_for(organization_id)
        type_counts = defaultdict(int)
        for node in graph.values():
            type_counts[node.get("entity_type", "UNKNOWN")] += 1

        return {
            "total_entities": len(graph),
            "type_distribution": dict(type_counts),
            "entity_types": self.entity_types
        }


graph_rag_service = GraphRAGService()
