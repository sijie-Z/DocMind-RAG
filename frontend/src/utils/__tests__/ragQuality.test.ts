/**
 * Dashboard RAG 质量分档：样本不足时不得判负。
 *
 * 变异：删掉 insufficient / no-data 分支（直接按命中率分档）→ 前两个用例红。
 */
import { describe, it, expect } from 'vitest'
import { classifyRagQuality, RAG_QUALITY_MIN_SAMPLES } from '../ragQuality'

describe('classifyRagQuality', () => {
  it('0 次检索 → no-data（不产生质量结论）', () => {
    expect(classifyRagQuality(0, 0)).toBe('no-data')
  })

  it('1 次检索 0 命中 → insufficient（不再判「需关注」/「较差」）', () => {
    expect(classifyRagQuality(1, 0)).toBe('insufficient')
  })

  it('样本量低于阈值（1..minSamples-1）一律 insufficient', () => {
    for (let n = 1; n < RAG_QUALITY_MIN_SAMPLES; n += 1) {
      expect(classifyRagQuality(n, 100), `n=${n}`).toBe('insufficient')
    }
  })

  it('样本充足后按既有阈值分档：>60 良好、>30 一般、其余较差', () => {
    expect(classifyRagQuality(10, 61)).toBe('good')
    expect(classifyRagQuality(10, 31)).toBe('fair')
    expect(classifyRagQuality(10, 30)).toBe('poor')
    expect(classifyRagQuality(10, 0)).toBe('poor')
  })

  it('边界不抖动：恰好 60 落「一般」、恰好 30 落「较差」（与既有 `>` 语义一致）', () => {
    expect(classifyRagQuality(5, 60)).toBe('fair')
    expect(classifyRagQuality(5, 30)).toBe('poor')
  })

  it('脏数据（负数 / NaN）不产生结论', () => {
    expect(classifyRagQuality(-1, 50)).toBe('no-data')
    expect(classifyRagQuality(Number.NaN, 50)).toBe('no-data')
    expect(classifyRagQuality(5, Number.NaN)).toBe('no-data')
  })

  it('阈值可注入：minSamples=1 时 1 次检索即可分档', () => {
    expect(classifyRagQuality(1, 0, 1)).toBe('poor')
  })
})
