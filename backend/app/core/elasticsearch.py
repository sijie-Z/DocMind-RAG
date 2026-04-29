# -*- coding: utf-8 -*-
"""
派聪明AI知识库系统 - Elasticsearch连接模块
(修正版：移除IK分词器依赖，使用标准分词器，适配无插件环境)
"""

import logging
from elasticsearch import AsyncElasticsearch, NotFoundError
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

# Elasticsearch客户端实例
es_client: Optional[AsyncElasticsearch] = None

async def init_elasticsearch():
    """初始化Elasticsearch连接"""
    global es_client
    
    try:
        logger.info(f"正在连接Elasticsearch: {settings.ELASTICSEARCH_HOSTS} ...")
        
        es_client = AsyncElasticsearch(
            hosts=settings.ELASTICSEARCH_HOSTS,
            verify_certs=False,
            ssl_show_warn=False,
            request_timeout=5 # 5秒超时，加速启动
        )
        
        # 测试连接
        info = await es_client.info()
        logger.info(f"Elasticsearch连接成功，版本: {info.get('version', {}).get('number', 'unknown')}")
        
        # 检查是否安装了 IK 分词器
        try:
            # 尝试使用 _analyze 接口测试 ik_smart
            test_res = await es_client.indices.analyze(body={"analyzer": "ik_smart", "text": "你好世界"})
            logger.info("检测到 IK 分词器，将使用 ik_smart/ik_max_word 进行语义分词")
            settings.ES_ANALYZER = "ik_smart"
            settings.ES_SEARCH_ANALYZER = "ik_max_word"
        except Exception:
            logger.warning("未检测到 IK 分词器，将回退到 standard 分词器 (对中文搜索效果较差)")
            settings.ES_ANALYZER = "standard"
            settings.ES_SEARCH_ANALYZER = "standard"

        # 创建索引（如果不存在）
        await create_index_if_not_exists()
        
    except Exception as e:
        logger.error(f"Elasticsearch连接失败: {str(e)}")

def _knowledge_index_mapping() -> dict:
    # 获取动态检测到的分词器
    analyzer = getattr(settings, "ES_ANALYZER", "standard")
    search_analyzer = getattr(settings, "ES_SEARCH_ANALYZER", "standard")
    
    return {
        "mappings": {
            "properties": {
                "content": {
                    "type": "text",
                    "analyzer": analyzer,
                    "search_analyzer": search_analyzer
                },
                "document_id": {"type": "keyword"},
                "filename": {
                    "type": "text",
                    "fields": {"keyword": {"type": "keyword"}}
                },
                "file_type": {"type": "keyword"},
                "file_size": {"type": "long"},
                "upload_time": {"type": "date"},
                "user_id": {"type": "keyword"},
                "organization_id": {"type": "keyword"},
                "tags": {"type": "keyword"},
                "embedding": {
                    "type": "dense_vector",
                    "dims": settings.VECTOR_DIMENSION,
                    "index": True,
                    "similarity": "cosine"
                },
                "metadata": {
                    "type": "object",
                    "enabled": False
                }
            }
        },
        "settings": {
            "number_of_shards": 1,
            "number_of_replicas": 0
        }
    }


async def create_index_if_not_exists():
    """创建索引（如果不存在），或者检查并修复维度不匹配的情况"""
    if es_client is None:
        return

    try:
        index_name = settings.ELASTICSEARCH_INDEX_NAME
        exists = await es_client.indices.exists(index=index_name)
        
        if not exists:
            logger.info(f"创建新索引: {index_name}")
            mapping = _knowledge_index_mapping()
            await es_client.indices.create(
                index=index_name,
                mappings=mapping["mappings"],
                settings=mapping["settings"]
            )
            logger.info("索引创建成功")
        else:
            # 检查索引维度是否匹配
            mapping = await es_client.indices.get_mapping(index=index_name)
            try:
                current_dims = mapping[index_name]['mappings']['properties']['embedding']['dims']
                if current_dims != settings.VECTOR_DIMENSION:
                    logger.warning(f"检测到维度不匹配: ES={current_dims}, Config={settings.VECTOR_DIMENSION}。正在重建索引...")
                    await recreate_knowledge_index()
                else:
                    logger.info(f"索引已存在且维度匹配: {index_name} (dims={current_dims})")
            except KeyError:
                logger.warning(f"索引 {index_name} 缺少 embedding 字段或 dims 属性，正在重建...")
                await recreate_knowledge_index()

    except Exception as e:
        logger.error(f"创建/检查索引失败: {str(e)}")


async def recreate_knowledge_index() -> bool:
    """强制重建知识库索引（用于向量维度/映射变更后修复）"""
    client = await get_elasticsearch()
    try:
        index_name = settings.ELASTICSEARCH_INDEX_NAME
        exists = await client.indices.exists(index=index_name)
        if exists:
            await client.indices.delete(index=index_name)
            logger.warning(f"已删除旧索引: {index_name}")

        mapping = _knowledge_index_mapping()
        await client.indices.create(
            index=index_name,
            mappings=mapping["mappings"],
            settings=mapping["settings"]
        )
        logger.info(f"已重建索引: {index_name}")
        return True
    except Exception as e:
        logger.error(f"重建索引失败: {e}")
        return False

async def get_elasticsearch() -> AsyncElasticsearch:
    """获取Elasticsearch客户端"""
    if es_client is None:
        # 尝试重新初始化
        await init_elasticsearch()
        if es_client is None:
            raise RuntimeError("Elasticsearch客户端未初始化")
    return es_client

async def close_elasticsearch():
    """关闭Elasticsearch连接"""
    global es_client
    
    if es_client:
        try:
            await es_client.close()
            logger.info("Elasticsearch连接已关闭")
        except Exception as e:
            logger.error(f"关闭Elasticsearch连接失败: {str(e)}")
        finally:
            es_client = None

from elasticsearch.helpers import async_bulk

class ElasticsearchTools:
    """Elasticsearch工具类"""
    
    @staticmethod
    async def bulk_index_documents(actions: list):
        """批量索引文档"""
        client = await get_elasticsearch()
        await async_bulk(client, actions)

    @staticmethod
    async def index_document(doc_id: str, document: dict):
        """索引文档"""
        client = await get_elasticsearch()
        return await client.index(
            index=settings.ELASTICSEARCH_INDEX_NAME,
            id=doc_id,
            document=document
        )
    
    @staticmethod
    async def get_document(doc_id: str):
        """获取文档"""
        client = await get_elasticsearch()
        try:
            return await client.get(
                index=settings.ELASTICSEARCH_INDEX_NAME,
                id=doc_id
            )
        except NotFoundError:
            return None
    
    @staticmethod
    async def update_document(doc_id: str, document: dict):
        """更新文档"""
        client = await get_elasticsearch()
        return await client.update(
            index=settings.ELASTICSEARCH_INDEX_NAME,
            id=doc_id,
            doc=document
        )
    
    @staticmethod
    async def delete_document(doc_id: str):
        """删除文档"""
        client = await get_elasticsearch()
        return await client.delete(
            index=settings.ELASTICSEARCH_INDEX_NAME,
            id=doc_id
        )
    
    @staticmethod
    async def search_documents(query: dict):
        """搜索文档 (增强版：透传所有参数)"""
        client = await get_elasticsearch()
        # 提取核心参数，其余透传
        search_params = {
            "index": settings.ELASTICSEARCH_INDEX_NAME,
            "query": query.get("query", {"match_all": {}}),
        }
        
        # 允许透传的常用 ES 参数
        passthrough_keys = [
            "size", "from", "sort", "_source", "highlight", 
            "min_score", "aggs", "post_filter", "runtime_mappings"
        ]
        for key in passthrough_keys:
            if key in query:
                search_params[key] = query[key]
        
        return await client.search(**search_params)
    
    @staticmethod
    async def vector_search(embedding: list, size: int = 10, min_score: float = 0.7):
        """向量搜索"""
        query = {
            "size": size,
            "min_score": min_score,
            "query": {
                "script_score": {
                    "query": {"match_all": {}},
                    "script": {
                        "source": "cosineSimilarity(params.query_vector, 'embedding') + 1.0",
                        "params": {
                            "query_vector": embedding
                        }
                    }
                }
            }
        }
        
        return await ElasticsearchTools.search_documents(query)
    
    @staticmethod
    async def keyword_search(keywords: str, fields: Optional[list[str]] = None, size: int = 10):
        """关键词搜索"""
        if fields is None:
            fields = ["content", "filename"]
            
        query = {
            "size": size,
            "query": {
                "multi_match": {
                    "query": keywords,
                    "fields": fields,
                    "type": "best_fields",
                    "fuzziness": "AUTO"
                }
            },
            "highlight": {
                "fields": {
                    "content": {},
                    "filename": {}
                }
            }
        }
        
        return await ElasticsearchTools.search_documents(query)
    
    @staticmethod
    async def hybrid_search(keywords: str, embedding: list, size: int = 10, min_score: float = 0.7):
        """混合搜索（关键词 + 向量）"""
        query = {
            "size": size,
            "query": {
                "bool": {
                    "should": [
                        {
                            "multi_match": {
                                "query": keywords,
                                "fields": ["content^2", "filename"],
                                "type": "best_fields",
                                "boost": 1.0
                            }
                        },
                        {
                            "script_score": {
                                "query": {"match_all": {}},
                                "script": {
                                    "source": "cosineSimilarity(params.query_vector, 'embedding') + 1.0",
                                    "params": {
                                        "query_vector": embedding
                                    }
                                },
                                "boost": 2.0
                            }
                        }
                    ]
                }
            },
            "min_score": min_score,
            "highlight": {
                "fields": {
                    "content": {},
                    "filename": {}
                }
            }
        }
        
        return await ElasticsearchTools.search_documents(query)
        