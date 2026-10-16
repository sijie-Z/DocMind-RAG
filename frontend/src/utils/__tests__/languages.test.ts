/**
 * ISS-4 · 语言清单单一事实源（utils/languages.ts）——
 * 值域 / 自称 / Beta 标注 / 兜底语言自洽性。
 */
import { describe, expect, it } from 'vitest'
import { FALLBACK_LANGUAGE, LANGUAGES, LANGUAGE_VALUES, isLanguageCode } from '../languages'

describe('LANGUAGES 语言清单', () => {
  it('恰好四种语言，码值与 locale 加载面一致', () => {
    expect(LANGUAGE_VALUES).toEqual(['zh', 'en', 'ja', 'fr'])
  })

  it('label 为语言自称，均非空且互异', () => {
    const labels = LANGUAGES.map((l) => l.label)
    expect(labels.every((l) => l.length > 0)).toBe(true)
    expect(new Set(labels).size).toBe(labels.length)
  })

  it('Beta 标注已全撤：四语均词条完备（ja/fr 随 T13 补齐至零缺口，机制保留）', () => {
    expect(LANGUAGES.filter((l) => l.beta).map((l) => l.value)).toEqual([])
  })

  it('兜底语言 en 必须存在且非 Beta（棘轮硬闸的清单侧表达）', () => {
    expect(FALLBACK_LANGUAGE).toBe('en')
    const fallback = LANGUAGES.find((l) => l.value === FALLBACK_LANGUAGE)
    expect(fallback, '兜底语言必须在清单中').toBeDefined()
    expect(fallback?.beta, '兜底语言不得带 Beta 标注（en 必须 100% 完备）').toBe(false)
  })

  it('isLanguageCode 守卫：合法放行，垃圾串 / 空串 / null 拒绝', () => {
    expect(isLanguageCode('zh')).toBe(true)
    expect(isLanguageCode('fr')).toBe(true)
    expect(isLanguageCode('de')).toBe(false)
    expect(isLanguageCode('')).toBe(false)
    expect(isLanguageCode(null)).toBe(false)
    expect(isLanguageCode(undefined)).toBe(false)
    expect(isLanguageCode(3)).toBe(false)
  })
})
