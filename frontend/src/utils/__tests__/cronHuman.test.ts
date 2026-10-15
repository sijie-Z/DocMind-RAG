/**
 * cron 人话预览测试。
 *
 * t 用真实 zh 文案 + 迷你插值器（免挂 i18n 实例）；四语渲染在页面测试里覆盖。
 */
import { describe, expect, it } from 'vitest'
import { cronToHuman } from '../cronHuman'
import zh from '@/locales/zh'

function lookup(messages: Record<string, unknown>, key: string): unknown {
  return key
    .split('.')
    .reduce<unknown>((acc, part) => (acc && typeof acc === 'object' ? (acc as Record<string, unknown>)[part] : undefined), messages)
}

function makeT(messages: Record<string, unknown>) {
  return (key: string, params?: Record<string, unknown>) => {
    const raw = lookup(messages, key)
    if (typeof raw !== 'string') return key
    if (!params) return raw
    return raw.replace(/\{(\w+)\}/g, (_match, name: string) => String(params[name] ?? `{${name}}`))
  }
}

const t = makeT(zh as unknown as Record<string, unknown>)

describe('cronToHuman · 子集内形状', () => {
  it('每分钟', () => {
    expect(cronToHuman('* * * * *', t)).toBe('每分钟')
  })

  it('每 N 分钟（*/n）', () => {
    expect(cronToHuman('*/15 * * * *', t)).toBe('每 15 分钟')
  })

  it('*/1 归一为每分钟', () => {
    expect(cronToHuman('*/1 * * * *', t)).toBe('每分钟')
  })

  it('每小时第 m 分', () => {
    expect(cronToHuman('30 * * * *', t)).toBe('每小时的第 30 分')
  })

  it('每天（补零）', () => {
    expect(cronToHuman('0 9 * * *', t)).toBe('每天 09:00')
    expect(cronToHuman('5 0 * * *', t)).toBe('每天 00:05')
  })

  it('每周：范围折成"至"', () => {
    expect(cronToHuman('0 8 * * 1-5', t)).toBe('每周一至周五 08:00')
  })

  it('每周：列表用顿号连接', () => {
    expect(cronToHuman('0 8 * * 1,3', t)).toBe('每周一、周三 08:00')
  })

  it('每周：单日 + 7 归一为周日', () => {
    expect(cronToHuman('15 0 * * 7', t)).toBe('每周日 00:15')
    expect(cronToHuman('15 0 * * 0', t)).toBe('每周日 00:15')
  })

  it('每月：单日/列表/范围', () => {
    expect(cronToHuman('0 0 1 * *', t)).toBe('每月1日 00:00')
    expect(cronToHuman('0 0 1,15 * *', t)).toBe('每月1、15日 00:00')
    expect(cronToHuman('0 0 1-5 * *', t)).toBe('每月1至5日 00:00')
  })

  it('每年：单月单日', () => {
    expect(cronToHuman('0 0 1 3 *', t)).toBe('每年3月1日 00:00')
    expect(cronToHuman('30 14 20 12 *', t)).toBe('每年12月20日 14:30')
  })
})

describe('cronToHuman · 子集外/非法 → null（不硬译）', () => {
  it('字段数不对', () => {
    expect(cronToHuman('0 9 * *', t)).toBeNull()
    expect(cronToHuman('0 9 * * * *', t)).toBeNull()
    expect(cronToHuman('', t)).toBeNull()
  })

  it('非法数值/非数字', () => {
    expect(cronToHuman('61 0 * * *', t)).toBeNull()
    expect(cronToHuman('0 25 * * *', t)).toBeNull()
    expect(cronToHuman('0 0 32 * *', t)).toBeNull()
    expect(cronToHuman('0 0 * 13 *', t)).toBeNull()
    expect(cronToHuman('abc * * * *', t)).toBeNull()
    expect(cronToHuman('0 9 * * MON', t)).toBeNull() // 非数字星期名：后端也不支持
  })

  it('合法但超子集：不硬译', () => {
    expect(cronToHuman('0 9,18 * * *', t)).toBeNull() // 小时列表
    expect(cronToHuman('*/20 9-18 * * *', t)).toBeNull() // 小时范围
    expect(cronToHuman('0 */6 * * *', t)).toBeNull() // 小时步进
    expect(cronToHuman('10-30/5 * * * *', t)).toBeNull() // 带步进的分钟范围
    expect(cronToHuman('0 0 * * 1,3-5', t)).toBeNull() // 混合列表
    expect(cronToHuman('0 0 1 * 1', t)).toBeNull() // 日与周同时限制（Vixie OR 语义）
    expect(cronToHuman('0 9 * 3 *', t)).toBeNull() // 月限制但日=*（非本子集形状）
  })

  it('范围倒置 / 步长为 0', () => {
    expect(cronToHuman('0 0 15-1 * *', t)).toBeNull()
    expect(cronToHuman('*/0 * * * *', t)).toBeNull()
  })
})
