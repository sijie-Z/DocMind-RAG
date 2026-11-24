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
  // metadata_filters 才是后端 KnowledgeSearchRequest 接受的字段（tags / file_type）；
  // 顶层 file_types 从未被 schema 消费，已停止发送。
  metadata_filters?: { tags?: string[]; file_type?: string[] }
  /** 可选知识库范围（服务端校验组织归属；空白 id 一律省略） */
  knowledge_base_id?: string
  // 检索自测：限定文档范围与返回诊断。
  document_ids?: string[]
  debug?: boolean
}

/** 后端 metadata_filters 的字段名与值域（backend/app/rag/metadata_filter.py）。 */
export type SearchMetadataFilters = NonNullable<SearchRequest['metadata_filters']>

/** 扩展名/展示值 → 后端 DocumentType 枚举（pdf|word|excel|ppt|txt|other）。 */
const FILE_TYPE_ENUM = new Set(['pdf', 'word', 'excel', 'ppt', 'txt', 'other'])
const FILE_TYPE_ALIASES: Record<string, string> = {
  doc: 'word',
  docx: 'word',
  xls: 'excel',
  xlsx: 'excel',
  pptx: 'ppt',
  text: 'txt',
  md: 'txt',
  // 上传路径把 .csv 归为 DocumentType.TXT（backend/app/services/file_service.py），
  // 过滤值必须与索引里的 file_type 一致，否则按 excel 过滤恒为空。
  csv: 'txt',
}

/**
 * 后端 metadata_filters.tags 的取值约束（backend/app/rag/metadata_filter.py）：
 * ^[\w.\-/一-鿿]+$、去空后 <=100 字符。上传端按逗号拆分标签且不校验，
 * 这里必须挡掉含空格/#等非法字符或超长的值，避免 UI 勾选后 422。
 */
// Python re.UNICODE 的 \w 覆盖所有 Unicode 字母/数字（含 CJK）；JS \w 仅 ASCII，
// 这里用 \p{L}\p{N}_ 对齐，避免合法的多语言标签被前端误删。
export const SAFE_METADATA_TAG_RE = /^[\p{L}\p{N}_.\-/]+$/u
export const MAX_METADATA_TAG_LENGTH = 100
const MAX_METADATA_FILTER_VALUES = 20

const normalizeValues = (values: string[] | undefined): string[] => {
  if (!values?.length) return []
  const seen = new Set<string>()
  for (const value of values) {
    const text = value.trim()
    if (text && !seen.has(text)) seen.add(text)
  }
  return [...seen].slice(0, MAX_METADATA_FILTER_VALUES)
}

export const normalizeSearchTags = (values: string[] | undefined): string[] =>
  normalizeValues(values).filter(
    (tag) => tag.length <= MAX_METADATA_TAG_LENGTH && SAFE_METADATA_TAG_RE.test(tag)
  )

export const normalizeSearchFileTypes = (values: string[] | undefined): string[] => {
  const mapped = (values ?? []).map((value) => {
    const lower = value.trim().toLowerCase()
    return FILE_TYPE_ALIASES[lower] ?? lower
  })
  return normalizeValues(mapped.filter((value) => FILE_TYPE_ENUM.has(value)))
}

export interface SearchPayloadInput {
  query: string
  /** 用户当前组织（必填——后端 schema 层强制） */
  organizationId: number
  /** 显式指定结果条数；缺省 = 后端组织设置/平台默认（B4/D5） */
  topK?: number
  /** 'auto'（或缺省）= 不发送 search_mode，走后端自动路由（issue #225 同语义） */
  searchMode?: 'auto' | 'hybrid' | 'vector' | 'keyword'
  /** 已选文件类型（扩展名或枚举原词）；映射到 metadata_filters.file_type */
  fileTypes?: string[]
  /** 文档 tags 多选；映射到 metadata_filters.tags（OR 语义） */
  tags?: string[]
  /** 知识库 id；空白省略，不传组织 id */
  knowledgeBaseId?: string
  /** 限定文档范围；空数组 = 全组织 */
  documentIds?: string[]
  /** 返回检索诊断（每臂候选/RRF/rerank/MMR） */
  debug?: boolean
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
  const tags = normalizeSearchTags(input.tags)
  const fileTypes = normalizeSearchFileTypes(input.fileTypes)
  const metadataFilters: SearchMetadataFilters = {
    ...(tags.length ? { tags } : {}),
    ...(fileTypes.length ? { file_type: fileTypes } : {}),
  }
  const knowledgeBaseId = input.knowledgeBaseId?.trim()
  return {
    query: input.query,
    organization_id: input.organizationId,
    ...(input.topK !== undefined ? { top_k: input.topK } : {}),
    // 'auto' = 自动路由：不发送该字段（与后端「缺省=自动」同语义）
    ...(input.searchMode && input.searchMode !== 'auto'
      ? { search_mode: input.searchMode }
      : {}),
    ...(Object.keys(metadataFilters).length ? { metadata_filters: metadataFilters } : {}),
    ...(knowledgeBaseId ? { knowledge_base_id: knowledgeBaseId } : {}),
    ...(input.documentIds && input.documentIds.length ? { document_ids: input.documentIds } : {}),
    ...(input.debug ? { debug: true } : {}),
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
  /** O 派生展示正文；parser_original 为原文片段，legacy 为检索片段 */
  content?: string | null
  /** parser_original / legacy_unverified；仅证明 parser 原文，不证明展示正文 */
  text_provenance?: string | null
  /** manual_override = 检索文本被人工修正；与 text_provenance 严格分离 */
  retrieval_text_provenance?: string | null
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
  /** 1-based 检索序位 */
  rank?: number | null
  /** 命中的分块 id（旧索引可能为 null） */
  chunk_id?: string | null
  chunk_index?: number | null
  section_title?: string | null
  clause_no?: number[]
  section_no?: string[]
  /** PDF 来源页码（非 PDF/旧索引为 null） */
  page_number?: number | null
}

export interface SearchScopeItem {
  document_id: string
  found: boolean
  title?: string | null
  status?: string | null
  chunk_count?: number | null
}

/** 检索自测的完整响应（诊断字段只在结果为空/开启 debug 时有值）。 */
export interface KnowledgeSearchResponse {
  query: string
  results: SearchResult[]
  total_count: number
  search_mode: string
  search_time?: number | null
  index_status: 'ok' | 'empty_index' | 'index_error' | 'unknown'
  index_doc_count?: number | null
  effective_top_k?: number | null
  document_ids?: string[] | null
  document_scope?: SearchScopeItem[] | null
  debug?: Record<string, unknown> | null
}

export interface SearchStats {
  total_files: number
  file_types: Record<string, number>
  recent_searches: string[]
}

// 搜索知识库（完整响应：结果 + 索引诊断 + 可选 debug）。
// E14/D2：失败由搜索页的内联错误态（含重试）呈现 —— 带 X-Silent-Error
// 关闭响应拦截器的全局 toast，避免同一失败出现两个提示表面。
export const searchKnowledgeResponse = async (
  params: SearchRequest
): Promise<KnowledgeSearchResponse> => {
  const response = await request.post('/knowledge/search', params, {
    headers: { 'X-Silent-Error': '1' }
  })
  return response.data
}

// 兼容入口：只需要结果列表的调用方沿用旧签名。
export const searchKnowledge = async (params: SearchRequest): Promise<SearchResult[]> => {
  const data = await searchKnowledgeResponse(params)
  return data.results || []
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