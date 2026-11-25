/**
 * Rubric 评测 API 客户端。
 *
 * 口径（与后端 docstring 一致，必须保持）：
 * - 评的是**答案**是否满足用户可编辑的加权 yes/no 评分表，不是检索 recall@k；
 *   检索评测走 `/knowledge/eval/*`（retrievalEval.ts），两者不共用运行记录。
 * - `judge` 模式是**模型判定**（LLM 评委），不是确定性真值；`fake` 模式是
 *   调用方给定判定的回放（测试/CI 用）。
 * - 校准（calibration）只在用例携带人工金标时给出；缺失时为 null。
 */
import request from '@/utils/request'

export type RubricStatus = 'active' | 'archived'
export type RubricStrictness = 'neutral' | 'strict' | 'lenient'
export type RubricVerdict = 'yes' | 'no' | 'unsure'
export type RubricRunMode = 'judge' | 'fake'
export type RubricRunStatus = 'completed' | 'failed'

export interface RubricItem {
  key: string
  question: string
  weight: number
  strictness: RubricStrictness
}

export interface RubricSummary {
  id: string
  organization_id: number
  name: string
  description: string | null
  status: RubricStatus
  latest_version: number
  created_by: number
  updated_by: number | null
  archived_at: string | null
  archived_by: number | null
  lock_version: number
  created_at: string | null
  updated_at: string | null
}

export interface RubricVersion {
  id: string
  rubric_id: string
  version_no: number
  item_count: number
  payload_sha256: string
  source: 'manual' | 'bootstrap'
  change_note: string | null
  created_by: number
  created_at: string | null
  items?: RubricItem[]
  intact?: boolean
}

export interface RubricDetail extends RubricSummary {
  version: RubricVersion | null
}

export interface RubricVersionSummary {
  id: string
  rubric_id: string
  version_no: number
  item_count: number
  payload_sha256: string
  source: 'manual' | 'bootstrap'
  change_note: string | null
  created_by: number
  created_at: string | null
}

export interface RubricDiffChange {
  key: string
  fields: Record<string, { base: unknown; target: unknown }>
}

export interface RubricDiff {
  rubric_id?: string
  base: { id: string; version_no: number; payload_sha256: string; item_count: number }
  target: { id: string; version_no: number; payload_sha256: string; item_count: number }
  identical: boolean
  added: RubricItem[]
  removed: RubricItem[]
  changed: RubricDiffChange[]
  unchanged_keys: string[]
  summary: { added: number; removed: number; changed: number; unchanged: number }
}

export interface RubricRunCasePayload {
  case_id: string
  name?: string | null
  answer: string
  gold_verdicts?: Record<string, RubricVerdict> | null
  fake_verdicts?: Record<string, RubricVerdict> | null
}

export interface RubricRunRequestPayload {
  rubric_version_no?: number | null
  mode: RubricRunMode
  cases: RubricRunCasePayload[]
}

export interface RubricQuestionAggregate {
  key: string
  question: string
  weight: number
  strictness: RubricStrictness
  pass_rate: number | null
  mean_credit: number | null
  verdict_counts: Record<RubricVerdict, number>
}

export interface RubricCalibrationQuestion {
  key: string
  compared: number
  agreed: number
  agreement_rate: number | null
}

export interface RubricCalibration {
  agreement_rate: number
  pairs_compared: number
  pairs_agreed: number
  cases_with_gold: number
  cases_total: number
  labels: RubricVerdict[]
  confusion: Record<RubricVerdict, Record<RubricVerdict, number>>
  questions: RubricCalibrationQuestion[]
  note: string
}

export interface RubricAggregate {
  score: number | null
  cases_scored: number
  questions: RubricQuestionAggregate[]
  /** 无人工金标时显式为 null —— 绝不展示成「100% 准确」。 */
  calibration: RubricCalibration | null
  note: string
}

export interface RubricRunItemResult {
  key: string
  verdict: RubricVerdict
  credit: number
  weight: number
  strictness: RubricStrictness
}

export interface RubricRunCaseResult {
  case_id: string
  name: string | null
  answer: string
  score: number
  weight_total: number
  items: RubricRunItemResult[]
  gold_verdicts: Record<string, RubricVerdict> | null
}

export interface RubricRunSummary {
  id: string
  organization_id: number
  rubric_id: string
  rubric_version_no: number
  dataset_hash: string
  mode: RubricRunMode
  status: RubricRunStatus
  case_count: number
  judge_model: string
  error_code: string | null
  score: number | null
  calibration_available: boolean
  created_by: number
  created_at: string | null
  completed_at: string | null
}

export interface RubricRunDetail extends RubricRunSummary {
  aggregate: RubricAggregate | null
  results: RubricRunCaseResult[] | null
}

const SILENT_HEADERS = { headers: { 'X-Silent-Error': '1' } }

export const rubricEvalApi = {
  list(params?: {
    status?: RubricStatus | 'all'
    q?: string
    page?: number
    page_size?: number
  }) {
    return request.get('/eval/rubrics', { params })
  },
  create(payload: {
    name: string
    description?: string | null
    items?: RubricItem[]
    change_note?: string | null
  }) {
    return request.post('/eval/rubrics', payload, SILENT_HEADERS)
  },
  bootstrap(payload: { name: string }) {
    return request.post('/eval/rubrics/bootstrap', payload, SILENT_HEADERS)
  },
  get(rubricId: string) {
    return request.get(`/eval/rubrics/${encodeURIComponent(rubricId)}`)
  },
  update(
    rubricId: string,
    payload: { name?: string; description?: string | null; clear_description?: boolean },
  ) {
    return request.put(`/eval/rubrics/${encodeURIComponent(rubricId)}`, payload, SILENT_HEADERS)
  },
  archive(rubricId: string) {
    return request.post(
      `/eval/rubrics/${encodeURIComponent(rubricId)}/archive`,
      undefined,
      SILENT_HEADERS,
    )
  },
  versions(rubricId: string, params?: { page?: number; page_size?: number }) {
    return request.get(`/eval/rubrics/${encodeURIComponent(rubricId)}/versions`, { params })
  },
  createVersion(
    rubricId: string,
    payload: { items: RubricItem[]; change_note?: string | null },
  ) {
    return request.post(
      `/eval/rubrics/${encodeURIComponent(rubricId)}/versions`,
      payload,
      SILENT_HEADERS,
    )
  },
  getVersion(rubricId: string, versionNo: number) {
    return request.get(`/eval/rubrics/${encodeURIComponent(rubricId)}/versions/${versionNo}`)
  },
  diff(rubricId: string, baseVersion: number, targetVersion: number) {
    return request.get(`/eval/rubrics/${encodeURIComponent(rubricId)}/diff`, {
      params: { base_version: baseVersion, target_version: targetVersion },
    })
  },
  createRun(rubricId: string, payload: RubricRunRequestPayload) {
    return request.post(
      `/eval/rubrics/${encodeURIComponent(rubricId)}/runs`,
      payload,
      SILENT_HEADERS,
    )
  },
  listRuns(params?: { rubric_id?: string; page?: number; page_size?: number }) {
    return request.get('/eval/runs', { params })
  },
  getRun(runId: string) {
    return request.get(`/eval/runs/${encodeURIComponent(runId)}`)
  },
}
