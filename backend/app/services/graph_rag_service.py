import logging
import json
import re
from typing import List, Dict, Any, Optional, Tuple
from collections import defaultdict
import hashlib

logger = logging.getLogger(__name__)

class GraphRAGService:
    def __init__(self):
        self.graph: Dict[str, Dict[str, Any]] = defaultdict(lambda: {
            "entity_type": "UNKNOWN",
            "description": "",
            "relationships": [],
            "occurrences": 0
        })
        self.entity_types = {
            "PERSON": "人物",
            "ORGANIZATION": "组织/公司",
            "LOCATION": "地点",
            "EVENT": "事件",
            "CONCEPT": "概念",
            "PRODUCT": "产品",
            "TECHNOLOGY": "技术"
        }

    def extract_entities_with_llm(self, text: str, llm_client=None) -> List[Dict[str, Any]]:
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
                response = llm_client.chat.completions.create(
                    model="deepseek-chat",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1
                )
                content = response.choices[0].message.content
                content = content.strip().strip('```json').strip('```').strip()
                entities = json.loads(content)
                return entities
        except Exception as e:
            logger.warning(f"LLM entity extraction failed: {e}")

        return self._rule_based_extraction(text)

    def _rule_based_extraction(self, text: str) -> List[Dict[str, Any]]:
        entities = []

        person_pattern = r'([A-Z\u4e00-\u9fa5][a-z\u4e00-\u9fa5]{1,20}(?:\s+[A-Z\u4e00-\u9fa5][a-z\u4e00-\u9fa5]{1,20})*(?:\s+(?:先生|女士|博士|教授|总监|经理|CEO|CTO|CFO|董事长|总裁|总经理))?)'
        org_pattern = r'([A-Z\u4e00-\u9fa5](?:[A-Za-z\u4e00-\u9fa5·]{1,30}(?:公司|集团|大学|医院|医院|研究所|医院|中心|机构|组织|银行|酒店|医院))?)'
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

    def build_graph_from_entities(self, entities: List[Dict[str, Any]]):
        for ent in entities:
            entity_name = ent.get("entity", "")
            if not entity_name:
                continue

            entity_key = self._normalize_entity(entity_name)
            self.graph[entity_key]["entity_name"] = entity_name
            self.graph[entity_key]["entity_type"] = ent.get("type", "UNKNOWN")
            self.graph[entity_key]["description"] = ent.get("description", "")
            self.graph[entity_key]["occurrences"] += 1

            for rel in ent.get("relations", []):
                target = rel.get("target", "")
                if target:
                    target_key = self._normalize_entity(target)
                    self.graph[entity_key]["relationships"].append({
                        "target": target_key,
                        "relation": rel.get("relation", "RELATED_TO")
                    })
                    self.graph[target_key]["relationships"].append({
                        "target": entity_key,
                        "relation": rel.get("relation", "RELATED_TO")
                    })

    def _normalize_entity(self, entity: str) -> str:
        return hashlib.md5(entity.lower().encode()).hexdigest()[:16]

    def search_graph(self, query: str, max_hops: int = 2) -> List[Dict[str, Any]]:
        query_entities = self.extract_entities_with_llm(query)
        if not query_entities:
            return []

        results = []
        for ent in query_entities[:5]:
            entity_name = ent.get("entity", "")
            entity_key = self._normalize_entity(entity_name)

            if entity_key in self.graph:
                node_data = self.graph[entity_key]
                results.append({
                    "entity": entity_name,
                    "type": node_data.get("entity_type", "UNKNOWN"),
                    "description": node_data.get("description", ""),
                    "occurrences": node_data.get("occurrences", 0),
                    "relationships": node_data.get("relationships", [])[:10]
                })

        return results

    def get_subgraph_context(self, entities: List[str], max_hops: int = 1) -> str:
        context_parts = []

        for ent in entities:
            entity_key = self._normalize_entity(ent)
            if entity_key not in self.graph:
                continue

            node = self.graph[entity_key]
            type_name = self.entity_types.get(node["entity_type"], node["entity_type"])

            rels = []
            for rel in node.get("relationships", [])[:5]:
                target_key = rel.get("target", "")
                for name, data in self.graph.items():
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

    def get_analytics(self) -> Dict[str, Any]:
        type_counts = defaultdict(int)
        for node in self.graph.values():
            type_counts[node.get("entity_type", "UNKNOWN")] += 1

        return {
            "total_entities": len(self.graph),
            "type_distribution": dict(type_counts),
            "entity_types": self.entity_types
        }


graph_rag_service = GraphRAGService()
