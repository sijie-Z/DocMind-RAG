import request from '@/utils/request'

export interface SearchRequest {
  query: string
  search_type?: 'semantic' | 'keyword' | 'hybrid'
  file_types?: string[]
  tags?: string[]
  date_start?: string
  date_end?: string
  size_min?: number
  size_max?: number
  limit?: number
}

export interface SearchResult {
  id: string
  filename: string
  content: string
  file_type: string
  file_size: number
  upload_time: string
  relevance_score: number
  metadata: Record<string, any>
  highlights: string[]
}

export interface SearchStats {
  total_files: number
  file_types: Record<string, number>
  recent_searches: string[]
}

// 搜索知识库
export const searchKnowledge = async (params: SearchRequest): Promise<SearchResult[]> => {
  const response = await request.post('/knowledge/search', params)
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