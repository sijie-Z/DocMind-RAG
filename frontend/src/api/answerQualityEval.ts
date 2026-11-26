/**
 * 答案质量评测 API 客户端（运行历史 / 详情 / 趋势 / 创建）。
 *
 * 口径（与后端 docstring 一致，必须保持）：
 * - 本客户端对应的是**生成答案质量**评测：faithfulness / relevancy /
 *   context_precision 三个固定维度的 LLM-as-judge 连续分；
 * - 与检索评测（`/knowledge/eval/*`，recall@k / MRR）和评分表评测
 *   （`/eval/rubrics/*`，可编辑加权 yes/no）不共用运行记录；
 * - 运行只保存指标、有界评审理由、来源哈希与脱敏预览，原始
 *   query/answer/context 不出现在响应里；
 * - 失败运行（评委不可用）`aggregate` / `results` 为 null，绝不把「没测」
 *   伪装成 0 分。
 */
import request from '@/utils/request'

export const ANSWER_QUALITY_METRICS = [
  'faithfulness',
  'relevancy',
  'context_precision'
] as const

export type AnswerQualityMetric = (typeof ANSWER_QUALITY_METRICS)[number]
export type AnswerQualityRunMode = 'judge' | 'fake'
export type AnswerQualityRunStatus = 'completed' | 'failed'
export type AnswerQualityStatusFilter = AnswerQualityRunStatus | 'all'

export interface AnswerQualityMetrics {
  faithfulness: number | null
  relevancy: number | null
  context_precision: number | null
}

export interface AnswerQualityAggregate extends AnswerQualityMetrics {
  cases_scored: number
  note: string
}

/** 来源引用：身份哈希 + 脱敏预览，绝不返回原文。 */
export interface AnswerQualitySourceRef {
  ref_sha256: string
  masked_preview: string | null
}

export interface AnswerQualityCaseResult {
  case_id: string
  metrics: AnswerQualityMetrics
  /** 评委理由（有界、可缺省）；fake 模式为空对象。 */
  reasons: Partial<Record<AnswerQualityMetric, string>>
  source_refs: AnswerQualitySourceRef[]
}

export interface AnswerQualityRunSummary {
  id: string
  organization_id: number
  user_id: number
  dataset_hash: string
  judge_model: string
  prompt_version: string
  mode: AnswerQualityRunMode
  status: AnswerQualityRunStatus
  case_count: number
  /** 列表行也携带三维聚合值；失败运行为 null。 */
  metrics: AnswerQualityMetrics
  error_code: string | null
  created_by: number
  created_at: string | null
  completed_at: string | null
}

export interface AnswerQualityRunDetail extends AnswerQualityRunSummary {
  aggregate: AnswerQualityAggregate | null
  results: AnswerQualityCaseResult[] | null
}

export interface AnswerQualityRunList {
  items: AnswerQualityRunSummary[]
  total: number
  page: number
  page_size: number
}

export interface AnswerQualityTrendPoint {
  id: string
  created_at: string | null
  status: AnswerQualityRunStatus
  mode: AnswerQualityRunMode
  judge_model: string
  prompt_version: string
  case_count: number
  metrics: AnswerQualityMetrics
  error_code: string | null
}

export interface AnswerQualityTrendSummary {
  runs: number
  succeeded_runs: number
  failed_runs: number
  /** 失败运行不参与均值；无成功运行时为 null。 */
  averages: AnswerQualityMetrics
}

export interface AnswerQualityTrend {
  points: AnswerQualityTrendPoint[]
  summary: AnswerQualityTrendSummary
}

export interface AnswerQualityRunListParams {
  user_id?: number
  status?: AnswerQualityStatusFilter
  /** UTC 日期（含），格式 YYYY-MM-DD。 */
  date_from?: string
  /** UTC 日期（含），格式 YYYY-MM-DD。 */
  date_to?: string
  page?: number
  page_size?: number
}

export type AnswerQualityTrendParams = Omit<AnswerQualityRunListParams, 'page' | 'page_size'> & {
  limit?: number
}

export interface AnswerQualityRunCasePayload {
  case_id: string
  question: string
  answer: string
  context?: Array<Record<string, unknown>>
  /** fake 模式必填且必须恰好覆盖三个指标（0..1）；judge 模式忽略。 */
  fake_scores?: Record<AnswerQualityMetric, number> | null
}

export interface AnswerQualityRunRequestPayload {
  mode: AnswerQualityRunMode
  /** 单次运行最多 20 条（后端 schema 上限）。 */
  cases: AnswerQualityRunCasePayload[]
}

const SILENT_HEADERS = { headers: { 'X-Silent-Error': '1' } }

/**
 * 创建运行会逐条调用评委模型（最多 20 条），默认 30s 不够；
 * 与 rubric 创建运行同一处理：不自动弹全局错误，由调用方呈现语义。
 */
export const ANSWER_QUALITY_CREATE_TIMEOUT_MS = 120_000

export const answerQualityEvalApi = {
  list(params: AnswerQualityRunListParams = {}) {
    return request.get('/eval/answer-quality/runs', { params })
  },
  trend(params: AnswerQualityTrendParams = {}) {
    return request.get('/eval/answer-quality/runs/trend', { params })
  },
  get(runId: string) {
    return request.get(`/eval/answer-quality/runs/${encodeURIComponent(runId)}`)
  },
  create(payload: AnswerQualityRunRequestPayload) {
    return request.post('/eval/answer-quality/runs', payload, {
      ...SILENT_HEADERS,
      timeout: ANSWER_QUALITY_CREATE_TIMEOUT_MS
    })
  }
}
