import request from '@/utils/request'

export type ReviewSide = 'policy' | 'base' | 'revised'
export type ReviewCaseStatus = 'blocked' | 'ready' | 'in_review' | 'completed'
export type ReviewChangeType =
  | 'unchanged'
  | 'modified'
  | 'added'
  | 'removed'
  | 'renumbered'
  | 'unidentified'
export type ReviewItemStatus =
  | 'pending'
  | 'verified'
  | 'needs_human'
  | 'not_applicable'
  | 'insufficient'
  | 'parse_failed'
export type ReviewDecisionStatus = 'verified' | 'needs_human' | 'not_applicable'
export type ReviewEvidenceRole = 'main' | 'condition' | 'exception' | 'cross_ref'

export interface ReviewReadiness {
  document_id?: string
  status: 'reviewable' | 'blocked'
  blocking_reasons: string[]
  warnings: string[]
  total_chunks?: number
  original_text_chunks?: number
  page_number_chunks?: number
  clause_candidate_chunks?: number
  legacy_chunk_count?: number
  meta?: ReviewCandidateMeta | null
}

/** 文档核验身份（review_document_meta）；合同必带版本族/版本标签/版本序。 */
export interface ReviewCandidateMeta {
  doc_role: 'policy' | 'template' | 'contract' | string
  family_key?: string | null
  version_label?: string | null
  version_seq?: number | null
  supersedes_document_id?: string | null
  effective_from?: string | null
  effective_to?: string | null
  content_sha256?: string | null
  registered_by?: number | null
}

/** 建案候选文档：本组织文档 + 可选核验身份（null=未登记）。 */
export interface ReviewCandidate {
  document_id: string
  filename: string
  title?: string | null
  file_type?: string | null
  status: string
  created_at?: string | null
  meta?: ReviewCandidateMeta | null
}

export interface ReviewCaseCreatePayload {
  title: string
  policy_document_id: string
  base_document_id: string
  revised_document_id: string
  checklist_version?: string
  assignee_id?: number | null
}

export interface ReviewMetaPayload {
  doc_role: 'policy' | 'template' | 'contract'
  family_key?: string | null
  version_label?: string | null
  version_seq?: number | null
}

export interface ReviewDocumentState {
  document_id: string
  filename?: string | null
  live_filename?: string | null
  doc_role?: string | null
  family_key?: string | null
  version_label?: string | null
  version_seq?: number | null
  content_sha256?: string | null
  missing?: boolean
  identity_changed?: boolean
  live_status?: string | null
  readiness?: ReviewReadiness
}

export interface ReviewEvidenceRow {
  id: string
  item_id: string
  side: ReviewSide
  role: string
  selected: boolean
  document_id: string
  doc_role: string
  doc_version_label?: string | null
  doc_version_seq?: number | null
  document_sha256: string
  chunk_id_at_capture: string
  chunk_index: number
  clause_no?: number[] | null
  section_no?: string[] | null
  page_number?: number | null
  char_start?: number | null
  char_end?: number | null
  quote: string
  quote_sha256: string
  text_provenance: string
  captured_at?: string | null
  stale: boolean
}

export interface ReviewItem {
  id: string
  item_key: string
  ordinal: number
  change_type: ReviewChangeType | null
  status: ReviewItemStatus
  facts?: Record<string, unknown> | null
  decided_by?: number | null
  decided_at?: string | null
  decision_note?: string | null
  evidence_count: number
  selected_evidence_count: number
  evidence: Record<ReviewSide, ReviewEvidenceRow[]>
}

export interface ReviewCaseDetail {
  id: string
  title: string
  status: ReviewCaseStatus
  checklist_version: string
  organization_id: number
  blocking_reasons: string[]
  created_by: number
  assignee_id?: number | null
  created_at?: string | null
  updated_at?: string | null
  completed_at?: string | null
  completed_by?: number | null
  archived_at?: string | null
  archived_by?: number | null
  lock_version: number
  documents: Record<ReviewSide, ReviewDocumentState>
  items: ReviewItem[]
  live_source_stale: boolean
  stale_evidence_count: number
}

export interface ReviewCaseSummary {
  id: string
  title: string
  status: ReviewCaseStatus
  checklist_version: string
  policy_document_id: string
  base_document_id: string
  revised_document_id: string
  base_version_label?: string | null
  revised_version_label?: string | null
  blocking_reasons: string[]
  created_by: number
  assignee_id?: number | null
  created_at?: string | null
  completed_at?: string | null
  archived_at?: string | null
  archived_by?: number | null
}

export interface ReviewExportSummary {
  id: string
  case_id: string
  schema_version: number
  payload_sha256: string
  item_count: number
  needs_human_count: number
  unidentified_count: number
  created_by: number
  created_at?: string | null
}

export interface DecisionPayload {
  status: ReviewDecisionStatus
  note?: string | null
}

/**
 * 合同核验 API：证据只读事件里返回的原文快照，绝不调用 /documents/{id}/content。
 *
 * E14/D1：建案弹窗的调用点自行呈递错误（X-Silent-Error + getErrorMessage），
 * 避免全局拦截器与弹窗重复弹 toast。
 */
const SILENT_HEADERS = { headers: { 'X-Silent-Error': '1' } } as const

export const reviewsApi = {
  list(params?: {
    status?: string
    archived?: 'active' | 'archived' | 'all'
    page?: number
    page_size?: number
  }) {
    return request.get('/reviews', { params })
  },
  candidates(params?: { q?: string; page?: number; page_size?: number }) {
    return request.get('/reviews/candidates', { params, ...SILENT_HEADERS })
  },
  createCase(payload: ReviewCaseCreatePayload) {
    return request.post('/reviews', payload, SILENT_HEADERS)
  },
  readiness(documentId: string) {
    return request.get(`/documents/${documentId}/readiness`, SILENT_HEADERS)
  },
  putDocumentMeta(documentId: string, payload: ReviewMetaPayload) {
    return request.put(`/documents/${documentId}/review-meta`, payload, SILENT_HEADERS)
  },
  get(id: string) {
    return request.get(`/reviews/${id}`)
  },
  analyze(id: string) {
    return request.post(`/reviews/${id}/analyze`)
  },
  selectEvidence(
    caseId: string,
    itemId: string,
    payload: { side: ReviewSide; chunk_id: string; role: ReviewEvidenceRole; selected: boolean }
  ) {
    return request.put(`/reviews/${caseId}/items/${itemId}/evidence`, payload)
  },
  decide(caseId: string, itemId: string, payload: DecisionPayload) {
    return request.put(`/reviews/${caseId}/items/${itemId}/decision`, payload)
  },
  exportCase(caseId: string) {
    return request.post(`/reviews/${caseId}/export`)
  },
  exports(caseId: string) {
    return request.get(`/reviews/${caseId}/exports`)
  },
  archive(caseId: string) {
    return request.post(`/reviews/${caseId}/archive`)
  },
  async download(caseId: string, exportId: string, format: 'json' | 'markdown') {
    const response = await request.get(`/reviews/${caseId}/exports/${exportId}/download`, {
      params: { format },
      responseType: 'blob'
    })
    return response
  }
}
