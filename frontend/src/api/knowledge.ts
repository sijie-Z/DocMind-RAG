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
  status: 'processing' | 'completed' | 'failed'
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
  onProgress?: (progressEvent: any) => void
): Promise<AxiosResponse<{ data: KnowledgeBase }>> => {
  return request.post('/documents/upload', data, {
    headers: {
      // @ts-ignore
      'Content-Type': undefined
    },
    onUploadProgress: onProgress
  })
}

export const updateKnowledgeBase = async (id: string, data: {
  title?: string
  tags?: string[]
  description?: string
}): Promise<AxiosResponse<{ data: KnowledgeBase }>> => {
  return request.put(`/knowledge/${id}`, data)
}

export const deleteKnowledgeBase = async (id: string): Promise<void> => {
  return request.delete(`/knowledge/document/${id}`)
}

export const batchDeleteKnowledgeBases = async (ids: string[]): Promise<void> => {
  return request.post('/knowledge/batch-delete', { document_ids: ids })
}

export const rebuildKnowledgeBase = async (id: string): Promise<AxiosResponse<ApiResponse<any>>> => {
  return request.post(`/knowledge/rebuild/${id}`)
}


export const searchKnowledgeBases = async (query: string): Promise<AxiosResponse<{ data: KnowledgeBase[] }>> => {
  return request.get('/knowledge/search', { params: { q: query } })
}
