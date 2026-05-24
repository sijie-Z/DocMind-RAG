# -*- coding: utf-8 -*-
"""Elasticsearch connection module with lazy-initialized holder pattern.

Uses a proxy object for `es_client` so that module-level imports
always resolve to the live client after `init_elasticsearch()`.
"""

import logging
from elasticsearch import AsyncElasticsearch, NotFoundError
from elasticsearch.helpers import async_bulk
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


class _ESHolder:
    """Lifecycle-managed Elasticsearch client holder."""

    def __init__(self):
        self._client: Optional[AsyncElasticsearch] = None
        self.es_analyzer: str = "cjk"
        self.es_search_analyzer: str = "cjk"

    @property
    def client(self) -> Optional[AsyncElasticsearch]:
        return self._client

    async def initialize(self):
        if self._client is not None:
            return
        try:
            logger.info(f"Connecting to Elasticsearch: {settings.ELASTICSEARCH_HOSTS} ...")
            self._client = AsyncElasticsearch(
                hosts=settings.ELASTICSEARCH_HOSTS,
                verify_certs=False,
                ssl_show_warn=False,
                request_timeout=5,
            )
            info = await self._client.info()
            logger.info(f"Elasticsearch connected, version: {info.get('version', {}).get('number', 'unknown')}")

            # Detect IK analyzer at init time — store on holder, not settings
            try:
                await self._client.indices.analyze(body={"analyzer": "ik_smart", "text": "你好世界"})
                logger.info("IK analyzer detected — using ik_smart/ik_max_word")
                self.es_analyzer = "ik_smart"
                self.es_search_analyzer = "ik_max_word"
            except Exception:
                logger.info(f"IK analyzer not found — using built-in '{self.es_analyzer}' analyzer")

            await _create_index_if_not_exists(self._client)
        except Exception as e:
            logger.error(f"Elasticsearch connection failed: {e}")
            self._client = None

    async def close(self):
        if self._client:
            try:
                await self._client.close()
                logger.info("Elasticsearch connection closed")
            except Exception as e:
                logger.error(f"Error closing Elasticsearch: {e}")
            finally:
                self._client = None


class _ESProxy:
    """Proxy that delegates attribute access to the holder's client."""

    __slots__ = ("_holder",)

    def __init__(self, holder: _ESHolder):
        object.__setattr__(self, "_holder", holder)

    def __bool__(self) -> bool:
        return self._holder.client is not None

    def __getattr__(self, name: str):
        client = self._holder.client
        if client is None:
            raise RuntimeError(
                "Elasticsearch client not available — call init_elasticsearch() first"
            )
        return getattr(client, name)

    def __repr__(self) -> str:
        client = self._holder.client
        return f"<ESProxy client={client!r}>"


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------
_holder = _ESHolder()
es_client = _ESProxy(_holder)


async def init_elasticsearch():
    """Initialize Elasticsearch connection (called at app startup)."""
    await _holder.initialize()


async def get_elasticsearch() -> AsyncElasticsearch:
    """Get Elasticsearch client, initializing lazily if needed."""
    if _holder.client is None:
        await _holder.initialize()
    if _holder.client is None:
        raise RuntimeError("Elasticsearch client not available")
    return _holder.client


async def close_elasticsearch():
    """Close Elasticsearch connection (called at app shutdown)."""
    await _holder.close()


# ---------------------------------------------------------------------------
# Index management
# ---------------------------------------------------------------------------
def _knowledge_index_mapping() -> dict:
    analyzer = _holder.es_analyzer
    search_analyzer = _holder.es_search_analyzer
    return {
        "mappings": {
            "properties": {
                "content": {
                    "type": "text",
                    "analyzer": analyzer,
                    "search_analyzer": search_analyzer,
                },
                "chunk_text": {
                    "type": "text",
                    "analyzer": analyzer,
                    "search_analyzer": search_analyzer,
                },
                "chunk_id": {"type": "keyword"},
                "chunk_index": {"type": "integer"},
                "section_title": {"type": "text"},
                "document_id": {"type": "keyword"},
                "filename": {"type": "text", "fields": {"keyword": {"type": "keyword"}}},
                "file_type": {"type": "keyword"},
                "file_size": {"type": "long"},
                "content_length": {"type": "integer"},
                "upload_time": {"type": "date"},
                "user_id": {"type": "keyword"},
                "organization_id": {"type": "keyword"},
                "tags": {"type": "keyword"},
                "embedding": {
                    "type": "dense_vector",
                    "dims": settings.VECTOR_DIMENSION,
                    "index": True,
                    "similarity": "cosine",
                },
                "metadata": {"type": "object", "enabled": False},
            }
        },
        "settings": {"number_of_shards": 1, "number_of_replicas": 0},
    }


async def _create_index_if_not_exists(client: AsyncElasticsearch):
    """Create the knowledge index if it doesn't exist, or fix dimension mismatches."""
    try:
        index_name = settings.ELASTICSEARCH_INDEX_NAME
        exists = await client.indices.exists(index=index_name)
        if not exists:
            logger.info(f"Creating index: {index_name}")
            mapping = _knowledge_index_mapping()
            await client.indices.create(
                index=index_name, mappings=mapping["mappings"], settings=mapping["settings"]
            )
            logger.info("Index created")
        else:
            mapping = await client.indices.get_mapping(index=index_name)
            try:
                current_dims = mapping[index_name]["mappings"]["properties"]["embedding"]["dims"]
                if current_dims != settings.VECTOR_DIMENSION:
                    logger.warning(
                        f"Dimension mismatch: ES={current_dims}, Config={settings.VECTOR_DIMENSION}. Recreating..."
                    )
                    await recreate_knowledge_index()
                else:
                    logger.info(f"Index exists with matching dims: {index_name} (dims={current_dims})")
            except KeyError:
                logger.warning(f"Index {index_name} missing embedding field — recreating...")
                await recreate_knowledge_index()
    except Exception as e:
        logger.error(f"Index creation/check failed: {e}")


async def create_index_if_not_exists():
    """Public wrapper — uses the holder's client."""
    client = await get_elasticsearch()
    await _create_index_if_not_exists(client)


async def recreate_knowledge_index() -> bool:
    """Force-recreate the knowledge index (for dimension/mapping changes)."""
    client = await get_elasticsearch()
    try:
        index_name = settings.ELASTICSEARCH_INDEX_NAME
        exists = await client.indices.exists(index=index_name)
        if exists:
            await client.indices.delete(index=index_name)
            logger.warning(f"Deleted old index: {index_name}")
        mapping = _knowledge_index_mapping()
        await client.indices.create(
            index=index_name, mappings=mapping["mappings"], settings=mapping["settings"]
        )
        logger.info(f"Recreated index: {index_name}")
        return True
    except Exception as e:
        logger.error(f"Index recreation failed: {e}")
        return False


# ---------------------------------------------------------------------------
# Elasticsearch helper utilities
# ---------------------------------------------------------------------------
class ElasticsearchTools:
    """Static convenience wrappers around common ES operations."""

    @staticmethod
    async def bulk_index_documents(actions: list):
        client = await get_elasticsearch()
        await async_bulk(client, actions)

    @staticmethod
    async def index_document(doc_id: str, document: dict):
        client = await get_elasticsearch()
        return await client.index(
            index=settings.ELASTICSEARCH_INDEX_NAME, id=doc_id, document=document
        )

    @staticmethod
    async def get_document(doc_id: str):
        client = await get_elasticsearch()
        try:
            return await client.get(index=settings.ELASTICSEARCH_INDEX_NAME, id=doc_id)
        except NotFoundError:
            return None

    @staticmethod
    async def update_document(doc_id: str, document: dict):
        client = await get_elasticsearch()
        return await client.update(
            index=settings.ELASTICSEARCH_INDEX_NAME, id=doc_id, doc=document
        )

    @staticmethod
    async def delete_document(doc_id: str):
        client = await get_elasticsearch()
        return await client.delete(index=settings.ELASTICSEARCH_INDEX_NAME, id=doc_id)

    @staticmethod
    async def search_documents(query: dict):
        client = await get_elasticsearch()
        search_params = {
            "index": settings.ELASTICSEARCH_INDEX_NAME,
            "query": query.get("query", {"match_all": {}}),
        }
        for key in [
            "size", "from", "sort", "_source", "highlight",
            "min_score", "aggs", "post_filter", "runtime_mappings",
        ]:
            if key in query:
                search_params[key] = query[key]
        return await client.search(**search_params)

    @staticmethod
    async def vector_search(embedding: list, size: int = 10, min_score: float = 0.7):
        query = {
            "size": size,
            "min_score": min_score,
            "query": {
                "script_score": {
                    "query": {"match_all": {}},
                    "script": {
                        "source": "cosineSimilarity(params.query_vector, 'embedding') + 1.0",
                        "params": {"query_vector": embedding},
                    },
                }
            },
        }
        return await ElasticsearchTools.search_documents(query)

    @staticmethod
    async def keyword_search(keywords: str, fields: Optional[list[str]] = None, size: int = 10):
        if fields is None:
            fields = ["chunk_text", "content", "filename"]
        query = {
            "size": size,
            "query": {
                "multi_match": {
                    "query": keywords,
                    "fields": fields,
                    "type": "best_fields",
                    "fuzziness": "AUTO",
                }
            },
            "highlight": {"fields": {"chunk_text": {}, "content": {}, "filename": {}}},
        }
        return await ElasticsearchTools.search_documents(query)

    @staticmethod
    async def hybrid_search(keywords: str, embedding: list, size: int = 10, min_score: float = 0.7):
        query = {
            "size": size,
            "query": {
                "bool": {
                    "should": [
                        {
                            "multi_match": {
                                "query": keywords,
                                "fields": ["chunk_text^2", "content^2", "filename"],
                                "type": "best_fields",
                                "boost": 1.0,
                            }
                        },
                        {
                            "script_score": {
                                "query": {"match_all": {}},
                                "script": {
                                    "source": "cosineSimilarity(params.query_vector, 'embedding') + 1.0",
                                    "params": {"query_vector": embedding},
                                },
                                "boost": 2.0,
                            }
                        },
                    ]
                }
            },
            "min_score": min_score,
            "highlight": {"fields": {"content": {}, "filename": {}}},
        }
        return await ElasticsearchTools.search_documents(query)
