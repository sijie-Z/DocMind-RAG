/**
 * 运行成本估算（体验第四波）：价目表默认值 / localStorage 覆盖 / 兜底档 /
 * 格式化（成本与时长）。
 *
 * 变异点：estimateCostUsd 去掉 blended 平均（如只取输入单价）→ 数值用例红；
 * getPriceTable 不合并覆盖 → 覆盖用例红。
 */
import { describe, it, expect, afterEach } from 'vitest'
import {
  COST_TABLE_STORAGE_KEY,
  DEFAULT_PRICE_TABLE,
  estimateCostUsd,
  formatCostUsd,
  formatDuration,
  getPriceTable,
} from '@/utils/cost'

afterEach(() => {
  localStorage.removeItem(COST_TABLE_STORAGE_KEY)
})

describe('utils/cost · estimateCostUsd', () => {
  it('默认模型按加权平均单价折算（v4-flash 10 万 token → $0.035）', () => {
    const blended = (DEFAULT_PRICE_TABLE['deepseek-v4-flash'].inputPerM
      + DEFAULT_PRICE_TABLE['deepseek-v4-flash'].outputPerM) / 2
    expect(estimateCostUsd('deepseek-v4-flash', 100_000)).toBeCloseTo(100_000 / 1e6 * blended, 6)
    expect(estimateCostUsd('deepseek-v4-flash', 100_000)).toBeCloseTo(0.035, 6)
  })

  it('未收录模型 → 兜底 default 档（不静默归零）', () => {
    expect(estimateCostUsd('some-future-model', 1_000_000)).toBeCloseTo(0.35, 6)
  })

  it('0 / undefined / 负数 → 0', () => {
    expect(estimateCostUsd('deepseek-v4-flash', 0)).toBe(0)
    expect(estimateCostUsd('deepseek-v4-flash', undefined)).toBe(0)
    expect(estimateCostUsd('deepseek-v4-flash', -5)).toBe(0)
  })

  it('totalTokens 为 NaN / Infinity → 0（评审 F5：估算不发散）', () => {
    expect(estimateCostUsd('deepseek-v4-flash', NaN)).toBe(0)
    expect(estimateCostUsd('deepseek-v4-flash', Infinity)).toBe(0)
  })

  it('价目表覆盖含 Infinity / 负值 → 忽略该覆盖、走 default 档（评审 F5）', () => {
    // JSON 溢出：1e999 解析为 Infinity（NaN 无法由 JSON 表达，Infinity 是等价探针）
    localStorage.setItem(COST_TABLE_STORAGE_KEY, '{"m-inf":{"inputPerM":1e999,"outputPerM":4}}')
    expect(estimateCostUsd('m-inf', 1_000_000)).toBeCloseTo(0.35, 6)

    localStorage.setItem(COST_TABLE_STORAGE_KEY, JSON.stringify({
      'm-neg': { inputPerM: -1, outputPerM: -1 },
    }))
    expect(estimateCostUsd('m-neg', 1_000_000)).toBeCloseTo(0.35, 6)

    // 合法覆盖仍生效（防止校验过严把正常配置也拒了）
    localStorage.setItem(COST_TABLE_STORAGE_KEY, JSON.stringify({
      'm-ok': { inputPerM: 2, outputPerM: 4 },
    }))
    expect(estimateCostUsd('m-ok', 1_000_000)).toBeCloseTo(3, 6)
  })

  it('localStorage 覆盖价目表（可配置）；坏 JSON / 坏形状 → 忽略覆盖', () => {
    localStorage.setItem(COST_TABLE_STORAGE_KEY, JSON.stringify({
      'my-model': { inputPerM: 2, outputPerM: 4 },
    }))
    expect(estimateCostUsd('my-model', 1_000_000)).toBeCloseTo(3, 6)
    // 覆盖不吞默认项
    expect(estimateCostUsd('deepseek-v4-flash', 1_000_000)).toBeCloseTo(0.35, 6)

    localStorage.setItem(COST_TABLE_STORAGE_KEY, '{bad json')
    expect(estimateCostUsd('deepseek-v4-flash', 1_000_000)).toBeCloseTo(0.35, 6)

    localStorage.setItem(COST_TABLE_STORAGE_KEY, JSON.stringify({
      'bad-model': { inputPerM: 'x' },
    }))
    expect(estimateCostUsd('bad-model', 1_000_000)).toBeCloseTo(0.35, 6)
  })

  it('getPriceTable 返回默认表 + 合并覆盖', () => {
    const before = getPriceTable()
    expect(before['deepseek-chat']).toEqual(DEFAULT_PRICE_TABLE['deepseek-chat'])
    localStorage.setItem(COST_TABLE_STORAGE_KEY, JSON.stringify({
      'deepseek-chat': { inputPerM: 1, outputPerM: 1 },
    }))
    expect(getPriceTable()['deepseek-chat']).toEqual({ inputPerM: 1, outputPerM: 1 })
  })
})

describe('utils/cost · 格式化', () => {
  it('formatCostUsd：四位小数 / 极小值下界 / 0', () => {
    expect(formatCostUsd(0.035)).toBe('$0.0350')
    expect(formatCostUsd(0.00043)).toBe('$0.0004')
    expect(formatCostUsd(0.00005)).toBe('<$0.0001')
    expect(formatCostUsd(0)).toBe('$0.0000')
  })

  it('formatDuration：mm:ss 与 h:mm:ss', () => {
    expect(formatDuration(0)).toBe('00:00')
    expect(formatDuration(65_000)).toBe('01:05')
    expect(formatDuration(3_725_000)).toBe('1:02:05')
    expect(formatDuration(-100)).toBe('00:00')
  })
})
