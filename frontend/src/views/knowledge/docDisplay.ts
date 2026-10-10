/**
 * 知识库文档展示层纯映射（真实枚举驱动）。
 *
 * file_type 真源：backend/app/models/document.py:25-32
 *   DocumentType = pdf / word / excel / ppt / txt / other
 * （knowledge.py:148 输出 .value）。前端此前的 'docx'/'doc'/'md' 分支是虚构值域
 * —— Word 文档永远落进通用图标分支、配色表失配（B15）。
 */

/** 文件类型 → 图标配色（按真实枚举重排；未知/空值统一灰） */
const FILE_TYPE_COLORS: Record<string, string> = {
  pdf: '#ff6b6b',
  word: '#4dabf7',
  excel: '#51cf66',
  ppt: '#ffa94d',
  txt: '#868e96',
  other: '#adb5bd',
}

const FALLBACK_COLOR = '#868e96'

export const docIconColor = (fileType: string | null | undefined): string =>
  FILE_TYPE_COLORS[fileType || ''] || FALLBACK_COLOR

/**
 * 文档状态真源 —— **两套词表**（浏览器复验 K1 实锤的接口漂移）：
 * - 列表 GET /knowledge/ 把 INDEXED 改写为 'completed'（knowledge.py:131-139
 *   status_map）：pending / uploaded / parsing / parsed / completed / failed；
 * - 详情 GET /documents/{id} 原样吐 'indexed'（documents.py:296-303）——
 *   详情弹窗合并详情响应后 status 变 'indexed'，此前映射表不含该词 →
 *   回退「未知」。此处统一：completed / indexed 同一语义，均渲染「已索引」。
 */
export type DocStatusType = 'success' | 'warning' | 'error' | 'info' | 'default'

const STATUS_LABELS: Record<string, string> = {
  pending: '等待处理',
  uploaded: '已上传',
  parsing: '解析中',
  parsed: '已解析',
  completed: '已索引',
  indexed: '已索引', // 详情端点同义词（documents.py:296-303）
  failed: '失败',
}

const STATUS_TYPES: Record<string, DocStatusType> = {
  pending: 'info',
  uploaded: 'info',
  parsing: 'warning',
  parsed: 'warning',
  completed: 'success',
  indexed: 'success',
  failed: 'error',
}

export const docStatusLabel = (status: string | null | undefined): string =>
  (status && STATUS_LABELS[status]) || '未知'

export const docStatusType = (status: string | null | undefined): DocStatusType =>
  (status && STATUS_TYPES[status]) || 'default'

/** 非终态集合（终态 = completed / failed）：轮询 gate 与统计口径共用 */
const IN_PROGRESS_STATUSES: ReadonlySet<string> = new Set(['pending', 'uploaded', 'parsing', 'parsed'])

export const isInProgressDoc = (status: string | null | undefined): boolean =>
  !!status && IN_PROGRESS_STATUSES.has(status)

export interface DocStats {
  total: number
  completed: number
  processing: number
  failed: number
}

/** 统计卡口径（真实状态域；旧 `status === 'processing'` 口径恒 0） */
export const computeDocStats = (list: Array<{ status?: string | null }>): DocStats => {
  let completed = 0
  let processing = 0
  let failed = 0
  for (const item of list) {
    if (item.status === 'completed') completed += 1
    else if (item.status === 'failed') failed += 1
    else if (isInProgressDoc(item.status)) processing += 1
  }
  return { total: list.length, completed, processing, failed }
}
