/**
 * 组织详情「知识库」tab 的文档状态映射（真实枚举驱动）。
 *
 * GET /organizations/{org_id}/documents 原样吐 ORM 枚举 value
 * （organizations.py:423-433 未做 status_map 映射）：
 *   pending / uploaded / parsing / parsed / indexed / failed
 * 注意与知识库列表口径的差异：这里 INDEXED 是裸的 'indexed'
 * （知识库列表端点会改写为 'completed'，见 knowledge.py:131-139）。
 * A_sweep 实锤：tag 直出英文 'indexed'，且 `status === 'completed'` 判色落
 * warning(黄) —— 文案与色档双漂移。
 *
 * T13：状态文案不再硬编码中文——复用知识库侧既有词条 knowledge.docStatus.*
 * （经全局 i18n 实例解析，与 knowledge/docDisplay.ts 同一词表），en/ja/fr
 * 界面不再回落中文。
 */
import i18n from '@/locales'

export type OrgDocStatusType = 'success' | 'warning' | 'error' | 'info' | 'default'

const STATUS_KEYS: Record<string, string> = {
  pending: 'knowledge.docStatus.pending',
  uploaded: 'knowledge.docStatus.uploaded',
  parsing: 'knowledge.docStatus.parsing',
  parsed: 'knowledge.docStatus.parsed',
  indexed: 'knowledge.docStatus.indexed',
  completed: 'knowledge.docStatus.indexed', // 与知识库侧词表统一（completed==indexed 同一语义；K1 双词表修复）
  failed: 'knowledge.docStatus.failed',
}

const UNKNOWN_STATUS_KEY = 'knowledge.docStatus.unknown'

const STATUS_TYPES: Record<string, OrgDocStatusType> = {
  pending: 'info',
  uploaded: 'info',
  parsing: 'warning',
  parsed: 'warning',
  indexed: 'success',
  completed: 'success',
  failed: 'error',
}

export const orgDocStatusLabel = (status: string | null | undefined): string =>
  i18n.global.t((status && STATUS_KEYS[status]) || UNKNOWN_STATUS_KEY)

export const orgDocStatusType = (status: string | null | undefined): OrgDocStatusType =>
  (status && STATUS_TYPES[status]) || 'default'
