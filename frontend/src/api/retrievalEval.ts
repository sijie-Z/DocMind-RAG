/** 检索评测运行历史 API（组织隔离由后端保证；本客户端只负责 URL/参数契约）。 */
import request from '@/utils/request'
import type { AxiosResponse } from 'axios'
import type { ApiResponse } from '@/types/common'

export interface RetrievalEvalCase {
  id: string
  organization_id: number
  name: string
  query: string
  expected_document_ids: string[]
  tags: string[]
  created_by: number
  created_at: string | null
  updated_at: string | null
}

/**
 * 保存为评测用例的载荷（bad-case 捕获，POST /knowledge/eval/cases/capture）。
 * 不传 name 时后端取 query 前 100 字符；同组织同 query + 同金标集合幂等返回
 * 既有用例（响应 `created=false`）。
 */
export interface CaptureEvalCasePayload {
  query: string
  expected_document_ids: string[]
  name?: string
  tags?: string[]
}

export interface CaptureEvalCaseResult {
  case: RetrievalEvalCase
  /** true = 新建；false = 命中同组织既有用例（幂等，未产生新行） */
  created: boolean
}

/**
 * 把一次不理想的检索保存为评测用例。
 *
 * 后端信封为 `{ success, created, data }`——`created` 与 `data` 并列，不能走
 * 通用的「取 data.data」解包假设，这里显式读取两个字段，避免把标志丢掉。
 */
export const captureEvalCase = async (
  payload: CaptureEvalCasePayload
): Promise<CaptureEvalCaseResult> => {
  const response = await request.post('/knowledge/eval/cases/capture', payload)
  const body = response.data as {
    success?: boolean
    created?: boolean
    data?: RetrievalEvalCase
  }
  return { case: body.data as RetrievalEvalCase, created: body.created === true }
}

export interface EvalRunMetrics {
  recall_at_k: number | null
  mrr: number | null
  document_hit_at_k: number | null
  hit_at_k: number | null
}

export interface EvalRunCounts {
  cases: number
  evaluated: number
  errors: number
}

export interface EvalRunSummary {
  id: string
  organization_id: number
  created_by: number
  top_k: number
  case_ids: string[]
  case_scope: 'all' | 'subset' | string
  case_set_hash: string
  counts: EvalRunCounts
  metrics: EvalRunMetrics
  error_case_ids: string[]
  created_at: string | null
}

export interface EvalRunDetail extends EvalRunSummary {
  results: Array<{ recall_at_k?: number | null; [key: string]: unknown }>
}

export interface EvalTrend {
  points: EvalRunSummary[]
  summary: {
    runs: number
    succeeded_runs: number
    failed_runs: number
    averages: EvalRunMetrics
  }
}

export interface EvalRunList {
  data: EvalRunSummary[]
  total: number
}

export interface RunEvalPayload {
  case_ids?: string[] | null
  top_k?: number
  /** 默认 false；UI 的「运行并保存」显式传 true。 */
  persist?: boolean
}

export const listEvalRuns = async (params?: {
  page?: number
  page_size?: number
  case_set_hash?: string
}): Promise<AxiosResponse<ApiResponse<EvalRunList>>> => request.get('/knowledge/eval/runs', { params })

export const getEvalRun = async (id: string): Promise<AxiosResponse<ApiResponse<EvalRunDetail>>> =>
  request.get(`/knowledge/eval/runs/${encodeURIComponent(id)}`)

export const getEvalTrend = async (params?: {
  case_set_hash?: string
  limit?: number
}): Promise<AxiosResponse<ApiResponse<EvalTrend>>> =>
  request.get('/knowledge/eval/runs/trend', { params })

export const runEval = async (
  payload: RunEvalPayload
): Promise<AxiosResponse<ApiResponse<Record<string, unknown>>>> =>
  request.post('/knowledge/eval/run', payload)

export const exportEvalRun = async (
  id: string,
  format: 'json' | 'csv'
): Promise<AxiosResponse<Blob>> =>
  request.get(`/knowledge/eval/runs/${encodeURIComponent(id)}/export`, {
    params: { format },
    responseType: 'blob'
  })
