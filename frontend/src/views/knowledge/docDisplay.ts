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
