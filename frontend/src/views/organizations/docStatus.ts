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
 */

export type OrgDocStatusType = 'success' | 'warning' | 'error' | 'info' | 'default'

const STATUS_LABELS: Record<string, string> = {
  pending: '等待处理',
  uploaded: '已上传',
  parsing: '解析中',
  parsed: '已解析',
  indexed: '已索引',
  completed: '已索引', // 与知识库侧词表统一（completed==indexed 同一语义；K1 双词表修复）
  failed: '失败',
}

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
  (status && STATUS_LABELS[status]) || '未知'

export const orgDocStatusType = (status: string | null | undefined): OrgDocStatusType =>
  (status && STATUS_TYPES[status]) || 'default'
