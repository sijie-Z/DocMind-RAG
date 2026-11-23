/**
 * 结构化索引错误码 → 可操作提示的展示映射（竞品批次四）。
 *
 * 后端闭集见 backend/app/services/index_progress.py；前端只在此单点按 code
 * 映射到 i18n 键，其他组件不得直接比较错误码字符串或写死文案。
 *
 * 安全口径：只展示后端脱敏后的 error_detail（有界、无异常原文/正文/查询）；
 * parse_error 原文仍由旧的错误详情模态框承载，二者不得混用。
 */
import i18n from '@/locales'
import type { IndexErrorCode, IndexPipeline, IndexStage } from '@/api/knowledge'

export const INDEX_STAGES: readonly IndexStage[] = ['parse', 'chunk', 'embed', 'index']

/** 阶段闭集 + 未知值兜底（旧文档没有 index_pipeline）。 */
export const indexStageLabel = (stage: IndexStage | null | undefined): string => {
  const key = stage && INDEX_STAGES.includes(stage)
    ? `knowledge.indexStage.${stage}`
    : 'knowledge.indexStage.unknown'
  return i18n.global.t(key)
}

/**
 * 错误码 → { 提示键, 可重试 }。
 * 未登记码统一走 unknown，绝不把后端原始文本当提示展示。
 */
const CODE_MESSAGES: Record<IndexErrorCode, { key: string; retryable: boolean }> = {
  PARSE_PROFILE_INVALID: { key: 'knowledge.indexError.PARSE_PROFILE_INVALID', retryable: true },
  PARSE_UNSUPPORTED_FORMAT: { key: 'knowledge.indexError.PARSE_UNSUPPORTED_FORMAT', retryable: true },
  PARSE_FAILED: { key: 'knowledge.indexError.PARSE_FAILED', retryable: true },
  CHUNK_EMPTY: { key: 'knowledge.indexError.CHUNK_EMPTY', retryable: true },
  CHUNK_FAILED: { key: 'knowledge.indexError.CHUNK_FAILED', retryable: true },
  EMBED_COUNT_MISMATCH: { key: 'knowledge.indexError.EMBED_COUNT_MISMATCH', retryable: true },
  EMBED_FAILED: { key: 'knowledge.indexError.EMBED_FAILED', retryable: true },
  INDEX_WRITE_FAILED: { key: 'knowledge.indexError.INDEX_WRITE_FAILED', retryable: true },
  INDEX_VERIFICATION_FAILED: { key: 'knowledge.indexError.INDEX_VERIFICATION_FAILED', retryable: true },
  INDEX_FAILED: { key: 'knowledge.indexError.INDEX_FAILED', retryable: true },
  DOCUMENT_NOT_FOUND: { key: 'knowledge.indexError.DOCUMENT_NOT_FOUND', retryable: false },
  JOB_SCHEDULE_FAILED: { key: 'knowledge.indexError.JOB_SCHEDULE_FAILED', retryable: true },
  UNEXPECTED: { key: 'knowledge.indexError.UNEXPECTED', retryable: true },
}

const UNKNOWN = { key: 'knowledge.indexError.UNEXPECTED', retryable: true }

export const indexErrorMessage = (code: IndexErrorCode | null | undefined): string =>
  i18n.global.t((code && CODE_MESSAGES[code]?.key) || UNKNOWN.key)

/** 可重试语义：文档不存在是终局，其余码走既有 rebuild 入口（不新增破坏性动作）。 */
export const canRetryIndexError = (code: IndexErrorCode | null | undefined): boolean =>
  (code && CODE_MESSAGES[code]?.retryable) ?? UNKNOWN.retryable

/** 失败时的一行展示文本：阶段 · 可操作提示；成功/无错误返回空串。 */
export const indexPipelineSummary = (pipeline: IndexPipeline | null | undefined): string => {
  if (!pipeline?.error_code) return ''
  return `${indexStageLabel(pipeline.stage)} · ${indexErrorMessage(pipeline.error_code)}`
}
