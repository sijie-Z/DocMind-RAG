import request from '@/utils/request'

export interface SearchRequest {
  query: string
  // P1 修复（UI 验收 round2 待办#2）：organization_id 是后端
  // KnowledgeSearchRequest 的必填字段，缺它请求 100% 422（此前搜索页
  // 即因此全程不可用）。由调用方按「用户当前组织」口径取值组包。
  organization_id: number
  // 结果条数。schema 字段名为 top_k（ge=1, le=50）。
  // B4（D5）：前端不再固化默认值——缺省交给后端按「组织设置 > 平台默认
  // settings.TOP_K_RESULTS=10」解析；组织未设置时行为与旧版发送 10 完全一致。
  // 旧字段名 limit 从未被 schema 接受（pydantic 静默丢弃），勿再使用。
  top_k?: number
  // issue #225：后端字段为 search_mode（hybrid/vector/keyword；缺省=自动路由）。
  // 旧的 search_type 字段从未在请求 schema 里出现过（一直被后端静默丢弃），不再发送。
  search_mode?: 'hybrid' | 'vector' | 'keyword'
  // 后端 KnowledgeSearchRequest 目前不消费 file_types（预存在漂移，已记录）；
  // 保持既有发送行为，空数组时不发送。
  file_types?: string[]
}

export interface SearchPayloadInput {
  query: string
  /** 用户当前组织（必填——后端 schema 层强制） */
  organizationId: number
  /** 显式指定结果条数；缺省 = 后端组织设置/平台默认（B4/D5） */
  topK?: number
  /** 'auto'（或缺省）= 不发送 search_mode，走后端自动路由（issue #225 同语义） */
  searchMode?: 'auto' | 'hybrid' | 'vector' | 'keyword'
  /** 已选文件类型；空数组 = 不发送 */
  fileTypes?: string[]
}

/**
 * 组包 /knowledge/search 请求体（P1 搜索 422 修复）。
 * 纯函数、无副作用——单测直接断言「与后端 schema 对齐」的全字段组包。
 *
 * B4（D5）：top_k 只在调用方显式传入时发送；缺省不发送，由后端解析
 * 「组织设置 > settings.TOP_K_RESULTS=10」。此前前端恒发 10，会把组织设置
 * 永久挡在门外（组织管理员改 top_k 对搜索页无效）。
 */
export function buildSearchPayload(input: SearchPayloadInput): SearchRequest {
  return {
    query: input.query,
    organization_id: input.organizationId,
    ...(input.topK !== undefined ? { top_k: input.topK } : {}),
    // 'auto' = 自动路由：不发送该字段（与后端「缺省=自动」同语义）
    ...(input.searchMode && input.searchMode !== 'auto'
      ? { search_mode: input.searchMode }
      : {}),
    ...(input.fileTypes && input.fileTypes.length ? { file_types: input.fileTypes } : {}),
  }
}

/** 结果项来源信息（真实响应字段，防御式声明为可缺省） */
export interface SearchResultSource {
  filename?: string
  document_id?: string
  [key: string]: unknown
}

/** 结果项元数据（真实响应字段，防御式声明为可缺省） */
export interface SearchResultMetadata {
  filename?: string
  snippet?: string
  overlap_score?: number
  mmr_score?: number
  [key: string]: unknown
}

// P1-3 修复（UI 复验 round2）：接口与真实响应同形（backend/app/schemas/
// knowledge.py KnowledgeSearchResponse.results 项）。此前声明的 file_type/
// file_size/upload_time/filename/content/relevance_score 平铺字段后端从未
// 返回过——UI 搜索此前 100% 422 从未成功，该渲染路径从未被执行（历史暗雷）。
// 展示层一律经访问器从 source/metadata/text/score 推导（见 search.vue）。
export interface SearchResult {
  id: string
  text: string
  /** 相对排序分（融合阶段；只用于排序，不是相关度 —— issue #185） */
  score: number
  /** issue #185：重排绝对分；null/缺省 = 无重排分，UI 只按序位展示 */
  relevance_score?: number | null
  metadata?: SearchResultMetadata
  source?: SearchResultSource
  has_keyword?: boolean | null
  has_vector?: boolean | null
  rewrite_hits?: number | null
  fresh_factor?: number | null
}

export interface SearchStats {
  total_files: number
  file_types: Record<string, number>
  recent_searches: string[]
}

// 搜索知识库
// E14/D2：失败由搜索页的内联错误态（含重试）呈现 —— 带 X-Silent-Error
// 关闭响应拦截器的全局 toast，避免同一失败出现两个提示表面。
export const searchKnowledge = async (params: SearchRequest): Promise<SearchResult[]> => {
  const response = await request.post('/knowledge/search', params, {
    headers: { 'X-Silent-Error': '1' }
  })
  return response.data.results || []
}

// 获取搜索建议
export const getSearchSuggestions = async (query: string, organizationId: string | number): Promise<string[]> => {
  const response = await request.get('/knowledge/suggestions', {
    params: { q: query, organization_id: organizationId }
  })
  return response.data.suggestions || []
}

// 获取搜索统计
export const getSearchStats = async (organizationId: string | number): Promise<SearchStats> => {
  const response = await request.get(`/knowledge/stats/${organizationId}`)
  return response.data.stats
}