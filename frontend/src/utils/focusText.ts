/**
 * 将「全文 + 命中块原文」切成可渲染片段，供预览弹窗与文档详情页共用。
 *
 * 只用纯文本切片、不用 v-html：命中块来自服务端解析文本，仍按不可信内容处理。
 * 命中块带首尾空白时退回 trim 形态；找不到时安静降级为整段非高亮。
 */
export interface FocusTextSegment {
  text: string
  focus: boolean
}

export function splitFocusText(content: string, focusText?: string | null): FocusTextSegment[] {
  const text = content || ''
  const raw = focusText || ''
  const needle = [raw, raw.trim()].find((candidate) => candidate && text.includes(candidate)) || ''
  if (!text) return []
  if (!needle) return [{ text, focus: false }]
  const index = text.indexOf(needle)
  return [
    { text: text.slice(0, index), focus: false },
    { text: text.slice(index, index + needle.length), focus: true },
    { text: text.slice(index + needle.length), focus: false }
  ].filter((segment) => segment.text.length > 0)
}
