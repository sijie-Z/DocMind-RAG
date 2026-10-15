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
 * 文档状态真源 —— **双词兼容**（原两套词表已由后端收敛，兼容层保留）：
 * - 列表 GET /knowledge/ 本波起同吐 DB 枚举原词 'indexed'
 *   （fix/status-vocab 收敛；此前改写为 'completed'，浏览器复验 K1 实锤）；
 * - 详情 GET /documents/{id} 原样吐 'indexed'（documents.py），两端同构；
 * - 'completed' 是列表端点收敛前的旧词，在途分支/旧缓存仍可能到达 ——
 *   双词容忍：completed / indexed 同一语义，均渲染「已索引」，统计同计。
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

/** 非终态集合（终态 = completed / indexed / failed）：轮询 gate 与统计口径共用 */
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
    // 双词容忍：indexed 为收敛后唯一词表，completed 为收敛前列表旧词 —— 同计
    if (item.status === 'indexed' || item.status === 'completed') completed += 1
    else if (item.status === 'failed') failed += 1
    else if (isInProgressDoc(item.status)) processing += 1
  }
  return { total: list.length, completed, processing, failed }
}
