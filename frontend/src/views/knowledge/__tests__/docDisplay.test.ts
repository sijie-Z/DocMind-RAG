/**
 * file_type 枚举配色判别用例（UI 验收 round2 · B15）。
 *
 * 真源：models/document.py:25-32 → pdf/word/excel/ppt/txt/other。
 * 原前端配色表是 'docx'/'doc'/'md' 死值域（Word 恒通用图标+失配色）。
 *
 * 变异点：配色表改回 docx/doc/md 死键 → 「真实枚举六值」用例精确红。
 */
import { describe, it, expect } from 'vitest'
import i18n, { setLanguage } from '@/locales'
import { docIconColor, docStatusLabel, docStatusType, isInProgressDoc, computeDocStats } from '../docDisplay'

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

describe('文档状态机 · 六值域 + 详情同义词（B16/K1 双词表）', () => {
  it('标签逐一正确 —— pending/parsing 绝不再显示「已完成」；completed/indexed 统一「已索引」', () => {
    expect(docStatusLabel('pending')).toBe('等待处理')
    expect(docStatusLabel('uploaded')).toBe('已上传')
    expect(docStatusLabel('parsing')).toBe('解析中')
    expect(docStatusLabel('parsed')).toBe('已解析')
    expect(docStatusLabel('completed')).toBe('已索引')
    // 详情端点词表（documents.py:296-303）：indexed 同一语义，不得回退「未知」
    expect(docStatusLabel('indexed')).toBe('已索引')
    expect(docStatusLabel('failed')).toBe('失败')

    // 判别：旧实现对非终态落 else 分支显示「已完成」
    expect(docStatusLabel('pending')).not.toBe('已索引')
    expect(docStatusLabel('parsing')).not.toBe('已索引')
    expect(docStatusLabel('uploaded')).not.toBe('已索引')
    // 判别：旧映射表不含 indexed → 曾回退「未知」（K1 弹窗现场）
    expect(docStatusLabel('indexed')).not.toBe('未知')
  })

  it('色档：pending/uploaded=info、parsing/parsed=warning、completed/indexed=success、failed=error', () => {
    expect(docStatusType('pending')).toBe('info')
    expect(docStatusType('uploaded')).toBe('info')
    expect(docStatusType('parsing')).toBe('warning')
    expect(docStatusType('parsed')).toBe('warning')
    expect(docStatusType('completed')).toBe('success')
    expect(docStatusType('indexed')).toBe('success')
    expect(docStatusType('failed')).toBe('error')
  })

  it('isInProgressDoc：非终态为 true，终态/空值 false（轮询 gate 口径）', () => {
    expect(isInProgressDoc('pending')).toBe(true)
    expect(isInProgressDoc('uploaded')).toBe(true)
    expect(isInProgressDoc('parsing')).toBe(true)
    expect(isInProgressDoc('parsed')).toBe(true)
    expect(isInProgressDoc('completed')).toBe(false)
    expect(isInProgressDoc('failed')).toBe(false)
    expect(isInProgressDoc('processing')).toBe(false) // 旧死值也应为 false（不是非终态口径）
    expect(isInProgressDoc(null)).toBe(false)
  })

  it('computeDocStats：非终态计入 processing（旧 `status === "processing"` 口径恒 0）', () => {
    const stats = computeDocStats([
      { status: 'parsing' },
      { status: 'pending' },
      { status: 'completed' },
      { status: 'failed' },
      { status: 'uploaded' },
    ])
    expect(stats).toEqual({ total: 5, completed: 1, processing: 3, failed: 1 })

    // 全非终态页：旧口径 processing=0，新口径=2
    expect(computeDocStats([{ status: 'parsing' }, { status: 'pending' }]).processing).toBe(2)
  })

  it('词表收敛（第二轮 7.2⑤）：列表索引终态吐 indexed 后，completed 计数不归零', () => {
    // 收敛前列表吐 'completed'、详情吐 'indexed'；收敛后列表也吐 'indexed'。
    // 统计卡口径必须双词同计「已索引」，否则列表页统计卡已完成恒 0。
    const stats = computeDocStats([
      { status: 'indexed' },
      { status: 'completed' },
      { status: 'parsing' },
      { status: 'failed' },
    ])
    expect(stats).toEqual({ total: 4, completed: 2, processing: 1, failed: 1 })
    // 判别：只认旧词时 completed 会少计（1 而非 2）
    expect(computeDocStats([{ status: 'indexed' }]).completed).toBe(1)
  })

  it('null/未知状态 → 未知 + default（不谎报任何终态）', () => {
    expect(docStatusLabel(null)).toBe('未知')
    expect(docStatusLabel(undefined)).toBe('未知')
    expect(docStatusType(null)).toBe('default')
    expect(docStatusType('weird')).toBe('default')
  })
})

describe('G波 · 状态词词条化（knowledge.docStatus.*）', () => {
  it('en/ja/fr 相位下返回对应译文（改动前恒为硬编码中文）', async () => {
    const prev = i18n.global.locale.value
    try {
      // E5：非默认语言包改为动态 chunk —— 切换统一走 setLanguage（先载包再落 locale）
      await setLanguage('en')
      expect(docStatusLabel('indexed')).toBe('Indexed')
      expect(docStatusLabel('pending')).toBe('Pending')
      expect(docStatusLabel('failed')).toBe('Failed')
      expect(docStatusLabel(null)).toBe('Unknown')
      await setLanguage('ja')
      expect(docStatusLabel('indexed')).toBe('インデックス済み')
      await setLanguage('fr')
      expect(docStatusLabel('indexed')).toBe('Indexé')
    } finally {
      await setLanguage(prev)
    }
  })
})
