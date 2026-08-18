import hashlib
import json
import logging
import re
from collections import defaultdict
from typing import Any

logger = logging.getLogger(__name__)

class GraphRAGService:
    def __init__(self):
        self._redis_key = "rag:graph"
        # 安全加固：图谱按组织分区（内存 dict 与 Redis key 均带 organization_id），
        # 防止跨组织读取实体/关系数据。
        self._graphs: dict[int, dict[str, dict[str, Any]]] = {}
        # 兼容旧引用：默认组织（org 1）的图谱视图
        self.graph: dict[str, dict[str, Any]] = self._graph_for(1)
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

    async def load(self, organization_id: int = 1) -> None:
        """Load the graph for an organization from Redis; keeps in-memory state when Redis is unavailable."""
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

    async def save(self, organization_id: int = 1) -> None:
        """Persist the graph for an organization to Redis when available."""
        from app.core.redis import redis_client

        if not redis_client:
            return
        try:
            await redis_client.setex(
                self._redis_key_for(organization_id),
                86400,
                json.dumps(dict(self._graph_for(organization_id)), ensure_ascii=False),
            )
        except Exception as e:
            logger.warning(f"GraphRAG save to Redis failed: {e}")

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
                return json.loads(content)
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

    async def build_graph_from_entities(self, entities: list[dict[str, Any]], organization_id: int = 1) -> None:
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
        await self.save(organization_id)

    async def clear(self, organization_id: int = 1) -> None:
        self._graph_for(organization_id).clear()
        await self.save(organization_id)

    def _normalize_entity(self, entity: str) -> str:
        return hashlib.md5(entity.lower().encode()).hexdigest()[:16]

    async def search_graph(self, query: str, organization_id: int = 1, max_hops: int = 2) -> list[dict[str, Any]]:
        await self.load(organization_id)
        graph = self._graph_for(organization_id)
        query_entities = await self.extract_entities_with_llm(query)
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

    def get_subgraph_context(self, entities: list[str], organization_id: int = 1, max_hops: int = 1) -> str:
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

    async def get_analytics(self, organization_id: int = 1) -> dict[str, Any]:
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
