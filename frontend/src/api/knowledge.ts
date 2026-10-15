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
  // 列表端点词表（knowledge.py:131-139 status_map，INDEXED → 'completed'）；
  // 详情端点（documents.py:296-303）同义词 'indexed'，viewKnowledge 合并详情后
  // 可能出现 —— 两词表均受支持（docDisplay 统一映射）
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

export const getKnowledgeBases = async (params?: {
  page?: number
  page_size?: number
  search?: string
  tags?: string[]
}): Promise<AxiosResponse<ApiResponse<KnowledgeBaseListResponse>>> => {
  // 添加末尾斜杠以避免 307 重定向导致 Header 丢失
  return request.get('/knowledge/', { params })
}

export const getKnowledgeBase = async (id: string): Promise<AxiosResponse<{ data: KnowledgeBase }>> => {
  return request.get(`/documents/${id}`)
}

export const getDocumentDetail = async (id: string): Promise<AxiosResponse<{ data: DocumentDetail }>> => {
  return request.get(`/documents/${id}`)
}

export const getDocumentContent = async (id: string): Promise<AxiosResponse<{ data: DocumentContent }>> => {
  return request.get(`/documents/${id}/content`)
}

export const uploadKnowledgeBase = async (
  data: FormData,
  onProgress?: (_progressEvent: { loaded: number; total?: number }) => void
): Promise<AxiosResponse<{ data: KnowledgeBase }>> => {
  return request.post('/documents/upload', data, {
    headers: {
      'Content-Type': undefined // axios sets multipart boundary when Content-Type is undefined
    },
    onUploadProgress: onProgress
  })
}

export const deleteKnowledgeBase = async (id: string): Promise<void> => {
  return request.delete(`/knowledge/document/${id}`)
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
  organizationId?: number | null
): Promise<void> => {
  return request.post('/knowledge/batch-delete', buildBatchDeletePayload(ids, organizationId))
}

export const rebuildKnowledgeBase = async (id: string): Promise<AxiosResponse<ApiResponse<unknown>>> => {
  return request.post(`/knowledge/rebuild/${id}`)
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
