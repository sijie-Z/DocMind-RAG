import { describe, it, expect } from 'vitest'
import { formatDate, formatFileSize, formatDuration, truncateText } from '../format'

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
