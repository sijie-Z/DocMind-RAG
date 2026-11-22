/** 检索评测运行历史 API（组织隔离由后端保证；本客户端只负责 URL/参数契约）。 */
import request from '@/utils/request'
import type { AxiosResponse } from 'axios'
import type { ApiResponse } from '@/types/common'

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
