/**
 * 知识库页：解析错误兜底文案走词条（不得是硬编码中文）。
 *
 * 背景：`showErrorDetail` 的兜底 `'未知解析错误'` 是硬编码中文，且
 * `knowledge.unknownParseError` 四语此前根本不存在 —— 英文界面下打开
 * 空错误详情的模态框会显示中文。
 *
 * 变异点：删掉任一语种的 unknownParseError → 用例红；en/ja/fr 照抄中文
 * → 用例红（逐语种与 zh 比对，不用 CJK 正则 —— 日文汉字是正常内容）。
 */
import { describe, it, expect } from 'vitest'

import zh from '@/locales/zh'
import en from '@/locales/en'
import ja from '@/locales/ja'
import fr from '@/locales/fr'

const val = (m: unknown): string => (m as { knowledge: { unknownParseError: string } }).knowledge.unknownParseError

describe('knowledge.unknownParseError · 四语齐备', () => {
  it('四语均非空，zh 为源文案', () => {
    expect(val(zh)).toBe('未知解析错误')
    for (const [name, m] of [['en', en], ['ja', ja], ['fr', fr]] as const) {
      expect(val(m), `${name}.knowledge.unknownParseError 应存在`).toBeTruthy()
    }
  })

  it('en/ja/fr 均为各自语言的译文（与 zh 不同）', () => {
    const zhVal = val(zh)
    for (const [name, m] of [['en', en], ['ja', ja], ['fr', fr]] as const) {
      expect(val(m), `${name} 不得照抄中文`).not.toBe(zhVal)
    }
    expect(val(en)).toBe('Unknown parsing error')
  })
})
