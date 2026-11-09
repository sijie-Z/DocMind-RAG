import { describe, it, expect, beforeAll, afterEach } from 'vitest'
import i18n from '@/locales'
import en from '@/locales/en'
import { formatDate, formatDateTime, formatFileSize, formatDuration, parseApiDate, truncateText } from '../format'

describe('formatDate', () => {
  it('returns "-" for null/undefined', () => {
    expect(formatDate(null)).toBe('-')
    expect(formatDate(undefined)).toBe('-')
    expect(formatDate('')).toBe('-')
  })

  it('returns "-" for invalid date string', () => {
    expect(formatDate('not-a-date')).toBe('-')
  })

  it('returns "刚刚" for dates less than 1 minute ago', () => {
    const now = new Date()
    expect(formatDate(now)).toBe('刚刚')
  })

  it('returns minutes ago for recent dates', () => {
    const fiveMinAgo = new Date(Date.now() - 5 * 60 * 1000)
    expect(formatDate(fiveMinAgo)).toBe('5分钟前')
  })

  it('returns hours ago', () => {
    const threeHoursAgo = new Date(Date.now() - 3 * 60 * 60 * 1000)
    expect(formatDate(threeHoursAgo)).toBe('3小时前')
  })

  it('returns days ago', () => {
    const twoDaysAgo = new Date(Date.now() - 2 * 24 * 60 * 60 * 1000)
    expect(formatDate(twoDaysAgo)).toBe('2天前')
  })

  it('returns weeks ago', () => {
    const twoWeeksAgo = new Date(Date.now() - 14 * 24 * 60 * 60 * 1000)
    expect(formatDate(twoWeeksAgo)).toBe('2周前')
  })

  it('returns formatted date for old dates', () => {
    const oldDate = new Date('2020-01-15T10:30:00')
    const result = formatDate(oldDate)
    expect(result).toContain('2020')
    expect(result).toContain('01')
  })

  it('accepts Date objects directly', () => {
    const date = new Date()
    expect(formatDate(date)).toBe('刚刚')
  })
})

describe('formatFileSize', () => {
  it('returns "0 B" for 0 bytes', () => {
    expect(formatFileSize(0)).toBe('0 B')
  })

  it('formats bytes correctly', () => {
    expect(formatFileSize(500)).toBe('500 B')
  })

  it('formats kilobytes correctly', () => {
    expect(formatFileSize(1024)).toBe('1 KB')
    expect(formatFileSize(1536)).toBe('1.5 KB')
  })

  it('formats megabytes correctly', () => {
    expect(formatFileSize(1024 * 1024)).toBe('1 MB')
    expect(formatFileSize(5 * 1024 * 1024)).toBe('5 MB')
  })

  it('formats gigabytes correctly', () => {
    expect(formatFileSize(1024 * 1024 * 1024)).toBe('1 GB')
  })
})

describe('formatDuration', () => {
  it('formats seconds', () => {
    expect(formatDuration(30)).toBe('30秒')
  })

  it('formats minutes and seconds', () => {
    expect(formatDuration(90)).toBe('1分30秒')
    expect(formatDuration(60)).toBe('1分0秒')
  })

  it('formats hours and minutes', () => {
    expect(formatDuration(3661)).toBe('1小时1分')
    expect(formatDuration(7200)).toBe('2小时0分')
  })
})

describe('truncateText', () => {
  it('returns original text if shorter than max', () => {
    expect(truncateText('hello', 10)).toBe('hello')
  })

  it('returns original text if equal to max', () => {
    expect(truncateText('hello', 5)).toBe('hello')
  })

  it('truncates and adds ellipsis when longer', () => {
    expect(truncateText('hello world', 5)).toBe('hello...')
  })

  it('handles empty string', () => {
    expect(truncateText('', 5)).toBe('')
  })
})

describe('formatDateTime（审计列表绝对时间）', () => {
  // 用相对当前时间构造：任何日期运行，legacy formatDate 都会返回「x小时前」。
  const iso = new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString()
  const year = String(new Date(iso).getFullYear())

  it('zh 输出绝对时间，不再是「x小时前」', () => {
    const text = formatDateTime(iso, 'zh')
    expect(text).toContain(year)
    expect(text).not.toContain('前')
  })

  it('en/ja/fr 跟随 locale，不回落中文相对词', () => {
    for (const locale of ['en', 'ja', 'fr']) {
      const text = formatDateTime(iso, locale)
      expect(text).toContain(year)
      expect(text).not.toContain('小时')
      expect(text).not.toContain('分钟')
    }
  })

  it('空值与非法日期返回 -', () => {
    expect(formatDateTime(null, 'en')).toBe('-')
    expect(formatDateTime('not-a-date', 'en')).toBe('-')
  })
})
describe('parseApiDate（后端 UTC 无时区字符串）', () => {
  it('无时区 ISO 按 UTC 解析（不再被当作本地时间）', () => {
    expect(parseApiDate('2026-11-08T19:15:33')?.toISOString()).toBe('2026-11-08T19:15:33.000Z')
  })

  it('空格分隔与毫秒同样按 UTC 解析', () => {
    expect(parseApiDate('2026-11-08 19:15:33.250')?.toISOString()).toBe('2026-11-08T19:15:33.250Z')
  })

  it('带 Z / offset 的字符串保持原有时刻', () => {
    expect(parseApiDate('2026-11-08T19:15:33Z')?.toISOString()).toBe('2026-11-08T19:15:33.000Z')
    expect(parseApiDate('2026-11-08T19:15:33+08:00')?.toISOString()).toBe('2026-11-08T11:15:33.000Z')
  })

  it('纯日期按规范即 UTC，不受补 Z 影响', () => {
    expect(parseApiDate('2026-11-08')?.toISOString()).toBe('2026-11-08T00:00:00.000Z')
  })

  it('秒可省略（后端仍可能给到分钟精度）', () => {
    expect(parseApiDate('2026-11-08T19:15')?.toISOString()).toBe('2026-11-08T19:15:00.000Z')
  })

  it('Date 对象：有效原样返回，Invalid Date 返回 null', () => {
    const d = new Date('2026-11-08T19:15:33Z')
    expect(parseApiDate(d)).toBe(d)
    expect(parseApiDate(new Date('nope'))).toBeNull()
  })

  it('空白字符串与数字字符串返回 null', () => {
    expect(parseApiDate('   ')).toBeNull()
    expect(parseApiDate('1759926405')).toBeNull()
  })

  it('空值/非法值返回 null', () => {
    expect(parseApiDate(null)).toBeNull()
    expect(parseApiDate('')).toBeNull()
    expect(parseApiDate('not-a-date')).toBeNull()
  })

  it('formatDateTime 对「无时区」与「带 Z」输出同一时刻', () => {
    expect(formatDateTime('2026-11-08T19:15:33', 'zh')).toBe(
      formatDateTime('2026-11-08T19:15:33Z', 'zh'),
    )
  })

  it('formatDate 的相对时间基于 UTC 解析（本地时区不再偏移）', () => {
    const naiveUtc = new Date(Date.now() - 5 * 60 * 1000).toISOString().slice(0, 19)
    expect(formatDate(naiveUtc)).toBe('5分钟前')
  })
})

describe('相对时间/时长跟随界面语言（全局 i18n）', () => {
  beforeAll(() => {
    // 测试环境只内置 zh；en 包按需装载后切 locale
    i18n.global.setLocaleMessage('en', en as never)
  })

  afterEach(() => {
    i18n.global.locale.value = 'zh'
  })

  it('en：just now / minutes / hours / days / weeks', () => {
    (i18n.global.locale as unknown as { value: string }).value = 'en'
    expect(formatDate(new Date())).toBe('Just now')
    expect(formatDate(new Date(Date.now() - 5 * 60 * 1000))).toBe('5 min ago')
    expect(formatDate(new Date(Date.now() - 3 * 3600 * 1000))).toBe('3 h ago')
    expect(formatDate(new Date(Date.now() - 2 * 86400 * 1000))).toBe('2 d ago')
    expect(formatDate(new Date(Date.now() - 14 * 86400 * 1000))).toBe('2 wk ago')
  })

  it('en：时长单位不再出现中文', () => {
    (i18n.global.locale as unknown as { value: string }).value = 'en'
    expect(formatDuration(30)).toBe('30s')
    expect(formatDuration(90)).toBe('1m 30s')
    expect(formatDuration(3661)).toBe('1h 1m')
  })

  it('切回 zh 后恢复中文相对时间', () => {
    i18n.global.locale.value = 'zh'
    expect(formatDate(new Date(Date.now() - 5 * 60 * 1000))).toBe('5分钟前')
    expect(formatDuration(90)).toBe('1分30秒')
  })
})
