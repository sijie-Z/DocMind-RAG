/**
 * file_type 枚举配色判别用例（UI 验收 round2 · B15）。
 *
 * 真源：models/document.py:25-32 → pdf/word/excel/ppt/txt/other。
 * 原前端配色表是 'docx'/'doc'/'md' 死值域（Word 恒通用图标+失配色）。
 *
 * 变异点：配色表改回 docx/doc/md 死键 → 「真实枚举六值」用例精确红。
 */
import { describe, it, expect } from 'vitest'
import { docIconColor } from '../docDisplay'

describe('docIconColor · 与后端 DocumentType 枚举对齐（B15）', () => {
  it('真实枚举六值全部命中且颜色各异（pdf/word/excel/ppt/txt/other）', () => {
    expect(docIconColor('pdf')).toBe('#ff6b6b')
    expect(docIconColor('word')).toBe('#4dabf7')
    expect(docIconColor('excel')).toBe('#51cf66')
    expect(docIconColor('ppt')).toBe('#ffa94d')
    expect(docIconColor('txt')).toBe('#868e96')
    expect(docIconColor('other')).toBe('#adb5bd')
  })

  it('死值域 docx/doc 不再命中 word 蓝（旧表已清除）', () => {
    expect(docIconColor('docx')).not.toBe('#4dabf7')
    expect(docIconColor('doc')).not.toBe('#4dabf7')
    expect(docIconColor('docx')).toBe('#868e96') // 走灰色兜底
  })

  it('空值/未知类型 → 灰色兜底，不崩', () => {
    expect(docIconColor('')).toBe('#868e96')
    expect(docIconColor(null)).toBe('#868e96')
    expect(docIconColor(undefined)).toBe('#868e96')
    expect(docIconColor('whatever')).toBe('#868e96')
  })
})
