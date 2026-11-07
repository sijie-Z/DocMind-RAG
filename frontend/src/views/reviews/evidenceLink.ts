import type { ReviewDocumentState, ReviewEvidenceRow } from '@/api/reviews'

export interface EvidenceLinkTarget {
  name: 'KnowledgeDocument'
  params: { documentId: string }
  query: { chunk: string }
}

/**
 * 证据行 → 文档深链。证据快照里的 document_id/chunk_id_at_capture 是捕获时
 * 的权威定位；文档已删除（missing）或快照缺 chunk id 时不生成链接，由界面
 * 按原因显示「原文不可用 / 无法定位原文」，不能跳到错误页面。
 */
export function buildEvidenceLinkTarget(
  row: Pick<ReviewEvidenceRow, 'document_id' | 'chunk_id_at_capture'>,
  doc?: Pick<ReviewDocumentState, 'missing'> | null
): EvidenceLinkTarget | null {
  if (!row.document_id || !row.chunk_id_at_capture) return null
  if (doc?.missing) return null
  return {
    name: 'KnowledgeDocument',
    params: { documentId: row.document_id },
    query: { chunk: row.chunk_id_at_capture }
  }
}
