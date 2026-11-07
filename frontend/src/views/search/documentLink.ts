import type { SearchResult } from '@/api/search'

export interface DocumentRouteTarget {
  name: 'KnowledgeDocument'
  params: { documentId: string }
  query: Record<string, string>
}

/** 检索命中 → 文档深链。source.document_id 是权威来源，旧响应退回 result.id。 */
export function buildDocumentRoute(result: SearchResult): DocumentRouteTarget | null {
  const documentId = result.source?.document_id || result.id
  if (!documentId) return null
  return {
    name: 'KnowledgeDocument',
    params: { documentId },
    query: result.chunk_id ? { chunk: result.chunk_id } : {}
  }
}
