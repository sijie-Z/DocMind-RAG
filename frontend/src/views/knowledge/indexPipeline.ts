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
 * 错误码 → i18n 键。
 * 未登记码统一走 UNEXPECTED 兜底提示，绝不把后端原始文本当提示展示。
 *
 * 重试语义：本模块不提供任何操作入口。失败文档的重试仍只走页面既有的
 * rebuild 按钮；未知码与 UNEXPECTED 一样提示「稍后重试」，不新增破坏性动作。
 */
const CODE_MESSAGES: Record<IndexErrorCode, string> = {
  PARSE_PROFILE_INVALID: 'knowledge.indexError.PARSE_PROFILE_INVALID',
  PARSE_UNSUPPORTED_FORMAT: 'knowledge.indexError.PARSE_UNSUPPORTED_FORMAT',
  PARSE_FAILED: 'knowledge.indexError.PARSE_FAILED',
  CHUNK_EMPTY: 'knowledge.indexError.CHUNK_EMPTY',
  CHUNK_FAILED: 'knowledge.indexError.CHUNK_FAILED',
  EMBED_COUNT_MISMATCH: 'knowledge.indexError.EMBED_COUNT_MISMATCH',
  EMBED_FAILED: 'knowledge.indexError.EMBED_FAILED',
  INDEX_WRITE_FAILED: 'knowledge.indexError.INDEX_WRITE_FAILED',
  INDEX_VERIFICATION_FAILED: 'knowledge.indexError.INDEX_VERIFICATION_FAILED',
  INDEX_FAILED: 'knowledge.indexError.INDEX_FAILED',
  DOCUMENT_NOT_FOUND: 'knowledge.indexError.DOCUMENT_NOT_FOUND',
  JOB_SCHEDULE_FAILED: 'knowledge.indexError.JOB_SCHEDULE_FAILED',
  UNEXPECTED: 'knowledge.indexError.UNEXPECTED',
}

const UNKNOWN_CODE_KEY = 'knowledge.indexError.UNEXPECTED'

export const indexErrorMessage = (code: IndexErrorCode | null | undefined): string =>
  i18n.global.t((code && CODE_MESSAGES[code]) || UNKNOWN_CODE_KEY)

/** 失败时的一行展示文本：阶段 · 可操作提示；成功/无错误返回空串。 */
export const indexPipelineSummary = (pipeline: IndexPipeline | null | undefined): string => {
  if (!pipeline?.error_code) return ''
  return `${indexStageLabel(pipeline.stage)} · ${indexErrorMessage(pipeline.error_code)}`
}
