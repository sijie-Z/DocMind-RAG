"""
示例数据端点 - 为新用户提供首次体验
"""
import logging
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.elasticsearch import ElasticsearchTools
from app.core.security import get_current_user, require_organization
from app.exceptions import AppError
from app.models.chat import ChatMessage, ChatSession, ChatSessionStatus, MessageType
from app.models.document import Document, DocumentChunk, DocumentStatus, DocumentType
from app.models.user import User
from app.services.embedding_service import embedding_service

router = APIRouter()
logger = logging.getLogger(__name__)

# ── 示例文档内容 ──────────────────────────────────────────────

DEMO_DOCUMENTS = [
    {
        "title": "FastAPI 快速入门指南",
        "filename": "fastapi_guide.txt",
        "file_type": DocumentType.TXT,
        "description": "FastAPI 框架核心概念与最佳实践",
        "keywords": ["FastAPI", "Python", "Web框架", "API"],
        "chunks": [
            "FastAPI 是一个现代、快速（高性能）的 Python Web 框架，用于构建 API。它基于标准 Python 类型提示，具有自动文档生成、数据验证、序列化等功能。FastAPI 的性能与 Node.js 和 Go 相当，这得益于它基于 Starlette 和 Pydantic 构建。",
            "路径参数和查询参数：FastAPI 使用 Python 类型提示来声明路径参数和查询参数。例如 @app.get('/items/{item_id}') 中的 item_id 就是路径参数。查询参数通过函数参数声明，如 def read_item(skip: int = 0, limit: int = 10)。FastAPI 会自动进行类型转换和验证。",
            "请求体和 Pydantic 模型：使用 Pydantic 的 BaseModel 来定义请求体模型。FastAPI 会自动解析 JSON 请求体，验证数据类型，并生成 OpenAPI 文档。例如 class Item(BaseModel): name: str; price: float; is_offer: bool = None。",
            "依赖注入系统：FastAPI 的依赖注入系统非常强大。使用 Depends() 可以声明依赖项，常用于数据库连接、认证、权限检查等场景。依赖项可以嵌套，形成依赖链。例如 current_user: User = Depends(get_current_user)。",
            "中间件和 CORS：FastAPI 支持中间件来处理请求和响应。常用的是 CORS 中间件，通过 app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_credentials=True, allow_methods=['*'], allow_headers=['*']) 来配置跨域资源共享。",
            "后台任务：FastAPI 支持后台任务处理，使用 BackgroundTasks 参数。适用于发送邮件、处理文件等不需要同步等待的操作。例如 def send_email(email: str, message: str): ... @app.post('/send-notification/') async def send_notification(background_tasks: BackgroundTasks): background_tasks.add_task(send_email, ...)。",
            "WebSocket 支持：FastAPI 原生支持 WebSocket 连接。使用 @app.websocket('/ws') 装饰器定义 WebSocket 端点，通过 websocket.receive_text() 和 websocket.send_text() 进行双向通信。适合实时聊天、通知推送等场景。",
            "安全和认证：FastAPI 提供了 OAuth2、JWT Token、API Key 等多种认证方式。使用 OAuth2PasswordBearer 声明安全方案，结合 passlib 和 python-jose 实现完整的用户认证流程。密码使用 bcrypt 加密存储。",
        ]
    },
    {
        "title": "Vue 3 组合式 API 完全指南",
        "filename": "vue3_composition_api.txt",
        "file_type": DocumentType.TXT,
        "description": "Vue 3 Composition API 核心概念与实战技巧",
        "keywords": ["Vue3", "Composition API", "前端", "TypeScript"],
        "chunks": [
            "Vue 3 引入了组合式 API（Composition API），这是一种全新的组件逻辑组织方式。与选项式 API 不同，组合式 API 使用 setup() 函数或 <script setup> 语法糖，让相关逻辑聚合在一起，提高代码可读性和可维护性。",
            "响应式系统：Vue 3 使用 ref() 和 reactive() 创建响应式数据。ref() 用于基本类型，返回一个可变的 ref 对象，通过 .value 访问。reactive() 用于对象类型，返回对象的响应式代理。两者都可以在模板中直接使用，Vue 会自动解包。",
            "计算属性和侦听器：computed() 用于创建计算属性，具有缓存特性。watch() 和 watchEffect() 用于侦听响应式数据变化并执行副作用。watch() 需要明确指定侦听源，而 watchEffect() 自动追踪依赖。两者都支持清理回调和 flush 选项。",
            "生命周期钩子：组合式 API 中的生命周期钩子以 on 为前缀，如 onMounted、onUpdated、onUnmounted。它们在 setup() 内部调用，可以注册多个钩子。onMounted 在组件挂载后执行，常用于 DOM 操作和数据获取。",
            "组件通信：父传子使用 defineProps()，子传父使用 defineEmits()。跨组件通信可使用 provide/inject 模式。Pinia 作为 Vue 3 推荐的状态管理库，提供了响应式的全局状态管理，支持 TypeScript 类型推断。",
            "组合式函数（Composables）：将可复用的逻辑提取为以 use 开头的函数，如 useMouse、useFetch。组合式函数可以使用所有 Vue 响应式 API，并在多个组件中复用。这是 Vue 3 生态中最强大的代码复用模式。",
            "TypeScript 集成：Vue 3 使用 TypeScript 重写，提供了一流的类型支持。使用泛型定义 Props 类型，如 const props = defineProps<{ title: string; count?: number }>()。defineModel() 宏简化了 v-model 的类型声明。",
            "性能优化：使用 shallowRef() 和 shallowReactive() 减少深层响应式开销。v-memo 指令缓存模板片段。defineAsyncComponent() 实现组件懒加载。合理使用 v-once 和 v-memo 避免不必要的重新渲染。",
        ]
    },
    {
        "title": "RAG 检索增强生成技术详解",
        "filename": "rag_technology.txt",
        "file_type": DocumentType.TXT,
        "description": "RAG 架构原理、实现方法与优化策略",
        "keywords": ["RAG", "LLM", "向量检索", "知识库"],
        "chunks": [
            "RAG（Retrieval-Augmented Generation，检索增强生成）是一种将信息检索与大语言模型结合的技术架构。它通过从外部知识库中检索相关文档，将其作为上下文提供给 LLM，从而生成更准确、更有依据的回答。RAG 解决了 LLM 知识截止和幻觉问题。",
            "文档处理流程：RAG 系统首先需要将原始文档进行解析、分块和向量化。文档解析支持 PDF、Word、TXT 等格式。分块策略包括固定长度分块、语义分块和递归分块。每个文本块通过 Embedding 模型转换为向量，存入向量数据库。",
            "混合检索策略：现代 RAG 系统通常采用混合检索，结合 BM25 关键词检索和向量语义检索。通过 RRF（Reciprocal Rank Fusion）算法融合两种检索结果，既保证关键词精确匹配，又支持语义相似度搜索。这种混合策略比单一检索效果提升 15-30%。",
            "重排序（Rerank）：检索结果经过 Rerank 模型重新排序，提升相关性。Rerank 模型（如 Cohere Rerank、bge-reranker）对 query-document 对进行交叉编码打分，比向量相似度更精确。通常保留 Top-5 结果作为 LLM 上下文。",
            "上下文压缩：当检索到的文档较多时，需要进行上下文压缩以适应 LLM 的 token 限制。策略包括：提取关键句子、摘要生成、去除冗余信息。先进的压缩方法会保留引用标记 [1][2]，让用户可以追溯信息来源。",
            "引用溯源：RAG 系统需要在回答中标注信息来源。实现方式是在 Prompt 中要求 LLM 使用 [n] 格式引用文档，并在前端渲染为可点击的引用卡片。这增强了回答的可信度和可验证性。",
            "质量评估：RAG 系统的核心指标包括：命中率（有检索结果的查询比例）、引用率（回答中有引用的比例）、相关性分数。通过 RAGAS 等框架可以自动化评估 Faithfulness、Answer Relevancy、Context Precision 等维度。",
            "Agent 增强：将 RAG 与 Agent 结合，可以实现更复杂的任务。Agent 可以自主决定何时检索、检索什么、是否需要多轮检索。通过 ReAct 模式（Reasoning + Acting），Agent 能够分解复杂问题，逐步获取信息并综合回答。",
        ]
    },
]

DEMO_CONVERSATION = {
    "title": "DocMind 功能演示对话",
    "messages": [
        {
            "content": "RAG 系统中混合检索是怎么工作的？",
            "type": MessageType.USER,
        },
        {
            "content": "RAG 系统中的混合检索是一种将多种检索策略结合的技术方案，主要有以下几个关键点：\n\n**1. 双通道检索**\n混合检索同时执行两种检索方式：\n- **BM25 关键词检索**：基于词频和文档频率的经典算法，擅长精确匹配专业术语和专有名词\n- **向量语义检索**：通过 Embedding 模型将文本转为向量，计算余弦相似度，擅长理解语义相近的表达 [1]\n\n**2. 结果融合（RRF）**\n两种检索结果通过 RRF（Reciprocal Rank Fusion）算法进行融合。RRF 不直接比较分数，而是基于排名位置计算权重，避免了不同检索方式分数尺度不一致的问题 [2]\n\n**3. 效果提升**\n实验数据表明，混合检索策略比单一检索方式的效果提升 15-30%。它既保证了关键词的精确匹配（如搜索 \"FastAPI\" 不会被 \"Flask\" 干扰），又支持语义理解（如搜索 \"Web框架\" 能匹配到 \"FastAPI\"）[2]\n\n**4. 典型实现**\n```\n# 伪代码示例\nbm25_results = bm25_search(query, top_k=20)\nvector_results = vector_search(query_embedding, top_k=20)\nfinal_results = rrf_fusion(bm25_results, vector_results, k=60)\n```\n\n这种混合策略是现代 RAG 系统（包括 DocMind）的核心检索架构 [1][2]",
            "type": MessageType.ASSISTANT,
            "metadata": {
                "sources": [
                    {"filename": "rag_technology.txt", "chunk_index": 2, "relevance_score": 0.92, "demo": True},
                    {"filename": "rag_technology.txt", "chunk_index": 1, "relevance_score": 0.85, "demo": True},
                ]
            }
        },
        {
            "content": "Vue 3 的组合式 API 和选项式 API 有什么区别？",
            "type": MessageType.USER,
        },
        {
            "content": "Vue 3 的组合式 API 和选项式 API 是两种不同的组件逻辑组织方式，主要区别如下：\n\n**选项式 API（Options API）**\n- 使用 data、methods、computed、watch 等选项组织代码\n- 相关逻辑分散在不同选项中\n- 通过 this 访问组件实例\n- Vue 2 的默认方式，学习曲线较平缓\n\n**组合式 API（Composition API）**\n- 使用 setup() 函数或 `<script setup>` 语法 [1]\n- 相关逻辑聚合在一起，提高可读性 [1]\n- 使用 ref() 和 reactive() 创建响应式数据 [2]\n- 更好的 TypeScript 类型推断支持 [7]\n\n**核心优势对比**：\n\n| 特性 | 选项式 | 组合式 |\n|------|--------|--------|\n| 逻辑复用 | Mixins（命名冲突风险）| Composables（类型安全）[6] |\n| 类型推断 | 有限 | 完整 TS 支持 [7] |\n| 代码组织 | 按选项分类 | 按功能聚合 [1] |\n| 学习门槛 | 较低 | 稍高但更灵活 |\n\n**推荐**：新项目建议使用组合式 API + `<script setup>` + TypeScript，可以获得最佳的开发体验和代码可维护性 [1][7]",
            "type": MessageType.ASSISTANT,
            "metadata": {
                "sources": [
                    {"filename": "vue3_composition_api.txt", "chunk_index": 0, "relevance_score": 0.95, "demo": True},
                    {"filename": "vue3_composition_api.txt", "chunk_index": 1, "relevance_score": 0.88, "demo": True},
                    {"filename": "vue3_composition_api.txt", "chunk_index": 5, "relevance_score": 0.82, "demo": True},
                    {"filename": "vue3_composition_api.txt", "chunk_index": 6, "relevance_score": 0.79, "demo": True},
                ]
            }
        },
    ]
}


@router.post("/seed", response_model=dict)
async def seed_demo_data(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    为当前用户创建示例数据：3 篇文档 + 索引 + 1 个示例对话。
    如果已有示例文档则跳过。
    """
    # 安全加固（issue #268）：示例数据归属「当前用户自己的组织」——文件中不存在
    # 「demo 组织」概念，此前 `organization_id or 1` 兜底会把固定示例文本写进 1 号
    # 组织的命名空间（且 1 号组织未必存在——见 security.py 注释：本库唯一组织的
    # id 是 3，写库会因外键失败炸成 500）。无组织的普通用户显式 403；超管无组织时
    # 按 #168 解析库中真实存在的组织。必须在 try 块之前 —— 否则 AuthorizationError
    # 会被下面的 `except Exception` 吞掉、包装成 AppError(500)。
    org_id = await require_organization(current_user, db)

    try:
        # 检查是否已经加载过示例数据
        existing = await db.execute(
            select(func.count(Document.id)).where(
                Document.uploaded_by == current_user.id,
                Document.description == "示例数据 - DocMind 首次体验"
            )
        )
        if existing.scalar() and existing.scalar() > 0:
            return {"success": True, "message": "示例数据已存在", "skipped": True}

        created_doc_ids = []
        documents_failed: list[dict] = []

        # 1. 创建文档记录并索引到 ES
        for doc_data in DEMO_DOCUMENTS:
            doc_id = str(uuid.uuid4())
            full_content = "\n\n".join(doc_data["chunks"])

            doc = Document(
                id=doc_id,
                filename=doc_data["filename"],
                file_path=f"demo/{doc_data['filename']}",
                file_size=len(full_content.encode("utf-8")),
                file_type=doc_data["file_type"],
                # issue #229（#301 批 D 收口）：创建时**不预写 INDEXED** ——
                # INDEXED 只能在确认「真的进了 ES」之后写（见下方成功分支）。
                # 修前创建即 INDEXED、embedding 失败仅 warning 跳过 ES：
                # 「DB 说 INDEXED，ES 里什么都没有」。
                status=DocumentStatus.PENDING,
                title=doc_data["title"],
                description="示例数据 - DocMind 首次体验",
                keywords=doc_data["keywords"],
                content_length=len(full_content),
                chunk_count=len(doc_data["chunks"]),
                organization_id=org_id,
                uploaded_by=current_user.id,
                parsed_at=datetime.now(UTC),
            )
            db.add(doc)

            # 2. 生成 embedding 并索引到 ES
            embed_error = None
            try:
                embeddings = await embedding_service.get_embeddings(doc_data["chunks"])
            except Exception as e:
                # 安全修复：不再写零向量（会污染向量检索）；#229：状态如实失败。
                logger.error(f"Embedding failed for {doc_data['title']}: {e}")
                embed_error = str(e)
                embeddings = None

            if embeddings is not None and len(embeddings) != len(doc_data["chunks"]):
                embed_error = (
                    f"向量数量与文本块数量不匹配: {len(embeddings)} vs {len(doc_data['chunks'])}"
                )
                logger.error(f"Embedding length mismatch for {doc_data['title']}: {embed_error}")
                embeddings = None

            if embeddings is None:
                # issue #229：embedding 失败 → 不索引 ES，且状态如实写 FAILED（带原因），
                # 绝不 INDEXED；失败文档明确记账（不再悄然少 1 仍报「加载成功」）。
                for i, chunk_text in enumerate(doc_data["chunks"]):
                    db.add(DocumentChunk(
                        id=str(uuid.uuid4()),
                        document_id=doc_id,
                        chunk_index=i,
                        chunk_text=chunk_text,
                        chunk_length=len(chunk_text),
                        start_pos=i * 500,
                        end_pos=(i + 1) * 500,
                    ))
                doc.status = DocumentStatus.FAILED
                doc.parse_error = f"示例数据向量化失败: {embed_error}"
                documents_failed.append({
                    "id": doc_id,
                    "title": doc_data["title"],
                    "error": doc.parse_error,
                })
                continue

            index_failures = 0
            for i, (chunk_text, embedding) in enumerate(zip(doc_data["chunks"], embeddings, strict=False)):
                # 保存 chunk 到数据库
                chunk = DocumentChunk(
                    id=str(uuid.uuid4()),
                    document_id=doc_id,
                    chunk_index=i,
                    chunk_text=chunk_text,
                    chunk_length=len(chunk_text),
                    start_pos=i * 500,
                    end_pos=(i + 1) * 500,
                )
                db.add(chunk)

                # 索引到 Elasticsearch
                es_doc = {
                    "content": chunk_text,
                    "document_id": doc_id,
                    "filename": doc_data["filename"],
                    "file_type": doc_data["file_type"].value,
                    "file_size": len(full_content.encode("utf-8")),
                    "upload_time": datetime.now(UTC).isoformat(),
                    "user_id": str(current_user.id),
                    "organization_id": str(org_id),
                    "tags": doc_data["keywords"],
                    "embedding": embedding,
                    "metadata": {
                        "chunk_index": i,
                        "title": doc_data["title"],
                        "description": doc_data["description"],
                    }
                }
                try:
                    await ElasticsearchTools.index_document(
                        doc_id=f"{doc_id}_{i}", document=es_doc
                    )
                except Exception as e:
                    index_failures += 1
                    logger.warning(f"ES index failed for chunk {i}: {e}")

            if index_failures >= len(doc_data["chunks"]):
                # issue #229：一条 chunk 都没进 ES —— 不得写 INDEXED
                # （「状态 = INDEXED ⟹ 可被检索」不变式的 demo 路径执行者）。
                doc.status = DocumentStatus.FAILED
                doc.parse_error = "示例数据 ES 索引全部失败"
                documents_failed.append({
                    "id": doc_id,
                    "title": doc_data["title"],
                    "error": doc.parse_error,
                })
                continue
            if index_failures:
                # 部分失败与主链路的 #224 部分索引语义对齐：文档整体可被检索，
                # 留痕但不阻断（demo 数据静态、可重试重建）。
                logger.warning(
                    f"Demo doc {doc_data['title']}: {index_failures}/"
                    f"{len(doc_data['chunks'])} chunk(s) failed to index"
                )

            doc.status = DocumentStatus.INDEXED
            doc.indexed_at = datetime.now(UTC)
            created_doc_ids.append(doc_id)
            logger.info(f"Created demo document: {doc_data['title']} ({doc_id})")

        # 3. 创建示例对话
        session_id = str(uuid.uuid4())
        now = datetime.now(UTC)

        session = ChatSession(
            id=session_id,
            user_id=current_user.id,
            organization_id=org_id,
            title=DEMO_CONVERSATION["title"],
            status=ChatSessionStatus.ACTIVE,
            message_count=len(DEMO_CONVERSATION["messages"]),
            last_message_at=now,
        )
        db.add(session)

        for i, msg_data in enumerate(DEMO_CONVERSATION["messages"]):
            msg = ChatMessage(
                id=str(uuid.uuid4()),
                session_id=session_id,
                content=msg_data["content"],
                message_type=msg_data["type"],
                meta_data=msg_data.get("metadata"),
                created_at=now + timedelta(seconds=i * 30),
            )
            db.add(msg)

        await db.commit()

        documents_failed_count = len(documents_failed)
        if documents_failed_count == 0:
            message = "示例数据加载成功"
        elif not created_doc_ids:
            message = "示例数据加载失败：全部文档未能完成索引（文档状态已如实标记为失败）"
        else:
            message = (
                f"示例数据部分加载：{documents_failed_count}/{len(DEMO_DOCUMENTS)} "
                f"篇文档未能完成索引（已标记为失败）"
            )

        # issue #229：响应如实 —— 有失败文档就 success=False 且明确报数，
        # 不再「documents_created 悄然少 1 仍报加载成功」。
        return {
            "success": documents_failed_count == 0,
            "message": message,
            "data": {
                "documents_created": len(created_doc_ids),
                "documents_failed": documents_failed_count,
                "failed_documents": documents_failed,
                "conversation_id": session_id,
                "conversation_title": DEMO_CONVERSATION["title"],
            }
        }

    except Exception as e:
        logger.error(f"Failed to seed demo data: {e}", exc_info=True)
        raise AppError(f"加载示例数据失败: {str(e)}")


@router.delete("/seed", response_model=dict)
async def clear_demo_data(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """清除当前用户的示例数据。"""
    try:
        # 查找示例文档
        result = await db.execute(
            select(Document).where(
                Document.uploaded_by == current_user.id,
                Document.description == "示例数据 - DocMind 首次体验"
            )
        )
        docs = result.scalars().all()

        for doc in docs:
            # 从 ES 删除
            try:
                for i in range(doc.chunk_count):
                    await ElasticsearchTools.delete_document(f"{doc.id}_{i}")
            except Exception:
                pass
            # 删除 chunks
            chunks_result = await db.execute(
                select(DocumentChunk).where(DocumentChunk.document_id == doc.id)
            )
            for chunk in chunks_result.scalars().all():
                await db.delete(chunk)
            await db.delete(doc)

        # 删除示例对话
        sessions_result = await db.execute(
            select(ChatSession).where(
                ChatSession.user_id == current_user.id,
                ChatSession.title == DEMO_CONVERSATION["title"]
            )
        )
        for session in sessions_result.scalars().all():
            for msg in session.messages:
                await db.delete(msg)
            await db.delete(session)

        await db.commit()

        return {"success": True, "message": "示例数据已清除"}

    except Exception as e:
        logger.error(f"Failed to clear demo data: {e}")
        raise AppError(f"清除示例数据失败: {str(e)}")
