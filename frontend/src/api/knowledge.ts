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
  upload_source_code?: 'chat' | 'knowledge'
  summary?: string
  suggested_tags?: string[]
  preview_content?: string
  // 预览内容的原文可信度与截断状态（/documents/{id}/content 的 data 透传）：
  // legacy/mixed 表示部分分块没有 parser 原文，预览文本不能直接当原文证据。
  preview_provenance?: DocumentContent['text_provenance']
  preview_truncated?: boolean
  parse_error?: string
  raw_status?: string
  created_at: string
  updated_at: string
  // 列表端点词表已收敛为 DB 枚举原词（fix/status-vocab，INDEXED → 'indexed'，
  // 与详情端点 documents.py:296-303 一致）；'completed' 为收敛前列表旧词
  // （在途分支/旧缓存仍可能到达）—— 两词表均受支持（docDisplay 兼容层统一映射）
  status: 'pending' | 'uploaded' | 'parsing' | 'parsed' | 'completed' | 'indexed' | 'failed'
  visibility?: 'organization' | 'private'
/** 启用 / 停用文档（停用后不参与检索召回，内容与索引保留）。 */
export const setKnowledgeEnabled = async (
  id: string,
  enabled: boolean,
  options?: SilentRequestOptions
): Promise<AxiosResponse<ApiResponse<unknown>>> => {
  return request.patch(
    `/documents/${id}/enabled`,
    { enabled },
    options?.silent ? SILENT_HEADERS : undefined
  )
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
