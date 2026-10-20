import type { AgentEvent, PlanStep } from '@/types/agent'

export interface ChatMessage {
  id?: string | number
  content: string
  messageType: 'user' | 'assistant'
  conversationId: string | number
  createdAt?: string
  sources?: KnowledgeSource[]
  files?: AttachedFile[]
  feedback?: number
  feedbackNote?: string
  isCached?: boolean
  thinking?: string
  /** E7：用户在生成中途点了「停止生成」——渲染「已停止」标记与继续提示（瞬态，不落库） */
  stopped?: boolean
  // ── Agent 模式（useAgent=true）PER 事件落账（issue #289）───────────────
  // 聊天页此前对这 7 型事件（run_report/execution_context/retry_hint/
  // plan_start/plan_step/plan_complete/reflection）传输放行但零消费者，
  // 同一 run 与 Agent 页信息不同构。字段口径对齐 Agent 页 store：
  events?: AgentEvent[]          // reflection / retry_hint（事件台账）
  runReport?: string             // run_report 文本（折叠块）
  executionContext?: Record<string, unknown>  // execution_context（总 tokens 等）
  planSteps?: PlanStep[]         // plan_*（计划树）
  planProgress?: number
}

export interface AttachedFile {
  name: string
  status: 'uploading' | 'parsing' | 'indexing' | 'done' | 'error'
  id?: string
  errorMsg?: string
  _originalFile?: File
  _errorExpanded?: boolean
  progress?: number
  statusDetail?: string
  parsedContent?: string
}

export interface Conversation {
  id: string | number
  title: string
  userId: number
  createdAt: string
  updatedAt: string
  messageCount?: number
  settings?: {
    bound_document_ids?: string[]
  }
}

// W2-T7：对齐后端真实响应形状（chat_service.build_sources_list 逐键对应）。
// issue #185（分数契约）：来源分数拆成两问——
//   relevanceScore：重排器绝对分（local CE logit / zhipu relevance_score），
//                   无重排分时为 null；**不得**用相对排序分回填；
//   rankScore：融合阶段相对排序分（只用于排序/序位，不是相关度）；
//   rank：最终序位（1-based）。旧消息可能缺 rank/rankScore（按序位回退展示）。
// 旧声明含 fileId/chunkIndex 等真实响应没有的键——本轮来源预览接线曾按旧键取值
// 导致实机永不开弹窗（vitest fixture 自造 fileId 掩盖），已修真。既有
// chatSourcePopover.test.ts 的 fixture 注释正是这一事实的旁证。
export interface KnowledgeSource {
  filename: string
  relevanceScore?: number | null
  rankScore?: number | null
  rank?: number
  content?: string
  documentId?: string | number | null
  hasKeyword?: boolean
  hasVector?: boolean
  rewriteHits?: number
  freshFactor?: number
  cited?: boolean
  /** T4（第二轮 §7.2④）：示例对话的写死来源（假分数）逐条标注，前端渲染「演示数据」角标 */
  demo?: boolean
}

export interface RetrievalDebugData {
  strategy?: string
  query?: string
  cache_hit?: boolean
  cache_type?: string
  keyword_top?: DebugHit[]
  vector_top?: DebugHit[]
  rrf_result?: DebugHit[]
  rerank_result?: DebugHit[]
  /** issue #189：重排路径状态（pipeline 顶层写入；status=degraded 表示全链失败回退原序） */
  rerank?: RerankDebugInfo
  rerank_reorder?: { id: string; filename: string }[]
  total_results?: number
  elapsed_ms?: number
  note?: string
  stages?: Record<string, RetrievalDebugData>
  /** The actual context content sent to the LLM */
  final_context?: FinalContextItem[]
}

export interface FinalContextItem {
  rank: number
  filename: string
  score: number
  content: string
}

/** issue #189：rerank 路径状态契约（后端 reranker.rerank 的 info 原样下发）。 */
export interface RerankDebugInfo {
  status: 'ok' | 'degraded' | 'disabled' | 'empty'
  path: 'local' | 'zhipu' | 'llm' | 'noop'
  reason?: string | null
  attempts?: { path: string; ok: boolean; reason?: string | null }[]
  scores_attached?: boolean
  model?: string | null
}

export interface DebugHit {
  id: string
  filename: string
  score: number
  /** issue #185：相对排序分（融合阶段 min-max） */
  rank_score?: number | null
  /** issue #190：重排器绝对分（仅 local/zhipu 路径；LLM 路径为 null） */
  rerank_score?: number | null
  keyword_rank?: number | null
  vector_rank?: number | null
  keyword_score?: number | null
  vector_score?: number | null
  rewrite_hits?: number
  has_keyword?: boolean
  has_vector?: boolean
  snippet?: string
}
