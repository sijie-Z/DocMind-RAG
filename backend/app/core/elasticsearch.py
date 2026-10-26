"""Elasticsearch connection module with lazy-initialized holder pattern.

Uses a proxy object for `es_client` so that module-level imports
always resolve to the live client after `init_elasticsearch()`.
"""

import logging

from elasticsearch import AsyncElasticsearch, NotFoundError
from elasticsearch.helpers import async_bulk

from app.core.config import resolve_embedding_model, settings

logger = logging.getLogger(__name__)


class IndexConfigError(Exception):
    """索引配置与现存索引不一致（向量维度不匹配、mapping 缺字段等）。

    **这个异常的存在意义是阻止自动删数据。**
    维度对不上时重建索引会销毁全部已索引内容，所以这里一律只报告、不重建 ——
    删除必须由人显式发起。
    """


class _ESHolder:
    """Lifecycle-managed Elasticsearch client holder."""

    def __init__(self):
        self._client: AsyncElasticsearch | None = None
        self.es_analyzer: str = "cjk"
        self.es_search_analyzer: str = "cjk"
        # 索引配置问题：不阻断 ES 客户端的其他用途，但索引/检索会不可用。
        # 由 /health 与文档处理路径读取（见 get_index_error）。
        self.index_error: str | None = None

    @property
    def client(self) -> AsyncElasticsearch | None:
        return self._client

    async def initialize(self):
        if self._client is not None:
            return
        try:
            logger.info(f"Connecting to Elasticsearch: {settings.ELASTICSEARCH_HOSTS} ...")
            self._client = AsyncElasticsearch(
                hosts=settings.ELASTICSEARCH_HOSTS,
                # 安全修复：TLS 校验由配置控制（生产 https 下默认不应关闭）
                verify_certs=getattr(settings, "ELASTICSEARCH_VERIFY_CERTS", False),
                ssl_show_warn=False,
                request_timeout=5,
            )
            info = await self._client.info()
            logger.info(
                f"Elasticsearch connected, version: {info.get('version', {}).get('number', 'unknown')}"
            )

            # Detect IK analyzer at init time — store on holder, not settings
            try:
                await self._client.indices.analyze(
                    body={"analyzer": "ik_smart", "text": "你好世界"}
                )
                logger.info("IK analyzer detected — using ik_smart/ik_max_word")
                self.es_analyzer = "ik_smart"
                self.es_search_analyzer = "ik_max_word"
            except Exception:
                logger.info(f"IK analyzer not found — using built-in '{self.es_analyzer}' analyzer")

            try:
                await _create_index_if_not_exists(self._client)
            except Exception as e:
                # **索引这一层出问题，不该把整个 ES 客户端打掉。**
                #
                # 这里原先是 `except IndexConfigError` —— 只保护了「索引已存在、维度不匹配」
                # 那一条路径。但**索引不存在时 `indices.create` 失败**（典型：mapping 里
                # `dims` 是字符串，ES 返回 400 mapper_parsing_exception）会落进下面的通用
                # `except`，把 `_client` 置为 None。后果是 `get_elasticsearch()` 每次都抛
                # RuntimeError ——「索引配置错了」表现成「ES 连不上」，正是本 PR 要消除的
                # 那种误导。（这一条由对抗评审指出，已在真机上复现。）
                #
                # 注意：**真的连不上 ES** 不会走到这里 —— 上面那句 `self._client.info()`
                # 会先失败并落进通用 except，那时把 client 置空才是对的。
                self.index_error = str(e)
                logger.error(f"[索引不可用] {type(e).__name__}: {e}")
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
def _source_provenance_mapping_properties() -> dict:
    """Additive fields for original-text provenance; retrieval mapping stays unchanged."""
    return {
        "original_text": {"type": "text", "index": False},
        "text_provenance": {"type": "keyword"},
    }


def _knowledge_index_mapping() -> dict:
    analyzer = _holder.es_analyzer
    search_analyzer = _holder.es_search_analyzer
    return {
        "mappings": {
            # issue #196：把 embedding 模型写进索引元数据。维度相同但模型不同的
            # 向量空间同样不可混用；没有这条元数据，换模型后检索会静默失真。
            "_meta": {
                "embedding_model": resolve_embedding_model(),
                "vector_dimension": settings.VECTOR_DIMENSION,
            },
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
                **_source_provenance_mapping_properties(),
                "chunk_id": {"type": "keyword"},
                "chunk_index": {"type": "integer"},
                "section_title": {"type": "text"},
                # #197：PDF 页码提升为顶层整型字段（可检索/可按页过滤）；
                # 非 PDF 格式写入 None（ES 视为缺省，不占索引值）。
                "page_number": {"type": "integer"},
                # #188：结构化条款字段（doc_processor 从定义位抽取后写入顶层）。
                # clause_no 为整型数组：一个 chunk 可定义多条（数组字段合法）；
                # section_no 为 X.Y 章节编号（keyword，精确 term 查询用）。
                "clause_no": {"type": "integer"},
                "section_no": {"type": "keyword"},
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
            },
        },
        # 安全修复：副本数由配置控制（生产建议 ≥1，避免节点故障丢数据）
        "settings": {
            "number_of_shards": 1,
            "number_of_replicas": int(getattr(settings, "ELASTICSEARCH_REPLICAS", 0)),
        },
    }


async def _count_index_docs(client: AsyncElasticsearch, index_name: str) -> int:
    """索引里现有文档数。

    用于在任何破坏性操作**之前**如实报告「将要失去什么」。
    统计失败返回 -1（区别于「确实是 0 篇」）。
    """
    try:
        res = await client.count(index=index_name)
        return int(res.get("count", 0))
    except Exception as e:  # noqa: BLE001 — 统计失败不该阻断主流程，但必须如实标注
        logger.warning(f"统计索引文档数失败（{index_name}）: {e}")
        return -1


async def _create_index_if_not_exists(client: AsyncElasticsearch):
    """索引不存在则创建；**存在则只校验，永不删除**。

    维度或 mapping 不一致时抛 `IndexConfigError`，由调用方决定怎么处理。
    这个函数**不会**销毁已有数据 —— 删除必须显式发起（见 recreate_knowledge_index）。

    此前的行为是「不匹配就重建」，等于**改一次 VECTOR_DIMENSION 就清空整个知识库**，
    而且它挂在每次文档处理都会走到的路径上。
    """
    index_name = settings.ELASTICSEARCH_INDEX_NAME

    try:
        exists = await client.indices.exists(index=index_name)
    except Exception as e:  # noqa: BLE001 — 连不上 ES 是另一类问题，由 initialize 处理
        logger.error(f"检查索引 {index_name} 是否存在失败: {e}")
        return

    if not exists:
        logger.info(f"Creating index: {index_name}")
        mapping = _knowledge_index_mapping()
        try:
            await client.indices.create(
                index=index_name, mappings=mapping["mappings"], settings=mapping["settings"]
            )
        except Exception as e:
            # 多副本首次启动竞态：另一个进程刚建好同名索引。这不是配置错误，
            # 不能让 index_error 闩锁把本进程的检索永久拒掉——改走已存在校验。
            if "resource_already_exists" not in str(e):
                raise
            logger.warning(
                "索引 %s 并发创建竞态（resource_already_exists），改走已存在校验",
                index_name,
            )
        else:
            logger.info("Index created")
            _holder.index_error = None
            return

    mapping = await client.indices.get_mapping(index=index_name)
    props = mapping.get(index_name, {}).get("mappings", {}).get("properties", {})
    current_dims = (props.get("embedding") or {}).get("dims")

    if current_dims is None:
        doc_count = await _count_index_docs(client, index_name)
        raise IndexConfigError(
            f"索引 {index_name} 存在但读不到 embedding 维度（mapping 缺该字段）。"
            f"索引现有 {doc_count} 篇文档，**不自动重建**。"
            f"确认可以丢弃这些数据后，显式调用 recreate_knowledge_index(confirm=True)。"
        )

    if current_dims != settings.VECTOR_DIMENSION:
        doc_count = await _count_index_docs(client, index_name)
        raise IndexConfigError(
            f"向量维度不匹配：索引里是 {current_dims}，配置（VECTOR_DIMENSION）是 "
            f"{settings.VECTOR_DIMENSION}。索引现有 {doc_count} 篇文档，**不自动重建**。"
            f"处理方式二选一："
            f"① 把 VECTOR_DIMENSION 改回 {current_dims} —— 不动数据，立刻恢复；"
            f"② 确认可以丢弃这 {doc_count} 篇索引后，显式调用 "
            f"recreate_knowledge_index(confirm=True)，**并重新索引全部文档**。"
        )

    # issue #196：模型一致性——维度相同不代表向量空间相同。
    # 索引 _meta 记录了建索引时的模型；与当前解析出的模型不一致时拒绝使用该索引
    # （绝不自动重建：删除必须由人显式发起，见 recreate_knowledge_index）。
    index_meta = mapping.get(index_name, {}).get("mappings", {}).get("_meta") or {}
    index_model = index_meta.get("embedding_model")
    current_model = resolve_embedding_model()
    if index_model is None:
        # 旧索引没有模型元数据：无法证明一致，也不能凭空判定不一致。
        # 显式告警，让「索引可能建于另一个模型」这件事可被发现。
        logger.warning(
            f"索引 {index_name} 未记录 embedding 模型（旧索引），"
            f"无法验证与当前模型 {current_model!r} 的一致性。"
            f"若曾更换 embedding 模型，请重建索引并重新索引全部文档"
            f"（recreate_knowledge_index + scripts/reconcile_knowledge_index.py --reindex）。"
        )
    elif index_model != current_model:
        doc_count = await _count_index_docs(client, index_name)
        raise IndexConfigError(
            f"embedding 模型不匹配：索引 {index_name} 建于模型 {index_model!r}，"
            f"当前配置解析为 {current_model!r}。不同模型的向量空间不可混用"
            f"（即使维度相同，检索结果也会失真）。索引现有 {doc_count} 篇文档，"
            f"**不自动重建**。处理方式二选一："
            f"① 把 embedding 配置改回 {index_model!r} —— 不动数据，立刻恢复；"
            f"② 确认可以丢弃这 {doc_count} 篇索引后，显式调用 "
            f"recreate_knowledge_index(confirm=True)，并运行 "
            f"scripts/reconcile_knowledge_index.py --reindex 重新索引全部文档。"
        )

    # 校验通过：闩锁反映「最近一次校验结果」——此前的瞬时错误/旧配置错误应被解除，
    # 否则查询侧 gate 会把一次启动抖动放大成永久拒检（issue #196 评审）。
    _holder.index_error = None
    logger.info(f"Index exists with matching dims: {index_name} (dims={current_dims})")

    # #188：结构化条款字段是后加字段——旧索引不会自动拥有它们。缺字段时不阻断
    # （写入照旧、查询侧精确腿自然无命中），但必须给出可执行的迁移提示，
    # 否则「条号精确检索没效果」会表现成沉默的质量问题。
    missing_structured = [f for f in ("clause_no", "section_no") if f not in props]
    if missing_structured:
        logger.warning(
            "索引 %s 缺少结构化条款字段 %s —— 条号/章节精确检索腿不会生效。"
            "迁移方式：确认可重建后调用 recreate_knowledge_index(confirm=True)，"
            "并重新索引全部文档（文档处理链会写入这些字段）。",
            index_name,
            missing_structured,
        )

    missing_provenance = [
        field for field in _source_provenance_mapping_properties() if field not in props
    ]
    if missing_provenance:
        logger.warning(
            "索引 %s 缺少原文溯源字段 %s —— 请显式运行 "
            "scripts/add_rag_source_provenance_mapping.py（仅 additive put_mapping，"
            "不会创建或重建索引）。",
            index_name,
            missing_provenance,
        )


async def create_index_if_not_exists():
    """公开包装 —— 用 holder 的客户端。

    **索引配置不一致时会抛 IndexConfigError**（而不是静默重建）。
    调用方应捕获它并如实上报，不要吞掉。
    """
    client = await get_elasticsearch()
    try:
        await _create_index_if_not_exists(client)
    except IndexConfigError as e:
        # 写路径上的漂移也要让查询侧感知：pipeline 的检索 gate 读这个闩锁，
        # 避免「写入被拒、查询仍用新模型撞旧索引」（issue #196）。
        _holder.index_error = str(e)
        raise


def get_index_error() -> str | None:
    """返回最近一次索引配置错误（没有则 None）。

    供 /health 与文档处理路径读取 —— 让"索引坏了"这件事有个可读的出口，
    而不是只留在日志里。
    """
    return _holder.index_error


async def recreate_knowledge_index(*, confirm: bool = False) -> bool:
    """**删除并重建**知识库索引。会丢失索引中的全部数据。

    必须显式传 `confirm=True` 才执行。

    加这道门槛是因为此前的自动调用路径极其危险：维度一旦不匹配就静默重建，
    而"改配置换 embedding 模型"正是维度不匹配的常见来源 ——
    等于换一次模型就把知识库清空，且没有二次确认、没有备份。
    """
    if not confirm:
        logger.error(
            "recreate_knowledge_index() 被调用但未确认（confirm=False）—— 拒绝执行。"
            "该操作会删除整个索引；确实需要时请显式传 confirm=True。"
        )
        return False

    client = await get_elasticsearch()
    index_name = settings.ELASTICSEARCH_INDEX_NAME
    try:
        exists = await client.indices.exists(index=index_name)
        if exists:
            doc_count = await _count_index_docs(client, index_name)
            logger.warning(
                f"[破坏性操作] 即将删除索引 {index_name}，其中含 {doc_count} 篇文档。"
                f"删除后这些文档必须重新索引才能被检索到。"
            )
            await client.indices.delete(index=index_name)
            logger.warning(f"已删除索引 {index_name}（原有 {doc_count} 篇文档）")
        mapping = _knowledge_index_mapping()
        await client.indices.create(
            index=index_name, mappings=mapping["mappings"], settings=mapping["settings"]
        )
        logger.info(f"Recreated index: {index_name}")
        _holder.index_error = None  # 重建成功，清掉此前记录的错误
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
        return await client.update(index=settings.ELASTICSEARCH_INDEX_NAME, id=doc_id, doc=document)

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
            "size",
            "from",
            "sort",
            "_source",
            "highlight",
            "min_score",
            "aggs",
            "post_filter",
            "runtime_mappings",
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
    async def keyword_search(keywords: str, fields: list[str] | None = None, size: int = 10):
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
