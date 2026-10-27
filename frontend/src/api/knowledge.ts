import request from '@/utils/request'
import type { AxiosResponse } from 'axios'
import type { ApiResponse } from '@/types/common'

export interface KnowledgeBase {
  id: string
  title: string
  file_name: string
  filename?: string
  file_type: string
  file_size: number
  tags: string[]
  description?: string
  keywords?: string[]
  upload_source?: string
  summary?: string
  suggested_tags?: string[]
  preview_content?: string
  parse_error?: string
  raw_status?: string
  created_at: string
  updated_at: string
  // 列表端点词表已收敛为 DB 枚举原词（fix/status-vocab，INDEXED → 'indexed'，
  // 与详情端点 documents.py:296-303 一致）；'completed' 为收敛前列表旧词
  // （在途分支/旧缓存仍可能到达）—— 两词表均受支持（docDisplay 兼容层统一映射）
  status: 'pending' | 'uploaded' | 'parsing' | 'parsed' | 'completed' | 'indexed' | 'failed'
}

export interface KnowledgeBaseListResponse {
  data: KnowledgeBase[]
  total: number
  page: number
  page_size: number
}

export interface KnowledgeJob {
  id: number
  document_id: string
  organization_id: number
  trigger_type: 'upload' | 'reprocess'
  status: 'queued' | 'processing' | 'success' | 'failed'
  retry_count: number
  error_message?: string
  started_at?: string
  finished_at?: string
  created_at?: string
}

export interface DocumentDetail {
  id: string
  title: string
  filename: string
  file_size: number
  file_type: string
  status: string
  parse_error?: string
  created_at?: string
  description?: string
  keywords?: string[]
  upload_source?: string
}

export interface DocumentContent {
  id: string
  filename: string
  content: string
  summary?: string
  suggested_tags?: string[]
}

export interface DocumentDownloadUrl {
  downloadUrl: string
  filename: string
  expiresIn: number
}

/**
 * 页面自理错误的静默请求选项（E14/D1）：
 * 带 `X-Silent-Error: 1` 时响应拦截器不再弹全局 toast，错误文案由调用方
 * （页面 catch / 页内错误态）唯一呈现，避免同一失败被提示两次。
 */
export interface SilentRequestOptions {
  silent?: boolean
}

const SILENT_HEADERS = { headers: { 'X-Silent-Error': '1' } } as const

export const getKnowledgeBases = async (params?: {
  page?: number
  page_size?: number
  search?: string
  tags?: string[]
}, options?: SilentRequestOptions): Promise<AxiosResponse<ApiResponse<KnowledgeBaseListResponse>>> => {
  // 添加末尾斜杠以避免 307 重定向导致 Header 丢失
  return request.get('/knowledge/', { params, ...(options?.silent ? SILENT_HEADERS : {}) })
}

export const getKnowledgeBase = async (id: string): Promise<AxiosResponse<{ data: KnowledgeBase }>> => {
  return request.get(`/documents/${id}`)
}

export const getDocumentDetail = async (
  id: string,
  options?: SilentRequestOptions
): Promise<AxiosResponse<{ data: DocumentDetail }>> => {
  return request.get(`/documents/${id}`, options?.silent ? SILENT_HEADERS : undefined)
}

export const getDocumentContent = async (
  id: string,
  options?: SilentRequestOptions
): Promise<AxiosResponse<{ data: DocumentContent }>> => {
  return request.get(`/documents/${id}/content`, options?.silent ? SILENT_HEADERS : undefined)
}

export const getDocumentDownloadUrl = async (
  id: string,
  options?: SilentRequestOptions
): Promise<AxiosResponse<{ data: DocumentDownloadUrl }>> => {
  return request.get(`/documents/${id}/download-url`, options?.silent ? SILENT_HEADERS : undefined)
}

export const uploadKnowledgeBase = async (
  data: FormData,
  onProgress?: (_progressEvent: { loaded: number; total?: number }) => void,
  signal?: AbortSignal
): Promise<AxiosResponse<{ data: KnowledgeBase }>> => {
  return request.post('/documents/upload', data, {
    headers: {
      'Content-Type': undefined, // axios sets multipart boundary when Content-Type is undefined
      // 用户主动取消上传时 axios 以「无 response 的 reject」收场，响应拦截器会误报
      // 「网络连接失败」。上传错误统一由调用方呈现（知识库上传页 / 聊天附件），
      // 故标记静默，避免取消与真实失败都被全局 toast 抢先误读。
      'X-Silent-Error': '1'
    },
    onUploadProgress: onProgress,
    // E9：上传列表「×」真取消的传输层入口（AbortController 中止请求）
    signal
  })
}

export const deleteKnowledgeBase = async (id: string, options?: SilentRequestOptions): Promise<void> => {
  return request.delete(`/knowledge/document/${id}`, options?.silent ? SILENT_HEADERS : undefined)
}

/**
 * 批量删除请求体构造（A2 判别先例：照 search P1-2 buildSearchPayload 模式）。
 *
 * 后端校验（knowledge.py:642-646）：`require_organization` 解析有效组织后，
 * 非超管必须满足 `request.organization_id == effective_org_id`，否则 403 ——
 * 原前端只发 {document_ids}（organization_id 缺省 None）对非超管**每次必 403**。
 * 超管无组织时后端自行解析组织上下文（校验短路），此时省略该字段即可。
 */
export const buildBatchDeletePayload = (
  documentIds: string[],
  organizationId: number | null | undefined
): { document_ids: string[]; organization_id?: number } => {
  if (organizationId == null) {
    return { document_ids: documentIds }
  }
  return { document_ids: documentIds, organization_id: organizationId }
}

export const batchDeleteKnowledgeBases = async (
  ids: string[],
  organizationId?: number | null,
  options?: SilentRequestOptions
): Promise<void> => {
  return request.post(
    '/knowledge/batch-delete',
    buildBatchDeletePayload(ids, organizationId),
    options?.silent ? SILENT_HEADERS : undefined
  )
}

export const rebuildKnowledgeBase = async (
  id: string,
  options?: SilentRequestOptions
): Promise<AxiosResponse<ApiResponse<unknown>>> => {
  return request.post(`/knowledge/rebuild/${id}`, undefined, options?.silent ? SILENT_HEADERS : undefined)
}


export interface GraphNode {
  id: string
  type: string
  description: string
  occurrences: number
}

export interface GraphEdge {
  source: string
  target: string
  relation: string
}

export interface GraphData {
  nodes: GraphNode[]
  edges: GraphEdge[]
  analytics: Record<string, unknown>
}

export const getKnowledgeGraph = async (query?: string, maxNodes?: number): Promise<AxiosResponse<{ data: GraphData }>> => {
  return request.get('/knowledge/graph', { params: { query, max_nodes: maxNodes } })
}
