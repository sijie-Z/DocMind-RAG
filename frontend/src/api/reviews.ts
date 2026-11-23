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
  /** 可选：从组织模板版本建案（服务端仍会固化为不可变规则快照）。 */
  template_id?: string | null
  template_version_no?: number | null
}

/** 组织级核验模板（playbook）头；规则内容在不可变版本行里。 */
export interface ReviewTemplateSummary {
  id: string
  organization_id: number
  name: string
  description?: string | null
  status: 'active' | 'archived'
  latest_version: number
  created_by: number
  updated_by?: number | null
  archived_at?: string | null
  archived_by?: number | null
  lock_version?: number
  created_at?: string | null
  updated_at?: string | null
}

export interface ReviewTemplateItem {
  key: string
  keywords: string[]
  label?: string
  configurable?: boolean
}

export interface ReviewTemplateVersionSummary {
  id: string
  template_id: string
  version_no: number
  version_label: string
  item_count: number
  payload_sha256: string
  source: 'manual' | 'bootstrap' | string
  change_note?: string | null
  created_by: number
  created_at?: string | null
}

export interface ReviewTemplateVersion extends ReviewTemplateVersionSummary {
  items: ReviewTemplateItem[]
  intact: boolean
}

export interface ReviewTemplateDetail extends ReviewTemplateSummary {
  version?: ReviewTemplateVersion | null
}

export interface ReviewTemplateList {
  items: ReviewTemplateSummary[]
  total: number
  page: number
  page_size: number
}

export interface ReviewTemplateVersionList {
  items: ReviewTemplateVersionSummary[]
  total: number
  page: number
  page_size: number
  latest_version: number
}

export interface ReviewTemplateDiffField {
  base: unknown
  target: unknown
}

export interface ReviewTemplateDiffEntry {
  key: string
  fields: Record<string, ReviewTemplateDiffField & { added?: string[]; removed?: string[] }>
}

export interface ReviewTemplateDiff {
  template_id?: string
  base: { id: string; version_no: number; version_label: string; payload_sha256: string; item_count: number }
  target: { id: string; version_no: number; version_label: string; payload_sha256: string; item_count: number }
  identical: boolean
  added: ReviewTemplateItem[]
  removed: ReviewTemplateItem[]
  changed: ReviewTemplateDiffEntry[]
  unchanged_keys: string[]
  summary: { added: number; removed: number; changed: number; unchanged: number }
}

export interface ReviewTemplateCreatePayload {
  name: string
  description?: string | null
  items?: Array<{ key: string; keywords: string[]; label?: string | null }> | null
  change_note?: string | null
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

export interface ReviewCaseItemSummaryEntry {
  item_key: string
  change_type: string | null
  reasons: string[]
}

/** 后端经 summarize_items 汇总的核验计数（含"能否宣称无差异"的判据）。 */
export interface ReviewCaseItemSummary {
  item_count: number
  changed_count: number
  added_count: number
  removed_count: number
  unidentified_count: number
  undetermined_count: number
  has_undetermined: boolean
  undetermined_items: ReviewCaseItemSummaryEntry[]
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
  /** 建案时选用的模板/版本（溯源；规则真源是 rule_snapshot_sha256）。 */
  template_id?: string | null
  template_version_no?: number | null
  documents: Record<ReviewSide, ReviewDocumentState>
  items: ReviewItem[]
  /** 后端汇总；配置项没有可用抽取器时 undetermined_count > 0，界面据此提示"需人工复核"。 */
  summary?: ReviewCaseItemSummary
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
  item_count?: number
  decided_count?: number
  changed_count?: number
  unidentified_count?: number
  needs_human_count?: number
  analysis_pending_count?: number
  all_analyzed?: boolean
  created_by: number
  assignee_id?: number | null
  created_at?: string | null
  completed_at?: string | null
  archived_at?: string | null
  archived_by?: number | null
  /** 模板溯源（可空）：列表页只展示，不参与判定。 */
  template_id?: string | null
  template_version_no?: number | null
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

export interface ReviewEvidenceDetail {
  evidence: ReviewEvidenceRow & { stale_reasons: string[] }
  case: {
    id: string
    title: string
    status: ReviewCaseStatus
    archived_at?: string | null
    created_at?: string | null
  }
  item: {
    id: string
    item_key: string
    ordinal: number
    change_type: ReviewChangeType | null
    status: ReviewItemStatus
  }
  document: {
    document_id: string
    filename?: string | null
    missing: boolean
    live_status?: string | null
    identity_changed: boolean
    document_hash_changed: boolean
    document_version_changed: boolean
    document_sha256_live?: string | null
    version_label_live?: string | null
    version_seq_live?: number | null
  }
  live_chunk: {
    found: boolean
    chunk_index?: number | null
    text?: string | null
    matches_snapshot: boolean
  }
  replay: {
    quote_unchanged: boolean
    can_highlight_current_chunk: boolean
    warnings: string[]
  }
}

export const reviewsApi = {
  list(params?: {
    status?: string
    archived?: 'active' | 'archived' | 'all'
    q?: string
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
  getEvidence(evidenceId: string) {
    return request.get(`/reviews/evidence/${encodeURIComponent(evidenceId)}`, SILENT_HEADERS)
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
    // E14/D1：归档失败由调用点用 getErrorMessage 呈现，避免全局拦截器双 toast
    //（409 detail 为对象，拦截器会渲染成 "[object Object]"）。
    return request.post(`/reviews/${caseId}/archive`, undefined, SILENT_HEADERS)
  },
  async download(caseId: string, exportId: string, format: 'json' | 'markdown') {
    const response = await request.get(`/reviews/${caseId}/exports/${exportId}/download`, {
      params: { format },
      responseType: 'blob'
    })
    return response
  },
  // ── 模板/playbook 库（批次五）──────────────────────────────────────────
  templates: {
    list(params?: {
      status?: 'active' | 'archived' | 'all'
      q?: string
      page?: number
      page_size?: number
    }) {
      return request.get('/reviews/templates', { params })
    },
    create(payload: ReviewTemplateCreatePayload) {
      return request.post('/reviews/templates', payload, SILENT_HEADERS)
    },
    bootstrap(payload: { name: string }) {
      return request.post('/reviews/templates/bootstrap', payload, SILENT_HEADERS)
    },
    get(templateId: string) {
      return request.get(`/reviews/templates/${encodeURIComponent(templateId)}`)
    },
    update(templateId: string, payload: { name?: string; description?: string | null; clear_description?: boolean }) {
      return request.put(`/reviews/templates/${encodeURIComponent(templateId)}`, payload, SILENT_HEADERS)
    },
    archive(templateId: string) {
      return request.post(
        `/reviews/templates/${encodeURIComponent(templateId)}/archive`,
        undefined,
        SILENT_HEADERS
      )
    },
    versions(templateId: string, params?: { page?: number; page_size?: number }) {
      return request.get(`/reviews/templates/${encodeURIComponent(templateId)}/versions`, { params })
    },
    createVersion(
      templateId: string,
      payload: { items: Array<{ key: string; keywords: string[]; label?: string | null }>; change_note?: string | null }
    ) {
      return request.post(
        `/reviews/templates/${encodeURIComponent(templateId)}/versions`,
        payload,
        SILENT_HEADERS
      )
    },
    getVersion(templateId: string, versionNo: number) {
      return request.get(
        `/reviews/templates/${encodeURIComponent(templateId)}/versions/${versionNo}`
      )
    },
    diff(templateId: string, baseVersion: number, targetVersion: number) {
      return request.get(`/reviews/templates/${encodeURIComponent(templateId)}/diff`, {
        params: { base_version: baseVersion, target_version: targetVersion }
      })
    }
  }
}
